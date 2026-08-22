"""V1.7 严格的模型输出、证据和人工审核契约。"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryEvidenceRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message_id: int = Field(gt=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def check_range(self) -> "MemoryEvidenceRange":
        if self.end_char <= self.start_char:
            raise ValueError("证据区间必须满足 end_char > start_char")
        if len(self.quote) != self.end_char - self.start_char:
            raise ValueError("证据引用长度与区间不一致")
        return self


class MemoryChangeSetOutput(BaseModel):
    """CLI 只允许产生 Shadow ChangeSet，不能直接写 Memory。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["create", "update", "no_change"]
    target_memory_id: int | None = Field(default=None, gt=0)
    target_revision: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2000)
    evidence_ranges: list[MemoryEvidenceRange] = Field(default_factory=list, max_length=16)
    retrieved_memory_ids: list[int] = Field(default_factory=list, max_length=100)
    risk_flags: list[str] = Field(default_factory=list, max_length=16)
    level: Literal["L1"] = "L1"
    scope: Literal["project"] = "project"

    @field_validator("retrieved_memory_ids")
    @classmethod
    def unique_retrieved_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError("检索目标必须是唯一正整数")
        return value

    @model_validator(mode="after")
    def check_operation(self) -> "MemoryChangeSetOutput":
        if self.operation == "create":
            if self.target_memory_id is not None or self.target_revision is not None:
                raise ValueError("create 不得包含 target memory")
            if not self.title or self.content is None or self.confidence is None or not self.evidence_ranges:
                raise ValueError("create 必须包含完整内容、置信度和证据")
        elif self.operation == "update":
            if self.target_memory_id is None or self.target_revision is None:
                raise ValueError("update 必须包含 target_memory_id 和 target_revision")
            if not self.title or self.content is None or self.confidence is None or not self.evidence_ranges:
                raise ValueError("update 必须包含完整内容、置信度和证据")
        else:
            if any(value is not None for value in (self.target_memory_id, self.target_revision, self.title, self.content, self.confidence)):
                raise ValueError("no_change 不得包含变更内容")
            if self.evidence_ranges:
                raise ValueError("no_change 不得包含证据")
        return self


class MemoryChangeSetReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=2000)


def memory_change_set_json_schema() -> dict[str, Any]:
    return MemoryChangeSetOutput.model_json_schema()


EvidenceRange = MemoryEvidenceRange
MemoryChangeSetOutputModel = MemoryChangeSetOutput
ChangeSetReviewRequest = MemoryChangeSetReviewRequest

__all__ = ["MemoryEvidenceRange", "EvidenceRange", "MemoryChangeSetOutput", "MemoryChangeSetOutputModel", "MemoryChangeSetReviewRequest", "ChangeSetReviewRequest", "memory_change_set_json_schema"]
