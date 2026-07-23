from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .migration_inventory import sha256_file


class BackupManifestError(ValueError):
    pass


@dataclass(frozen=True)
class BackupResult:
    source_sha256: str
    sha256: str
    destination: Path
    manifest_path: Path


def backup_sqlite(source: str | Path, destination: str | Path) -> BackupResult:
    import sqlite3

    source_path = Path(source).resolve()
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{quote(source_path.as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as source_db, sqlite3.connect(destination_path) as target_db:
        source_db.backup(target_db)
    result = BackupResult(
        source_sha256=sha256_file(source_path),
        sha256=sha256_file(destination_path),
        destination=destination_path,
        manifest_path=destination_path.with_suffix(f"{destination_path.suffix}.manifest.json"),
    )
    result.manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source_sha256": result.source_sha256,
                "backup_sha256": result.sha256,
                "backup_name": result.destination.name,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return result


def verify_backup_manifest(backup: str | Path, manifest_path: str | Path) -> None:
    backup_path = Path(backup)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackupManifestError(f"backup manifest cannot be read: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise BackupManifestError("backup manifest has an unsupported format")
    expected = manifest.get("backup_sha256")
    if not isinstance(expected, str) or expected != sha256_file(backup_path):
        raise BackupManifestError("backup manifest does not match the migration source")