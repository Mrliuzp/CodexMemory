from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .git_manifest import GitSnapshot, build_change_manifest, collect_git_snapshot
from .hook_client import HookApiClient, PermanentHookError, RetryableHookError
from .hook_events import (
    COMMAND_MAX_BYTES,
    EVENT_MAX_BYTES,
    RESULT_MAX_BYTES,
    HookMessage,
    bounded_text,
    parse_assistant_event,
    parse_user_event,
    redact_credentials,
)
from .hook_state import HookStateError, HookStateStore
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
    """保留旧 append/context 行为，同时记录 V1.4 UserPromptSubmit。"""
    values = _environment(env)
    try:
        config = load_project_memory_config(_cwd(event))
        if not config.enabled:
            return ""
        message = parse_user_event(event, project_id=config.project_id or "")
        api = client or _client(values)
    except (ProjectConfigError, PermanentHookError, ValueError):
        return ""

    append_failed = False
    try:
        api.append(message.to_append_payload())
    except RetryableHookError as error:
        append_failed = True
        _queue(values, message.project_id, message.to_append_payload(), str(error))
    except Exception:
        append_failed = True

    result = ""
    if not append_failed:
        try:
            result = format_context(api.context(message.project_id, message.content))
        except Exception:
            result = ""
    task_result = _record_task_event("UserPromptSubmit", event, message.project_id, values, api)
    if task_result.error and not result:
        return ""
    return result


def handle_pre_tool_use(
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> HookResult:
    return _handle_tool_event("PreToolUse", event, env, client=client)


def handle_post_tool_use(
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> HookResult:
    return _handle_tool_event("PostToolUse", event, env, client=client)


def handle_assistant_stop(
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> HookResult:
    return _handle_end_event("Stop", event, env, client=client, preserve_append=True)


def handle_session_end(
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> HookResult:
    return _handle_end_event("SessionEnd", event, env, client=client, preserve_append=False)


def handle_event(
    event_type: str,
    event: dict[str, Any],
    env: Mapping[str, str] | None = None,
    *,
    client: HookApiClient | None = None,
) -> str | HookResult:
    normalized = _normalize_event_type(event_type)
    if normalized == "UserPromptSubmit":
        return handle_user_prompt(event, env, client=client)
    if normalized == "PreToolUse":
        return handle_pre_tool_use(event, env, client=client)
    if normalized == "PostToolUse":
        return handle_post_tool_use(event, env, client=client)
    if normalized == "Stop":
        return handle_assistant_stop(event, env, client=client)
    if normalized == "SessionEnd":
        return handle_session_end(event, env, client=client)
    return HookResult(error="未知 Hook 事件")


def replay_outbox(
    env: Mapping[str, str] | None = None,
    *,
    project_id: str | None = None,
    client: HookApiClient | None = None,
) -> ReplayReport:
    values = _environment(env)
    api = client or _client(values)

    def send(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("event_type"):
            sender = getattr(api, "task_event", None) or getattr(api, "send_task_event", None)
            if sender is None:
                raise RetryableHookError("客户端不支持 task-events")
            return sender(payload)
        return api.append(payload)

    return _outbox(values).replay(send, project_id=project_id)


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


def _handle_tool_event(
    event_type: str,
    event: dict[str, Any],
    env: Mapping[str, str] | None,
    *,
    client: HookApiClient | None,
) -> HookResult:
    values = _environment(env)
    try:
        config = load_project_memory_config(_cwd(event))
        if not config.enabled:
            return HookResult()
        session_id = _event_string(event, "session_id", "session_key")
        api = client or _client(values)
    except (ProjectConfigError, PermanentHookError, ValueError) as error:
        return HookResult(error=str(error))

    metadata = _tool_metadata(event, event_type)
    if event_type == "PreToolUse":
        baseline = _load_baseline(values, config.project_id or "", session_id)
        if baseline is None:
            baseline = collect_git_snapshot(_cwd(event))
            _save_baseline(values, config.project_id or "", session_id, _cwd(event), baseline)
        metadata["git_baseline"] = baseline.to_dict()
    payload = _task_payload(event_type, event, config.project_id or "", session_id, metadata)
    return _deliver_task_event(values, config.project_id or "", payload, api)


def _handle_end_event(
    event_type: str,
    event: dict[str, Any],
    env: Mapping[str, str] | None,
    *,
    client: HookApiClient | None,
    preserve_append: bool,
) -> HookResult:
    values = _environment(env)
    try:
        config = load_project_memory_config(_cwd(event))
        if not config.enabled:
            return HookResult()
        session_id = _event_string(event, "session_id", "session_key")
        api = client or _client(values)
    except (ProjectConfigError, PermanentHookError, ValueError) as error:
        return HookResult(error=str(error))

    append_result = HookResult()
    if preserve_append:
        message = None
        if "last_assistant_message" in event:
            try:
                message = parse_assistant_event(event, project_id=config.project_id or "")
            except ValueError as error:
                append_result = HookResult(error=str(error))
        if message is not None:
            append_result = _archive_message(message, api, _outbox_safe(values))

    current = collect_git_snapshot(_cwd(event))
    baseline = _load_baseline(values, config.project_id or "", session_id)
    if baseline is None:
        baseline = GitSnapshot(available=False, error="基线未记录")
    manifest = build_change_manifest(baseline, current, cwd=_cwd(event))
    metadata = {
        "change_manifest": manifest,
        "git_snapshot": current.to_dict(),
    }
    result = _deliver_task_event(
        values,
        config.project_id or "",
        _task_payload(event_type, event, config.project_id or "", session_id, metadata),
        api,
    )
    if result.error and append_result.error:
        return HookResult(queued=result.queued or append_result.queued, error=result.error)
    return HookResult(queued=result.queued or append_result.queued, error=result.error or append_result.error)


def _archive_message(message: HookMessage, api: HookApiClient, outbox: LocalOutbox | None) -> HookResult:
    try:
        api.append(message.to_append_payload())
    except RetryableHookError as error:
        queued = _queue_outbox(outbox, message.project_id, message.to_append_payload(), str(error))
        return HookResult(queued=queued, error=None if queued else "本地 Outbox 不可用")
    except Exception as error:
        return HookResult(error=str(error))
    return HookResult()


def _record_task_event(
    event_type: str,
    event: dict[str, Any],
    project_id: str,
    values: Mapping[str, str],
    api: HookApiClient,
) -> HookResult:
    try:
        session_id = _event_string(event, "session_id", "session_key")
    except ValueError as error:
        return HookResult(error=str(error))
    payload = _task_payload(event_type, event, project_id, session_id, {"prompt": _bounded_field(event.get("prompt", ""), EVENT_MAX_BYTES - 4096)})
    return _deliver_task_event(values, project_id, payload, api)


def _deliver_task_event(values: Mapping[str, str], project_id: str, payload: dict[str, Any], api: HookApiClient) -> HookResult:
    sender = getattr(api, "task_event", None) or getattr(api, "send_task_event", None)
    if sender is None:
        return HookResult()
    try:
        sender(payload)
    except RetryableHookError as error:
        queued = _queue(values, project_id, payload, str(error))
        return HookResult(queued=queued, error=None if queued else "本地 Outbox 不可用")
    except Exception as error:
        return HookResult(error=str(error))
    return HookResult()


def _task_payload(event_type: str, event: Mapping[str, Any], project_id: str, session_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    turn_id = _event_string(event, "turn_id", "turn_key", default="session")
    tool_call = _event_string(event, "tool_call_id", "call_id", default="")
    suffix = f":{tool_call}" if tool_call else ""
    payload = {
        "project_key": project_id,
        "session_key": session_id,
        "event_key": f"codex:{project_id}:{session_id}:{event_type}:{turn_id}{suffix}",
        "event_type": event_type,
        "metadata": redact_credentials(metadata),
    }
    return _fit_event_payload(payload)


def _tool_metadata(event: Mapping[str, Any], event_type: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    tool_name = _event_string(event, "tool_name", "tool", default="")
    if tool_name:
        metadata["tool_name"] = bounded_text(tool_name, 512)["value"]
    if event_type == "PostToolUse":
        command = _first(event, "command", "tool_input", "input")
        result = _first(event, "result", "tool_output", "output", "last_tool_result")
        metadata["command"] = _bounded_field(command, COMMAND_MAX_BYTES)
        metadata["result"] = _bounded_field(result, RESULT_MAX_BYTES)
        exit_code = _first(event, "exit_code", "returncode")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            metadata["exit_code"] = exit_code
    elif event_type == "PreToolUse":
        metadata["command"] = _bounded_field(_first(event, "command", "tool_input", "input"), COMMAND_MAX_BYTES)
    return metadata


def _bounded_field(value: Any, limit: int) -> dict[str, Any]:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(redact_credentials(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result = bounded_text(value if value is not None else "", limit)
    field: dict[str, Any] = {"value": result["value"]}
    if result["truncated"]:
        field.update({"length": result["length"], "sha256": result["sha256"], "truncated": True})
    return field


def _fit_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= EVENT_MAX_BYTES:
        return payload
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key, limit in (("change_manifest", 40 * 1024), ("git_snapshot", 12 * 1024)):
            value = metadata.get(key)
            if isinstance(value, dict):
                serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                metadata[key] = _bounded_field(serialized, limit)
        for key, limit in (("prompt", 48 * 1024), ("command", COMMAND_MAX_BYTES), ("result", RESULT_MAX_BYTES)):
            value = metadata.get(key)
            if isinstance(value, dict) and isinstance(value.get("value"), str):
                metadata[key] = _bounded_field(value["value"], limit)
        metadata["event_truncated"] = True
    while len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) > EVENT_MAX_BYTES and isinstance(metadata, dict):
        removable = next((key for key in ("git_snapshot", "result", "command", "prompt") if key in metadata), None)
        if removable is None:
            break
        metadata.pop(removable)
    return payload


def _load_baseline(values: Mapping[str, str], project_id: str, session_id: str) -> GitSnapshot | None:
    try:
        raw = HookStateStore(values.get("CODEX_MEMORY_STATE_DIR")).load(project_id, session_id)
    except HookStateError:
        return None
    baseline = raw.get("baseline") if isinstance(raw, dict) else None
    return _snapshot_from_dict(baseline) if isinstance(baseline, dict) else None


def _save_baseline(values: Mapping[str, str], project_id: str, session_id: str, cwd: str, snapshot: GitSnapshot) -> None:
    try:
        HookStateStore(values.get("CODEX_MEMORY_STATE_DIR")).save(
            project_id,
            session_id,
            {"project_id": project_id, "session_id": session_id, "cwd": cwd, "baseline": snapshot.to_dict()},
        )
    except HookStateError:
        pass


def _snapshot_from_dict(value: dict[str, Any]) -> GitSnapshot:
    untracked = value.get("untracked", [])
    return GitSnapshot(
        available=bool(value.get("available")),
        root=value.get("root") if isinstance(value.get("root"), str) else None,
        branch=value.get("branch") if isinstance(value.get("branch"), str) else None,
        head=value.get("head") if isinstance(value.get("head"), str) else None,
        porcelain=value.get("porcelain") if isinstance(value.get("porcelain"), str) else "",
        diff_hash=value.get("diff_hash") if isinstance(value.get("diff_hash"), str) else None,
        untracked=tuple(item for item in untracked if isinstance(item, dict)),
        error=value.get("error") if isinstance(value.get("error"), str) else None,
    )


def _queue(values: Mapping[str, str], project_id: str, payload: dict[str, Any], reason: str) -> bool:
    return _queue_outbox(_outbox_safe(values), project_id, payload, reason)


def _queue_outbox(outbox: LocalOutbox | None, project_id: str, payload: dict[str, Any], reason: str) -> bool:
    if outbox is None:
        return False
    try:
        outbox.enqueue(project_id, payload, reason)
    except Exception:
        return False
    return True


def _outbox_safe(env: Mapping[str, str]) -> LocalOutbox | None:
    try:
        return _outbox(env)
    except Exception:
        return None


def _environment(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _cwd(event: Mapping[str, Any]) -> str:
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ProjectConfigError("Hook 事件缺少有效 cwd")
    return cwd


def _event_string(event: Mapping[str, Any], *keys: str, default: str | None = None) -> str:
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    if default is not None:
        return default
    raise ValueError("Hook 事件缺少有效会话标识")


def _first(event: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in event:
            return event[key]
    return ""


def _normalize_event_type(value: str) -> str:
    aliases = {
        "user": "UserPromptSubmit",
        "userpromptsubmit": "UserPromptSubmit",
        "pre": "PreToolUse",
        "pretooluse": "PreToolUse",
        "post": "PostToolUse",
        "posttooluse": "PostToolUse",
        "stop": "Stop",
        "sessionend": "SessionEnd",
        "session_end": "SessionEnd",
    }
    return aliases.get(value.strip().lower(), value)


def _client(env: Mapping[str, str]) -> HookApiClient:
    token = env.get("CODEX_MEMORY_API_TOKEN") or env.get("CODEX_MEMORY_SERVICE_TOKEN")
    if not token:
        raise PermanentHookError("Codex Memory 认证令牌缺失")
    return HookApiClient(env.get("CODEX_MEMORY_API_URL", "http://127.0.0.1:8001"), token)


def _outbox(env: Mapping[str, str]) -> LocalOutbox:
    root = env.get("CODEX_MEMORY_OUTBOX_DIR")
    if root:
        return LocalOutbox(root)
    return LocalOutbox(Path.home() / ".codex" / "codex-memory-outbox")
