from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def test_outbox_append_preserves_concurrent_records(tmp_path: Path) -> None:
    path = Path(__file__).parents[1] / ".codex" / "scripts" / "hook_common.py"
    spec = importlib.util.spec_from_file_location("hook_common_concurrency", path)
    assert spec and spec.loader
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    outbox = tmp_path / "outbox.jsonl"

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: hook._append_outbox({"index": index}, outbox), range(40)))

    records = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    assert sorted(record["index"] for record in records) == list(range(40))
