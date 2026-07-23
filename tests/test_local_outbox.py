from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from codex_memory.hook_client import PermanentHookError, RetryableHookError
from codex_memory.local_outbox import LocalOutbox, retry_delay_seconds


def _payload(event_key: str = "event-1") -> dict[str, str]:
    return {
        "project_key": "erp",
        "event_key": event_key,
        "role": "user",
        "content": "\\u4fee\\u6539\\u8ba2\\u5355",
        "access_token": "secret-token",
    }


def test_enqueue_never_persists_token(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)

    outbox.enqueue("erp", _payload(), reason="offline secret-token")

    text = (tmp_path / "erp" / "pending.jsonl").read_text(encoding="utf-8")
    assert "secret-token" not in text
    assert '"project_id": "erp"' in text
    assert '"access_token"' not in text


def test_replay_keeps_retryable_and_dead_letters_permanent(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)
    outbox.enqueue("erp", _payload("retry"), reason="offline")
    outbox.enqueue("erp", _payload("denied"), reason="offline")

    def send(payload: dict) -> dict:
        if payload["event_key"] == "retry":
            raise RetryableHookError("offline")
        raise PermanentHookError("HTTP 403")

    report = outbox.replay(send)

    assert report.delivered == 0
    assert report.remaining == 1
    assert report.dead_lettered == 1
    assert _records(tmp_path / "erp" / "pending.jsonl")[0]["event_key"] == "retry"
    assert _records(tmp_path / "erp" / "dead-letter.jsonl")[0]["event_key"] == "denied"


def test_replay_removes_accepted_and_duplicate_events(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)
    outbox.enqueue("erp", _payload("accepted"), reason="offline")
    outbox.enqueue("erp", _payload("duplicate"), reason="offline")

    report = outbox.replay(
        lambda payload: {"status": "duplicate" if payload["event_key"] == "duplicate" else "accepted"}
    )

    assert report.delivered == 2
    assert report.remaining == 0
    assert not (tmp_path / "erp" / "pending.jsonl").exists()


def test_replay_defers_record_until_next_attempt(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)
    outbox.enqueue("erp", _payload(), reason="offline")
    first = outbox.replay(lambda _payload: (_ for _ in ()).throw(RetryableHookError("offline")))
    assert first.remaining == 1

    record = _records(tmp_path / "erp" / "pending.jsonl")[0]
    next_attempt = datetime.fromisoformat(record["next_attempt_at"])
    report = outbox.replay(lambda _payload: {"status": "accepted"}, now=next_attempt - timedelta(seconds=1))

    assert report.delivered == 0
    assert report.remaining == 1


def test_retry_delay_is_bounded() -> None:
    assert retry_delay_seconds(0) == 1
    assert retry_delay_seconds(2) == 4
    assert retry_delay_seconds(20) == 2048


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
