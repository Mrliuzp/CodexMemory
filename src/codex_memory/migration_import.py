from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    MemoryRow,
    MemorySourceRow,
    MemoryVersionRow,
    MessageRow,
    MigrationBatchRow,
    MigrationIssueRow,
    ProjectRow,
    SessionRow,
)
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
    memories: Counter = field(default_factory=Counter)
    memory_versions: Counter = field(default_factory=Counter)
    memory_sources: Counter = field(default_factory=Counter)
    issues: IssueCounter = field(default_factory=IssueCounter)
    batch_id: int | None = None


def source_fingerprint(source_sha256: str, table: str, source_id: str) -> str:
    return hashlib.sha256(f"{source_sha256}:{table}:{source_id}".encode("utf-8")).hexdigest()


def _legacy_uri(source: Path) -> str:
    return f"file:{quote(source.resolve().as_posix())}?mode=ro"


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


class MigrationImporter:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def import_batch(self, source: str | Path, project_map: dict[str, str]) -> ImportReport:
        source_path = Path(source)
        manifest = inventory_source(source_path)
        report = ImportReport()
        with self.session_factory() as session:
            batch = MigrationBatchRow(
                source_path_hash=manifest.source_path_hash,
                source_sha256=manifest.sha256,
                status="importing",
                manifest=manifest.public_dict(),
                report={},
            )
            session.add(batch)
            session.flush()
            report.batch_id = batch.id
            self._import_raw_logs(session, source_path, manifest.sha256, project_map, report, batch.id)
            self._import_memories(session, source_path, manifest.sha256, project_map, report, batch.id)
            batch.status = "completed"
            batch.report = {
                "messages": report.messages.__dict__,
                "sessions": report.sessions.__dict__,
                "memories": report.memories.__dict__,
                "memory_versions": report.memory_versions.__dict__,
                "memory_sources": report.memory_sources.__dict__,
                "issues": report.issues.by_code,
            }
            session.commit()
        return report

    def _import_raw_logs(
        self,
        session: Session,
        source: Path,
        digest: str,
        project_map: dict[str, str],
        report: ImportReport,
        batch_id: int,
    ) -> None:
        with sqlite3.connect(_legacy_uri(source), uri=True) as legacy:
            legacy.row_factory = sqlite3.Row
            rows = legacy.execute(
                "SELECT id, project_id, conversation_id, role, content, metadata_json FROM raw_logs ORDER BY id"
            ).fetchall()
        projects: dict[str, ProjectRow] = {}
        conversations: dict[tuple[int, str], SessionRow] = {}
        for row in rows:
            project = self._mapped_project(
                session, project_map, str(row["project_id"]), projects, report, batch_id, "raw_logs", str(row["id"])
            )
            if project is None:
                continue
            role = str(row["role"])
            if role not in {"user", "assistant", "system"}:
                self._issue(session, batch_id, "raw_logs", str(row["id"]), "unknown_role", report)
                continue
            fingerprint = source_fingerprint(digest, "raw_logs", str(row["id"]))
            if session.scalar(
                select(MessageRow).where(
                    MessageRow.project_id == project.id,
                    MessageRow.source_fingerprint == fingerprint,
                )
            ):
                report.messages.duplicates += 1
                continue
            conversation_key = (project.id, str(row["conversation_id"]))
            conversation = conversations.get(conversation_key)
            if conversation is None:
                conversation = session.scalar(
                    select(SessionRow).where(
                        SessionRow.project_id == project.id,
                        SessionRow.session_key == conversation_key[1],
                    )
                )
                if conversation is None:
                    conversation = SessionRow(project_id=project.id, session_key=conversation_key[1])
                    session.add(conversation)
                    session.flush()
                    report.sessions.created += 1
                conversations[conversation_key] = conversation
            content = str(row["content"])
            session.add(
                MessageRow(
                    project_id=project.id,
                    session_id=conversation.id,
                    event_key=f"migration:{fingerprint}",
                    role=role,
                    content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    source="migration",
                    source_fingerprint=fingerprint,
                    metadata_json=_json_object(row["metadata_json"]),
                )
            )
            report.messages.created += 1

    def _import_memories(
        self,
        session: Session,
        source: Path,
        digest: str,
        project_map: dict[str, str],
        report: ImportReport,
        batch_id: int,
    ) -> None:
        required_memory_columns = {
            "id", "project_id", "layer", "title", "body", "tags_json", "memory_type",
            "source_log_ids_json", "metadata_json", "version", "weight", "access_count",
        }
        required_version_columns = {
            "id", "memory_id", "version", "title", "body", "tags_json", "source_log_ids_json", "metadata_json",
        }
        with sqlite3.connect(_legacy_uri(source), uri=True) as legacy:
            legacy.row_factory = sqlite3.Row
            memory_columns = {str(row[1]) for row in legacy.execute("PRAGMA table_info(memories)")}
            if not required_memory_columns <= memory_columns:
                return
            memory_rows = legacy.execute("SELECT * FROM memories ORDER BY id").fetchall()
            version_columns = {str(row[1]) for row in legacy.execute("PRAGMA table_info(memory_versions)")}
            version_rows = (
                legacy.execute("SELECT * FROM memory_versions ORDER BY id").fetchall()
                if required_version_columns <= version_columns
                else []
            )
        projects: dict[str, ProjectRow] = {}
        imported_memories: dict[int, MemoryRow] = {}
        for row in memory_rows:
            source_id = str(row["id"])
            source_project = row["project_id"]
            project: ProjectRow | None = None
            layer = str(row["layer"])
            if source_project is None:
                if layer != "L2":
                    self._issue(session, batch_id, "memories", source_id, "invalid_global_memory", report)
                    continue
            else:
                project = self._mapped_project(
                    session, project_map, str(source_project), projects, report, batch_id, "memories", source_id
                )
                if project is None:
                    continue
            fingerprint = source_fingerprint(digest, "memories", source_id)
            memory = session.scalar(select(MemoryRow).where(MemoryRow.source_fingerprint == fingerprint))
            if memory is None:
                metadata = _json_object(row["metadata_json"])
                tags = [str(tag) for tag in _json_list(row["tags_json"])]
                source_ids = [int(value) for value in _json_list(row["source_log_ids_json"]) if str(value).isdigit()]
                weight = float(row["weight"] or 0)
                memory = MemoryRow(
                    project_id=project.id if project is not None else None,
                    level=layer,
                    memory_type=str(row["memory_type"]),
                    title=str(row["title"]),
                    content={
                        "text": str(row["body"]),
                        "tags": tags,
                        "legacy_metadata": metadata,
                        "legacy": {
                            "version": int(row["version"] or 1),
                            "weight": weight,
                            "source_log_ids": source_ids,
                        },
                    },
                    confidence=min(1.0, max(0.0, weight)),
                    usage_count=int(row["access_count"] or 0),
                    status="active",
                    scope="project" if project is not None else "global",
                    source_kind="migration",
                    review_status="accepted",
                    source_fingerprint=fingerprint,
                )
                session.add(memory)
                session.flush()
                report.memories.created += 1
            else:
                report.memories.duplicates += 1
            imported_memories[int(row["id"])] = memory
            self._import_memory_sources(
                session,
                memory,
                _json_list(row["source_log_ids_json"]),
                digest,
                report,
                batch_id,
                source_id,
            )
        for row in version_rows:
            source_id = str(row["id"])
            memory = imported_memories.get(int(row["memory_id"]))
            if memory is None:
                self._issue(session, batch_id, "memory_versions", source_id, "orphan_memory_version", report)
                continue
            fingerprint = source_fingerprint(digest, "memory_versions", source_id)
            if session.scalar(select(MemoryVersionRow).where(MemoryVersionRow.source_fingerprint == fingerprint)):
                report.memory_versions.duplicates += 1
                continue
            session.add(
                MemoryVersionRow(
                    memory_id=memory.id,
                    version=int(row["version"]),
                    content={
                        "text": str(row["body"]),
                        "title": str(row["title"]),
                        "tags": [str(tag) for tag in _json_list(row["tags_json"])],
                        "legacy_metadata": _json_object(row["metadata_json"]),
                        "legacy_source_log_ids": _json_list(row["source_log_ids_json"]),
                    },
                    source_fingerprint=fingerprint,
                )
            )
            report.memory_versions.created += 1

    def _import_memory_sources(
        self,
        session: Session,
        memory: MemoryRow,
        source_ids: list[Any],
        digest: str,
        report: ImportReport,
        batch_id: int,
        memory_source_id: str,
    ) -> None:
        for raw_id in source_ids:
            if not str(raw_id).isdigit():
                self._issue(session, batch_id, "memories", memory_source_id, "invalid_source_log_id", report)
                continue
            fingerprint = source_fingerprint(digest, "raw_logs", str(raw_id))
            message = session.scalar(select(MessageRow).where(MessageRow.source_fingerprint == fingerprint))
            if message is None:
                self._issue(session, batch_id, "memories", memory_source_id, "missing_source_message", report)
                continue
            existing = session.scalar(
                select(MemorySourceRow).where(
                    MemorySourceRow.memory_id == memory.id,
                    MemorySourceRow.message_id == message.id,
                )
            )
            if existing is not None:
                report.memory_sources.duplicates += 1
                continue
            session.add(MemorySourceRow(memory_id=memory.id, message_id=message.id))
            report.memory_sources.created += 1

    def _mapped_project(
        self,
        session: Session,
        project_map: dict[str, str],
        source_project: str,
        projects: dict[str, ProjectRow],
        report: ImportReport,
        batch_id: int,
        source_type: str,
        source_id: str,
    ) -> ProjectRow | None:
        target_key = project_map.get(source_project)
        if not target_key:
            self._issue(session, batch_id, source_type, source_id, "unmapped_project", report)
            return None
        project = projects.get(target_key)
        if project is None:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == target_key))
            if project is None:
                self._issue(session, batch_id, source_type, source_id, "unknown_target_project", report)
                return None
            projects[target_key] = project
        return project

    def _issue(
        self,
        session: Session,
        batch_id: int,
        source_type: str,
        source_id: str,
        code: str,
        report: ImportReport,
    ) -> None:
        session.add(
            MigrationIssueRow(
                batch_id=batch_id,
                source_type=source_type,
                source_id=source_id,
                code=code,
                detail={},
            )
        )
        report.issues.add(code)