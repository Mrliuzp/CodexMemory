from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_model_decision_output_is_normalized_and_publish_requires_confidence() -> None:
    from codex_memory.v11_decision import normalize_candidate_decision

    result = normalize_candidate_decision(
        {
            "action": "publish",
            "confidence": 0.91,
            "reason_codes": ["高置信度"],
            "metadata": {"source": "测试适配器"},
        }
    )
    assert result.decision == "publish"
    assert result.confidence == 0.91
    with pytest.raises(ValueError, match="confidence"):
        normalize_candidate_decision({"decision": "publish"})


def test_model_decision_output_rejects_out_of_range_or_unknown_action() -> None:
    from codex_memory.v11_decision import normalize_candidate_decision

    with pytest.raises(ValueError, match="0 到 1"):
        normalize_candidate_decision({"decision": "publish", "confidence": 1.1})
    with pytest.raises(ValueError, match="publish、skip"):
        normalize_candidate_decision({"decision": "approve", "confidence": 0.99})


def test_default_adapter_never_calls_real_model_and_enters_review() -> None:
    from codex_memory.v11_decision import CandidateSnapshot, UnconfiguredCandidateDecisionModel

    snapshot = CandidateSnapshot(1, 2, "L1", "project", "rule", "标题", {"text": "证据"}, 3, ())
    result = UnconfiguredCandidateDecisionModel().decide(snapshot)
    assert result.decision == "needs_review"
    assert result.abstain is True


def test_codex_cli_adapter_maps_strict_output_from_fake_process() -> None:
    from codex_memory.codex_cli_runner import CodexCliRunner, CodexCliSettings, ProcessResult
    from codex_memory.v11_decision import CandidateEvidenceSnapshot, CandidateSnapshot, CodexCliDecisionAdapter

    class FakeProcess:
        def run(self, invocation):
            cli_schema = json.loads(Path(invocation.argv[invocation.argv.index("--output-schema") + 1]).read_text(encoding="utf-8"))
            serialized_schema = json.dumps(cli_schema, ensure_ascii=False)
            assert "$defs" not in serialized_schema
            assert "$ref" not in serialized_schema
            request = json.loads(invocation.stdin.decode("utf-8"))
            evidence = request["context"]["candidate"]["evidence"][0]
            payload = {
                "decision": "publish",
                "confidence": 0.93,
                "title": "订单更新规则",
                "content": {"text": "Use OrderService."},
                "reason": "证据可追溯",
                "evidence_ranges": [evidence | {"content_hash": None}],
                "risk_flags": [],
                "level": "L1",
                "scope": "project",
            }
            payload["evidence_ranges"][0].pop("content_hash", None)
            return ProcessResult(0, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    snapshot = CandidateSnapshot(
        7,
        2,
        "L1",
        "project",
        "rule",
        "订单规则",
        {"text": "Use OrderService."},
        3,
        (CandidateEvidenceSnapshot(3, 0, 17, "Use OrderService.", "a" * 64),),
        "project-a",
    )
    adapter = CodexCliDecisionAdapter(
        CodexCliRunner(
            CodexCliSettings(enabled=True, daily_budget_tokens=0, max_output_bytes=32 * 1024),
            process_runner=FakeProcess(),
        )
    )
    result = adapter.decide(snapshot)
    assert result.decision == "publish"
    assert result.confidence == 0.93
    assert result.metadata["level"] == "L1"
    assert result.metadata["scope"] == "project"
    assert result.metadata["validation_passed"] is True


def test_codex_cli_schema_is_flat_but_server_model_stays_strict() -> None:
    from codex_memory.decision_models import ModelDecisionOutput, cli_decision_json_schema, decision_json_schema

    cli_schema = cli_decision_json_schema()
    serialized = json.dumps(cli_schema, ensure_ascii=False)
    assert "$defs" not in serialized
    assert "$ref" not in serialized
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    assert set(cli_schema["required"]) == {
        "decision", "confidence", "title", "content", "reason",
        "evidence_ranges", "risk_flags", "level", "scope",
    }
    assert "$defs" in json.dumps(decision_json_schema(), ensure_ascii=False)
    assert ModelDecisionOutput.model_validate({
        "decision": "needs_review",
        "confidence": 0.5,
        "title": "测试候选",
        "content": {"text": "事实"},
        "reason": "需要人工确认",
        "evidence_ranges": [],
        "risk_flags": [],
        "level": "L1",
        "scope": "project",
    }).decision.value == "needs_review"


@pytest.mark.skipif(
    os.environ.get("CODEX_MEMORY_RUN_REAL_CLI_PROBE") != "1",
    reason="real Codex CLI probe is opt-in",
)
def test_real_codex_cli_flat_schema_probe_without_project_context() -> None:
    from codex_memory.codex_cli_runner import CodexCliRequest, CodexCliRunner, CodexCliSettings
    from codex_memory.decision_models import cli_decision_json_schema

    runner = CodexCliRunner(
        CodexCliSettings(
            enabled=True,
            cli_path=os.environ.get("CODEX_MEMORY_CODEX_CLI_PATH", "codex"),
            auth_root=os.environ.get("CODEX_MEMORY_CODEX_CLI_AUTH_ROOT"),
            daily_budget_tokens=0,
            timeout_seconds=45,
        )
    )
    result = runner.run(
        CodexCliRequest(
            project_key="diagnostic-schema",
            task="Return one conservative needs_review decision using the schema. Do not inspect files, run tools, or access project data.",
            context={"diagnostic_schema_probe": True},
            output_schema=cli_decision_json_schema(),
            request_id="diagnostic-schema",
        )
    )
    assert result.process_returncode == 0
    assert isinstance(result.data, dict)
    assert result.data["decision"] in {"publish", "skip", "needs_review"}


def test_codex_cli_adapter_disabled_and_invalid_json_are_observable() -> None:
    from codex_memory.codex_cli_runner import CodexCliRunner, CodexCliSettings, ProcessResult
    from codex_memory.v11_decision import CandidateDecisionError, CandidateSnapshot, CodexCliDecisionAdapter

    snapshot = CandidateSnapshot(1, 2, "L1", "project", "rule", "标题", {"text": "证据"}, 3, (), "project-a")
    with pytest.raises(CandidateDecisionError) as disabled:
        CodexCliDecisionAdapter(CodexCliRunner(CodexCliSettings())).decide(snapshot)
    assert disabled.value.code == "codex_cli_disabled"
    assert disabled.value.retryable is False

    class InvalidJsonProcess:
        def run(self, invocation):
            return ProcessResult(0, b"not-json")

    adapter = CodexCliDecisionAdapter(
        CodexCliRunner(
            CodexCliSettings(enabled=True, daily_budget_tokens=0),
            process_runner=InvalidJsonProcess(),
        )
    )
    with pytest.raises(CandidateDecisionError) as invalid:
        adapter.decide(snapshot)
    assert invalid.value.code == "codex_cli_output_not_json"
    assert invalid.value.retryable is True


def test_l1_threshold_defaults_to_point_eight_and_allows_explicit_override(monkeypatch) -> None:
    from codex_memory.v11_decision import resolve_l1_auto_publish_threshold

    monkeypatch.delenv("CODEX_MEMORY_L1_AUTO_PUBLISH_THRESHOLD", raising=False)
    assert resolve_l1_auto_publish_threshold() == 0.80
    assert resolve_l1_auto_publish_threshold(0.73) == 0.73
    monkeypatch.setenv("CODEX_MEMORY_L1_AUTO_PUBLISH_THRESHOLD", "0.86")
    assert resolve_l1_auto_publish_threshold() == 0.86
