from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import MessageRow, MigrationBatchRow, MigrationIssueRow
from .migration_import import source_fingerprint
from .migration_inventory import inventory_source


@dataclass(frozen=True)
class VerificationReport:
    counts_match: bool
    duplicate_fingerprints: int
    error_issues: int
    ready_to_cutover: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def verify_migration(source: str | Path, session_factory: sessionmaker[Session], batch_id: int | None) -> VerificationReport:
    if batch_id is None:
        raise ValueError("migration batch is required")
    manifest = inventory_source(source)
    source_path = Path(source).resolve()
    uri = f"file:{quote(source_path.as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as legacy:
        raw_ids = [str(row[0]) for row in legacy.execute("SELECT id FROM raw_logs")]
    fingerprints = [source_fingerprint(manifest.sha256, "raw_logs", value) for value in raw_ids]
    with session_factory() as session:
        batch = session.get(MigrationBatchRow, batch_id)
        if batch is None or batch.source_sha256 != manifest.sha256:
            raise ValueError("migration batch does not match source")
        imported = session.scalar(select(func.count(MessageRow.id)).where(MessageRow.source_fingerprint.in_(fingerprints))) or 0
        duplicates = session.scalar(select(func.count()).select_from(select(MessageRow.source_fingerprint).where(MessageRow.source_fingerprint.in_(fingerprints)).group_by(MessageRow.project_id, MessageRow.source_fingerprint).having(func.count(MessageRow.id) > 1).subquery())) or 0
        errors = session.scalar(select(func.count(MigrationIssueRow.id)).where(MigrationIssueRow.batch_id == batch_id, MigrationIssueRow.severity == "error")) or 0
    counts_match = imported == len(fingerprints)
    return VerificationReport(counts_match, int(duplicates), int(errors), counts_match and not duplicates and not errors)
