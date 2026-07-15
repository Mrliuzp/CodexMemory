from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import MessageRow, MigrationBatchRow, MigrationIssueRow, ProjectRow, SessionRow
from .migration_inventory import inventory_source


@dataclass
class Counter:
    created: int = 0
    duplicates: int = 0


@dataclass
class IssueCounter:
    by_code: dict[str, int] = field(default_factory=dict)

    def add(self, code: str) -> None:
        self.by_code[code] = self.by_code.get(code, 0) + 1


@dataclass
class ImportReport:
    messages: Counter = field(default_factory=Counter)
    sessions: Counter = field(default_factory=Counter)
    issues: IssueCounter = field(default_factory=IssueCounter)
    batch_id: int | None = None


def source_fingerprint(source_sha256: str, table: str, source_id: str) -> str:
    return hashlib.sha256(f"{source_sha256}:{table}:{source_id}".encode("utf-8")).hexdigest()


class MigrationImporter:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def import_batch(self, source: str | Path, project_map: dict[str, str]) -> ImportReport:
        manifest = inventory_source(source)
        report = ImportReport()
        with self.session_factory() as session:
            batch = MigrationBatchRow(source_path_hash=manifest.source_path_hash, source_sha256=manifest.sha256, status="importing", manifest=manifest.public_dict(), report={})
            session.add(batch); session.flush(); report.batch_id = batch.id
            self._import_raw_logs(session, Path(source), manifest.sha256, project_map, report, batch.id)
            batch.status = "completed"
            batch.report = {"messages": report.messages.__dict__, "sessions": report.sessions.__dict__, "issues": report.issues.by_code}
            session.commit()
        return report

    def _import_raw_logs(self, session: Session, source: Path, digest: str, project_map: dict[str, str], report: ImportReport, batch_id: int) -> None:
        uri = f"file:{source.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as legacy:
            legacy.row_factory = sqlite3.Row
            rows = legacy.execute("SELECT id, project_id, conversation_id, role, content, metadata_json FROM raw_logs ORDER BY id").fetchall()
        projects: dict[str, ProjectRow] = {}
        conversations: dict[tuple[int, str], SessionRow] = {}
        for row in rows:
            target_key = project_map.get(str(row["project_id"]))
            if not target_key:
                self._issue(session, batch_id, "raw_logs", str(row["id"]), "unmapped_project", report)
                continue
            project = projects.get(target_key)
            if project is None:
                project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == target_key))
                if project is None:
                    self._issue(session, batch_id, "raw_logs", str(row["id"]), "unknown_target_project", report)
                    continue
                projects[target_key] = project
            role = str(row["role"])
            if role not in {"user", "assistant", "system"}:
                self._issue(session, batch_id, "raw_logs", str(row["id"]), "unknown_role", report)
                continue
            fingerprint = source_fingerprint(digest, "raw_logs", str(row["id"]))
            if session.scalar(select(MessageRow).where(MessageRow.project_id == project.id, MessageRow.source_fingerprint == fingerprint)):
                report.messages.duplicates += 1
                continue
            conversation_key = (project.id, str(row["conversation_id"]))
            conversation = conversations.get(conversation_key)
            if conversation is None:
                conversation = session.scalar(select(SessionRow).where(SessionRow.project_id == project.id, SessionRow.session_key == conversation_key[1]))
                if conversation is None:
                    conversation = SessionRow(project_id=project.id, session_key=conversation_key[1])
                    session.add(conversation); session.flush(); report.sessions.created += 1
                conversations[conversation_key] = conversation
            content = str(row["content"])
            try: metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError: metadata = {}
            session.add(MessageRow(project_id=project.id, session_id=conversation.id, event_key=f"migration:{fingerprint}", role=role, content=content, content_hash=hashlib.sha256(content.encode()).hexdigest(), source="migration", source_fingerprint=fingerprint, metadata_json=metadata))
            report.messages.created += 1

    def _issue(self, session: Session, batch_id: int, source_type: str, source_id: str, code: str, report: ImportReport) -> None:
        session.add(MigrationIssueRow(batch_id=batch_id, source_type=source_type, source_id=source_id, code=code, detail={}))
        report.issues.add(code)
