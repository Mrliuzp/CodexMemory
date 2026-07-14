from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


class HookEventError(ValueError):
    pass


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
        return {
            "project_key": self.project_id,
            "session_key": self.session_id,
            "event_key": self.event_key,
            "role": self.role,
            "content": self.content,
            "source": "hook",
            "metadata": {"turn_id": self.turn_id},
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