from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal
from .config import Settings
from .db import create_engine_from_url, create_session_factory
from .db_models import ProjectRow
from .v1_service import V1MemoryService
from .v11_worker import OutboxDispatcher, V11JobWorker
from .v13_worker import WorkerRuntime


def run_once(session_factory: sessionmaker[Session]) -> dict[str, dict[str, int]]:
    with session_factory() as session:
        projects = session.scalars(select(ProjectRow).where(ProjectRow.status == "active")).all()
        project_keys = [project.project_key for project in projects]
    service = V1MemoryService(session_factory)
    principal = Principal(project_key="*", permissions=frozenset({"admin", "reflect", "read"}))
    return {project_key: service.reflect_project(principal, project_key) for project_key in project_keys}


def run_v11_once(session_factory: sessionmaker[Session], worker_id: str = "v11-worker") -> dict[str, int]:
    dispatched = OutboxDispatcher(session_factory).dispatch_once(worker_id)
    worker = V11JobWorker(session_factory)
    from .v11_handlers import V11JobHandlers

    processed = worker.process_once(worker_id, V11JobHandlers(session_factory).handle)
    return {"dispatched": dispatched, **processed}


def run_v13_once(session_factory: sessionmaker[Session], worker_id: str = "v13-worker") -> dict[str, int]:
    return WorkerRuntime(session_factory, worker_id=worker_id).run_once().as_dict()

def seconds_until_schedule(schedule: str, now: datetime | None = None) -> float:
    """Return the delay until the next local daily HH:MM execution window."""
    try:
        hour_text, minute_text = schedule.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as error:
        raise ValueError("调度时间必须使用 HH:MM 格式") from error
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("调度时间必须使用 HH:MM 格式")

    current = now or datetime.now()
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return max(0.0, (target - current).total_seconds())


def main() -> None:
    parser = argparse.ArgumentParser(prog="codex-memory-worker")
    parser.add_argument("--mode", choices=["async", "reflection"], default="async")
    parser.add_argument("--schedule", dest="reflection_schedule", default="02:00")
    parser.add_argument("--reflection-schedule", dest="reflection_schedule", default=argparse.SUPPRESS)
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("CODEX_MEMORY_WORKER_POLL_INTERVAL", "2")))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("CODEX_MEMORY_WORKER_BATCH_SIZE", "10")))
    parser.add_argument("--lease-seconds", type=int, default=int(os.environ.get("CODEX_MEMORY_WORKER_LEASE_SECONDS", "60")))
    parser.add_argument("--worker-id", default="v13-worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    factory = create_session_factory(create_engine_from_url(settings.database_url))
    if args.once:
        if args.mode == "async":
            print(run_v13_once(factory, args.worker_id))
        else:
            print(run_once(factory))
        return
    if args.mode == "reflection":
        while True:
            time.sleep(seconds_until_schedule(args.reflection_schedule))
            run_once(factory)
        return

    runtime = WorkerRuntime(
        factory,
        worker_id=args.worker_id,
        role=os.environ.get("CODEX_MEMORY_WORKER_ROLE", "async"),
        lease_seconds=args.lease_seconds,
        batch_size=args.batch_size,
        poll_interval_seconds=args.poll_interval,
        reflection_runner=lambda: run_once(factory),
        reflection_delay_seconds=seconds_until_schedule(args.reflection_schedule),
    )
    runtime.run_forever()


if __name__ == "__main__":
    main()
