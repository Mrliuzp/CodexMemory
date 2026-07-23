from __future__ import annotations

import sqlite3
from pathlib import Path


def _legacy(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE raw_logs(id INTEGER PRIMARY KEY, project_id TEXT, conversation_id TEXT, role TEXT, content TEXT, metadata_json TEXT); CREATE TABLE memories(id INTEGER); CREATE TABLE memory_versions(id INTEGER);")
        db.executemany("INSERT INTO raw_logs VALUES (?, ?, ?, ?, ?, ?)", [(1, "legacy", "c1", "user", "one", "{}"), (2, "legacy", "c1", "assistant", "two", "{}")])


def test_verification_reports_counts_and_fingerprints(tmp_path: Path) -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.migration_import import MigrationImporter
    from codex_memory.migration_verify import verify_migration
    source = tmp_path / "legacy.db"; _legacy(source)
    engine = create_sqlite_engine(); create_schema(engine); factory = create_session_factory(engine)
    with factory() as session: session.add(ProjectRow(project_key="erp", name="ERP")); session.commit()
    report = MigrationImporter(factory).import_batch(source, {"legacy": "erp"})
    verification = verify_migration(source, factory, report.batch_id)
    assert verification.counts_match is True
    assert verification.duplicate_fingerprints == 0
    assert verification.ready_to_cutover is True


def test_verify_migration_cli_outputs_cutover_report(tmp_path: Path, monkeypatch, capsys) -> None:
    from codex_memory.cli import main
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.migration_import import MigrationImporter

    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    _legacy(source)
    engine = create_sqlite_engine(f"sqlite:///{target}")
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    batch = MigrationImporter(factory).import_batch(source, {"legacy": "erp"})

    monkeypatch.setattr("sys.argv", ["codex-memory", "--db", str(target), "verify-migration", "--source", str(source), "--batch-id", str(batch.batch_id)])
    main()

    import json

    output = json.loads(capsys.readouterr().out)
    assert output["ready_to_cutover"] is True
    assert output["counts_match"] is True

def test_verification_opens_source_read_only(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.migration_import import MigrationImporter
    from codex_memory.migration_verify import verify_migration

    source = tmp_path / "legacy.db"
    _legacy(source)
    engine = create_sqlite_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    batch = MigrationImporter(factory).import_batch(source, {"legacy": "erp"})
    original_connect = sqlite3.connect
    calls: list[tuple[object, dict]] = []

    def track_connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", track_connect)
    verify_migration(source, factory, batch.batch_id)

    assert calls
    assert all(str(database).startswith("file:") and kwargs.get("uri") is True for database, kwargs in calls)