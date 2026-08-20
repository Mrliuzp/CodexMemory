from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select


class FakeCodexProcess:
    """只返回结构化 JSON，不启动真实 Codex 进程。"""

    def __init__(self, *, decision: str = "publish", confidence: float = 0.95, error: Exception | None = None, raw_output: bytes | None = None) -> None:
        self.decision = decision
        self.confidence = confidence
        self.error = error
        self.raw_output = raw_output
        self.invocations = []

    def run(self, invocation):
        from codex_memory.codex_cli_runner import ProcessResult

        self.invocations.append(invocation)
        if self.error is not None:
            raise self.error
        if self.raw_output is not None:
            return ProcessResult(0, self.raw_output)
        request = json.loads(invocation.stdin.decode("utf-8"))
        candidate = request["context"]["candidate"]
        evidence = candidate["evidence"][0]
        output = {
            "decision": self.decision,
            "confidence": self.confidence,
            "title": candidate["title"] or "候选规则",
            "content": json.dumps(candidate["content"], ensure_ascii=False),
            "reason": "fake runner 仅用于本地集成测试",
            "evidence_ranges": [
                {
                    "message_id": evidence["message_id"],
                    "start_char": evidence["start_char"],
                    "end_char": evidence["end_char"],
                    "quote": evidence["quote"],
                }
            ],
            "risk_flags": [],
            "level": candidate["level"],
            "scope": candidate["scope"],
        }
        return ProcessResult(0, json.dumps(output, ensure_ascii=False).encode("utf-8"))


def _decision_adapter(*, decision: str = "publish", confidence: float = 0.95, error: Exception | None = None, raw_output: bytes | None = None):
    from codex_memory.codex_cli_runner import CodexCliRunner, CodexCliSettings
    from codex_memory.v11_decision import CodexCliDecisionAdapter

    process = FakeCodexProcess(decision=decision, confidence=confidence, error=error, raw_output=raw_output)
    runner = CodexCliRunner(
        CodexCliSettings(enabled=True, daily_budget_tokens=0, max_output_bytes=32 * 1024),
        process_runner=process,
    )
    return CodexCliDecisionAdapter(runner)


def _factory_with_candidate(*, level: str = "L1", scope: str = "project", publish_enabled: bool = True):
    from codex_memory.db import create_postgres_test_engine, create_schema, create_session_factory
    from codex_memory.db_models import MessageRow, ProjectDecisionPolicyRow, ProjectFeatureFlagRow, ProjectRow, SessionRow, V11Base, V16Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    V16Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    content = "Use OrderService for order updates."
    with factory() as session:
        project = ProjectRow(project_key="decision-test", name="决策测试")
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
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        session.add(message)
        flags = ProjectFeatureFlagRow(
            project_id=project.id,
            candidate_publish_enabled=publish_enabled,
            decision_engine_enabled=True,
        )
        session.add_all(
            [
                flags,
                ProjectDecisionPolicyRow(
                    project_id=project.id,
                    enabled=True,
                    auto_publish_enabled=True,
                    strategy="auto_publish",
                ),
            ]
        )
        session.commit()
        project_id, message_id = project.id, message.id
    from codex_memory.v11_candidates import CandidatePolicyService

    candidate = CandidatePolicyService(factory).create_candidate(
        project_id=project_id,
        source_message_id=message_id,
        task_type="decision_test",
        level=level,
        scope=scope,
        memory_type="solution",
        title="OrderService 规则",
        content={"text": content},
        evidence=[(message_id, 0, len(content))],
    )
    return factory, project_id, message_id, candidate.id


def test_candidate_creation_queues_one_decision_job_and_accepted_uses_publish_worker() -> None:
    from codex_memory.db_models import DecisionRunRow, MemoryCandidateRow, MemoryRow, ModelDecisionRow, OutboxEventRow, ProcessingJobRow
    from codex_memory.v11_handlers import V11JobHandlers
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory, project_id, _, candidate_id = _factory_with_candidate()
    dispatcher = OutboxDispatcher(factory)
    assert dispatcher.dispatch_once("decision-dispatcher") == 1
    assert dispatcher.dispatch_once("decision-dispatcher-duplicate") == 0
    with factory() as session:
        jobs = session.scalars(select(ProcessingJobRow)).all()
        assert len(jobs) == 1
        assert jobs[0].job_type == "decide_candidate"
        assert len(session.scalars(select(OutboxEventRow).where(OutboxEventRow.event_type == "candidate.decision.requested.v1")).all()) == 1

    worker = V11JobWorker(factory)
    processed = worker.process_once("decision-worker", V11JobHandlers(factory, candidate_decision_model=_decision_adapter()))
    assert processed["completed"] == 1
    with factory() as session:
        candidate = session.get(MemoryCandidateRow, candidate_id)
        assert candidate.status == "approved"
        assert session.scalar(select(MemoryRow)) is None
        accepted = session.scalar(select(OutboxEventRow).where(OutboxEventRow.event_type == "candidate.accepted.v1"))
        assert accepted is not None
        assert session.scalar(select(DecisionRunRow)) is not None
        assert session.scalar(select(ModelDecisionRow)) is not None

    assert dispatcher.dispatch_once("publish-dispatcher") == 1
    processed = worker.process_once("publish-worker", V11JobHandlers(factory, candidate_decision_model=_decision_adapter()))
    assert processed["completed"] == 1
    with factory() as session:
        candidate = session.get(MemoryCandidateRow, candidate_id)
        assert candidate.status == "published"
        assert session.scalar(select(MemoryRow)) is not None
        assert session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.job_type == "publish_memory")) is not None


@pytest.mark.parametrize(
    ("level", "scope", "confidence", "reason"),
    [("L2", "project", 0.99, "level_allowed"), ("L3", "project", 0.99, "level_allowed"), ("L1", "global", 0.99, "scope_valid"), ("L1", "project", 0.50, "confidence_threshold")],
)
def test_server_policy_gate_sends_non_l1_or_low_confidence_to_review(level: str, scope: str, confidence: float, reason: str) -> None:
    from codex_memory.db_models import MemoryCandidateRow, OutboxEventRow
    from codex_memory.v11_candidates import CandidatePolicyService
    from codex_memory.v11_decision import CandidateDecision

    factory, _, _, candidate_id = _factory_with_candidate(level=level, scope=scope)
    result = CandidatePolicyService(factory).apply_model_decision(
        candidate_id,
        CandidateDecision(decision="publish", confidence=confidence, model="test-model"),
    )
    assert result.outcome == "needs_review"
    assert reason in result.reason_codes
    with factory() as session:
        assert session.get(MemoryCandidateRow, candidate_id).status == "needs_review"
        assert session.scalar(select(OutboxEventRow).where(OutboxEventRow.event_type == "candidate.accepted.v1")) is None


def test_model_failure_retries_then_dead_job_and_needs_review_without_success() -> None:
    from codex_memory.db_models import MemoryCandidateRow, ProcessingJobRow, SecurityAuditRow
    from codex_memory.v11_handlers import V11JobHandlers
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory, _, _, candidate_id = _factory_with_candidate()
    OutboxDispatcher(factory).dispatch_once("failure-dispatcher")
    with factory() as session:
        job = session.scalar(select(ProcessingJobRow))
        job.max_attempts = 2
        session.commit()

    worker = V11JobWorker(factory)
    from codex_memory.codex_cli_runner import ProcessTimeoutError

    first = worker.process_once(
        "failure-worker",
        V11JobHandlers(factory, candidate_decision_model=_decision_adapter(error=ProcessTimeoutError("测试超时"))),
    )
    assert first["retry_wait"] == 1
    with factory() as session:
        assert session.get(MemoryCandidateRow, candidate_id).status == "validating"
        job = session.scalar(select(ProcessingJobRow))
        job.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()
    second = worker.process_once(
        "failure-worker",
        V11JobHandlers(factory, candidate_decision_model=_decision_adapter(error=ProcessTimeoutError("测试超时"))),
    )
    assert second["dead"] == 1
    with factory() as session:
        candidate = session.get(MemoryCandidateRow, candidate_id)
        job = session.scalar(select(ProcessingJobRow))
        assert candidate.status == "needs_review"
        assert job.status == "dead"
        assert job.last_error_code == "codex_cli_timeout"
        assert session.scalar(select(SecurityAuditRow).where(SecurityAuditRow.event_type == "candidate_model_failed")) is not None


def test_disabled_runner_is_observable_and_never_publishes() -> None:
    from codex_memory.db_models import MemoryCandidateRow, OutboxEventRow, ProcessingJobRow
    from codex_memory.v11_handlers import V11JobHandlers
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory, _, _, candidate_id = _factory_with_candidate()
    OutboxDispatcher(factory).dispatch_once("disabled-runner-dispatcher")
    with factory() as session:
        job = session.scalar(select(ProcessingJobRow))
        job.max_attempts = 1
        session.commit()
    processed = V11JobWorker(factory).process_once("disabled-runner-worker", V11JobHandlers(factory))
    assert processed["dead"] == 1
    with factory() as session:
        candidate = session.get(MemoryCandidateRow, candidate_id)
        job = session.scalar(select(ProcessingJobRow))
        assert candidate.status == "needs_review"
        assert job.last_error_code == "codex_cli_disabled"
        assert session.scalar(select(OutboxEventRow).where(OutboxEventRow.event_type == "candidate.accepted.v1")) is None


def test_invalid_json_is_retryable_then_needs_review_when_attempts_exhausted() -> None:
    from codex_memory.db_models import MemoryCandidateRow, ProcessingJobRow
    from codex_memory.v11_handlers import V11JobHandlers
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory, _, _, candidate_id = _factory_with_candidate()
    OutboxDispatcher(factory).dispatch_once("invalid-json-dispatcher")
    with factory() as session:
        job = session.scalar(select(ProcessingJobRow))
        job.max_attempts = 1
        session.commit()
    processed = V11JobWorker(factory).process_once(
        "invalid-json-worker",
        V11JobHandlers(factory, candidate_decision_model=_decision_adapter(raw_output=b"not-json")),
    )
    assert processed["dead"] == 1
    with factory() as session:
        candidate = session.get(MemoryCandidateRow, candidate_id)
        job = session.scalar(select(ProcessingJobRow))
        assert candidate.status == "needs_review"
        assert job.last_error_code == "codex_cli_output_not_json"


def test_model_skip_is_audited_without_accepted_event() -> None:
    from codex_memory.db_models import MemoryCandidateRow, OutboxEventRow, SecurityAuditRow
    from codex_memory.v11_candidates import CandidatePolicyService
    from codex_memory.v11_decision import CandidateDecision

    factory, _, _, candidate_id = _factory_with_candidate()
    result = CandidatePolicyService(factory).apply_model_decision(
        candidate_id,
        CandidateDecision(decision="skip", reason_codes=("重复候选",), model="test-model"),
    )
    assert result.outcome == "skip"
    with factory() as session:
        assert session.get(MemoryCandidateRow, candidate_id).status == "rejected"
        assert session.scalar(select(OutboxEventRow).where(OutboxEventRow.event_type == "candidate.accepted.v1")) is None
        assert session.scalar(select(SecurityAuditRow).where(SecurityAuditRow.event_type == "candidate_skipped")) is not None


def test_risk_metadata_cannot_bypass_server_policy_gate() -> None:
    from codex_memory.db_models import MemoryCandidateRow
    from codex_memory.v11_candidates import CandidatePolicyService
    from codex_memory.v11_decision import CandidateDecision

    factory, _, _, candidate_id = _factory_with_candidate()
    result = CandidatePolicyService(factory).apply_model_decision(
        candidate_id,
        CandidateDecision(decision="publish", confidence=0.99, metadata={"risk_flags": ["prompt_injection"]}),
    )
    assert result.outcome == "needs_review"
    assert "risk_check" in result.reason_codes
    with factory() as session:
        assert session.get(MemoryCandidateRow, candidate_id).status == "needs_review"


def test_admin_policy_api_can_override_threshold_without_opening_global_scope() -> None:
    import hashlib

    from fastapi.testclient import TestClient
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app

    factory, project_id, _, _ = _factory_with_candidate()
    with factory() as session:
        project = session.get(ProjectRow, project_id)
        session.add(ApiKeyRow(project_id=project_id, token_hash=hashlib.sha256(b"decision-admin").hexdigest(), permissions=["admin"]))
        project_key = project.project_key
        session.commit()
    client = TestClient(create_v1_app(factory))
    headers = {"Authorization": "Bearer decision-admin"}
    updated = client.put(
        f"/api/admin/v1/projects/{project_key}/decision-policy",
        headers=headers,
        json={"min_confidence": 0.71},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["min_confidence"] == 0.71
    rejected = client.put(
        f"/api/admin/v1/projects/{project_key}/decision-policy",
        headers=headers,
        json={"allowed_scope": "global"},
    )
    assert rejected.status_code == 422


def test_human_replace_creates_new_candidate_and_does_not_modify_l0() -> None:
    from codex_memory.db_models import MemoryCandidateRow, MessageRow, SecurityAuditRow
    from codex_memory.v11_candidates import CandidatePolicyService

    factory, _, message_id, candidate_id = _factory_with_candidate()
    with factory() as session:
        before = session.get(MessageRow, message_id)
        before_hash = before.content_hash
        before_content = before.content
    result = CandidatePolicyService(factory).correct(
        candidate_id,
        action="replace",
        reviewer="admin",
        reason="补充更准确的规则",
        replacement={"content": {"text": "Use OrderService and keep the update atomic."}},
    )
    assert result["replacement_candidate_id"] is not None
    with factory() as session:
        original = session.get(MemoryCandidateRow, candidate_id)
        replacement = session.get(MemoryCandidateRow, result["replacement_candidate_id"])
        source = session.get(MessageRow, message_id)
        assert original.status == "superseded"
        assert replacement.status == "generated"
        assert source.content_hash == before_hash
        assert source.content == before_content
        assert session.scalar(select(SecurityAuditRow).where(SecurityAuditRow.event_type == "candidate_human_correction")) is not None
