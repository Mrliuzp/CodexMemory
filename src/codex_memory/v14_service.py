"""V1.4 任务事件写入与确定性报告投影服务。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal, require_permission, require_project_access
from .persistence.db_models import AuditLogRow, MemoryRow, ProjectRow
from .persistence.v11_models import OutboxEventRow
from .persistence.v14_models import TaskEventRow, TaskFileChangeRow, TaskReportRow, TaskRunRow
from .idempotency import IdempotencyKeyBuilder


EVENT_TYPES = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"}
MAX_EVENT_BYTES = 64 * 1024
MAX_COMMAND_BYTES = 4 * 1024
MAX_RESULT_BYTES = 8 * 1024
_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|authorization|bearer|credential|password|secret|token)", re.I)
_SENSITIVE_VALUE = re.compile(r"(?i)\b(?:bearer\s+|basic\s+)[A-Za-z0-9._~+/=-]+|(?:sk|gh[pousr]|xox[baprs])-[A-Za-z0-9_-]{8,}")


class TaskEventConflictError(Exception):
    def __init__(self, event_id: int) -> None:
        self.event_id = event_id
        super().__init__("event_key_conflict")


class TaskEventValidationError(ValueError):
    pass


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact(value: Any, *, key: str | None = None) -> tuple[Any, bool]:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]", True
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        applied = False
        for item_key, item in value.items():
            clean, changed = _redact(item, key=str(item_key))
            result[str(item_key)] = clean
            applied = applied or changed
        return result, applied
    if isinstance(value, list):
        result = []
        applied = False
        for item in value:
            clean, changed = _redact(item)
            result.append(clean)
            applied = applied or changed
        return result, applied
    if isinstance(value, str):
        clean = _SENSITIVE_VALUE.sub("[REDACTED]", value)
        return clean, clean != value
    return value, False


def _truncate(value: str | None, limit: int) -> tuple[str | None, int, str | None, bool]:
    if value is None:
        return None, 0, None, False
    original_length = len(value.encode("utf-8"))
    digest = _sha256(value)
    if original_length <= limit:
        return value, original_length, digest, False
    raw = value.encode("utf-8")[: max(0, limit - len("…".encode("utf-8")))]
    while True:
        try:
            return raw.decode("utf-8") + "…", original_length, digest, True
        except UnicodeDecodeError:
            raw = raw[:-1]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class TaskEventService:
    """负责项目隔离、幂等事件入库和同事务 Outbox 投递。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def append_event(
        self,
        principal: Principal,
        *,
        project_key: str,
        session_key: str,
        event_key: str,
        event_type: str,
        occurred_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        command_summary: str | None = None,
        result_summary: str | None = None,
        exit_code: int | None = None,
        git: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        require_permission(principal, "append")
        require_project_access(principal, project_key)
        if not session_key.strip() or not event_key.strip():
            raise TaskEventValidationError("session_key 和 event_key 不能为空")
        if event_type not in EVENT_TYPES:
            raise TaskEventValidationError(f"不支持的事件类型：{event_type}")

        source_payload = payload or {}
        if command_summary is None and isinstance(source_payload.get("command_summary"), str):
            command_summary = source_payload["command_summary"]
        if result_summary is None and isinstance(source_payload.get("result_summary"), str):
            result_summary = source_payload["result_summary"]
        if exit_code is None and isinstance(source_payload.get("exit_code"), int):
            exit_code = source_payload["exit_code"]
        if not git and isinstance(source_payload.get("git"), dict):
            git = source_payload["git"]
        clean_payload, payload_redacted = _redact(source_payload)
        clean_metadata, metadata_redacted = _redact(metadata or {})
        clean_git, git_redacted = _redact(git or {})
        clean_command, command_redacted = _redact(command_summary, key="command_summary")
        clean_result, result_redacted = _redact(result_summary, key="result_summary")
        command, command_length, command_hash, command_truncated = _truncate(
            clean_command if isinstance(clean_command, str) else None, MAX_COMMAND_BYTES
        )
        result, result_length, result_hash, result_truncated = _truncate(
            clean_result if isinstance(clean_result, str) else None, MAX_RESULT_BYTES
        )
        normalized = {
            "event_type": event_type,
            "payload": clean_payload,
            "metadata": clean_metadata,
            "git": clean_git,
            "command_summary": clean_command,
            "result_summary": clean_result,
            "exit_code": exit_code,
        }
        normalized_text = _canonical(normalized)
        original_length = len(normalized_text.encode("utf-8"))
        if original_length > MAX_EVENT_BYTES and not (command_truncated or result_truncated):
            raise TaskEventValidationError("事件载荷超过 64 KiB 限制")
        content_hash = _sha256(normalized_text)
        stored_payload = {
            "payload": clean_payload,
            "metadata": clean_metadata,
            "git": clean_git,
            "event_type": event_type,
            "command_summary": command,
            "result_summary": result,
            "exit_code": exit_code,
            "redaction_applied": payload_redacted or metadata_redacted or git_redacted or command_redacted or result_redacted,
        }
        if len(_canonical(stored_payload).encode("utf-8")) > MAX_EVENT_BYTES:
            raise TaskEventValidationError("脱敏和截断后的事件载荷仍超过 64 KiB 限制")
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"项目不存在：{project_key}")
            run = session.scalar(
                select(TaskRunRow).where(TaskRunRow.project_id == project.id, TaskRunRow.session_key == session_key)
            )
            if run is None:
                run = TaskRunRow(project_id=project.id, session_key=session_key, status="running")
                session.add(run)
                session.flush()
            existing = session.scalar(
                select(TaskEventRow).where(TaskEventRow.task_run_id == run.id, TaskEventRow.event_key == event_key)
            )
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise TaskEventConflictError(existing.id)
                return {"id": existing.id, "event_id": existing.id, "task_run_id": run.id, "status": "duplicate"}
            sequence = int(session.scalar(select(TaskEventRow.sequence_no).where(TaskEventRow.task_run_id == run.id).order_by(TaskEventRow.sequence_no.desc()).limit(1)) or 0) + 1
            event = TaskEventRow(
                project_id=project.id,
                task_run_id=run.id,
                event_key=event_key,
                event_type=event_type,
                sequence_no=sequence,
                occurred_at=_utc_naive(occurred_at),
                payload_json=stored_payload,
                metadata_json=clean_metadata if isinstance(clean_metadata, dict) else {},
                content_hash=content_hash,
                original_length=original_length,
                command_summary=command,
                command_original_length=command_length,
                command_sha256=command_hash,
                result_summary=result,
                result_original_length=result_length,
                result_sha256=result_hash,
                exit_code=exit_code,
                redaction_applied=payload_redacted or metadata_redacted or git_redacted or command_redacted or result_redacted,
                truncated=command_truncated or result_truncated,
            )
            session.add(event)
            session.flush()
            session.add(
                OutboxEventRow(
                    project_id=project.id,
                    aggregate_type="task_event",
                    aggregate_id=event.id,
                    event_type="task.event.received.v1",
                    payload_version="v1.4",
                    idempotency_key=IdempotencyKeyBuilder(project_key).build("task.event.received", "task_event", event.id, "v1.4"),
                    payload={"project_id": project.id, "project_key": project_key, "task_run_id": run.id, "event_id": event.id},
                )
            )
            session.add(AuditLogRow(project_id=project.id, event_type="task_event_appended", subject_type="task_event", subject_id=event_key, metadata_json={"event_type": event_type}))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                duplicate = session.scalar(select(TaskEventRow).where(TaskEventRow.task_run_id == run.id, TaskEventRow.event_key == event_key))
                if duplicate is None:
                    raise
                if duplicate.content_hash != content_hash:
                    raise TaskEventConflictError(duplicate.id)
                return {"id": duplicate.id, "event_id": duplicate.id, "task_run_id": run.id, "status": "duplicate"}
            return {"id": event.id, "event_id": event.id, "task_run_id": run.id, "status": "accepted"}


class TaskReportProjector:
    """消费 task event 并生成不依赖 LLM 的确定性报告。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def handle(self, event_id: int) -> TaskReportRow | None:
        with self.session_factory() as session:
            event = session.get(TaskEventRow, event_id)
            if event is None:
                raise LookupError(f"任务事件不存在：{event_id}")
            run = session.get(TaskRunRow, event.task_run_id)
            if run is None or run.project_id != event.project_id:
                raise LookupError("任务事件与项目不匹配")
            if event.event_type == "PreToolUse" and run.git_available is None:
                self._capture_baseline(run, event)
                session.commit()
                return None
            if event.event_type not in {"Stop", "SessionEnd"}:
                session.commit()
                return None
            existing = session.scalar(select(TaskReportRow).where(TaskReportRow.source_event_id == event.id))
            if existing is not None:
                return existing
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                session.refresh(run, with_for_update=True)
            else:
                session.expire(run)
                run = session.get(TaskRunRow, event.task_run_id)
            revision = int(run.current_report_revision or 0) + 1
            kind = "checkpoint" if event.event_type == "Stop" else "final"
            events = session.scalars(select(TaskEventRow).where(TaskEventRow.task_run_id == run.id).order_by(TaskEventRow.sequence_no, TaskEventRow.id)).all()
            changes = self._changes_for(events)
            uncertain = self._is_uncertain(run)
            if kind == "final":
                run.status = "completed"
                run.ended_at = _utc_naive(event.occurred_at)
            report_json = self._report_json(run, events, changes, kind, revision, uncertain)
            body = _canonical(report_json)
            report = TaskReportRow(
                project_id=run.project_id,
                task_run_id=run.id,
                source_event_id=event.id,
                revision=revision,
                report_kind=kind,
                status=run.status,
                report_json=report_json,
                body=body,
                content_hash=_sha256(body),
                uncertain=uncertain,
                truncated=any(item.truncated for item in events),
            )
            session.add(report)
            session.flush()
            for index, item in enumerate(changes):
                session.add(TaskFileChangeRow(project_id=run.project_id, task_run_id=run.id, report_id=report.id, change_index=index, path=str(item.get("path", "")), old_path=item.get("old_path"), change_type=str(item.get("change_type", "modified")), before_hash=item.get("before_hash"), after_hash=item.get("after_hash"), attribution="uncertain" if uncertain else str(item.get("attribution", "certain")), metadata_json=item.get("metadata", {})))
            run.current_report_revision = revision
            self._project_l1(session, run, report)
            session.add(AuditLogRow(project_id=run.project_id, event_type="task_report_created", subject_type="task_report", subject_id=str(report.id), metadata_json={"kind": kind, "revision": revision}))
            session.commit()
            return report

    @staticmethod
    def _capture_baseline(run: TaskRunRow, event: TaskEventRow) -> None:
        payload = event.payload_json or {}
        git = payload.get("git", {}) if isinstance(payload, dict) else {}
        if not isinstance(git, dict):
            git = {}
        run.git_branch = git.get("branch") or git.get("git_branch")
        run.git_head = git.get("head") or git.get("git_head")
        run.git_status_porcelain = git.get("status_porcelain") or git.get("status")
        run.git_diff_hash = git.get("diff_hash")
        run.git_untracked_json = git.get("untracked")
        run.git_available = git.get("available", bool(git))

    @staticmethod
    def _is_uncertain(run: TaskRunRow) -> bool:
        return run.git_available is False or bool((run.git_status_porcelain or "").strip())

    @staticmethod
    def _changes_for(events: list[TaskEventRow]) -> list[dict[str, Any]]:
        result: dict[tuple[str, str | None], dict[str, Any]] = {}
        for event in events:
            payload = event.payload_json or {}
            raw_changes = payload.get("file_changes") or payload.get("changes") if isinstance(payload, dict) else []
            if not isinstance(raw_changes, list):
                continue
            for item in raw_changes:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                clean, _ = _redact(item)
                key = (str(clean.get("path")), clean.get("old_path"))
                result[key] = clean
        return sorted(result.values(), key=lambda item: (str(item.get("path", "")), str(item.get("old_path", "")), str(item.get("change_type", "modified"))))

    @staticmethod
    def _report_json(run: TaskRunRow, events: list[TaskEventRow], changes: list[dict[str, Any]], kind: str, revision: int, uncertain: bool) -> dict[str, Any]:
        return {
            "run": {"id": run.id, "session_key": run.session_key, "status": "completed" if kind == "final" else run.status, "revision": revision},
            "timeline": [{"sequence": event.sequence_no, "event_key": event.event_key, "event_type": event.event_type, "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None} for event in events],
            "tool_summary": [{"event_key": event.event_key, "command_summary": event.command_summary, "result_summary": event.result_summary, "exit_code": event.exit_code} for event in events if event.event_type == "PostToolUse"],
            "git_baseline": {"branch": run.git_branch, "head": run.git_head, "status_porcelain": run.git_status_porcelain, "diff_hash": run.git_diff_hash, "untracked": run.git_untracked_json, "available": run.git_available},
            "change_manifest": {"changes": changes, "count": len(changes)},
            "attribution": {"status": "uncertain" if uncertain else "certain", "reason": "基线存在既有变更或 Git 不可用" if uncertain else "基线可用且未发现既有变更"},
            "integrity": {"kind": kind, "uncertain": uncertain, "truncated": any(item.truncated for item in events)},
            "limitations": ["报告由固定输入确定性生成", "V1.4 不调用 LLM，也不生成 L2/L3 记忆"],
        }

    @staticmethod
    def _project_l1(session: Session, run: TaskRunRow, report: TaskReportRow) -> None:
        content = {"report_id": report.id, "task_run_id": run.id, "revision": report.revision, "kind": report.report_kind, "status": report.status, "body": report.body}
        existing = session.scalars(select(MemoryRow).where(MemoryRow.project_id == run.project_id, MemoryRow.memory_type == "task_report", MemoryRow.level == "L1")).all()
        if any(row.content.get("report_id") == report.id for row in existing):
            return
        session.add(MemoryRow(project_id=run.project_id, level="L1", memory_type="task_report", title=f"任务报告 #{report.id}", content=content, status="active", confidence=1.0, scope="project", source_kind="task_report", review_status="accepted"))


__all__ = ["EVENT_TYPES", "TaskEventConflictError", "TaskEventValidationError", "TaskEventService", "TaskReportProjector"]
