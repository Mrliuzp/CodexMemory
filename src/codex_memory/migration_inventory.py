from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote


class UnsupportedSourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceManifest:
    sha256: str
    source_path_hash: str
    schema_family: str
    tables: dict[str, int]

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory_source(path: str | Path) -> SourceManifest:
    source = Path(path).resolve()
    uri = f"file:{quote(source.as_posix())}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        family = _schema_family(set(names))
        counts = {name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in names if not name.startswith("sqlite_")}
    return SourceManifest(sha256_file(source), hashlib.sha256(str(source).encode()).hexdigest(), family, counts)


def _schema_family(names: set[str]) -> str:
    if {"raw_logs", "memories", "memory_versions"} <= names:
        return "legacy-layered"
    if {"projects", "sessions", "messages"} <= names:
        return "v1-relational"
    raise UnsupportedSourceError("不支持的 SQLite 来源结构")
