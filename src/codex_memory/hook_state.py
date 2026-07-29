from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class HookStateError(OSError):
    pass


class HookStateStore:
    """将运行状态放在用户本地 Codex 目录，不在项目目录创建文件。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else Path.home() / ".codex" / "codex-memory-hook-state"

    def load(self, project_id: str, session_id: str) -> dict[str, Any] | None:
        path = self._path(project_id, session_id)
        if not path.exists():
            return None
        try:
            with _lock(path):
                value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise HookStateError("本地 Hook 状态不可读") from error
        return value if isinstance(value, dict) else None

    def save(self, project_id: str, session_id: str, value: dict[str, Any]) -> None:
        path = self._path(project_id, session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with _lock(path):
                descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
                        handle.write("\n")
                    temporary.replace(path)
                finally:
                    if temporary.exists():
                        temporary.unlink()
        except OSError as error:
            raise HookStateError("本地 Hook 状态不可写") from error

    def _path(self, project_id: str, session_id: str) -> Path:
        digest = hashlib.sha256(f"{project_id}\0{session_id}".encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(" ")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as error:
        raise HookStateError("本地 Hook 锁不可用") from error
