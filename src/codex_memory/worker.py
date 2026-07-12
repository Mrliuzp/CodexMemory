from __future__ import annotations

import argparse
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal
from .config import Settings
from .db import create_engine_from_url, create_session_factory
from .db_models import ProjectRow
from .v1_service import V1MemoryService


def run_once(session_factory: sessionmaker[Session]) -> dict[str, dict[str, int]]:
    with session_factory() as session:
        projects = session.scalars(select(ProjectRow).where(ProjectRow.status == "active")).all()
        project_keys = [project.project_key for project in projects]
    service = V1MemoryService(session_factory)
    principal = Principal(project_key="*", permissions=frozenset({"admin", "reflect", "read"}))
    return {project_key: service.reflect_project(principal, project_key) for project_key in project_keys}


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
        run_once(factory)
        time.sleep(60)


if __name__ == "__main__":
    main()