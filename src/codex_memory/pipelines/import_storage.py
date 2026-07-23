"""???????????

?????????????????????????????????????
S3/MinIO ?????????Worker ?????????????
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredImport:
    backend: str
    key: str
    content: str | None


class ImportStorage(Protocol):
    backend: str

    def put(self, content: str, batch_id: int, content_hash: str) -> StoredImport:
        """???????????????"""

    def get(self, stored: StoredImport) -> str:
        """????????????"""


class DatabaseImportStorage:
    backend = "database"

    def put(self, content: str, batch_id: int, content_hash: str) -> StoredImport:
        return StoredImport(self.backend, f"db://import-files/{batch_id}/{content_hash}", content)

    def get(self, stored: StoredImport) -> str:
        if stored.content is None:
            raise ValueError("???????????")
        return stored.content


class FilesystemImportStorage:
    """???????????????????????????"""

    backend = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        prefix = "fs://import-files/"
        if not key.startswith(prefix):
            raise ValueError("????????")
        relative = Path(key[len(prefix):])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("????????")
        return self.root / relative

    def put(self, content: str, batch_id: int, content_hash: str) -> StoredImport:
        key = f"fs://import-files/{batch_id}/{content_hash}.payload"
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".import-", dir=target.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
        return StoredImport(self.backend, key, None)

    def get(self, stored: StoredImport) -> str:
        return self._path(stored.key).read_text(encoding="utf-8")


def build_import_storage() -> ImportStorage:
    backend = os.environ.get("IMPORT_STORAGE_BACKEND", "database").strip().lower()
    if backend in {"filesystem", "file", "local"}:
        root = os.environ.get("IMPORT_STORAGE_PATH", ".codex-import-storage")
        return FilesystemImportStorage(root)
    if backend in {"database", "db"}:
        return DatabaseImportStorage()
    raise ValueError(f"???????????{backend}")
