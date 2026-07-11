from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


def _normalise_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").lower()


def project_key(event: dict[str, Any], env: dict[str, str]) -> str:
    mapping = json.loads(env["CODEX_MEMORY_PROJECT_MAP"])
    cwd = _normalise_path(str(event["cwd"]))
    for root, key in mapping.items():
        if cwd == _normalise_path(root) or cwd.startswith(_normalise_path(root) + "/"):
            return str(key)
    raise ValueError(f"no project mapping for cwd: {event['cwd']}")


def _base_url(env: dict[str, str]) -> str:
    return env["CODEX_MEMORY_API_URL"].rstrip("/")


def post_json(path: str, payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        path,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {env['CODEX_MEMORY_API_TOKEN']}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _outbox_path(env: dict[str, str]) -> Path:
    return Path(env.get("CODEX_MEMORY_OUTBOX_PATH", Path(tempfile.gettempdir()) / "codex-memory-outbox.jsonl"))


def _append_outbox(record: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _replay_outbox(env: dict[str, str]) -> None:
    path = _outbox_path(env)
    if not path.exists():
        return
    remaining: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        try:
            post_json(record["path"], record["body"], env)
        except OSError:
            remaining.append(record)
    replacement = path.with_suffix(".tmp")
    replacement.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in remaining), encoding="utf-8")
    replacement.replace(path)


def _send_or_queue(path: str, body: dict[str, Any], env: dict[str, str]) -> dict[str, Any] | None:
    try:
        _replay_outbox(env)
        return post_json(path, body, env)
    except OSError:
        _append_outbox({"path": path, "body": body}, _outbox_path(env))
        return None


def _format_context(context: dict[str, Any]) -> str:
    lines: list[str] = []
    for section in ("critical_rules", "long_term_rules", "recent_insights"):
        for item in context.get(section, []):
            lines.append(str(item.get("content", item)))
    return "\n".join(lines)


def handle_user_prompt(event: dict[str, Any], env: dict[str, str] | None = None) -> str:
    values = dict(os.environ if env is None else env)
    project = project_key(event, values)
    body = {
        "project_key": project,
        "session_key": event["session_id"],
        "event_key": f"{event['session_id']}:{event['turn_id']}:user",
        "role": "user",
        "content": event["prompt"],
        "source": "hook",
    }
    _send_or_queue(f"{_base_url(values)}/api/v1/append", body, values)
    context = _send_or_queue(f"{_base_url(values)}/api/v1/context", {"project_key": project, "task": event["prompt"]}, values)
    return _format_context(context or {})


def handle_stop(event: dict[str, Any], env: dict[str, str] | None = None) -> None:
    content = event.get("last_assistant_message")
    if not content:
        return
    values = dict(os.environ if env is None else env)
    project = project_key(event, values)
    _send_or_queue(
        f"{_base_url(values)}/api/v1/append",
        {
            "project_key": project,
            "session_key": event["session_id"],
            "event_key": f"{event['session_id']}:{event['turn_id']}:assistant",
            "role": "assistant",
            "content": content,
            "source": "hook",
        },
        values,
    )
