from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codex_memory.codex_hooks import handle_event, replay_outbox
from codex_memory.git_manifest import build_change_manifest, collect_git_snapshot
from codex_memory.hook_events import EVENT_MAX_BYTES
from codex_memory.hook_client import RetryableHookError
from codex_memory.local_outbox import LocalOutbox


class TaskClient:
    def __init__(self, *, retry: bool = False) -> None:
        self.events: list[dict] = []
        self.retry = retry

    def append(self, payload: dict) -> dict:
        return {"status": "accepted"}

    def context(self, _project: str, _task: str) -> dict:
        return {"long_term_rules": []}

    def task_event(self, payload: dict) -> dict:
        if self.retry:
            raise RetryableHookError("服务超时")
        self.events.append(payload)
        return {"status": "accepted"}


def _enable(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\n"
        "CODEX_MEMORY_PROJECT_ID=demo\n"
        "CODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _event(root: Path, **values: object) -> dict:
    return {"cwd": str(root), "session_id": "s1", "turn_id": "t1", **values}


def test_five_hook_events_record_git_baseline_and_deterministic_manifest(tmp_path: Path) -> None:
    _enable(tmp_path)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "测试")
    (tmp_path / "tracked.txt").write_text("初始\n", encoding="utf-8")
    _git(tmp_path, "add", "AGENTS.md", "tracked.txt")
    _git(tmp_path, "commit", "-m", "初始")
    client = TaskClient()
    env = {"CODEX_MEMORY_STATE_DIR": str(tmp_path.parent / f"{tmp_path.name}-state"), "CODEX_MEMORY_OUTBOX_DIR": str(tmp_path / "outbox")}

    assert handle_event("UserPromptSubmit", _event(tmp_path, prompt="开始"), env, client=client) == ""
    assert not handle_event("PreToolUse", _event(tmp_path, tool_name="shell", command="git status"), env, client=client).error
    (tmp_path / "tracked.txt").write_text("修改\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("新增\n", encoding="utf-8")
    assert not handle_event("PostToolUse", _event(tmp_path, tool_name="shell", command="echo token=secret", result="完成", exit_code=0), env, client=client).error
    assert not handle_event("Stop", _event(tmp_path, last_assistant_message="完成"), env, client=client).error
    assert not handle_event("SessionEnd", _event(tmp_path), env, client=client).error

    assert [event["event_type"] for event in client.events] == ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]
    manifest = next(event for event in client.events if event["event_type"] == "Stop")["metadata"]["change_manifest"]
    assert [(item["path"], item["change"]) for item in manifest["files"]] == [("new.txt", "untracked"), ("tracked.txt", "modified")]
    assert manifest["uncertain"] is False
    post = next(event for event in client.events if event["event_type"] == "PostToolUse")
    assert "secret" not in json.dumps(post, ensure_ascii=False)


def test_dirty_baseline_is_uncertain_and_non_git_is_available_as_limited_state(tmp_path: Path) -> None:
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    _enable(dirty)
    _git(dirty, "init")
    _git(dirty, "config", "user.email", "test@example.com")
    _git(dirty, "config", "user.name", "测试")
    (dirty / "tracked.txt").write_text("初始\n", encoding="utf-8")
    _git(dirty, "add", "AGENTS.md", "tracked.txt")
    _git(dirty, "commit", "-m", "初始")
    (dirty / "tracked.txt").write_text("基线已有修改\n", encoding="utf-8")
    baseline = collect_git_snapshot(dirty)
    (dirty / "tracked.txt").write_text("后续修改\n", encoding="utf-8")
    manifest = build_change_manifest(baseline, collect_git_snapshot(dirty))
    assert manifest["uncertain"] is True
    assert manifest["files"] == []

    plain = tmp_path / "plain"
    plain.mkdir()
    _enable(plain)
    client = TaskClient()
    result = handle_event("SessionEnd", _event(plain), {"CODEX_MEMORY_STATE_DIR": str(tmp_path / "state")}, client=client)
    assert result.error is None
    assert client.events[0]["metadata"]["change_manifest"]["uncertain"] is True
    assert client.events[0]["metadata"]["change_manifest"]["baseline"]["available"] is False
    assert client.events[0]["metadata"]["change_manifest"]["current"]["available"] is False


def test_change_manifest_covers_added_deleted_renamed_and_restored(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    root.mkdir()
    _enable(root)
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "测试")
    (root / "modified.txt").write_text("初始\n", encoding="utf-8")
    (root / "deleted.txt").write_text("删除\n", encoding="utf-8")
    (root / "rename-old.txt").write_text("重命名\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "初始")
    baseline = collect_git_snapshot(root)

    (root / "modified.txt").write_text("修改\n", encoding="utf-8")
    (root / "deleted.txt").unlink()
    _git(root, "mv", "rename-old.txt", "rename-new.txt")
    (root / "added.txt").write_text("新增\n", encoding="utf-8")
    _git(root, "add", "added.txt")
    (root / "untracked.txt").write_text("未跟踪\n", encoding="utf-8")
    manifest = build_change_manifest(baseline, collect_git_snapshot(root))
    changes = {(item["path"], item["change"]) for item in manifest["files"]}
    assert changes == {
        ("added.txt", "added"),
        ("deleted.txt", "deleted"),
        ("modified.txt", "modified"),
        ("rename-new.txt", "renamed"),
        ("untracked.txt", "untracked"),
    }
    renamed = next(item for item in manifest["files"] if item["change"] == "renamed")
    assert renamed["old_path"] == "rename-old.txt"

    (root / "modified.txt").write_text("初始\n", encoding="utf-8")
    (root / "deleted.txt").write_text("删除\n", encoding="utf-8")
    _git(root, "mv", "rename-new.txt", "rename-old.txt")
    (root / "added.txt").unlink()
    _git(root, "reset", "--", "added.txt")
    (root / "untracked.txt").unlink()
    restored = build_change_manifest(baseline, collect_git_snapshot(root))
    assert restored["uncertain"] is False
    assert restored["files"] == []


def test_event_limit_and_outbox_lock_failure_fail_open(tmp_path: Path) -> None:
    _enable(tmp_path)
    client = TaskClient()
    huge = "令牌 token=secret " + "中" * (EVENT_MAX_BYTES * 2)
    result = handle_event(
        "PostToolUse",
        _event(tmp_path, tool_name="shell", command=huge, result=huge, exit_code=1),
        {"CODEX_MEMORY_STATE_DIR": str(tmp_path.parent / f"{tmp_path.name}-state"), "CODEX_MEMORY_OUTBOX_DIR": str(tmp_path / "outbox")},
        client=client,
    )
    assert result.error is None
    payload = client.events[0]
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= EVENT_MAX_BYTES
    assert "secret" not in json.dumps(payload, ensure_ascii=False)

    bad_outbox = tmp_path / "outbox-file"
    bad_outbox.write_text("不可写目录", encoding="utf-8")
    result = handle_event(
        "PostToolUse",
        _event(tmp_path, command="命令", result="结果"),
        {"CODEX_MEMORY_OUTBOX_DIR": str(bad_outbox)},
        client=TaskClient(retry=True),
    )
    assert result.error == "本地 Outbox 不可用"


def test_outbox_replays_legacy_append_and_task_event_records_to_matching_endpoints(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path / "outbox")
    outbox.enqueue("demo", {"event_key": "legacy", "content": "旧事件"}, "离线")
    outbox.enqueue("demo", {"event_key": "task", "event_type": "Stop", "metadata": {}}, "离线")
    client = TaskClient()
    report = replay_outbox({"CODEX_MEMORY_OUTBOX_DIR": str(tmp_path / "outbox")}, client=client)
    assert report.delivered == 2
    assert [event["event_key"] for event in client.events] == ["task"]
