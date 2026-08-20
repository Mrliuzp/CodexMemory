"""候选记忆决策 Worker 的稳定边界。

这个模块定义候选决策的输入、Codex CLI adapter 和失败语义。默认 Runner
关闭，不会访问真实模型；测试只能在 adapter 内替换 fake process。所有返回值
仍必须经过 CandidatePolicyService 的服务器策略门。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


DECISION_JOB_TYPE = "decide_candidate"
DECISION_HANDLER_VERSION = "candidate-decision-v1"
DECISION_EVENT_TYPE = "candidate.decision.requested.v1"
ACCEPTED_EVENT_TYPE = "candidate.accepted.v1"
ALLOWED_DECISIONS = frozenset({"publish", "skip", "needs_review"})


def resolve_l1_auto_publish_threshold(value: float | None = None) -> float:
    """解析项目 L1 自动发布阈值，默认高置信度 0.80。"""

    raw = value
    if raw is None:
        raw = os.environ.get("CODEX_MEMORY_L1_AUTO_PUBLISH_THRESHOLD", "0.80")
    try:
        threshold = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("L1 自动发布阈值必须是 0 到 1 之间的数字") from error
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("L1 自动发布阈值必须是 0 到 1 之间的数字")
    return threshold


@dataclass(frozen=True)
class CandidateEvidenceSnapshot:
    message_id: int
    start_char: int
    end_char: int
    quoted_text: str
    content_hash: str


@dataclass(frozen=True)
class CandidateSnapshot:
    """传给模型适配器的只读候选快照。

    快照不提供 L0 写入能力；适配器只能返回建议，不能直接写 Memory。
    """

    candidate_id: int
    project_id: int
    level: str
    scope: str
    memory_type: str
    title: str | None
    content: dict[str, Any]
    source_message_id: int | None
    evidence: tuple[CandidateEvidenceSnapshot, ...]
    project_key: str = ""


@dataclass(frozen=True)
class CandidateDecision:
    decision: str
    confidence: float | None = None
    reason_codes: tuple[str, ...] = ()
    model: str | None = None
    prompt_version: str | None = None
    abstain: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionApplication:
    """服务器策略门应用后的结果。"""

    candidate_id: int
    outcome: str
    candidate_status: str
    reason_codes: tuple[str, ...] = ()
    policy_result_id: int | None = None
    accepted_event_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "outcome": self.outcome,
            "candidate_status": self.candidate_status,
            "reason_codes": list(self.reason_codes),
            "policy_result_id": self.policy_result_id,
            "accepted_event_id": self.accepted_event_id,
        }


class CandidateDecisionModel(Protocol):
    """未来模型/CLI Runner 必须实现的最小适配接口。"""

    name: str
    version: str

    def decide(self, candidate: CandidateSnapshot) -> CandidateDecision | Mapping[str, Any]:
        """只返回建议，不得写入数据库或发布正式 Memory。"""


class CandidateDecisionError(ValueError):
    """模型调用、超时或输出校验失败，默认可重试。"""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class InvalidCandidateDecision(CandidateDecisionError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_model_output", message, retryable=True)


class CandidateModelUnavailable(CandidateDecisionError):
    def __init__(self, message: str = "候选决策模型暂不可用") -> None:
        super().__init__("model_unavailable", message, retryable=True)


class UnconfiguredCandidateDecisionModel:
    """默认安全适配器：不调用真实模型，所有候选进入人工队列。"""

    name = "unconfigured"
    version = "none"

    def decide(self, candidate: CandidateSnapshot) -> CandidateDecision:
        del candidate
        return CandidateDecision(
            decision="needs_review",
            reason_codes=("model_adapter_unconfigured",),
            model=self.name,
            prompt_version=self.version,
            abstain=True,
        )


class CodexCliDecisionAdapter:
    """把严格 ``ModelDecisionOutput`` 映射为候选 Worker 建议。

    Worker 只接收这个 adapter 的结果。Runner 本身不接触数据库，也没有发布
    正式 Memory 的能力；所有项目、层级、作用域、证据和风险门禁仍由服务器
    ``CandidatePolicyService`` 在事务内重新检查。
    """

    is_codex_cli_adapter = True
    version = "codex-cli-adapter-v1"

    def __init__(self, runner: Any | None = None) -> None:
        from ..codex_cli_runner import CodexCliRunner

        self.runner = runner or CodexCliRunner()
        configured_model = getattr(getattr(self.runner, "settings", None), "model", None)
        self.name = f"codex-cli:{configured_model or 'default'}"

    def decide(self, candidate: CandidateSnapshot) -> CandidateDecision:
        from pydantic import ValidationError

        from ..codex_cli_runner import (
            CodexCliError,
            CodexCliRequest,
        )
        from ..decision_models import ModelDecisionOutput, cli_decision_json_schema

        if not candidate.project_key.strip():
            raise CandidateDecisionError("project_key_missing", "候选快照缺少明确项目标识", retryable=False)
        context = {
            "project_key": candidate.project_key,
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "level": candidate.level,
                "scope": candidate.scope,
                "memory_type": candidate.memory_type,
                "title": candidate.title,
                "content": candidate.content,
                "source_message_id": candidate.source_message_id,
                "evidence": [
                    {
                        "message_id": item.message_id,
                        "start_char": item.start_char,
                        "end_char": item.end_char,
                        "quote": item.quoted_text,
                        "content_hash": item.content_hash,
                    }
                    for item in candidate.evidence
                ],
            },
        }
        request = CodexCliRequest(
            project_key=candidate.project_key,
            task="根据同项目候选和证据，返回严格的 ModelDecisionOutput；不执行任何写操作。",
            context=context,
            output_schema=cli_decision_json_schema(),
            request_id=f"candidate:{candidate.candidate_id}",
        )
        try:
            result = self.runner.run(request)
        except CodexCliError as error:
            retryable = bool(getattr(error, "retryable", False))
            if getattr(error, "code", "") in {
                "codex_cli_output_not_json",
                "codex_cli_output_schema_mismatch",
            }:
                retryable = True
            raise CandidateDecisionError(
                str(getattr(error, "code", "codex_cli_error")),
                str(getattr(error, "message", error)),
                retryable=retryable,
            ) from error
        except Exception as error:
            raise CandidateDecisionError("model_call_failed", "Codex CLI adapter 调用失败", retryable=True) from error

        try:
            output = ModelDecisionOutput.model_validate(result.data)
        except ValidationError as error:
            raise CandidateDecisionError(
                "invalid_model_output",
                "Codex CLI 返回内容未通过 ModelDecisionOutput 校验",
                retryable=True,
            ) from error

        output_payload = output.model_dump(mode="json")
        runner_metadata = {}
        as_dict = getattr(result, "as_dict", None)
        if callable(as_dict):
            runner_metadata = {key: value for key, value in as_dict().items() if key != "data"}
        metadata = {
            "model_decision": output_payload,
            "title": output.title,
            "content": output.content,
            "reason": output.reason,
            "level": output.level.value,
            "scope": output.scope.value,
            "evidence_ranges": [item.model_dump(mode="json") for item in output.evidence_ranges],
            "risk_flags": [item.value for item in output.risk_flags],
            "validation_passed": True,
            "runner": runner_metadata,
        }
        reason_codes = tuple(
            [flag.value for flag in output.risk_flags]
            or [
                "model_needs_review"
                if output.decision.value == "needs_review"
                else "model_skip"
                if output.decision.value == "skip"
                else "model_output_validated"
            ]
        )
        return CandidateDecision(
            decision=output.decision.value,
            confidence=float(output.confidence),
            reason_codes=reason_codes,
            model=self.name,
            prompt_version=self.version,
            abstain=output.decision.value == "needs_review",
            metadata=metadata,
        )


def normalize_candidate_decision(value: CandidateDecision | Mapping[str, Any]) -> CandidateDecision:
    """严格校验模型输出，防止任意 JSON 绕过策略门。"""

    if isinstance(value, CandidateDecision):
        decision = value
        if not isinstance(decision.decision, str):
            raise InvalidCandidateDecision("decision 必须是字符串")
        if decision.confidence is not None and (isinstance(decision.confidence, bool) or not isinstance(decision.confidence, (int, float))):
            raise InvalidCandidateDecision("confidence 必须是数字或 null")
        if isinstance(decision.reason_codes, str) or not isinstance(decision.reason_codes, Sequence):
            raise InvalidCandidateDecision("reason_codes 必须是字符串数组")
        if not all(isinstance(item, str) and item.strip() for item in decision.reason_codes):
            raise InvalidCandidateDecision("reason_codes 必须只包含非空字符串")
        if decision.model is not None and not isinstance(decision.model, str):
            raise InvalidCandidateDecision("model 必须是字符串或 null")
        if decision.prompt_version is not None and not isinstance(decision.prompt_version, str):
            raise InvalidCandidateDecision("prompt_version 必须是字符串或 null")
        if not isinstance(decision.abstain, bool) or not isinstance(decision.metadata, Mapping):
            raise InvalidCandidateDecision("abstain 或 metadata 类型无效")
    elif isinstance(value, Mapping):
        action = value.get("decision", value.get("action"))
        confidence = value.get("confidence")
        reasons = value.get("reason_codes", ())
        model = value.get("model")
        prompt_version = value.get("prompt_version")
        abstain = value.get("abstain", False)
        metadata = value.get("metadata", {})
        if not isinstance(action, str):
            raise InvalidCandidateDecision("模型输出缺少 decision")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
            raise InvalidCandidateDecision("confidence 必须是数字或 null")
        if isinstance(reasons, str) or not isinstance(reasons, Sequence):
            raise InvalidCandidateDecision("reason_codes 必须是字符串数组")
        if not all(isinstance(item, str) and item.strip() for item in reasons):
            raise InvalidCandidateDecision("reason_codes 必须只包含非空字符串")
        if model is not None and not isinstance(model, str):
            raise InvalidCandidateDecision("model 必须是字符串或 null")
        if prompt_version is not None and not isinstance(prompt_version, str):
            raise InvalidCandidateDecision("prompt_version 必须是字符串或 null")
        if not isinstance(abstain, bool):
            raise InvalidCandidateDecision("abstain 必须是布尔值")
        if not isinstance(metadata, Mapping):
            raise InvalidCandidateDecision("metadata 必须是对象")
        decision = CandidateDecision(
            decision=action,
            confidence=float(confidence) if confidence is not None else None,
            reason_codes=tuple(item.strip() for item in reasons),
            model=model,
            prompt_version=prompt_version,
            abstain=abstain,
            metadata=dict(metadata),
        )
    else:
        raise InvalidCandidateDecision("模型输出必须是 CandidateDecision 或对象")

    action = decision.decision.strip().lower() if isinstance(decision.decision, str) else ""
    if action not in ALLOWED_DECISIONS:
        raise InvalidCandidateDecision("decision 必须是 publish、skip 或 needs_review")
    confidence = decision.confidence
    if confidence is not None and (isinstance(confidence, bool) or not math.isfinite(float(confidence)) or not 0.0 <= float(confidence) <= 1.0):
        raise InvalidCandidateDecision("confidence 必须是 0 到 1 之间的有限数字")
    if action == "publish" and confidence is None:
        raise InvalidCandidateDecision("publish 决策必须提供 confidence")
    return CandidateDecision(
        decision=action,
        confidence=float(confidence) if confidence is not None else None,
        reason_codes=tuple(decision.reason_codes),
        model=decision.model,
        prompt_version=decision.prompt_version,
        abstain=decision.abstain,
        metadata=dict(decision.metadata),
    )


class CandidateDecisionWorker:
    """将模型建议交给服务器策略门，并处理模型失败降级。"""

    job_type = DECISION_JOB_TYPE
    handler_version = DECISION_HANDLER_VERSION

    def __init__(
        self,
        session_factory: Any,
        model: CandidateDecisionModel | None = None,
        *,
        runner: Any | None = None,
        decision_service: Any | None = None,
        auto_publish_threshold: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.model = model or CodexCliDecisionAdapter(runner)
        if not getattr(self.model, "is_codex_cli_adapter", False):
            raise TypeError("候选决策 Worker 只能使用 CodexCliRunner adapter")
        if decision_service is None:
            from ..decision_service import DecisionService

            decision_service = DecisionService(session_factory)
        self.decision_service = decision_service
        self.auto_publish_threshold = (
            resolve_l1_auto_publish_threshold(auto_publish_threshold)
            if auto_publish_threshold is not None
            else None
        )

    def validate(self, claim: Any) -> None:
        if claim.job_type != self.job_type:
            raise ValueError(f"不支持的候选决策任务类型：{claim.job_type}")
        if not isinstance(claim.payload.get("candidate_id"), int) or isinstance(claim.payload.get("candidate_id"), bool):
            raise ValueError("候选决策任务缺少 candidate_id")

    def execute(self, claim: Any, context: Any) -> Any:
        del context
        from .v11_candidates import CandidatePolicyService
        from .v13_handlers import HandlerResult

        service = CandidatePolicyService(self.session_factory, auto_publish_threshold=self.auto_publish_threshold)
        candidate_id = int(claim.payload["candidate_id"])
        current_status = service.get_candidate_status(candidate_id)
        if current_status in {"approved", "published", "rejected", "superseded"}:
            return HandlerResult(
                status="duplicate_noop",
                metadata={"candidate_id": candidate_id, "candidate_status": current_status},
            )
        service.begin_model_decision(candidate_id)
        if not service.decision_engine_enabled(candidate_id):
            error = CandidateDecisionError(
                "decision_engine_disabled",
                "项目 decision_engine_enabled 未开启",
                retryable=False,
            )
            service.record_model_failure(candidate_id, error.code, str(error), claim.attempt_no)
            raise error
        snapshot = service.get_candidate_snapshot(candidate_id)
        decision_service = self.decision_service
        decision_run = None
        try:
            decision_run = self._start_decision_run(decision_service, snapshot, claim)
            raw_result = self.model.decide(snapshot)
            decision = normalize_candidate_decision(raw_result)
            self._record_decision_contract(decision_service, decision_run, decision, candidate_id)
            self._finish_decision_run(decision_service, decision_run, claim, status="succeeded")
        except CandidateDecisionError as error:
            self._finish_decision_run_for_error(decision_service, decision_run, claim, error)
            service.record_model_failure(candidate_id, error.code, str(error), claim.attempt_no)
            raise
        except Exception as error:
            wrapped = CandidateDecisionError("model_call_failed", "模型调用失败", retryable=True)
            self._finish_decision_run_for_error(decision_service, decision_run, claim, wrapped)
            service.record_model_failure(candidate_id, wrapped.code, str(error), claim.attempt_no)
            raise wrapped from error
        result = service.apply_model_decision(candidate_id, decision)
        return HandlerResult(status=result.outcome, metadata=result.as_dict())

    def _start_decision_run(self, decision_service: Any, snapshot: CandidateSnapshot, claim: Any) -> Any:
        """为每次 adapter 调用建立 V1.6 可审计运行记录。"""

        from sqlalchemy import inspect

        with self.session_factory() as session:
            if session.bind is None or not inspect(session.bind).has_table("decision_runs"):
                raise CandidateDecisionError(
                    "decision_contract_unavailable",
                    "V1.6 决策持久化表不可用",
                    retryable=True,
                )
        snapshot_payload = {
            "project_key": snapshot.project_key,
            "candidate_id": snapshot.candidate_id,
            "project_id": snapshot.project_id,
            "level": snapshot.level,
            "scope": snapshot.scope,
            "memory_type": snapshot.memory_type,
            "title": snapshot.title,
            "content": snapshot.content,
            "source_message_id": snapshot.source_message_id,
            "evidence": [item.__dict__ for item in snapshot.evidence],
        }
        input_hash = hashlib.sha256(
            json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        try:
            return decision_service.start_run(
                project_id=snapshot.project_id,
                model=str(self.model.name)[:128],
                prompt_version=str(self.model.version)[:64],
                input_hash=input_hash,
                max_retries=int(claim.payload.get("max_retries", 3)),
                processing_job_id=claim.job_id,
                outbox_event_id=claim.payload.get("outbox_event_id"),
                metadata={"candidate_id": snapshot.candidate_id},
            )
        except Exception as error:
            raise CandidateDecisionError(
                "decision_run_start_failed",
                "无法建立决策运行审计记录",
                retryable=True,
            ) from error

    @staticmethod
    def _record_decision_contract(decision_service: Any, decision_run: Any, decision: CandidateDecision, candidate_id: int) -> None:
        from ..decision_service import DecisionValidationError

        payload = decision.metadata.get("model_decision")
        if decision_run is None or not isinstance(payload, Mapping):
            raise CandidateDecisionError(
                "invalid_model_output",
                "adapter 未提供严格 ModelDecisionOutput",
                retryable=True,
            )
        try:
            decision_service.record_decision(decision_run.id, payload, candidate_id=candidate_id)
        except DecisionValidationError as error:
            raise CandidateDecisionError(error.code, str(error), retryable=False) from error

    @staticmethod
    def _finish_decision_run(decision_service: Any, decision_run: Any, claim: Any, *, status: str) -> None:
        if decision_run is None:
            return
        try:
            decision_service.finish_run(decision_run.id, status=status, retry_count=claim.attempt_no)
        except Exception as error:
            raise CandidateDecisionError(
                "decision_run_finish_failed",
                "无法完成决策运行审计记录",
                retryable=True,
            ) from error

    @staticmethod
    def _finish_decision_run_for_error(
        decision_service: Any,
        decision_run: Any,
        claim: Any,
        error: CandidateDecisionError,
    ) -> None:
        if decision_run is None:
            return
        error_class = "timeout" if "timeout" in error.code else "validation" if "invalid" in error.code else "provider"
        CandidateDecisionWorker._finish_decision_run_with_error(
            decision_service,
            decision_run,
            claim,
            status="retry_wait" if error.retryable else "failed",
            error_class=error_class,
            error=error,
        )

    @staticmethod
    def _finish_decision_run_with_error(
        decision_service: Any,
        decision_run: Any,
        claim: Any,
        *,
        status: str,
        error_class: str,
        error: CandidateDecisionError,
    ) -> None:
        try:
            decision_service.finish_run(
                decision_run.id,
                status=status,
                retry_count=claim.attempt_no,
                error_class=error_class,
                error_code=error.code,
                error_message=str(error)[:4_000],
            )
        except Exception:
            return

    def handle(self, claim: Any) -> None:
        self.validate(claim)
        self.execute(claim, None)

    def compensate(self, claim: Any, error: Exception) -> None:
        del claim, error

    def classify_error(self, error: Exception) -> Any:
        from .v13_handlers import ErrorClassification

        if isinstance(error, CandidateDecisionError):
            return ErrorClassification(
                kind="retryable" if error.retryable else "permanent",
                code=error.code,
                retryable=error.retryable,
            )
        if isinstance(error, ValueError):
            return ErrorClassification(kind="permanent", code="invalid_candidate", retryable=False)
        return ErrorClassification(kind="retryable", code="model_call_failed", retryable=True)

    def on_dead(self, claim: Any, error: Exception) -> None:
        """最终重试耗尽时进入人工队列；Job 保持 dead，不伪装为成功。"""

        from .v11_candidates import CandidatePolicyService

        candidate_id = int(claim.payload["candidate_id"])
        code = getattr(error, "code", "model_failed_after_retries")
        CandidatePolicyService(self.session_factory, auto_publish_threshold=self.auto_publish_threshold).mark_model_failure_needs_review(
            candidate_id,
            code,
            f"模型失败达到最大重试次数（{claim.attempt_no}）",
        )


__all__ = [
    "ACCEPTED_EVENT_TYPE",
    "ALLOWED_DECISIONS",
    "CandidateDecision",
    "CandidateDecisionError",
    "CandidateDecisionModel",
    "CandidateDecisionWorker",
    "CandidateEvidenceSnapshot",
    "CandidateModelUnavailable",
    "CandidateSnapshot",
    "CodexCliDecisionAdapter",
    "DECISION_EVENT_TYPE",
    "DECISION_HANDLER_VERSION",
    "DECISION_JOB_TYPE",
    "DecisionApplication",
    "InvalidCandidateDecision",
    "UnconfiguredCandidateDecisionModel",
    "normalize_candidate_decision",
    "resolve_l1_auto_publish_threshold",
]
