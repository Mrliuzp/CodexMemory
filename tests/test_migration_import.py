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

def test_migrate_cli_uses_explicit_database_url_without_initializing_schema(tmp_path: Path, monkeypatch, capsys) -> None:
    import json

    from codex_memory.cli import main
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.migration_backup import backup_sqlite

    source = tmp_path / "legacy.db"
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    _legacy(source)
    result = backup_sqlite(source, backup)
    engine = create_sqlite_engine(f"sqlite:///{target}")
    create_schema(engine)
    with create_session_factory(engine)() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    monkeypatch.setattr("codex_memory.cli.create_schema", lambda _engine: (_ for _ in ()).throw(AssertionError("must not initialize explicit target")))
    monkeypatch.setattr(
        "sys.argv",
        [
            "codex-memory",
            "--database-url",
            f"sqlite+pysqlite:///{target}",
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

def test_postgresql_target_must_be_ready_before_import(monkeypatch) -> None:
    import argparse

    from codex_memory.cli import _migration_target_session_factory

    class FakeEngine:
        class dialect:
            name = "postgresql"

    monkeypatch.setattr("codex_memory.cli.create_engine_from_url", lambda _url: FakeEngine())
    monkeypatch.setattr("codex_memory.cli.create_session_factory", lambda _engine: object())
    monkeypatch.setattr("codex_memory.cli.build_readiness", lambda _factory: {"status": "degraded"})

    with pytest.raises(SystemExit, match="not ready"):
        _migration_target_session_factory(argparse.Namespace(database_url="postgresql+psycopg://example"))

def test_imports_legacy_memories_versions_and_message_sources_idempotently(tmp_path: Path) -> None:
    from sqlalchemy import select

    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import MemoryRow, MemorySourceRow, MemoryVersionRow, ProjectRow
    from codex_memory.migration_import import MigrationImporter
    from codex_memory.migration_verify import verify_migration

    source = tmp_path / "legacy-full.db"
    with sqlite3.connect(source) as legacy:
        legacy.executescript(
            """
            CREATE TABLE raw_logs(id INTEGER PRIMARY KEY, project_id TEXT, conversation_id TEXT, role TEXT, content TEXT, metadata_json TEXT);
            CREATE TABLE memories(id INTEGER PRIMARY KEY, project_id TEXT, layer TEXT, title TEXT, body TEXT, tags_json TEXT, memory_type TEXT, source_log_ids_json TEXT, metadata_json TEXT, version INTEGER, weight REAL, access_count INTEGER);
            CREATE TABLE memory_versions(id INTEGER PRIMARY KEY, memory_id INTEGER, version INTEGER, title TEXT, body TEXT, tags_json TEXT, source_log_ids_json TEXT, metadata_json TEXT);
            """
        )
        legacy.execute("INSERT INTO raw_logs VALUES (1, 'legacy-project', 'conversation-1', 'user', 'original question', '{}')")
        legacy.execute("INSERT INTO memories VALUES (10, 'legacy-project', 'L1', '缓存规则', '更新缓存键', '[\"cache\"]', 'rule', '[1]', '{\"module\":\"cache\"}', 1, 0.8, 4)")
        legacy.execute("INSERT INTO memory_versions VALUES (11, 10, 1, '缓存规则', '更新缓存键', '[\"cache\"]', '[1]', '{\"module\":\"cache\"}')")

    engine = create_sqlite_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()

    first = MigrationImporter(factory).import_batch(source, {"legacy-project": "erp"})
    second = MigrationImporter(factory).import_batch(source, {"legacy-project": "erp"})

    assert first.memories.created == 1
    assert first.memory_versions.created == 1
    assert first.memory_sources.created == 1
    assert second.memories.duplicates == 1
    verification = verify_migration(source, factory, first.batch_id)
    assert verification.memory_counts_match is True
    assert verification.version_counts_match is True
    assert verification.broken_memory_sources == 0
    with factory() as session:
        memory = session.scalar(select(MemoryRow))
        assert memory is not None
        assert memory.content["text"] == "更新缓存键"
        assert session.scalar(select(MemoryVersionRow).where(MemoryVersionRow.memory_id == memory.id)) is not None
        assert session.scalar(select(MemorySourceRow).where(MemorySourceRow.memory_id == memory.id)) is not None