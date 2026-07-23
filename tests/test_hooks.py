from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _hook_module():
    path = Path(__file__).parents[1] / ".codex" / "scripts" / "hook_common.py"
    spec = importlib.util.spec_from_file_location("hook_common", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_user_event_posts_append_and_returns_context(monkeypatch, tmp_path: Path) -> None:
    hook = _hook_module()
    calls: list[tuple[str, dict]] = []

    def post(path: str, payload: dict, _env: dict) -> dict:
        calls.append((path, payload))
        if path.endswith("/context"):
            return {"critical_rules": [], "long_term_rules": [{"content": "use service"}], "recent_insights": [], "source_ids": [3]}
        return {"id": 1, "status": "stored"}

    monkeypatch.setattr(hook, "post_json", post)
    result = hook.handle_user_prompt(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "prompt": "change order"},
        {"CODEX_MEMORY_PROJECT_MAP": json.dumps({"G:/erp": "erp"}), "CODEX_MEMORY_API_URL": "http://memory", "CODEX_MEMORY_API_TOKEN": "secret", "CODEX_MEMORY_OUTBOX_PATH": str(tmp_path / "outbox.jsonl")},
    )

    assert calls[0][0].endswith("/append")
    assert calls[0][1]["event_key"] == "s1:t1:user"
    assert "use service" in result


def test_failed_stop_event_is_stored_without_token(monkeypatch, tmp_path: Path) -> None:
    hook = _hook_module()

    def fail(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(hook, "post_json", fail)
    outbox = tmp_path / "outbox.jsonl"
    hook.handle_stop(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "last_assistant_message": "done"},
        {"CODEX_MEMORY_PROJECT_MAP": json.dumps({"G:/erp": "erp"}), "CODEX_MEMORY_API_URL": "http://memory", "CODEX_MEMORY_API_TOKEN": "secret-token", "CODEX_MEMORY_OUTBOX_PATH": str(outbox)},
    )

    content = outbox.read_text(encoding="utf-8")
    assert "secret-token" not in content
    assert json.loads(content)["body"]["event_key"] == "s1:t1:assistant"


def test_stop_ignores_empty_assistant_message(tmp_path: Path) -> None:
    hook = _hook_module()
    outbox = tmp_path / "outbox.jsonl"

    hook.handle_stop(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "last_assistant_message": ""},
        {"CODEX_MEMORY_PROJECT_MAP": json.dumps({"G:/erp": "erp"}), "CODEX_MEMORY_API_URL": "http://memory", "CODEX_MEMORY_OUTBOX_PATH": str(outbox)},
    )

    assert not outbox.exists()
