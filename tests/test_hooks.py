from __future__ import annotations

from pathlib import Path

from codex_memory.codex_hooks import handle_assistant_stop, handle_user_prompt
from codex_memory.hook_client import PermanentHookError, RetryableHookError


class FakeClient:
    def __init__(self, *, append_error: Exception | None = None, context: dict | None = None) -> None:
        self.append_error = append_error
        self.context_result = context or {"long_term_rules": []}
        self.calls: list[tuple[str, object]] = []

    def append(self, payload: dict) -> dict:
        self.calls.append(("append", payload))
        if self.append_error:
            raise self.append_error
        return {"status": "accepted"}

    def context(self, project_id: str, task: str) -> dict:
        self.calls.append(("context", (project_id, task)))
        return self.context_result


def _event(cwd: Path) -> dict[str, str]:
    return {"cwd": str(cwd), "session_id": "s1", "turn_id": "t1", "prompt": "change order"}


def _stop_event(cwd: Path) -> dict[str, str]:
    return {"cwd": str(cwd), "session_id": "s1", "turn_id": "t1", "last_assistant_message": "done"}


def _env(outbox: Path) -> dict[str, str]:
    return {"CODEX_MEMORY_OUTBOX_DIR": str(outbox), "CODEX_MEMORY_API_URL": "http://memory", "CODEX_MEMORY_API_TOKEN": "token"}


def _enable(cwd: Path, project_id: str) -> None:
    (cwd / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        f"CODEX_MEMORY_PROJECT_ID={project_id}\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )


def test_disabled_project_does_not_call_api(tmp_path: Path) -> None:
    client = FakeClient()

    result = handle_user_prompt(_event(tmp_path), _env(tmp_path / "outbox"), client=client)

    assert result == ""
    assert client.calls == []


def test_enabled_user_event_appends_then_returns_context(tmp_path: Path) -> None:
    _enable(tmp_path, "erp")
    client = FakeClient(context={"long_term_rules": [{"content": "use domain service"}]})

    result = handle_user_prompt(_event(tmp_path), _env(tmp_path / "outbox"), client=client)

    assert [call[0] for call in client.calls] == ["append", "context"]
    assert "use domain service" in result


def test_retryable_assistant_failure_is_queued(tmp_path: Path) -> None:
    _enable(tmp_path, "erp")
    client = FakeClient(append_error=RetryableHookError("offline"))

    result = handle_assistant_stop(_stop_event(tmp_path), _env(tmp_path / "outbox"), client=client)

    assert result.queued is True
    assert list((tmp_path / "outbox" / "erp").glob("pending.jsonl"))


def test_permanent_failure_is_not_queued(tmp_path: Path) -> None:
    _enable(tmp_path, "erp")
    client = FakeClient(append_error=PermanentHookError("HTTP 403"))

    result = handle_assistant_stop(_stop_event(tmp_path), _env(tmp_path / "outbox"), client=client)

    assert result.queued is False
    assert result.error is not None
    assert not list((tmp_path / "outbox").rglob("pending.jsonl"))
