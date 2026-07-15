from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .hook_client import HookApiClient, PermanentHookError, RetryableHookError
from .hook_events import HookMessage, parse_assistant_event, parse_user_event
from .local_outbox import LocalOutbox, ReplayReport
from .project_config import ProjectConfigError, load_project_memory_config


@dataclass(frozen=True)
class HookResult:
    queued: bool = False
    error: str | None = None


def handle_user_prompt(
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> str:
    values = _environment(env)
    config = load_project_memory_config(_cwd(event))
    if not config.enabled:
        return ""
    message = parse_user_event(event, project_id=config.project_id or "")
    api = client or _client(values)
    outbox = _outbox(values)
    try:
        api.append(message.to_append_payload())
        return format_context(api.context(message.project_id, message.content))
    except RetryableHookError as error:
        outbox.enqueue(message.project_id, message.to_append_payload(), str(error))
    except PermanentHookError:
        return ""
    return ""


def handle_assistant_stop(
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> HookResult:
    values = _environment(env)
    config = load_project_memory_config(_cwd(event))
    if not config.enabled:
        return HookResult()
    message = parse_assistant_event(event, project_id=config.project_id or "")
    if message is None:
        return HookResult()
    api = client or _client(values)
    return _archive_message(message, api, _outbox(values))


def replay_outbox(
    env: Mapping[str, str] | None = None,
    *,
    project_id: str | None = None,
    client: HookApiClient | None = None,
) -> ReplayReport:
    values = _environment(env)
    api = client or _client(values)
    return _outbox(values).replay(api.append, project_id=project_id)


def format_context(context: Mapping[str, Any]) -> str:
    sections = ("critical_rules", "long_term_rules", "recent_insights")
    lines: list[str] = []
    for section in sections:
        values = context.get(section, [])
        if not isinstance(values, list):
            continue
        for item in values:
            content = item.get("content") if isinstance(item, dict) else item
            if isinstance(content, str) and content:
                lines.append(content)
    return "\n".join(lines)


def _archive_message(message: HookMessage, api: HookApiClient, outbox: LocalOutbox) -> HookResult:
    try:
        api.append(message.to_append_payload())
    except RetryableHookError as error:
        outbox.enqueue(message.project_id, message.to_append_payload(), str(error))
        return HookResult(queued=True)
    except PermanentHookError as error:
        return HookResult(error=str(error))
    return HookResult()


def _environment(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _cwd(event: Mapping[str, Any]) -> str:
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ProjectConfigError("Hook \u4e8b\u4ef6\u7f3a\u5c11\u6709\u6548 cwd")
    return cwd


def _client(env: Mapping[str, str]) -> HookApiClient:
    token = env.get("CODEX_MEMORY_API_TOKEN") or env.get("CODEX_MEMORY_SERVICE_TOKEN")
    if not token:
        raise PermanentHookError("Codex Memory \u8ba4\u8bc1\u4ee4\u724c\u7f3a\u5931")
    return HookApiClient(env.get("CODEX_MEMORY_API_URL", "http://127.0.0.1:8001"), token)


def _outbox(env: Mapping[str, str]) -> LocalOutbox:
    root = env.get("CODEX_MEMORY_OUTBOX_DIR")
    if root:
        return LocalOutbox(root)
    return LocalOutbox(Path.home() / ".codex" / "codex-memory-outbox")
