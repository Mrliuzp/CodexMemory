from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


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


def test_migrate_cli_apply_requires_verified_backup_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    from codex_memory.cli import main

    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    _legacy(source)
    monkeypatch.setattr(
        "sys.argv",
        [
            "codex-memory",
            "--db",
            str(target),
            "migrate",
            "--source",
            str(source),
            "--project-map",
            '{"legacy-project":"erp"}',
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as error:
        main()

    assert "backup-manifest" in str(error.value)

def test_migrate_cli_apply_accepts_matching_backup_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    from codex_memory.cli import main
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.migration_backup import backup_sqlite

    source = tmp_path / "legacy.db"
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    engine = create_sqlite_engine(f"sqlite:///{target}")
    create_schema(engine)
    with create_session_factory(engine)() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    _legacy(source)
    result = backup_sqlite(source, backup)
    monkeypatch.setattr(
        "sys.argv",
        [
            "codex-memory",
            "--db",
            str(target),
            "migrate",
            "--source",
            str(backup),
            "--project-map",
            '{"legacy-project":"erp"}',
            "--backup-manifest",
            str(result.manifest_path),
            "--apply",
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out)["messages"]["created"] == 2