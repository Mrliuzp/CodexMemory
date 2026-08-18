"""V1.6 决策契约、项目隔离、默认关闭和审核审计测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]


def _factory_with_messages() -> tuple[object, int, int, int, int]:
    from codex_memory.db import create_postgres_test_engine, create_schema, create_session_factory
    from codex_memory.db_models import Base, MessageRow, ProjectRow, ProjectFeatureFlagRow, SessionRow, V11Base, V16Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    V16Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        first = ProjectRow(project_key="decision-a", name="决策项目 A")
        second = ProjectRow(project_key="decision-b", name="决策项目 B")
        session.add_all([first, second])
        session.flush()
        first_session = SessionRow(project_id=first.id, session_key="s-a")
        second_session = SessionRow(project_id=second.id, session_key="s-b")
        session.add_all([first_session, second_session])
        session.flush()
        first_text = "Use OrderService for order updates."
        second_text = "Only project B may use this evidence."
        first_message = MessageRow(
            project_id=first.id,
            session_id=first_session.id,
            event_key="s-a:user-1",
            role="user",
            content=first_text,
            content_hash=hashlib.sha256(first_text.encode()).hexdigest(),
        )
        second_message = MessageRow(
            project_id=second.id,
            session_id=second_session.id,
            event_key="s-b:user-1",
            role="user",
            content=second_text,
            content_hash=hashlib.sha256(second_text.encode()).hexdigest(),
        )
        session.add_all(
            [
                first_message,
                second_message,
                ProjectFeatureFlagRow(project_id=first.id),
            ]
        )
        session.commit()
        return factory, first.id, second.id, first_message.id, second_message.id


def _payload(message_id: int, *, quote: str = "OrderService", level: str = "L1", scope: str = "project", decision: str = "publish") -> dict[str, object]:
    start = 4 if quote == "OrderService" else 0
    return {
        "decision": decision,
        "confidence": 0.95,
        "title": "订单更新规则",
        "content": {"text": "Use OrderService for order updates."},
        "reason": "证据明确且内容可追溯",
        "evidence_ranges": [
            {"message_id": message_id, "start_char": start, "end_char": start + len(quote), "quote": quote}
        ],
        "risk_flags": [],
        "level": level,
        "scope": scope,
    }


def test_model_output_schema_is_strict_and_rejects_invalid_fields() -> None:
    from codex_memory.decision_models import ModelDecisionOutput, decision_json_schema

    schema = decision_json_schema()
    assert schema["additionalProperties"] is False
    assert {
        "decision",
        "confidence",
        "title",
        "content",
        "reason",
        "evidence_ranges",
        "risk_flags",
        "level",
        "scope",
    } <= set(schema["required"])
    valid = _payload(1)
    with pytest.raises(ValidationError):
        ModelDecisionOutput.model_validate({**valid, "unexpected": True})
    with pytest.raises(ValidationError):
        ModelDecisionOutput.model_validate({**valid, "confidence": 1.1})
    with pytest.raises(ValidationError):
        ModelDecisionOutput.model_validate({**valid, "risk_flags": ["not-a-risk-flag"]})
    with pytest.raises(ValidationError):
        ModelDecisionOutput.model_validate(
            {**valid, "evidence_ranges": [{"message_id": 1, "start_char": 4, "end_char": 4, "quote": ""}]}
        )


def test_default_policy_and_feature_flag_keep_auto_publish_closed() -> None:
    from codex_memory.decision_service import DecisionService
    from codex_memory.db_models import ProjectFeatureFlagRow

    factory, project_id, _, message_id, _ = _factory_with_messages()
    service = DecisionService(factory)
    policy = service.get_policy(project_id)
    assert policy.enabled is False
    assert policy.auto_publish_enabled is False
    assert policy.allowed_level == "L1"
    assert policy.allowed_scope == "project"
    with pytest.raises(ValueError, match="L1"):
        service.update_policy(project_id, allowed_level="L2")
    with pytest.raises(ValueError, match="project"):
        service.update_policy(project_id, allowed_scope="global")

    run = service.start_run(
        project_id=project_id,
        model="test-model",
        prompt_version="prompt-v1",
        input_hash="a" * 64,
    )
    decision = service.record_decision(run.id, _payload(message_id))
    assert decision.auto_publish_allowed is False
    assert service.can_auto_publish(decision.id, project_id=project_id) is False
    with factory() as session:
        flags = session.get(ProjectFeatureFlagRow, project_id)
        assert flags is not None and flags.decision_engine_enabled is False


def test_cross_project_evidence_is_rejected_and_audited() -> None:
    from codex_memory.decision_service import DecisionService, DecisionValidationError
    from codex_memory.db_models import AuditLogRow, ModelDecisionRow, DecisionRunRow

    factory, project_id, _, _, other_message_id = _factory_with_messages()
    service = DecisionService(factory)
    run = service.start_run(
        project_id=project_id,
        model="test-model",
        prompt_version="prompt-v1",
        input_hash="b" * 64,
    )
    with pytest.raises(DecisionValidationError, match="项目与证据"):
        service.record_decision(run.id, _payload(other_message_id, quote="Only project B may use this evidence.", decision="needs_review"))

    with factory() as session:
        assert session.scalar(select(ModelDecisionRow).where(ModelDecisionRow.decision_run_id == run.id)) is None
        stored_run = session.get(DecisionRunRow, run.id)
        assert stored_run is not None and stored_run.status == "failed"
        audit = session.scalar(
            select(AuditLogRow)
            .where(AuditLogRow.project_id == project_id, AuditLogRow.event_type == "model_decision_rejected")
            .order_by(AuditLogRow.id.desc())
        )
        assert audit is not None


def test_l2_global_and_l3_are_recordable_but_never_auto_publishable() -> None:
    from codex_memory.decision_service import DecisionService

    factory, project_id, _, message_id, _ = _factory_with_messages()
    service = DecisionService(factory)
    service.update_policy(project_id, enabled=True, auto_publish_enabled=True, strategy="auto_publish")
    with factory() as session:
        from codex_memory.db_models import ProjectFeatureFlagRow

        flags = session.get(ProjectFeatureFlagRow, project_id)
        assert flags is not None
        flags.decision_engine_enabled = True
        flags.candidate_publish_enabled = True
        session.commit()

    for level, scope in (("L2", "global"), ("L3", "project")):
        run = service.start_run(
            project_id=project_id,
            model="test-model",
            prompt_version="prompt-v1",
            input_hash=("c" if level == "L2" else "d") * 64,
        )
        decision = service.record_decision(run.id, _payload(message_id, level=level, scope=scope))
        assert decision.level == level
        assert decision.scope == scope
        assert decision.auto_publish_allowed is False
        assert service.can_auto_publish(decision.id) is False


def test_manual_correction_is_immutable_and_audited() -> None:
    from codex_memory.decision_service import DecisionService
    from codex_memory.db_models import DecisionReviewActionRow, ModelDecisionRow, AuditLogRow

    factory, project_id, _, message_id, _ = _factory_with_messages()
    service = DecisionService(factory)
    run = service.start_run(
        project_id=project_id,
        model="test-model",
        prompt_version="prompt-v1",
        input_hash="e" * 64,
    )
    decision = service.record_decision(run.id, _payload(message_id, decision="needs_review"))
    action = service.review_decision(
        decision.id,
        project_id=project_id,
        reviewer="reviewer-a",
        action="correct",
        reason="标题需要更准确",
        correction=_payload(message_id, decision="publish"),
    )
    assert action.action == "correct"
    with factory() as session:
        stored = session.get(ModelDecisionRow, decision.id)
        review = session.get(DecisionReviewActionRow, action.id)
        assert stored is not None and stored.title == "订单更新规则"
        assert stored.review_status == "corrected"
        assert review is not None and review.correction_json is not None
        audit = session.scalar(
            select(AuditLogRow)
            .where(AuditLogRow.project_id == project_id, AuditLogRow.event_type == "decision_reviewed")
            .order_by(AuditLogRow.id.desc())
        )
        assert audit is not None


def test_finish_run_persists_retry_error_and_cost_fields() -> None:
    from codex_memory.decision_models import DecisionErrorClass, DecisionRunStatus
    from codex_memory.decision_service import DecisionService

    factory, project_id, _, _, _ = _factory_with_messages()
    service = DecisionService(factory)
    run = service.start_run(
        project_id=project_id,
        model="test-model",
        prompt_version="prompt-v1",
        input_hash="f" * 64,
        max_retries=5,
    )
    finished = service.finish_run(
        run.id,
        status=DecisionRunStatus.FAILED,
        retry_count=2,
        error_class=DecisionErrorClass.TIMEOUT,
        error_code="provider_timeout",
        error_message="模型服务超时",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cost_micros=321,
    )
    assert finished.status == "failed"
    assert finished.retry_count == 2
    assert finished.error_class == "timeout"
    assert finished.total_tokens == 120
    assert finished.cost_micros == 321


def test_v16_migration_follows_flags_initialization() -> None:
    flags_migration = (ROOT / "alembic" / "versions" / "0026_initialize_project_feature_flags.py").read_text(encoding="utf-8")
    migration = (ROOT / "alembic" / "versions" / "0027_v16_decision_contracts.py").read_text(encoding="utf-8")
    assert 'revision = "0026_init_project_flags"' in flags_migration
    assert 'down_revision = "0025_expand_audit_subject_id"' in flags_migration
    assert 'revision = "0027_v16_decision_contracts"' in migration
    assert 'down_revision = "0026_init_project_flags"' in migration
    assert '"decision_runs"' in migration
    assert '"model_decisions"' in migration
    assert '"decision_review_actions"' in migration
