from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from codex_memory.local_outbox import LocalOutbox


def test_outbox_append_preserves_concurrent_records(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: outbox.enqueue(
                    "erp",
                    {"event_key": str(index), "content": str(index), "token": "never-save"},
                    reason="offline",
                ),
                range(40),
            )
        )

    path = tmp_path / "erp" / "pending.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert sorted(int(record["event_key"]) for record in records) == list(range(40))
