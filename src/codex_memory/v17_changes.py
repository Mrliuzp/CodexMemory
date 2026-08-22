"""V1.7 Shadow ChangeSet 生成与服务端校验。"""

from __future__ import annotations
import hashlib
import json
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .codex_cli_runner import CodexCliRequest, CodexCliRunner
from .decision_service import DecisionService
from .persistence.db_models import MemoryRow, ProjectRow
from .persistence.v17_models import MemoryChangeSetRow, MemoryWindowRow, ProjectMemoryWindowPolicyRow
from .v17_models import MemoryChangeSetOutput, memory_change_set_json_schema
from .v17_windows import MemoryWindowService


class MemoryChangeSetService:
    def __init__(self, session_factory: sessionmaker[Session], *, runner: CodexCliRunner | None = None) -> None:
        self.session_factory = session_factory
        self.runner = runner or CodexCliRunner()

    def generate(self, window_id: int) -> MemoryChangeSetRow:
        # 先锁住窗口，再检查既有结果并声明 processing。构建上下文和调用
        # Runner 必须发生在 claim 之后，否则两个请求都可能通过 sealed 检查。
        with self.session_factory() as session:
            window = session.scalar(
                select(MemoryWindowRow)
                .where(MemoryWindowRow.id == window_id)
                .with_for_update()
            )
            project = session.get(ProjectRow, window.project_id if window else 0)
            policy = session.get(ProjectMemoryWindowPolicyRow, window.project_id if window else 0)
            if window is None or project is None or policy is None or not policy.enabled or window.status not in {"sealed", "processing", "completed"}:
                raise ValueError("窗口不存在或尚未封口")
            input_hash = str(window.input_hash or "")
            if len(input_hash) != 64:
                raise ValueError("窗口未生成稳定 input_hash")
            existing = session.scalar(
                select(MemoryChangeSetRow)
                .where(MemoryChangeSetRow.window_id == window_id)
                .order_by(MemoryChangeSetRow.id.desc())
            )
            if existing is not None:
                if existing.input_hash != input_hash:
                    raise ValueError("ChangeSet 输入哈希已漂移")
                session.expunge(existing)
                return existing
            if window.status == "processing":
                raise RuntimeError("Memory Window 正在生成 ChangeSet")
            if window.status == "completed":
                # completed 但没有持久化 ChangeSet 属于不一致状态；宁可拒绝，
                # 也不能在不确定的情况下再次调用模型。
                raise RuntimeError("Memory Window 已完成但缺少 ChangeSet")
            window.status = "processing"
            session.commit()
            project_id = int(window.project_id)
            project_key = str(project.project_key or "")
        if not project_key:
            self._mark_window_failed(window_id)
            raise ValueError("窗口项目不存在")

        windows = MemoryWindowService(self.session_factory)
        run = None
        try:
            # claim 后再读取，确保 seal/build 阶段的 L0 哈希校验也覆盖
            # no_change 等无证据变更路径。
            context = windows.build_input(window_id)
            if str(context.get("input_hash") or "") != input_hash:
                raise ValueError("窗口 input_hash 已漂移")
            run = DecisionService(self.session_factory).start_run(project_id=project_id, model="codex-cli", prompt_version="memory-window-v1", input_hash=input_hash)
            result = self.runner.run(CodexCliRequest(project_key=project_key, task="根据窗口 L0 和已发布 L1 生成 MemoryChangeSet，不得直接写入 Memory。", context=context, output_schema=memory_change_set_json_schema(), request_id=f"memory-window:{window_id}"))
            output = MemoryChangeSetOutput.model_validate(result.data)
            self._validate_output(context, output)
        except Exception as error:
            if run is not None:
                DecisionService(self.session_factory).finish_run(run.id, status="failed", error_code="v17_changeset_validation_failed", error_message="ChangeSet 校验失败")
            self._mark_window_failed(window_id)
            raise ValueError("ChangeSet 生成或校验失败") from error
        try:
            DecisionService(self.session_factory).finish_run(run.id, status="succeeded", input_tokens=result.input_tokens, output_tokens=result.output_tokens, total_tokens=result.input_tokens + result.output_tokens)
            with self.session_factory() as session:
                row = MemoryChangeSetRow(project_id=int(context["project_id"]), window_id=window_id, decision_run_id=run.id, operation=output.operation, target_memory_id=output.target_memory_id, target_revision=output.target_revision, output_json=output.model_dump(mode="json"), input_hash=input_hash, status="shadow")
                session.add(row)
                window = session.get(MemoryWindowRow, window_id)
                if window is not None:
                    window.status = "completed"
                session.commit()
                session.refresh(row)
                session.expunge(row)
                return row
        except Exception as error:
            # 结果落库失败时不能留下 processing，避免后续请求误以为仍可继续。
            self._mark_window_failed(window_id)
            raise ValueError("ChangeSet 结果保存失败") from error

    def _mark_window_failed(self, window_id: int) -> None:
        """释放 processing claim；失败状态禁止后续无依据重跑。"""
        with self.session_factory() as session:
            row = session.scalar(
                select(MemoryWindowRow)
                .where(MemoryWindowRow.id == window_id)
                .with_for_update()
            )
            if row is not None and row.status == "processing":
                row.status = "failed"
                session.commit()

    def seal_and_generate(self, *, project_id: int, session_id: int, sealed_by: str = "admin") -> MemoryChangeSetRow:
        """封口后生成 ChangeSet 的唯一公共入口；重复调用只复用既有结果。"""
        window = MemoryWindowService(self.session_factory).seal(
            project_id=project_id, session_id=session_id, sealed_by=sealed_by
        )
        return self.generate(window.id)

    def review(self, change_set_id: int, *, action: str, reviewer: str, reason: str) -> MemoryChangeSetRow:
        if action not in {"approve", "reject"}:
            raise ValueError("审核动作必须是 approve 或 reject")
        if not reason.strip():
            raise ValueError("审核原因不能为空")
        with self.session_factory() as session:
            row = session.scalar(select(MemoryChangeSetRow).where(MemoryChangeSetRow.id == change_set_id).with_for_update())
            if row is None:
                raise LookupError("ChangeSet 不存在")
            if row.status != "shadow":
                # 相同审核动作是幂等重试；相反动作仍然拒绝，避免篡改审核结论。
                if (action == "approve" and row.status == "approved") or (action == "reject" and row.status == "rejected"):
                    session.expunge(row)
                    return row
                raise ValueError("ChangeSet 已审核，不能重复审核")
            row.status = "approved" if action == "approve" else "rejected"
            row.reviewer = reviewer[:255]
            row.review_reason = reason[:2000]
            from datetime import datetime, timezone
            row.reviewed_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def approve(self, change_set_id: int, *, reviewer: str, reason: str) -> MemoryChangeSetRow:
        return self.review(change_set_id, action="approve", reviewer=reviewer, reason=reason)

    def reject(self, change_set_id: int, *, reviewer: str, reason: str) -> MemoryChangeSetRow:
        return self.review(change_set_id, action="reject", reviewer=reviewer, reason=reason)

    @staticmethod
    def _validate_output(context: dict[str, Any], output: MemoryChangeSetOutput) -> None:
        message_ids = {int(item["message_id"]) for item in context.get("messages", [])}
        retrieved = {int(item["memory_id"]) for item in context.get("published_l1", [])}
        if not set(output.retrieved_memory_ids).issubset(retrieved):
            raise ValueError("模型使用了未检索的 target memory")
        if output.operation in {"create", "update"}:
            if not output.evidence_ranges:
                raise ValueError("变更必须包含证据")
            messages = {int(item["message_id"]): item for item in context.get("messages", [])}
            for item in output.evidence_ranges:
                message = messages.get(item.message_id)
                if message is None or item.end_char > len(str(message["content"])):
                    raise ValueError("证据不属于当前窗口")
                if hashlib.sha256(str(message["content"]).encode("utf-8")).hexdigest() != str(message["content_hash"]):
                    raise ValueError("窗口消息哈希已漂移")
                if str(message["content"])[item.start_char:item.end_char] != item.quote:
                    raise ValueError("证据区间或引用文本无效")
            content_text = json.dumps(output.content or {}, ensure_ascii=False, sort_keys=True)
            if any(item.quote not in content_text for item in output.evidence_ranges):
                raise ValueError("ChangeSet 内容必须引用证据原文")
        if output.operation == "update":
            if output.target_memory_id not in output.retrieved_memory_ids:
                raise ValueError("update target 必须出现在检索目标中")
            target = next((item for item in context["published_l1"] if int(item["memory_id"]) == output.target_memory_id), None)
            if target is None or int(target["revision"]) != output.target_revision:
                raise ValueError("target revision 已冲突")
        if output.level != "L1" or output.scope != "project" or output.risk_flags:
            raise ValueError("ChangeSet 只能是无风险 project/L1")
        if output.operation == "create":
            candidate = json.dumps(output.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for memory in context.get("published_l1", []):
                if json.dumps(memory.get("content") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == candidate:
                    raise ValueError("create 与现有 L1 重复")

    generate_change_set = generate


__all__ = ["MemoryChangeSetService"]
