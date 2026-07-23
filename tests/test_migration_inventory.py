from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codex_memory.migration_backup import backup_sqlite
from codex_memory.migration_inventory import UnsupportedSourceError, inventory_source, sha256_file


def _legacy(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE raw_logs(id INTEGER); CREATE TABLE memories(id INTEGER); CREATE TABLE memory_versions(id INTEGER);")
        db.executemany("INSERT INTO raw_logs VALUES (?)", [(1,), (2,)])


def test_inventory_records_hash_schema_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _legacy(path)
    manifest = inventory_source(path)
    assert manifest.sha256 == sha256_file(path)
    assert manifest.tables["raw_logs"] == 2
    assert manifest.schema_family == "legacy-layered"
    assert str(path) not in manifest.public_dict()["source_path_hash"]


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unknown.db"
    sqlite3.connect(path).execute("CREATE TABLE other(id INTEGER)").connection.close()
    with pytest.raises(UnsupportedSourceError): inventory_source(path)


def test_backup_uses_consistent_sqlite_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"; _legacy(source)
    destination = tmp_path / "backup" / "memory.db"
    result = backup_sqlite(source, destination)
    assert result.sha256 == sha256_file(destination)
    assert result.source_sha256 == sha256_file(source)
    with sqlite3.connect(destination) as db: assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
