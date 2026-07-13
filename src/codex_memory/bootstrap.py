from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .auth import hash_token
from .config import Settings
from .db import create_engine_from_url, create_session_factory
from .db_models import ApiKeyRow, ProjectRow


BOOTSTRAP_PERMISSIONS = ["append", "read", "memory_write"]


def ensure_bootstrap(
    session_factory: sessionmaker[Session],
    project_key: str,
    token: str,
    project_name: str | None = None,
) -> None:
    if not project_key.strip():
        raise ValueError("引导项目键不能为空")
    if not token.strip() or token == "change-me":
        raise ValueError("必须配置非占位符的引导令牌（placeholder）")

    token_hash = hash_token(token)
    with session_factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
        if project is None:
            project = ProjectRow(project_key=project_key, name=project_name or project_key)
            session.add(project)
            session.flush()

        existing = session.scalar(select(ApiKeyRow).where(ApiKeyRow.token_hash == token_hash))
        if existing is not None:
            if existing.project_id != project.id:
                raise ValueError("引导令牌已绑定到其他项目")
            return

        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=token_hash,
                permissions=BOOTSTRAP_PERMISSIONS,
                status="active",
            )
        )
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="codex-memory-bootstrap")
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--project-name")
    args = parser.parse_args()
    settings = Settings.from_env()
    factory = create_session_factory(create_engine_from_url(settings.database_url))
    ensure_bootstrap(factory, args.project_key, args.token, args.project_name)
    print(f"bootstrap ready: project={args.project_key}")


if __name__ == "__main__":
    main()
