from __future__ import annotations

import sqlite3
from pathlib import Path


def _legacy(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE raw_logs(id INTEGER PRIMARY KEY, project_id TEXT, conversation_id TEXT, role TEXT, content TEXT, metadata_json TEXT); CREATE TABLE memories(id INTEGER); CREATE TABLE memory_versions(id INTEGER)")
        db.executemany("INSERT INTO raw_logs VALUES (?, ?, ?, ?, ?, ?)", [(1, "legacy-project", "c1", "user", "hello", "{}"), (2, "legacy-project", "c1", "assistant", "world", "{}")])


def test_imports_raw_logs_with_stable_fingerprint(tmp_path: Path) -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.migration_import import MigrationImporter

    source = tmp_path / "legacy.db"; _legacy(source)
    engine = create_sqlite_engine(); create_schema(engine); factory = create_session_factory(engine)
    with factory() as session: session.add(ProjectRow(project_key="erp", name="ERP")); session.commit()
    first = MigrationImporter(factory).import_batch(source, {"legacy-project": "erp"})
    second = MigrationImporter(factory).import_batch(source, {"legacy-project": "erp"})
    assert first.messages.created == 2
    assert first.sessions.created == 1
    assert second.messages.created == 0
    assert second.messages.duplicates == 2


def test_unmapped_project_creates_issue_without_writing(tmp_path: Path) -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.migration_import import MigrationImporter
    source = tmp_path / "legacy.db"; _legacy(source)
    engine = create_sqlite_engine(); create_schema(engine); factory = create_session_factory(engine)
    report = MigrationImporter(factory).import_batch(source, {})
    assert report.issues.by_code["unmapped_project"] == 2
    assert report.messages.created == 0
