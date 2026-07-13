from __future__ import annotations

import argparse
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
    parser.add_argument("--schedule", default="02:00")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    factory = create_session_factory(create_engine_from_url(settings.database_url))
    if args.once:
        print(run_once(factory))
        return
    while True:
        time.sleep(seconds_until_schedule(args.schedule))
        run_once(factory)


if __name__ == "__main__":
    main()