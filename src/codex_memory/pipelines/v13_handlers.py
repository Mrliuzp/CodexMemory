from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .v11_worker import JobClaim


@dataclass(frozen=True)
class HandlerContext:
    project_id: int | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HandlerResult:
    status: str = "succeeded"
    emitted_event_types: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorClassification:
    kind: str
    code: str
    retryable: bool


class JobHandler(Protocol):
    job_type: str
    handler_version: str

    def validate(self, claim: JobClaim) -> None:
        ...

    def execute(self, claim: JobClaim, context: HandlerContext) -> HandlerResult:
        ...

    def compensate(self, claim: JobClaim, error: Exception) -> None:
        ...

    def classify_error(self, error: Exception) -> ErrorClassification:
        ...
