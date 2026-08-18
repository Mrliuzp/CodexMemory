"""Codex CLI 决策数据契约。

模型输出永远是不可信输入。这里的 Pydantic 模型只负责第一层结构校验，
涉及项目归属和证据内容的校验由 :mod:`codex_memory.decision_service` 在数据库事务中完成。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator


class DecisionAction(str, Enum):
    """模型建议的下一步动作。"""

    PUBLISH = "publish"
    SKIP = "skip"
    NEEDS_REVIEW = "needs_review"


class DecisionLevel(str, Enum):
    """记忆层级；自动发布只允许 L1。"""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class DecisionScope(str, Enum):
    """记忆作用域；自动发布只允许项目级。"""

    PROJECT = "project"
    GLOBAL = "global"


class DecisionRiskFlag(str, Enum):
    """可审计的风险标记集合，拒绝模型自行扩展枚举。"""

    LOW_CONFIDENCE = "low_confidence"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    PROMPT_INJECTION = "prompt_injection"
    SENSITIVE_CONTENT = "sensitive_content"
    SECRET_OR_CREDENTIAL = "secret_or_credential"
    CROSS_PROJECT_REFERENCE = "cross_project_reference"
    EXTERNAL_ACTION = "external_action"
    POLICY_VIOLATION = "policy_violation"


class DecisionRunStatus(str, Enum):
    """决策运行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"


class DecisionErrorClass(str, Enum):
    """可供 Worker 分类重试的错误类别。"""

    VALIDATION = "validation"
    POLICY = "policy"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    PROVIDER = "provider"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class ReviewAction(str, Enum):
    """人工审核动作。"""

    APPROVE = "approve"
    REJECT = "reject"
    CORRECT = "correct"


class DecisionStrategy(str, Enum):
    """项目决策策略。"""

    MANUAL_REVIEW = "manual_review"
    AUTO_PUBLISH = "auto_publish"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class EvidenceRange(_StrictModel):
    """指向同一项目 L0 message 的半开字符区间。"""

    message_id: StrictInt = Field(gt=0)
    start_char: StrictInt = Field(ge=0, le=1_000_000)
    end_char: StrictInt = Field(gt=0, le=1_000_000)
    quote: StrictStr = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_order(self) -> "EvidenceRange":
        if self.end_char <= self.start_char:
            raise ValueError("证据范围必须满足 end_char > start_char")
        return self


class ModelDecisionOutput(_StrictModel):
    """模型返回的结构化决策，不包含任何可直接发布的副作用。"""

    decision: DecisionAction
    confidence: StrictFloat = Field(ge=0, le=1)
    title: StrictStr = Field(min_length=1, max_length=300)
    content: dict[str, Any]
    reason: StrictStr = Field(min_length=1, max_length=2_000)
    evidence_ranges: list[EvidenceRange] = Field(max_length=16)
    risk_flags: list[DecisionRiskFlag] = Field(max_length=16)
    level: DecisionLevel
    scope: DecisionScope

    @model_validator(mode="after")
    def validate_content_and_evidence(self) -> "ModelDecisionOutput":
        try:
            encoded = json.dumps(self.content, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("content 必须是可序列化的 JSON 对象") from error
        if len(encoded) > 12_000:
            raise ValueError("content 超过 12000 个字符")
        values = [flag.value for flag in self.risk_flags]
        if len(values) != len(set(values)):
            raise ValueError("risk_flags 不得重复")
        if self.decision is DecisionAction.PUBLISH and not self.evidence_ranges:
            raise ValueError("publish 决策必须包含 evidence_ranges")
        return self


class DecisionRunInput(_StrictModel):
    """启动一次模型决策所需的可审计输入元数据。"""

    model: StrictStr = Field(min_length=1, max_length=128)
    prompt_version: StrictStr = Field(min_length=1, max_length=64)
    input_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    max_retries: int = Field(default=3, ge=0, le=20)


class DecisionRunFinish(_StrictModel):
    """Worker 完成或分类一次决策运行时使用的更新契约。"""

    status: DecisionRunStatus
    retry_count: int = Field(default=0, ge=0, le=20)
    error_class: DecisionErrorClass | None = None
    error_code: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    error_message: StrictStr | None = Field(default=None, max_length=4_000)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_micros: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal_status(self) -> "DecisionRunFinish":
        if self.status in {DecisionRunStatus.PENDING, DecisionRunStatus.RUNNING}:
            raise ValueError("完成运行时 status 必须是终态或 retry_wait")
        if self.status is DecisionRunStatus.SUCCEEDED and self.error_class is not None:
            raise ValueError("succeeded 不应携带 error_class")
        return self


class ProjectDecisionPolicy(_StrictModel):
    """项目级决策策略的 JSON 契约，固定安全边界为 L1/project。"""

    enabled: StrictBool = False
    auto_publish_enabled: StrictBool = False
    strategy: DecisionStrategy = DecisionStrategy.MANUAL_REVIEW
    allowed_level: DecisionLevel = DecisionLevel.L1
    allowed_scope: DecisionScope = DecisionScope.PROJECT
    min_confidence: float = Field(default=0.8, ge=0, le=1)
    require_evidence: StrictBool = True
    allow_risk_flags: StrictBool = False
    max_title_length: int = Field(default=300, ge=1, le=300)
    max_content_chars: int = Field(default=12_000, ge=1, le=100_000)
    max_reason_length: int = Field(default=2_000, ge=1, le=10_000)
    max_evidence_ranges: int = Field(default=16, ge=1, le=64)
    policy_version: StrictStr = Field(default="decision-policy-v1", min_length=1, max_length=64)
    updated_by: StrictStr = Field(default="system", min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_safety_boundary(self) -> "ProjectDecisionPolicy":
        if self.allowed_level is not DecisionLevel.L1:
            raise ValueError("自动决策策略只允许 L1")
        if self.allowed_scope is not DecisionScope.PROJECT:
            raise ValueError("自动决策策略只允许 project 作用域")
        if self.auto_publish_enabled and not self.enabled:
            raise ValueError("auto_publish_enabled 开启前必须先开启 enabled")
        if self.strategy is DecisionStrategy.AUTO_PUBLISH and not self.auto_publish_enabled:
            raise ValueError("auto_publish 策略必须同时开启 auto_publish_enabled")
        return self


class DecisionReviewInput(_StrictModel):
    """人工审核或纠错请求。"""

    reviewer: StrictStr = Field(min_length=1, max_length=255)
    action: ReviewAction
    reason: StrictStr = Field(min_length=1, max_length=2_000)
    correction: ModelDecisionOutput | None = None

    @model_validator(mode="after")
    def validate_correction(self) -> "DecisionReviewInput":
        if self.action is ReviewAction.CORRECT and self.correction is None:
            raise ValueError("correct 动作必须提供 correction")
        if self.action is not ReviewAction.CORRECT and self.correction is not None:
            raise ValueError("只有 correct 动作可以提供 correction")
        return self


# 为 Runner/Integration 提供更短的兼容名称。
DecisionOutput = ModelDecisionOutput


def decision_json_schema() -> dict[str, Any]:
    """返回可上传到后续 Runner 的严格 JSON Schema。"""

    return ModelDecisionOutput.model_json_schema()


__all__ = [
    "DecisionAction",
    "DecisionErrorClass",
    "DecisionLevel",
    "DecisionOutput",
    "DecisionReviewInput",
    "DecisionRiskFlag",
    "DecisionRunFinish",
    "DecisionRunInput",
    "DecisionRunStatus",
    "DecisionStrategy",
    "DecisionScope",
    "EvidenceRange",
    "ModelDecisionOutput",
    "ProjectDecisionPolicy",
    "ReviewAction",
    "decision_json_schema",
]
