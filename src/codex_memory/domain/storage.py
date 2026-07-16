from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .models import Layer, MemoryItem, RawLog


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS raw_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    layer TEXT NOT NULL CHECK (layer IN ('L1', 'L2', 'L3')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    memory_type TEXT NOT NULL,
    source_log_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    weight REAL NOT NULL DEFAULT 1.0,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (project_id IS NOT NULL OR layer = 'L2'),
    UNIQUE(project_id, layer, title, memory_type)
);

CREATE TABLE IF NOT EXISTS memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    source_log_ids_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reflection_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS governance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subject_id INTEGER,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_raw_logs_project ON raw_logs(project_id, processed_at);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status, job_type, project_id);
CREATE INDEX IF NOT EXISTS idx_memories_project_layer ON memories(project_id, layer);
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memory_versions_memory ON memory_versions(memory_id, version);
CREATE INDEX IF NOT EXISTS idx_reflection_reports_project ON reflection_reports(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_governance_events_project ON governance_events(project_id, created_at);
"""

REQUIRED_TABLES = [
    "raw_logs",
    "processing_jobs",
    "memories",
    "memory_versions",
    "reflection_reports",
    "governance_events",
]


class MemoryStore:
    def __init__(self, path: str | Path = "memory.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def health_status(self) -> dict[str, Any]:
        with self.connect() as connection:
            integrity_check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys_enabled = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                ORDER BY name
                """
            ).fetchall()
            existing_tables = [str(row["name"]) for row in rows]
            missing_tables = [table for table in REQUIRED_TABLES if table not in existing_tables]
            row_counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in REQUIRED_TABLES
                if table in existing_tables
            }
        return {
            "ok": integrity_check == "ok" and foreign_keys_enabled and not missing_tables,
            "database_path": str(self.path),
            "integrity_check": integrity_check,
            "foreign_keys_enabled": foreign_keys_enabled,
            "required_tables": REQUIRED_TABLES,
            "existing_tables": existing_tables,
            "missing_tables": missing_tables,
            "row_counts": row_counts,
        }

    def append_raw_log(
        self,
        project_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO raw_logs(project_id, conversation_id, role, content, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            raw_log_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO processing_jobs(project_id, job_type, payload_json)
                VALUES (?, 'layer_raw_logs', ?)
                """,
                (project_id, json.dumps({"raw_log_ids": [raw_log_id]})),
            )
            return raw_log_id

    def list_pending_layering_projects(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT project_id
                FROM processing_jobs
                WHERE job_type = 'layer_raw_logs' AND status = 'pending'
                ORDER BY project_id
                """
            ).fetchall()
        return [row["project_id"] for row in rows]

    def mark_layering_jobs_running(self, project_id: str) -> list[int]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM processing_jobs
                WHERE project_id = ? AND job_type = 'layer_raw_logs' AND status = 'pending'
                ORDER BY id
                """,
                (project_id,),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE processing_jobs
                    SET status = 'running', updated_at = datetime('now')
                    WHERE id IN ({placeholders})
                    """,
                    ids,
                )
        return ids

    def complete_jobs(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE processing_jobs
                SET status = 'done', updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                ids,
            )

    def fail_jobs(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE processing_jobs
                SET status = 'failed', error = ?, updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                [error, *ids],
            )

    def retry_failed_layering_jobs(self, project_id: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'pending', error = NULL, updated_at = datetime('now')
                WHERE project_id = ? AND job_type = 'layer_raw_logs' AND status = 'failed'
                """,
                (project_id,),
            )
            return int(cursor.rowcount)

    def reset_stale_running_layering_jobs(self, project_id: str, older_than_minutes: int = 30) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE processing_jobs
                SET status = 'pending', error = NULL, updated_at = datetime('now')
                WHERE project_id = ?
                    AND job_type = 'layer_raw_logs'
                    AND status = 'running'
                    AND updated_at <= datetime('now', ?)
                """,
                (project_id, f"-{older_than_minutes} minutes"),
            )
            return int(cursor.rowcount)

    def count_jobs(self, project_id: str, status: str | None = None) -> int:
        query = "SELECT count(*) AS count FROM processing_jobs WHERE project_id = ?"
        params: list[Any] = [project_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["count"])

    def list_jobs(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, job_type, status, payload_json, error, created_at, updated_at
                FROM processing_jobs
                WHERE project_id = ?
                ORDER BY id
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "project_id": row["project_id"],
                "job_type": row["job_type"],
                "status": row["status"],
                "payload": json.loads(row["payload_json"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_job_raw_log_ids(self, ids: list[int]) -> list[int]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM processing_jobs WHERE id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
        raw_ids: list[int] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            raw_ids.extend(int(raw_id) for raw_id in payload.get("raw_log_ids", []))
        return raw_ids

    def list_raw_logs(self, project_id: str, unprocessed_only: bool = False) -> list[RawLog]:
        query = "SELECT * FROM raw_logs WHERE project_id = ?"
        clauses: list[str] = []
        params: list[Any] = [project_id]
        if unprocessed_only:
            clauses.append("processed_at IS NULL")
        if clauses:
            query += " AND " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._raw_log_from_row(row) for row in rows]

    def list_raw_logs_by_ids(self, project_id: str, ids: list[int], unprocessed_only: bool = False) -> list[RawLog]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        query = f"SELECT * FROM raw_logs WHERE project_id = ? AND id IN ({placeholders})"
        params: list[Any] = [project_id, *ids]
        if unprocessed_only:
            query += " AND processed_at IS NULL"
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._raw_log_from_row(row) for row in rows]

    def mark_raw_processed(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE raw_logs SET processed_at = datetime('now') WHERE id IN ({placeholders})",
                ids,
            )

    def upsert_memory(
        self,
        project_id: str | None,
        layer: Layer,
        title: str,
        body: str,
        tags: list[str],
        memory_type: str,
        source_log_ids: list[int],
        metadata: dict[str, Any] | None = None,
        weight: float = 1.0,
    ) -> int:
        if project_id is None and layer != Layer.L2:
            raise ValueError("only L2 knowledge can be stored without project_id")
        existing = self.find_exact_memory(project_id, layer, title, memory_type)
        if existing is None:
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO memories(
                        project_id, layer, title, body, tags_json, memory_type,
                        source_log_ids_json, metadata_json, weight
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        layer.value,
                        title,
                        body,
                        json.dumps(sorted(set(tags)), ensure_ascii=False),
                        memory_type,
                        json.dumps(sorted(set(source_log_ids))),
                        json.dumps(metadata or {}, ensure_ascii=False),
                        weight,
                    ),
                )
                memory_id = int(cursor.lastrowid)
                self._insert_version(connection, memory_id, 1, title, body, tags, source_log_ids, metadata or {})
                return memory_id

        merged_body = merge_text(existing.body, body)
        merged_tags = sorted(set(existing.tags) | set(tags))
        merged_sources = sorted(set(existing.source_log_ids) | set(source_log_ids))
        merged_metadata = {**existing.metadata, **(metadata or {})}
        next_version = existing.version + 1
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET body = ?, tags_json = ?, source_log_ids_json = ?, metadata_json = ?,
                    version = ?, weight = max(weight, ?), updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    merged_body,
                    json.dumps(merged_tags, ensure_ascii=False),
                    json.dumps(merged_sources),
                    json.dumps(merged_metadata, ensure_ascii=False),
                    next_version,
                    weight,
                    existing.id,
                ),
            )
            self._insert_version(
                connection, existing.id, next_version, title, merged_body, merged_tags, merged_sources, merged_metadata
            )
        return existing.id

    def find_exact_memory(
        self, project_id: str | None, layer: Layer, title: str, memory_type: str
    ) -> MemoryItem | None:
        if project_id is None:
            clause = "project_id IS NULL"
            params: list[Any] = [layer.value, title, memory_type]
        else:
            clause = "project_id = ?"
            params = [project_id, layer.value, title, memory_type]
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM memories WHERE {clause} AND layer = ? AND title = ? AND memory_type = ?",
                params,
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def list_memories(
        self,
        project_id: str,
        layers: list[Layer] | None = None,
        memory_types: list[str] | None = None,
        include_global_l2: bool = True,
    ) -> list[MemoryItem]:
        params: list[Any] = [project_id]
        project_clause = "project_id = ?"
        if include_global_l2:
            project_clause = "(project_id = ? OR (project_id IS NULL AND layer = 'L2'))"
        query = f"SELECT * FROM memories WHERE {project_clause}"
        if layers:
            placeholders = ",".join("?" for _ in layers)
            query += f" AND layer IN ({placeholders})"
            params.extend(layer.value for layer in layers)
        if memory_types:
            placeholders = ",".join("?" for _ in memory_types)
            query += f" AND memory_type IN ({placeholders})"
            params.extend(memory_types)
        query += " ORDER BY updated_at DESC, id DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def get_memory(self, memory_id: int) -> MemoryItem | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._memory_from_row(row) if row else None

    def list_memory_versions(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    memory_versions.id,
                    memory_versions.memory_id,
                    memory_versions.version,
                    memory_versions.title,
                    memory_versions.body,
                    memory_versions.tags_json,
                    memory_versions.source_log_ids_json,
                    memory_versions.metadata_json,
                    memory_versions.created_at
                FROM memory_versions
                JOIN memories ON memories.id = memory_versions.memory_id
                WHERE memories.project_id = ?
                ORDER BY memory_versions.memory_id, memory_versions.version
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "memory_id": int(row["memory_id"]),
                "version": int(row["version"]),
                "title": row["title"],
                "body": row["body"],
                "tags": json.loads(row["tags_json"]),
                "source_log_ids": json.loads(row["source_log_ids_json"]),
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def increment_access(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE memories
                SET access_count = access_count + 1,
                    weight = weight + 0.05,
                    updated_at = datetime('now')
                WHERE id IN ({placeholders})
                """,
                ids,
            )

    def decay_l1(self, factor: float = 0.95, floor: float = 0.1) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET weight = max(?, weight * ?) WHERE layer = 'L1'",
                (floor, factor),
            )

    def decay_stale_l1(
        self,
        project_id: str | None = None,
        unused_days: int = 30,
        factor: float = 0.95,
        floor: float = 0.1,
    ) -> int:
        project_clause = ""
        params: list[Any] = [floor, factor, f"-{unused_days} days"]
        if project_id is not None:
            project_clause = "AND project_id = ?"
            params.append(project_id)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE memories
                SET weight = max(?, weight * ?)
                WHERE layer = 'L1'
                  AND updated_at <= datetime('now', ?)
                  {project_clause}
                """,
                params,
            )
            return int(cursor.rowcount)

    def delete_low_value_l1(
        self,
        project_id: str | None = None,
        unused_days: int = 30,
        min_weight: float = 0.2,
        min_access_count: int = 1,
    ) -> int:
        project_clause = ""
        params: list[Any] = [min_weight, min_access_count, f"-{unused_days} days"]
        if project_id is not None:
            project_clause = "AND project_id = ?"
            params.append(project_id)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                DELETE FROM memories
                WHERE layer = 'L1'
                  AND weight < ?
                  AND access_count < ?
                  AND updated_at <= datetime('now', ?)
                  {project_clause}
                """,
                params,
            )
            return int(cursor.rowcount)

    def delete_project_derived_memories(self, project_id: str, layers: list[Layer]) -> int:
        safe_layers = [layer for layer in layers if layer != Layer.L3]
        if not safe_layers:
            return 0
        placeholders = ",".join("?" for _ in safe_layers)
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM memories WHERE project_id = ? AND layer IN ({placeholders})",
                [project_id, *(layer.value for layer in safe_layers)],
            )
            return int(cursor.rowcount)

    def delete_memories(self, ids: list[int], allowed_layers: list[Layer] | None = None) -> int:
        if not ids:
            return 0
        layers = [layer for layer in (allowed_layers or [Layer.L1]) if layer != Layer.L3]
        if not layers:
            return 0
        placeholders = ",".join("?" for _ in ids)
        layer_placeholders = ",".join("?" for _ in layers)
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders}) AND layer IN ({layer_placeholders})",
                [*ids, *(layer.value for layer in layers)],
            )
            return int(cursor.rowcount)

    def add_reflection_report(self, project_id: str, summary: str, metrics: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reflection_reports(project_id, summary, metrics_json)
                VALUES (?, ?, ?)
                """,
                (project_id, summary, json.dumps(metrics, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def list_reflection_reports(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, summary, metrics_json, created_at
                FROM reflection_reports
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "project_id": row["project_id"],
                "summary": row["summary"],
                "metrics": json.loads(row["metrics_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def add_governance_event(
        self,
        project_id: str,
        event_type: str,
        subject_id: int | None,
        reviewer: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO governance_events(
                    project_id, event_type, subject_id, reviewer, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    event_type,
                    subject_id,
                    reviewer,
                    reason,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def list_governance_events(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, event_type, subject_id, reviewer, reason, metadata_json, created_at
                FROM governance_events
                WHERE project_id = ?
                ORDER BY id
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "project_id": row["project_id"],
                "event_type": row["event_type"],
                "subject_id": row["subject_id"],
                "reviewer": row["reviewer"],
                "reason": row["reason"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        memory_id: int,
        version: int,
        title: str,
        body: str,
        tags: list[str],
        source_log_ids: list[int],
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_versions(
                memory_id, version, title, body, tags_json, source_log_ids_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                version,
                title,
                body,
                json.dumps(sorted(set(tags)), ensure_ascii=False),
                json.dumps(sorted(set(source_log_ids))),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

    def _raw_log_from_row(self, row: sqlite3.Row) -> RawLog:
        return RawLog(
            id=int(row["id"]),
            project_id=row["project_id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            processed_at=row["processed_at"],
        )

    def _memory_from_row(self, row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            id=int(row["id"]),
            project_id=row["project_id"],
            layer=Layer(row["layer"]),
            title=row["title"],
            body=row["body"],
            tags=json.loads(row["tags_json"]),
            memory_type=row["memory_type"],
            source_log_ids=json.loads(row["source_log_ids_json"]),
            metadata=json.loads(row["metadata_json"]),
            version=int(row["version"]),
            weight=float(row["weight"]),
            access_count=int(row["access_count"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def merge_text(existing: str, incoming: str) -> str:
    if incoming in existing:
        return existing
    if existing in incoming:
        return incoming
    return existing.rstrip() + "\n\n---\n" + incoming.strip()
