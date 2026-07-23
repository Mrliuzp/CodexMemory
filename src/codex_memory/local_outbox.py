from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .hook_client import PermanentHookError, RetryableHookError


_PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "api_key")


@dataclass(frozen=True)
class ReplayReport:
    delivered: int = 0
    remaining: int = 0
    dead_lettered: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def retry_delay_seconds(attempts: int) -> int:
    return min(3600, 2 ** min(max(attempts, 0), 11))


class LocalOutbox:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def enqueue(self, project_id: str, payload: dict[str, Any], reason: str) -> None:
        project_dir = self._project_dir(project_id)
        event_key = payload.get("event_key")
        if not isinstance(event_key, str) or not event_key:
            raise ValueError("outbox \u4e8b\u4ef6\u7f3a\u5c11\u6709\u6548 event_key")

        record = {
            "schema": 1,
            "project_id": project_id,
            "event_key": event_key,
            "payload": _strip_sensitive(payload),
            "queued_at": _utc_now().isoformat(),
            "attempts": 0,
            "next_attempt_at": None,
            "last_error": _safe_reason(reason),
        }
        pending = project_dir / "pending.jsonl"
        with _file_lock(pending):
            _append_record(pending, record)

    def replay(
        self,
        send: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        project_id: str | None = None,
        now: datetime | None = None,
    ) -> ReplayReport:
        current_time = _ensure_utc(now or _utc_now())
        delivered = 0
        dead_lettered = 0
        remaining_total = 0
        project_dirs = [self._project_dir(project_id)] if project_id else self._project_dirs()

        for project_dir in project_dirs:
            pending = project_dir / "pending.jsonl"
            if not pending.exists():
                continue
            with _file_lock(pending):
                records = _read_records(pending)
                remaining: list[dict[str, Any]] = []
                dead_records: list[dict[str, Any]] = []
                for record in records:
                    if not _is_due(record, current_time):
                        remaining.append(record)
                        continue
                    try:
                        send(record["payload"])
                    except PermanentHookError:
                        dead_records.append(_dead_letter(record))
                        dead_lettered += 1
                    except (RetryableHookError, OSError):
                        remaining.append(_retry_record(record, current_time))
                    except Exception:
                        remaining.append(_retry_record(record, current_time))
                    else:
                        delivered += 1
                if dead_records:
                    dead_path = project_dir / "dead-letter.jsonl"
                    for record in dead_records:
                        _append_record(dead_path, record)
                _rewrite_records(pending, remaining)
                remaining_total += len(remaining)

        return ReplayReport(delivered=delivered, remaining=remaining_total, dead_lettered=dead_lettered)

    def _project_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [path for path in self.root.iterdir() if path.is_dir() and _PROJECT_ID_PATTERN.fullmatch(path.name)]

    def _project_dir(self, project_id: str) -> Path:
        if not _PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ValueError("outbox \u9879\u76ee\u6807\u8bc6\u683c\u5f0f\u65e0\u6548")
        return self.root / project_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_due(record: dict[str, Any], current_time: datetime) -> bool:
    next_attempt = record.get("next_attempt_at")
    if next_attempt is None:
        return True
    if not isinstance(next_attempt, str):
        return True
    try:
        return _ensure_utc(datetime.fromisoformat(next_attempt)) <= current_time
    except ValueError:
        return True


def _retry_record(record: dict[str, Any], current_time: datetime) -> dict[str, Any]:
    attempts = int(record.get("attempts", 0)) + 1
    retry = dict(record)
    retry["attempts"] = attempts
    retry["next_attempt_at"] = (current_time + timedelta(seconds=retry_delay_seconds(attempts))).isoformat()
    retry["last_error"] = "Codex Memory \u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528"
    return retry


def _dead_letter(record: dict[str, Any]) -> dict[str, Any]:
    dead = dict(record)
    dead["dead_lettered_at"] = _utc_now().isoformat()
    dead["last_error"] = "Codex Memory \u62d2\u7edd\u8be5\u5f52\u6863\u4e8b\u4ef6"
    return dead


def _strip_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_sensitive(item)
            for key, item in value.items()
            if isinstance(key, str) and not any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value]
    return value


def _safe_reason(_reason: str) -> str:
    return "Codex Memory \u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528"


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("payload"), dict):
            records.append(record)
    return records


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _rewrite_records(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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
