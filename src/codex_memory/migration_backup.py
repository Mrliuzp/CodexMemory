from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .migration_inventory import sha256_file


@dataclass(frozen=True)
class BackupResult:
    source_sha256: str
    sha256: str
    destination: Path


def backup_sqlite(source: str | Path, destination: str | Path) -> BackupResult:
    import sqlite3

    source_path = Path(source).resolve()
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{quote(source_path.as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as source_db, sqlite3.connect(destination_path) as target_db:
        source_db.backup(target_db)
    return BackupResult(sha256_file(source_path), sha256_file(destination_path), destination_path)
