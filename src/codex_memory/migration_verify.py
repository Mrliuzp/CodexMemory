from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import MemoryRow, MemorySourceRow, MemoryVersionRow, MessageRow, MigrationBatchRow, MigrationIssueRow
from .migration_import import source_fingerprint
from .migration_inventory import inventory_source


@dataclass(frozen=True)
class VerificationReport:
    counts_match: bool
    memory_counts_match: bool
    version_counts_match: bool
    broken_memory_sources: int
    duplicate_fingerprints: int
    error_issues: int
    ready_to_cutover: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _json_list(value: object) -> list[object]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def verify_migration(source: str | Path, session_factory: sessionmaker[Session], batch_id: int | None) -> VerificationReport:
    if batch_id is None:
        raise ValueError("migration batch is required")
    manifest = inventory_source(source)
    source_path = Path(source).resolve()
    uri = f"file:{quote(source_path.as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as legacy:
        raw_ids = [str(row[0]) for row in legacy.execute("SELECT id FROM raw_logs")]
        memory_columns = {str(row[1]) for row in legacy.execute("PRAGMA table_info(memories)")}
        required_memory_columns = {"id", "source_log_ids_json"}
        memory_rows = (
            legacy.execute("SELECT id, source_log_ids_json FROM memories ORDER BY id").fetchall()
            if required_memory_columns <= memory_columns
            else []
        )
        version_columns = {str(row[1]) for row in legacy.execute("PRAGMA table_info(memory_versions)")}
        version_rows = (
            legacy.execute("SELECT id FROM memory_versions ORDER BY id").fetchall()
            if {"id", "memory_id", "version"} <= version_columns
            else []
        )
    raw_fingerprints = [source_fingerprint(manifest.sha256, "raw_logs", value) for value in raw_ids]
    memory_fingerprints = [source_fingerprint(manifest.sha256, "memories", str(row[0])) for row in memory_rows]
    version_fingerprints = [source_fingerprint(manifest.sha256, "memory_versions", str(row[0])) for row in version_rows]
    with session_factory() as session:
        batch = session.get(MigrationBatchRow, batch_id)
        if batch is None or batch.source_sha256 != manifest.sha256:
            raise ValueError("migration batch does not match source")
        imported_messages = _count_fingerprints(session, MessageRow.source_fingerprint, raw_fingerprints)
        imported_memories = _count_fingerprints(session, MemoryRow.source_fingerprint, memory_fingerprints)
        imported_versions = _count_fingerprints(session, MemoryVersionRow.source_fingerprint, version_fingerprints)
        duplicates = sum(
            (
                _duplicate_fingerprints(session, MessageRow.source_fingerprint, raw_fingerprints),
                _duplicate_fingerprints(session, MemoryRow.source_fingerprint, memory_fingerprints),
                _duplicate_fingerprints(session, MemoryVersionRow.source_fingerprint, version_fingerprints),
            )
        )
        broken_sources = _broken_memory_sources(session, manifest.sha256, memory_rows)
        errors = session.scalar(
            select(func.count(MigrationIssueRow.id)).where(
                MigrationIssueRow.batch_id == batch_id,
                MigrationIssueRow.severity == "error",
            )
        ) or 0
    counts_match = imported_messages == len(raw_fingerprints)
    memory_counts_match = imported_memories == len(memory_fingerprints)
    version_counts_match = imported_versions == len(version_fingerprints)
    ready = counts_match and memory_counts_match and version_counts_match and not broken_sources and not duplicates and not errors
    return VerificationReport(
        counts_match=counts_match,
        memory_counts_match=memory_counts_match,
        version_counts_match=version_counts_match,
        broken_memory_sources=int(broken_sources),
        duplicate_fingerprints=int(duplicates),
        error_issues=int(errors),
        ready_to_cutover=ready,
    )


def _count_fingerprints(session: Session, column: Any, fingerprints: list[str]) -> int:
    if not fingerprints:
        return 0
    return int(session.scalar(select(func.count()).select_from(select(column).where(column.in_(fingerprints)).subquery())) or 0)


def _duplicate_fingerprints(session: Session, column: Any, fingerprints: list[str]) -> int:
    if not fingerprints:
        return 0
    return int(
        session.scalar(
            select(func.count()).select_from(
                select(column)
                .where(column.in_(fingerprints))
                .group_by(column)
                .having(func.count() > 1)
                .subquery()
            )
        )
        or 0
    )


def _broken_memory_sources(session: Session, digest: str, memory_rows: list[tuple[object, object]]) -> int:
    broken = 0
    for legacy_memory_id, source_log_ids_json in memory_rows:
        memory = session.scalar(
            select(MemoryRow).where(
                MemoryRow.source_fingerprint == source_fingerprint(digest, "memories", str(legacy_memory_id))
            )
        )
        if memory is None:
            continue
        for raw_id in _json_list(source_log_ids_json):
            if not str(raw_id).isdigit():
                broken += 1
                continue
            message = session.scalar(
                select(MessageRow).where(
                    MessageRow.source_fingerprint == source_fingerprint(digest, "raw_logs", str(raw_id))
                )
            )
            if message is None or session.scalar(
                select(MemorySourceRow).where(
                    MemorySourceRow.memory_id == memory.id,
                    MemorySourceRow.message_id == message.id,
                )
            ) is None:
                broken += 1
    return broken