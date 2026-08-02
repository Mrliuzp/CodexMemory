"""历史数据导入内容的持久化适配层。

默认将内容保存在数据库中，也支持把载荷写入本地文件系统。
未来接入 S3/MinIO 时，只需实现相同协议，Worker 无需感知存储差异。
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
        """保存导入内容并返回存储引用。"""

    def get(self, stored: StoredImport) -> str:
        """读取存储引用对应的导入内容。"""


class DatabaseImportStorage:
    backend = "database"

    def put(self, content: str, batch_id: int, content_hash: str) -> StoredImport:
        return StoredImport(self.backend, f"db://import-files/{batch_id}/{content_hash}", content)

    def get(self, stored: StoredImport) -> str:
        if stored.content is None:
            raise ValueError("数据库存储记录缺少导入内容")
        return stored.content


class FilesystemImportStorage:
    """将导入载荷原子写入受控目录的文件系统存储。"""

    backend = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        prefix = "fs://import-files/"
        if not key.startswith(prefix):
            raise ValueError("无效的导入存储键")
        relative = Path(key[len(prefix):])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("导入存储路径不安全")
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
    raise ValueError(f"不支持的导入存储后端：{backend}")
