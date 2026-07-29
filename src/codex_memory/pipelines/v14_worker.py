"""V1.4 任务事件 Worker 入口。"""

from __future__ import annotations

from typing import Any

from ..v13_handlers import HandlerContext, HandlerResult
from ..v14_service import TaskReportProjector


class TaskReportWorker:
    """将 Outbox 中的任务事件投影为 checkpoint/final 报告。"""

    job_type = "task.event.received.v1"
    handler_version = "task-report-v1.4"

    def __init__(self, session_factory: Any) -> None:
        self.projector = TaskReportProjector(session_factory)

    def validate(self, claim: Any) -> None:
        if claim.job_type != self.job_type:
            raise ValueError(f"不支持的任务类型：{claim.job_type}")
        if not isinstance(claim.payload.get("event_id"), int):
            raise ValueError("任务事件缺少 event_id")

    def execute(self, claim: Any, context: HandlerContext) -> HandlerResult:
        del context
        self.projector.handle(int(claim.payload["event_id"]))
        return HandlerResult(status="succeeded", emitted_event_types=("task.report.created.v1",))

    def handle(self, claim: Any) -> None:
        self.validate(claim)
        self.execute(claim, HandlerContext())

    def process_event(self, event_id: int) -> Any:
        """提供给定向 Worker 测试和运维调用的单事件入口。"""
        return self.projector.handle(event_id)

    def compensate(self, claim: Any, error: Exception) -> None:
        del claim, error

    def classify_error(self, error: Exception) -> Any:
        from ..v13_handlers import ErrorClassification

        if isinstance(error, ValueError):
            return ErrorClassification(kind="permanent", code="invalid_task_event", retryable=False)
        return ErrorClassification(kind="retryable", code="task_report_projection_failed", retryable=True)


__all__ = ["TaskReportWorker"]
