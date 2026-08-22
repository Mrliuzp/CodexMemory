"""V1.7 ChangeSet 输出的 fail-closed 契约。"""

from __future__ import annotations

import hashlib

import pytest


class _CountingRunner:
    def __init__(self, result):
        self.calls = 0
        self.result = result

    def run(self, request):
        self.calls += 1
        return self.result


def _context(content: str = "采用 OrderService") -> dict:
    return {"messages": [{"message_id": 1, "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest()}], "published_l1": []}


def _output(**overrides):
    value = {"operation": "create", "title": "规则", "content": {"text": "采用 OrderService"}, "confidence": 0.9, "reason": "证据支持", "evidence_ranges": [{"message_id": 1, "start_char": 0, "end_char": 15, "quote": "采用 OrderService"}], "retrieved_memory_ids": [], "risk_flags": [], "level": "L1", "scope": "project"}
    value.update(overrides)
    from codex_memory.v17_models import MemoryChangeSetOutput
    return MemoryChangeSetOutput.model_validate(value)


def test_evidence_must_match_exact_l0_range_and_content() -> None:
    from codex_memory.v17_changes import MemoryChangeSetService

    with pytest.raises(ValueError, match="证据"):
        MemoryChangeSetService._validate_output(_context("不同原文"), _output())


def test_create_duplicate_l1_fails_closed() -> None:
    from codex_memory.v17_changes import MemoryChangeSetService

    context = _context()
    context["published_l1"] = [{"memory_id": 20, "revision": 1, "content": {"text": "采用 OrderService"}}]
    with pytest.raises(ValueError, match="重复"):
        MemoryChangeSetService._validate_output(context, _output())


def test_update_target_must_be_retrieved_and_revision_match() -> None:
    from codex_memory.v17_changes import MemoryChangeSetService

    with pytest.raises(ValueError, match="检索目标"):
        MemoryChangeSetService._validate_output(_context(), _output(operation="update", target_memory_id=20, target_revision=1))


def test_generate_rejects_drift_before_no_change_runner_call() -> None:
    from codex_memory.persistence.db_models import MessageRow
    from codex_memory.v17_changes import MemoryChangeSetService
    from codex_memory.v17_windows import MemoryWindowService
    from test_v17_windows import _window_db

    factory = _window_db()
    MemoryWindowService(factory).seal(project_id=1, session_id=1)
    with factory() as session:
        message = session.get(MessageRow, 1)
        message.content = "改写后的内容"
        # 即使攻击者同步伪造 MessageRow.content_hash，窗口证据哈希仍必须拒绝。
        message.content_hash = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
        session.commit()
    runner = _CountingRunner(None)
    with pytest.raises(ValueError, match="生成或校验失败"):
        MemoryChangeSetService(factory, runner=runner).generate(1)
    assert runner.calls == 0


def test_generate_safely_rejects_processing_without_second_runner_call() -> None:
    from codex_memory.persistence.v17_models import MemoryWindowRow
    from codex_memory.v17_changes import MemoryChangeSetService
    from codex_memory.v17_windows import MemoryWindowService
    from test_v17_windows import _window_db

    factory = _window_db()
    MemoryWindowService(factory).seal(project_id=1, session_id=1)
    with factory() as session:
        window = session.get(MemoryWindowRow, 1)
        window.status = "processing"
        session.commit()
    runner = _CountingRunner(None)
    with pytest.raises(RuntimeError, match="正在生成"):
        MemoryChangeSetService(factory, runner=runner).generate(1)
    assert runner.calls == 0
