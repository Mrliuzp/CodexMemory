"""V1.7 Memory Window 的关键安全契约。"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _window_db(*, content: str = "原始内容", status: str = "open"):
    from codex_memory.persistence.db_models import Base, MessageRow, ProjectRow, SessionRow
    from codex_memory.persistence.v17_models import (
        V17Base,
        ProjectMemoryWindowPolicyRow,
        MemoryWindowMessageRow,
        MemoryWindowRow,
    )

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    V17Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with factory() as session:
        session.add_all([
            ProjectRow(id=1, project_key="demo", name="演示项目"),
            SessionRow(id=1, project_id=1, session_key="s-1"),
            MessageRow(id=1, project_id=1, session_id=1, event_key="m-1", role="user", content=content, content_hash=content_hash),
            ProjectMemoryWindowPolicyRow(project_id=1, enabled=True),
            MemoryWindowRow(id=1, project_id=1, session_id=1, session_key="s-1", status="open"),
            MemoryWindowMessageRow(id=1, window_id=1, message_id=1, position=0, content_hash=content_hash),
        ])
        session.commit()
    if status != "open":
        from codex_memory.v17_windows import MemoryWindowService
        MemoryWindowService(factory).seal(project_id=1, session_id=1)
        if status != "sealed":
            with factory() as session:
                window = session.get(MemoryWindowRow, 1)
                window.status = status
                session.commit()
    return factory


def test_window_input_hash_is_recomputed_from_immutable_l0() -> None:
    from codex_memory.v17_changes import MemoryChangeSetService

    context = {
        "messages": [{
            "message_id": 1,
            "content": "采用 OrderService",
            "content_hash": hashlib.sha256("采用 OrderService".encode()).hexdigest(),
        }],
        "published_l1": [],
    }
    output = {
        "operation": "create",
        "title": "订单服务规则",
        "content": {"text": "采用 OrderService"},
        "confidence": 0.9,
        "reason": "窗口证据支持",
        "evidence_ranges": [{"message_id": 1, "start_char": 0, "end_char": 15, "quote": "采用 OrderService"}],
        "retrieved_memory_ids": [],
        "risk_flags": [],
        "level": "L1",
        "scope": "project",
    }
    from codex_memory.v17_models import MemoryChangeSetOutput

    MemoryChangeSetService._validate_output(context, MemoryChangeSetOutput.model_validate(output))


def test_seal_and_generate_is_public_single_entrypoint() -> None:
    import inspect
    from codex_memory.v17_changes import MemoryChangeSetService

    assert "seal_and_generate" in dir(MemoryChangeSetService)
    assert "generate" in inspect.getsource(MemoryChangeSetService.seal_and_generate)


def test_window_service_rejects_drifted_hash() -> None:
    from codex_memory.v17_changes import MemoryChangeSetService
    from codex_memory.v17_models import MemoryChangeSetOutput

    output = MemoryChangeSetOutput.model_validate({
        "operation": "create", "title": "规则", "content": {"text": "原文"}, "confidence": 0.8,
        "reason": "证据", "evidence_ranges": [{"message_id": 1, "start_char": 0, "end_char": 2, "quote": "原文"}],
        "retrieved_memory_ids": [], "risk_flags": [], "level": "L1", "scope": "project",
    })
    with pytest.raises(ValueError, match="哈希"):
        MemoryChangeSetService._validate_output({"messages": [{"message_id": 1, "content": "原文", "content_hash": "0" * 64}], "published_l1": []}, output)


def test_seal_rechecks_message_content_hash_and_window_hash() -> None:
    from codex_memory.persistence.db_models import MessageRow
    from codex_memory.v17_windows import MemoryWindowService

    factory = _window_db()
    service = MemoryWindowService(factory)
    service.seal(project_id=1, session_id=1)
    with factory() as session:
        message = session.get(MessageRow, 1)
        message.content = "篡改内容"
        session.commit()
    with pytest.raises(ValueError, match="哈希"):
        service.seal(project_id=1, session_id=1)


def test_build_input_rejects_content_with_forged_synchronized_message_hash() -> None:
    from codex_memory.persistence.db_models import MessageRow
    from codex_memory.v17_windows import MemoryWindowService

    factory = _window_db()
    service = MemoryWindowService(factory)
    service.seal(project_id=1, session_id=1)
    with factory() as session:
        message = session.get(MessageRow, 1)
        message.content = "伪造后内容"
        message.content_hash = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
        session.commit()
    with pytest.raises(ValueError, match="哈希"):
        service.build_input(1)
