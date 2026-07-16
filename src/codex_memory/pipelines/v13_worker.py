from __future__ import annotations

import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import WorkerInstanceRow
from .v11_handlers import V11JobHandlers
from .v11_worker import OutboxDispatcher, V11JobWorker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class WorkerCycleResult:
    recovered: int
    dispatched: int
    claimed: int
    completed: int
    retry_wait: int
    dead: int

    def as_dict(self) -> dict[str, int]:
        return {
            "recovered": self.recovered,
            "dispatched": self.dispatched,
            "claimed": self.claimed,
            "completed": self.completed,
            "retry_wait": self.retry_wait,
            "dead": self.dead,
        }


class WorkerRuntime:
    """V1.3.0 单进程异步 Worker Runtime。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str = "v13-worker",
        role: str = "async",
        lease_seconds: int = 60,
        batch_size: int = 10,
        poll_interval_seconds: float = 2.0,
        reflection_runner: Callable[[], object] | None = None,
        reflection_delay_seconds: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.role = role
        self.batch_size = batch_size
        self.poll_interval_seconds = poll_interval_seconds
        self.reflection_runner = reflection_runner
        self.reflection_delay_seconds = reflection_delay_seconds
        self.dispatcher = OutboxDispatcher(session_factory, lease_seconds=lease_seconds)
        self.job_worker = V11JobWorker(session_factory, lease_seconds=lease_seconds)
        self.handlers = V11JobHandlers(session_factory)
        self.stop_requested = Event()
        self._reflection_due_at: float | None = None

    def run_once(self) -> WorkerCycleResult:
        self._upsert_instance("running")
        recovered = self.job_worker.sweep_expired()
        dispatched = self.dispatcher.dispatch_once(self.worker_id, limit=self.batch_size)
        processed = self.job_worker.process_once(
            self.worker_id,
            self.handlers,
            limit=self.batch_size,
        )
        self._upsert_instance("healthy")
        return WorkerCycleResult(
            recovered=recovered,
            dispatched=dispatched,
            claimed=processed["claimed"],
            completed=processed["completed"],
            retry_wait=processed["retry_wait"],
            dead=processed["dead"],
        )

    def run_forever(self) -> None:
        self._install_signal_handlers()
        self._reflection_due_at = time.monotonic() + (self.reflection_delay_seconds or 0)
        self._upsert_instance("starting")
        try:
            while not self.stop_requested.is_set():
                try:
                    self.run_once()
                    self._run_due_reflection()
                except Exception as error:
                    self._upsert_instance("degraded", metadata={"last_error": str(error)[:500]})
                self.stop_requested.wait(self.poll_interval_seconds)
        finally:
            self._upsert_instance("stopped", stopped=True)

    def stop(self) -> None:
        self.stop_requested.set()

    def _run_due_reflection(self) -> None:
        if self.reflection_runner is None or self._reflection_due_at is None:
            return
        now = time.monotonic()
        if now < self._reflection_due_at:
            return
        self.reflection_runner()
        self._reflection_due_at = now + 24 * 60 * 60

    def _upsert_instance(
        self,
        status: str,
        *,
        stopped: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        now = _utcnow()
        with self.session_factory() as session:
            worker = session.get(WorkerInstanceRow, self.worker_id)
            if worker is None:
                worker = WorkerInstanceRow(
                    worker_id=self.worker_id,
                    role=self.role,
                    version="v13",
                    status=status,
                    last_seen_at=now,
                    started_at=now,
                    metadata_json=metadata or {},
                )
                session.add(worker)
            else:
                worker.status = status
                worker.last_seen_at = now
                if metadata:
                    worker.metadata_json = {**(worker.metadata_json or {}), **metadata}
                if stopped:
                    worker.stopped_at = now
            session.commit()

    def _install_signal_handlers(self) -> None:
        def request_stop(signum: int, _frame: object) -> None:
            self.stop_requested.set()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is not None:
                signal.signal(signal_value, request_stop)
