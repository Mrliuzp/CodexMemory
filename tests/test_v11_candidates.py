from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select


def _factory_with_message():
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import (
        MessageRow,
        ProjectFeatureFlagRow,
        ProjectRow,
        SessionRow,
        V11Base,
    )

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        conversation = SessionRow(project_id=project.id, session_key="s1")
        session.add(conversation)
        session.flush()
        message = MessageRow(
            project_id=project.id,
            session_id=conversation.id,
            event_key="s1:t1:user",
            role="user",
            content="Use OrderService for order updates.",
            content_hash=hashlib.sha256(b"Use OrderService for order updates.").hexdigest(),
        )
        session.add(message)
        session.add(ProjectFeatureFlagRow(project_id=project.id, candidate_publish_enabled=False))
        session.commit()
        return factory, project.id, message.id


def test_candidate_requires_verified_evidence_before_publish() -> None:
    from codex_memory.db_models import MemoryCandidateRow, ProjectFeatureFlagRow
    from codex_memory.v11_candidates import CandidatePolicyService

    factory, project_id, message_id = _factory_with_message()
    service = CandidatePolicyService(factory)
    candidate = service.create_candidate(
        project_id=project_id,
        source_message_id=message_id,
        task_type="rule",
        level="L1",
        scope="project",
        memory_type="solution",
        title="Order update rule",
        content={"text": "Use OrderService for order updates."},
        evidence=[(message_id, 4, 16)],
    )
    assert candidate.status == "generated"
    assert service.evaluate(candidate.id).decision == "reject"

    with factory() as session:
        flags = session.get(ProjectFeatureFlagRow, project_id)
        flags.candidate_publish_enabled = True
        session.commit()

    published = service.publish(candidate.id)
    assert published.status == "published"
    with factory() as session:
        row = session.get(MemoryCandidateRow, candidate.id)
        assert row.published_memory_id == published.id


def test_tampered_evidence_cannot_publish() -> None:
    from codex_memory.v11_candidates import CandidatePolicyService

    factory, project_id, message_id = _factory_with_message()
    service = CandidatePolicyService(factory)
    candidate = service.create_candidate(
        project_id=project_id,
        source_message_id=message_id,
        task_type="rule",
        level="L1",
        scope="project",
        memory_type="solution",
        title="Bad evidence",
        content={"text": "Use another service."},
        evidence=[(message_id, 4, 16)],
    )
    with pytest.raises(ValueError, match="evidence"):
        service.publish(candidate.id)


def test_candidate_cannot_use_invalid_scope() -> None:
    from codex_memory.v11_candidates import CandidatePolicyService

    factory, project_id, message_id = _factory_with_message()
    with pytest.raises(ValueError, match="scope"):
        CandidatePolicyService(factory).create_candidate(
            project_id=project_id,
            source_message_id=message_id,
            task_type="rule",
            level="L1",
            scope="user",
            memory_type="solution",
            title="Invalid scope",
            content={"text": "rule"},
            evidence=[(message_id, 0, 4)],
        )