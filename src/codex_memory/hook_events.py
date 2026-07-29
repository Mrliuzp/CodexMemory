from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping


class HookEventError(ValueError):
    pass


EVENT_MAX_BYTES = 64 * 1024
COMMAND_MAX_BYTES = 4 * 1024
RESULT_MAX_BYTES = 8 * 1024
_REDACTED = "<已脱敏>"
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer\s+)?)([^\s,;]+)"),
    re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password|passwd|token)\s*[:=]\s*[\"']?)([^\s,;\"']+)"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b"),
)


@dataclass(frozen=True)
class HookMessage:
    project_id: str
    session_id: str
    turn_id: str
    role: Literal["user", "assistant"]
    content: str
    cwd: str

    @property
    def event_key(self) -> str:
        return f"codex:{self.project_id}:{self.session_id}:{self.turn_id}:{self.role}"

    def to_append_payload(self) -> dict[str, Any]:
        content = bounded_text(self.content, EVENT_MAX_BYTES - 1024)
        metadata: dict[str, Any] = {"turn_id": self.turn_id}
        if content["truncated"]:
            metadata["content_length"] = content["length"]
            metadata["content_sha256"] = content["sha256"]
            metadata["content_truncated"] = True
        return {
            "project_key": self.project_id,
            "session_key": self.session_id,
            "event_key": self.event_key,
            "role": self.role,
            "content": content["value"],
            "source": "hook",
            "metadata": metadata,
        }


def parse_user_event(event: Mapping[str, Any], *, project_id: str) -> HookMessage:
    return _parse_event(event, project_id=project_id, role="user", content_key="prompt")


def parse_assistant_event(event: Mapping[str, Any], *, project_id: str) -> HookMessage | None:
    content = event.get("last_assistant_message")
    if content == "":
        return None
    return _parse_event(event, project_id=project_id, role="assistant", content_key="last_assistant_message")


def _parse_event(
    event: Mapping[str, Any],
    *,
    project_id: str,
    role: Literal["user", "assistant"],
    content_key: str,
) -> HookMessage:
    values = {key: event.get(key) for key in ("cwd", "session_id", "turn_id", content_key)}
    if not isinstance(project_id, str) or not project_id:
        raise HookEventError("项目标识缺少或无效")
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise HookEventError("Hook 事件字段缺少或无效")
    return HookMessage(
        project_id=project_id,
        session_id=values["session_id"],
        turn_id=values["turn_id"],
        role=role,
        content=values[content_key],
        cwd=values["cwd"],
    )


def redact_credentials(value: Any) -> Any:
    """在 Hook 边界移除常见凭证，服务端仍需执行第二层脱敏。"""
    if isinstance(value, str):
        result = value
        for pattern in _CREDENTIAL_PATTERNS:
            if pattern.groups == 2:
                result = pattern.sub(lambda match: match.group(1) + _REDACTED, result)
            else:
                result = pattern.sub(_REDACTED, result)
        return result
    if isinstance(value, Mapping):
        return {
            str(key): redact_credentials(item)
            for key, item in value.items()
            if str(key).lower() not in {"transcript", "transcript_path", "transcript_file"}
        }
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    if isinstance(value, tuple):
        return [redact_credentials(item) for item in value]
    return value


def bounded_text(value: Any, limit_bytes: int) -> dict[str, Any]:
    """返回 UTF-8 字节上限内的文本，并在截断时保留安全摘要。"""
    if isinstance(value, str):
        text = redact_credentials(value)
    else:
        text = redact_credentials(str(value))
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return {"value": text, "length": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(), "truncated": False}
    clipped = encoded[: max(0, limit_bytes)].decode("utf-8", errors="ignore")
    return {
        "value": clipped,
        "length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "truncated": True,
    }
