"""将 V1 SQLite 数据幂等迁移到 PostgreSQL。"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import MetaData, create_engine, select


def parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"text": str(value)}


def parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f"SELECT * FROM {table}").fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description="将 V1 SQLite 数据迁移到 PostgreSQL")
    parser.add_argument("--source", required=True, help="SQLite 数据库路径")
    parser.add_argument("--target-project", required=True, help="目标 PostgreSQL 项目键")
    parser.add_argument(
        "--target-url",
        default=os.environ.get("CODEX_MEMORY_DATABASE_URL"),
        help="目标 PostgreSQL 连接串，默认读取 CODEX_MEMORY_DATABASE_URL",
    )
    args = parser.parse_args()
    if not args.target_url or not args.target_url.startswith("postgresql"):
        raise SystemExit("目标连接串必须是 PostgreSQL 的 CODEX_MEMORY_DATABASE_URL")

    source = sqlite3.connect(args.source)
    source.row_factory = sqlite3.Row
    try:
        project = source.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()
        if project is None:
            raise SystemExit("SQLite 数据库没有项目数据")
        source_sessions = rows(source, "sessions")
        source_messages = rows(source, "messages")
        source_memories = rows(source, "memories")
        source_sources = rows(source, "memory_sources")
        source_versions = rows(source, "memory_versions")
    finally:
        source.close()

    engine = create_engine(args.target_url, future=True, pool_pre_ping=True)
    metadata = MetaData()
    metadata.reflect(
        bind=engine,
        only=["projects", "sessions", "messages", "memories", "memory_sources", "memory_versions"],
    )
    projects = metadata.tables["projects"]
    sessions = metadata.tables["sessions"]
    messages = metadata.tables["messages"]
    memories = metadata.tables["memories"]
    memory_sources = metadata.tables["memory_sources"]
    memory_versions = metadata.tables["memory_versions"]

    inserted = {"sessions": 0, "messages": 0, "memories": 0, "memory_sources": 0, "memory_versions": 0}
    skipped = {key: 0 for key in inserted}

    with engine.begin() as connection:
        target_project = connection.execute(
            select(projects.c.id).where(projects.c.project_key == args.target_project)
        ).scalar_one_or_none()
        if target_project is None:
            raise SystemExit(f"PostgreSQL 中不存在目标项目: {args.target_project}")

        session_map: dict[int, int] = {}
        existing_sessions = connection.execute(
            select(sessions.c.id, sessions.c.session_key).where(sessions.c.project_id == target_project)
        )
        session_keys = {row.session_key: row.id for row in existing_sessions}
        for row in source_sessions:
            existing_id = session_keys.get(row["session_key"])
            if existing_id is not None:
                session_map[row["id"]] = existing_id
                skipped["sessions"] += 1
                continue
            values = {
                "project_id": target_project,
                "session_key": row["session_key"],
                "title": row["title"],
                "status": row["status"],
                "started_at": parse_datetime(row["started_at"]),
                "ended_at": parse_datetime(row["ended_at"]),
            }
            session_map[row["id"]] = connection.execute(
                sessions.insert().returning(sessions.c.id), values
            ).scalar_one()
            session_keys[row["session_key"]] = session_map[row["id"]]
            inserted["sessions"] += 1

        message_map: dict[int, int] = {}
        existing_messages = connection.execute(
            select(messages.c.id, messages.c.event_key).where(messages.c.project_id == target_project)
        )
        message_keys = {row.event_key: row.id for row in existing_messages}
        for row in source_messages:
            existing_id = message_keys.get(row["event_key"])
            if existing_id is not None:
                message_map[row["id"]] = existing_id
                skipped["messages"] += 1
                continue
            message_map[row["id"]] = connection.execute(
                messages.insert().returning(messages.c.id),
                {
                    "project_id": target_project,
                    "session_id": session_map[row["session_id"]],
                    "event_key": row["event_key"],
                    "role": row["role"],
                    "content": row["content"],
                    "content_hash": row["content_hash"],
                    "source": row["source"],
                    "metadata_json": parse_json(row["metadata_json"], {}),
                    "created_at": parse_datetime(row["created_at"]),
                    "occurred_at": parse_datetime(row["occurred_at"]),
                    "ingestion_version": row["ingestion_version"],
                    "conflict_status": row["conflict_status"],
                },
            ).scalar_one()
            message_keys[row["event_key"]] = message_map[row["id"]]
            inserted["messages"] += 1

        memory_map: dict[int, int] = {}
        existing_memories = connection.execute(
            select(
                memories.c.id,
                memories.c.level,
                memories.c.memory_type,
                memories.c.title,
            ).where(memories.c.project_id == target_project)
        )
        memory_keys = {
            (row.level, row.memory_type, row.title): row.id
            for row in existing_memories
        }
        for row in source_memories:
            key = (row["level"], row["memory_type"], row["title"])
            existing_id = memory_keys.get(key)
            if existing_id is not None:
                memory_map[row["id"]] = existing_id
                skipped["memories"] += 1
                continue
            memory_map[row["id"]] = connection.execute(
                memories.insert().returning(memories.c.id),
                {
                    "project_id": target_project,
                    "level": row["level"],
                    "memory_type": row["memory_type"],
                    "title": row["title"],
                    "content": parse_json(row["content"], {}),
                    "confidence": row["confidence"],
                    "status": row["status"],
                    "usage_count": row["usage_count"],
                    "last_used_at": parse_datetime(row["last_used_at"]),
                    "deprecated": bool(row["deprecated"]),
                    "created_at": parse_datetime(row["created_at"]),
                    "updated_at": parse_datetime(row["updated_at"]),
                    "scope": row["scope"],
                    "source_kind": row["source_kind"],
                    "review_status": row["review_status"],
                },
            ).scalar_one()
            memory_keys[key] = memory_map[row["id"]]
            inserted["memories"] += 1

        existing_source_pairs = {
            (row.memory_id, row.message_id)
            for row in connection.execute(select(memory_sources.c.memory_id, memory_sources.c.message_id))
        }
        for row in source_sources:
            pair = (memory_map[row["memory_id"]], message_map[row["message_id"]])
            if pair in existing_source_pairs:
                skipped["memory_sources"] += 1
                continue
            connection.execute(
                memory_sources.insert(),
                {"memory_id": pair[0], "message_id": pair[1]},
            )
            existing_source_pairs.add(pair)
            inserted["memory_sources"] += 1

        existing_versions = {
            (row.memory_id, row.version)
            for row in connection.execute(select(memory_versions.c.memory_id, memory_versions.c.version))
        }
        for row in source_versions:
            key = (memory_map[row["memory_id"]], row["version"])
            if key in existing_versions:
                skipped["memory_versions"] += 1
                continue
            connection.execute(
                memory_versions.insert(),
                {
                    "memory_id": key[0],
                    "version": key[1],
                    "content": parse_json(row["content"], {}),
                    "created_at": parse_datetime(row["created_at"]),
                },
            )
            existing_versions.add(key)
            inserted["memory_versions"] += 1

    print(json.dumps({
        "source_project": project["project_key"],
        "target_project": args.target_project,
        "source_counts": {
            "sessions": len(source_sessions),
            "messages": len(source_messages),
            "memories": len(source_memories),
            "memory_sources": len(source_sources),
            "memory_versions": len(source_versions),
        },
        "inserted": inserted,
        "skipped": skipped,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
