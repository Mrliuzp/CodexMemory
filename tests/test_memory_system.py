from __future__ import annotations

import argparse
import json

from codex_memory.classifier import MemoryClassifier
from codex_memory.cli import main, parse_json_object, parse_layer
from codex_memory.embedding import CachedEmbeddingBackend, cosine_similarity
from codex_memory.jobs import LayeringJobRunner, ReflectionJobRunner
from codex_memory.models import Layer
from codex_memory.processor import LayeringProcessor
from codex_memory.runtime import CodexMemoryRuntime, ConversationMessage
from codex_memory.service import MemoryService
from codex_memory.storage import MemoryStore


class CountingEmbeddingBackend:
    name = "counting-test"

    def __init__(self) -> None:
        self.calls = 0
        self.embed_calls = 0

    def embed(self, text: str) -> dict[str, float]:
        self.embed_calls += 1
        tokens = text.lower().split()
        return {token: 1.0 for token in tokens}

    def similarity(self, left_text: str, right_text: str) -> float:
        self.calls += 1
        left = set(left_text.lower().split())
        right = set(right_text.lower().split())
        return 1.0 if left & right else 0.0


class DenseTestEmbeddingBackend:
    name = "dense-test"

    def embed(self, text: str) -> list[float]:
        if text == "left":
            return [1.0, 0.0, 1.0]
        if text == "right":
            return [1.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0]

    def similarity(self, left_text: str, right_text: str) -> float:
        return cosine_similarity(left_text, right_text, backend=self)


class FailingLayeringClassifier:
    def __init__(self) -> None:
        self.default = MemoryClassifier()

    def classify(self, logs):
        if any("boom" in log.content for log in logs):
            raise RuntimeError("classifier boom")
        return self.default.classify(logs)


def run_cli(monkeypatch, capsys, args: list[str]) -> str:
    monkeypatch.setattr("sys.argv", ["codex-memory", *args])
    main()
    return capsys.readouterr().out


def test_l0_raw_log_is_preserved_and_layered(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    content = "Bug: traceback when module:auth token expires. Fix: refresh token before retry."

    raw_id = service.append_conversation("project-a", "conv-1", "user", content, process_now=True)

    raw_logs = service.store.list_raw_logs(project_id="project-a")
    assert raw_logs[0].id == raw_id
    assert raw_logs[0].content == content
    assert raw_logs[0].processed_at is not None

    memories = service.store.list_memories("project-a", include_global_l2=False)
    assert any(memory.layer == Layer.L3 for memory in memories)
    assert any(memory.layer == Layer.L1 and memory.memory_type == "solution" for memory in memories)
    error = next(memory for memory in memories if memory.layer == Layer.L3)
    assert "root_cause" in error.body
    assert "anti_pattern" in error.body


def test_l0_raw_log_preserves_metadata_for_audit(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    metadata = {"tool": "codex", "source": "cli", "turn": 7}

    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: metadata audit preservation",
        metadata=metadata,
        process_now=True,
    )

    raw_log = service.store.list_raw_logs(project_id="project-a")[0]
    audit = service.export_project_audit("project-a")

    assert raw_log.metadata == metadata
    assert audit["raw_logs"][0]["metadata"] == metadata


def test_l0_raw_log_listing_requires_project_scope(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: auth cache crashes", process_now=True)
    service.append_conversation("project-b", "conv-2", "user", "Bug: billing export fails", process_now=True)

    project_a_logs = service.store.list_raw_logs(project_id="project-a")

    assert len(project_a_logs) == 1
    assert project_a_logs[0].project_id == "project-a"
    assert "billing" not in project_a_logs[0].content


def test_service_raw_log_listing_is_project_scoped_for_audit(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: auth audit log", process_now=True)
    service.append_conversation("project-b", "conv-2", "user", "Bug: billing audit log", process_now=True)

    logs = service.list_raw_logs("project-a")

    assert len(logs) == 1
    assert logs[0]["project_id"] == "project-a"
    assert "auth audit log" in logs[0]["content"]
    assert "billing audit log" not in json_dump(logs)


def test_l0_raw_log_does_not_directly_participate_in_retrieval_or_context(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: raw-only sentinel should stay out of rag",
        process_now=False,
    )

    results = service.retrieve("project-a", "raw-only sentinel", limit=10)
    context = service.build_context("project-a", "raw-only sentinel")

    assert results == []
    assert "raw-only sentinel" not in context.split("[Current Task]")[0]


def test_project_isolation_blocks_l1_and_l3_cross_project_retrieval(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: auth cache crashes on refresh", process_now=True)
    service.append_conversation("project-b", "conv-2", "user", "Bug: billing invoice fails on export", process_now=True)

    results = service.retrieve("project-a", "billing invoice export crash", limit=10)

    assert results
    assert all(result.item.project_id == "project-a" or result.item.layer == Layer.L2 for result in results)
    assert not any("billing invoice" in result.item.body for result in results if result.item.layer in {Layer.L1, Layer.L3})


def test_l3_error_memory_is_prioritized_in_context(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: migration error drops nullable index", process_now=True)
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: add a guarded migration with rollback",
        process_now=True,
    )

    context = service.build_context("project-a", "fix migration nullable index")

    l3_index = context.index("[Error Memory - L3]")
    l2_index = context.index("[Knowledge Base - L2]")
    l1_index = context.index("[Working Memory - L1]")
    assert l3_index < l2_index < l1_index
    assert "Forbidden anti-pattern" in context


def test_l3_error_memory_is_first_in_retrieval_even_when_l1_has_stronger_semantic_match(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: deployment freeze",
        body="error: unsafe deployment pattern\ncontext: release\nroot_cause: missing guard\nfix: pause rollout\nanti_pattern: deploy without checks",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
        weight=3.0,
    )
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: exact semantic match",
        body="cache invalidation retry queue exact semantic match",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )

    results = service.retrieve("project-a", "cache invalidation retry queue exact semantic match", limit=2)

    assert results[0].item.layer == Layer.L3
    assert results[1].item.layer == Layer.L1


def test_l3_error_memory_injects_forbidden_anti_pattern(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        (
            "Error: queue retry storm. "
            "Context: worker overload. "
            "Trigger: immediate retry after worker timeout. "
            "Root cause: no jitter. "
            "Fix: add bounded exponential backoff. "
            "Anti-pattern: immediate retry loop."
        ),
        process_now=True,
    )

    context = service.build_context("project-a", "fix queue retry storm")

    assert "[Error Memory - L3]" in context
    assert "Trigger condition: immediate retry after worker timeout" in context
    assert "Forbidden anti-pattern: immediate retry loop" in context
    assert "Fix: add bounded exponential backoff" in context


def test_context_layer_filter_limits_injected_memory(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: hidden error in context layer filter",
        body="error: hidden error in context layer filter\ncontext: test\nroot_cause: setup\nfix: none\nanti_pattern: expose filtered L3",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: visible working memory in context layer filter",
        process_now=True,
    )

    context = service.build_context("project-a", "context layer filter", layers=[Layer.L1], limit=10)

    l3_section = context.split("[Error Memory - L3]")[1].split("[Knowledge Base - L2]")[0]
    assert "- none" in l3_section
    assert "hidden error" not in context
    assert "visible working memory" in context


def test_context_memory_type_filter_limits_injected_memory(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: hidden error memory type filter", process_now=True)
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: visible solution memory type filter",
        process_now=True,
    )

    context = service.build_context("project-a", "memory type filter", memory_types=["solution"], limit=10)

    assert "hidden error" not in context
    assert "visible solution" in context


def test_global_l2_is_available_without_exposing_other_project_memory(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.store.upsert_memory(
        project_id=None,
        layer=Layer.L2,
        title="Knowledge: always write regression tests",
        body="Stable rule: write regression tests for bug fixes.",
        tags=["knowledge", "testing"],
        memory_type="knowledge",
        source_log_ids=[],
        weight=2.0,
    )
    service.append_conversation("project-b", "conv-2", "user", "Bug: private billing error", process_now=True)

    results = service.retrieve("project-a", "regression tests for bug fixes", limit=10)

    assert any(result.item.project_id is None and result.item.layer == Layer.L2 for result in results)
    assert not any("private billing" in result.item.body for result in results)


def test_cli_context_includes_global_l2_without_other_project_private_memory(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.store.upsert_memory(
        project_id=None,
        layer=Layer.L2,
        title="Knowledge: cli context shared rollback rule",
        body="Stable rule: shared rollback rule belongs in every project.",
        tags=["knowledge", "testing"],
        memory_type="knowledge",
        source_log_ids=[],
        weight=2.0,
    )
    service.append_conversation("project-b", "conv-2", "user", "Bug: cli context private billing outage", process_now=True)

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "context",
            "--project",
            "project-a",
            "--task",
            "shared rollback rule",
        ],
    )

    assert "[Knowledge Base - L2]" in output
    assert "cli context shared rollback rule" in output
    assert "private billing outage" not in output


def test_only_l2_can_be_global_without_project_id(tmp_path):
    service = MemoryService(tmp_path / "memory.db")

    for layer in [Layer.L1, Layer.L3]:
        try:
            service.store.upsert_memory(
                project_id=None,
                layer=layer,
                title=f"{layer.value}: invalid global memory",
                body="Project-scoped memory must not be global.",
                tags=["isolation"],
                memory_type="test",
                source_log_ids=[],
            )
        except ValueError as error:
            assert "only L2" in str(error)
        else:
            raise AssertionError(f"{layer.value} should require project_id")


def test_project_l2_can_be_promoted_to_global_l2_with_governance_event(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    memory_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L2,
        title="Knowledge: prefer migrations with rollback",
        body="Stable rule: every migration needs rollback.",
        tags=["knowledge"],
        memory_type="knowledge",
        source_log_ids=[],
        weight=2.0,
    )

    result = service.promote_to_global_l2(
        project_id="project-a",
        memory_id=memory_id,
        reviewer="lead",
        reason="applies to every project",
    )
    global_memory = service.store.get_memory(result["global_memory_id"])
    events = service.store.list_governance_events("project-a")

    assert global_memory is not None
    assert global_memory.project_id is None
    assert global_memory.layer == Layer.L2
    assert events[0]["event_type"] == "promote_global_l2"
    assert events[0]["reviewer"] == "lead"


def test_cli_promote_global_creates_governed_cross_project_l2(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    memory_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L2,
        title="Knowledge: cli global retry rule",
        body="Global retry rule from cli promotion",
        tags=["knowledge"],
        memory_type="knowledge",
        source_log_ids=[],
        weight=2.0,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "promote-global",
            "--project",
            "project-a",
            "--memory-id",
            str(memory_id),
            "--reviewer",
            "lead",
            "--reason",
            "shared retry rule",
        ],
    )
    result = json.loads(output)
    verifier = MemoryService(db_path)
    global_memory = verifier.store.get_memory(result["global_memory_id"])
    events = verifier.store.list_governance_events("project-a")
    retrieved = verifier.retrieve("project-b", "global retry rule", layers=[Layer.L2], limit=5)

    assert global_memory is not None
    assert global_memory.project_id is None
    assert global_memory.layer == Layer.L2
    assert events[-1]["event_type"] == "promote_global_l2"
    assert events[-1]["metadata"]["global_memory_id"] == result["global_memory_id"]
    assert any(item.item.id == result["global_memory_id"] for item in retrieved)


def test_l2_knowledge_covers_final_conclusion_standard_and_design(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Final conclusion: token refresh belongs in the auth boundary",
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Standard: all migrations include rollback SQL",
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-3",
        "assistant",
        "Design spec: cache invalidation uses versioned keys",
        process_now=True,
    )

    knowledge = service.store.list_memories("project-a", layers=[Layer.L2], include_global_l2=False)

    assert len(knowledge) == 3
    assert all(item.memory_type == "knowledge" for item in knowledge)
    assert all(item.weight == 2.0 for item in knowledge)


def test_global_l2_promotion_rejects_cross_project_memory(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    memory_id = service.store.upsert_memory(
        project_id="project-b",
        layer=Layer.L2,
        title="Knowledge: private billing rule",
        body="Stable rule: private billing behavior.",
        tags=["knowledge"],
        memory_type="knowledge",
        source_log_ids=[],
    )

    try:
        service.promote_to_global_l2("project-a", memory_id, reviewer="lead", reason="wrong project")
    except ValueError as error:
        assert "does not belong" in str(error)
    else:
        raise AssertionError("cross-project promotion should fail")


def test_duplicate_memories_keep_version_history(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    message = "Solution: use sqlite transaction for raw log writes"

    service.append_conversation("project-a", "conv-1", "assistant", message, process_now=True)
    service.append_conversation("project-a", "conv-2", "assistant", message, process_now=True)

    memories = service.store.list_memories("project-a", layers=[Layer.L1], include_global_l2=False)
    matching = [memory for memory in memories if "sqlite transaction" in memory.body]
    assert len(matching) == 1
    assert matching[0].version == 2
    assert len(matching[0].source_log_ids) == 2
    versions = service.store.list_memory_versions("project-a")
    assert [version["version"] for version in versions] == [1, 2]


def test_reflection_promotes_frequently_used_l1_and_decays_working_memory(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: mock clock in expiry tests",
        process_now=True,
    )

    for _ in range(3):
        service.retrieve("project-a", "mock clock expiry tests")

    report = service.run_reflection("project-a")
    memories = service.store.list_memories("project-a", include_global_l2=False)

    assert report["promoted"] >= 1
    assert any(memory.layer == Layer.L2 and "mock clock" in memory.body for memory in memories)
    assert report["decayed"] >= 0


def test_reflection_synthesizes_stable_rules_from_repeated_l1(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    message = "Solution: use idempotent retry keys for payment callbacks"
    service.append_conversation("project-a", "conv-1", "assistant", message, process_now=True)
    service.append_conversation("project-a", "conv-2", "assistant", message, process_now=True)

    report = service.run_reflection("project-a")
    knowledge = service.store.list_memories("project-a", layers=[Layer.L2], include_global_l2=False)
    reports = service.list_reflection_reports("project-a")

    assert report["synthesized"] == 1
    assert any("idempotent retry keys" in item.body and "stable-rule" in item.tags for item in knowledge)
    assert "Synthesized stable rules: 1" in reports[0]["summary"]


def test_reflection_synthesizes_stable_rules_after_similarity_clustering(tmp_path):
    service = MemoryService(tmp_path / "memory.db", embedding_backend=CountingEmbeddingBackend())
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: retry key",
        body="retry key prevents duplicate payment callback",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[1],
        weight=1.0,
    )
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: callback idempotency",
        body="retry key blocks repeated payment callback",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[2],
        weight=1.0,
    )

    report = service.run_reflection("project-a")
    knowledge = service.store.list_memories("project-a", layers=[Layer.L2], include_global_l2=False)
    stable_rules = [item for item in knowledge if "stable-rule" in item.tags]

    assert report["merged"] == 1
    assert report["synthesized"] >= 1
    assert any(set(item.source_log_ids) == {1, 2} and "retry key" in item.body for item in stable_rules)


def test_reflection_decays_only_stale_l1(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    stale_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: stale",
        body="old working memory",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )
    fresh_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: fresh",
        body="fresh working memory",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )
    with service.store.connect() as connection:
        connection.execute("UPDATE memories SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (stale_id,))

    report = service.run_reflection("project-a")
    stale = service.store.get_memory(stale_id)
    fresh = service.store.get_memory(fresh_id)

    assert report["decayed"] >= 1
    assert stale is not None and stale.weight < 1.0
    assert fresh is not None and fresh.weight == 1.0


def test_reflection_lifecycle_changes_are_project_scoped(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    project_a_stale = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: project a stale",
        body="project a stale working memory",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )
    project_b_stale = service.store.upsert_memory(
        project_id="project-b",
        layer=Layer.L1,
        title="Working: project b stale",
        body="project b stale working memory",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )
    project_b_low_value = service.store.upsert_memory(
        project_id="project-b",
        layer=Layer.L1,
        title="Working: project b low value",
        body="project b low value working memory",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=0.1,
    )
    with service.store.connect() as connection:
        connection.execute(
            "UPDATE memories SET updated_at = '2000-01-01 00:00:00' WHERE id IN (?, ?)",
            (project_a_stale, project_b_stale),
        )

    report = service.run_reflection("project-a")

    assert report["decayed"] == 1
    assert service.store.get_memory(project_a_stale).weight < 1.0
    assert service.store.get_memory(project_b_stale).weight == 1.0
    assert service.store.get_memory(project_b_low_value) is not None


def test_reflection_deletes_only_stale_low_value_l1(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    stale_low = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: stale low value",
        body="stale low value working memory",
        tags=["working"],
        memory_type="conversation",
        source_log_ids=[],
        weight=0.1,
    )
    fresh_low = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: fresh low value",
        body="fresh low value working memory",
        tags=["working"],
        memory_type="conversation",
        source_log_ids=[],
        weight=0.1,
    )
    with service.store.connect() as connection:
        connection.execute("UPDATE memories SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (stale_low,))

    report = service.run_reflection("project-a")

    assert report["deleted"] == 1
    assert service.store.get_memory(stale_low) is None
    assert service.store.get_memory(fresh_low) is not None


def test_retrieval_prefers_newer_memory_when_scores_are_otherwise_similar(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    old_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: cache invalidation old",
        body="cache invalidation uses versioned keys",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )
    new_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: cache invalidation new",
        body="cache invalidation uses versioned keys",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )
    with service.store.connect() as connection:
        connection.execute("UPDATE memories SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (old_id,))

    results = service.retrieve("project-a", "cache invalidation versioned keys", limit=2)

    assert [result.item.id for result in results] == [new_id, old_id]
    assert results[0].recency_score > results[1].recency_score


def test_retrieval_increases_access_count_and_weight(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    memory_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: usage lift",
        body="cache warming avoids cold starts",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
        weight=1.0,
    )

    service.retrieve("project-a", "cache warming cold starts", limit=1)
    lifted = service.store.get_memory(memory_id)

    assert lifted is not None
    assert lifted.access_count == 1
    assert lifted.weight > 1.0


def test_memory_type_filter_limits_retrieval(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: cache invalidation error", process_now=True)
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: cache invalidation uses versioned keys",
        process_now=True,
    )

    results = service.retrieve("project-a", "cache invalidation", memory_types=["solution"], limit=10)

    assert results
    assert all(result.item.memory_type == "solution" for result in results)


def test_l1_working_memory_covers_problem_debug_code_and_temporary_types(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: checkout form hangs after submit", process_now=True)
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Debug: trace checkout submit handler and inspect event payload",
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-3",
        "assistant",
        "```python\ndef submit_checkout():\n    return True\n```",
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-4",
        "assistant",
        "Temporary hypothesis: payment iframe steals focus",
        process_now=True,
    )

    working_types = {
        memory.memory_type
        for memory in service.store.list_memories("project-a", layers=[Layer.L1], include_global_l2=False)
    }

    assert {"problem", "debug", "code", "temporary"} <= working_types


def test_general_conversation_becomes_low_weight_l1_or_is_ignored(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    user_text = "Please remember that the next planning session is about naming cleanup"
    assistant_text = "Sounds good, I will keep that in mind for the discussion"
    service.append_conversation("project-a", "conv-1", "user", user_text, process_now=True)
    service.append_conversation("project-a", "conv-1", "assistant", assistant_text, process_now=True)

    memories = service.store.list_memories("project-a", layers=[Layer.L1], include_global_l2=False)
    conversation = next(memory for memory in memories if memory.memory_type == "conversation")

    assert conversation.body == user_text
    assert conversation.weight == 0.4
    assert not any(assistant_text in memory.body for memory in memories)


def test_layer_filter_limits_retrieval(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: layer filter error", process_now=True)
    service.append_conversation("project-a", "conv-2", "assistant", "Solution: layer filter fix", process_now=True)

    results = service.retrieve("project-a", "layer filter", layers=[Layer.L1], limit=10)

    assert results
    assert all(result.item.layer == Layer.L1 for result in results)


def test_parse_layer_accepts_lowercase_cli_values():
    assert parse_layer("l3") == Layer.L3


def test_parse_json_object_accepts_cli_metadata():
    assert parse_json_object('{"tool":"codex","turn":1}') == {"tool": "codex", "turn": 1}


def test_parse_json_object_rejects_non_object_metadata():
    try:
        parse_json_object('["not", "an", "object"]')
    except argparse.ArgumentTypeError as error:
        assert "must be an object" in str(error)
    else:
        raise AssertionError("metadata JSON arrays should be rejected")


def test_cli_context_processes_pending_l0_before_output(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: cli context pending answer hook",
        process_now=False,
    )
    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "context",
            "--project",
            "project-a",
            "--task",
            "pending answer hook",
        ],
    )

    verifier = MemoryService(db_path)

    assert "[Working Memory - L1]" in output
    assert "cli context pending answer hook" in output
    assert verifier.store.count_jobs("project-a", status="done") == 1


def test_cli_context_skip_pending_keeps_l0_out_of_injected_memory(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: cli skip pending should stay raw",
        process_now=False,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "context",
            "--project",
            "project-a",
            "--task",
            "skip pending raw memory",
            "--skip-pending",
        ],
    )
    verifier = MemoryService(db_path)
    memory_sections = output.split("[Current Task]")[0]

    assert "cli skip pending should stay raw" not in memory_sections
    assert verifier.store.count_jobs("project-a", status="pending") == 1
    assert verifier.store.list_raw_logs(project_id="project-a")[0].processed_at is None


def test_cli_context_applies_module_type_and_memory_type_filters(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: cli context refresh token before retry",
        metadata={"module": "auth", "type": "api"},
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: cli context export invoice after settlement",
        metadata={"module": "billing", "type": "batch"},
        process_now=True,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "context",
            "--project",
            "project-a",
            "--task",
            "refresh token retry",
            "--module",
            "auth",
            "--tag-type",
            "api",
            "--type",
            "solution",
        ],
    )

    assert "[Working Memory - L1]" in output
    assert "cli context refresh token before retry" in output
    assert "module:auth" in output
    assert "type:api" in output
    assert "export invoice" not in output


def test_cli_context_layer_filter_limits_injected_memory(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: cli context hidden layer error",
        body="error: cli context hidden layer error\ncontext: test\nroot_cause: setup\nfix: none\nanti_pattern: expose filtered L3",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: cli context visible layer fix",
        process_now=True,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "context",
            "--project",
            "project-a",
            "--task",
            "cli context layer filter",
            "--layer",
            "l1",
        ],
    )
    l3_section = output.split("[Error Memory - L3]")[1].split("[Knowledge Base - L2]")[0]

    assert "- none" in l3_section
    assert "cli context visible layer fix" in output
    assert "cli context hidden layer error" not in output


def test_cli_context_injects_structured_error_memory(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        (
            "Error: cli context retry storm. "
            "Context: worker queue overload. "
            "Trigger: timeout repeats twice. "
            "Root cause: missing backoff. "
            "Fix: add bounded backoff. "
            "Anti-pattern: immediate retry loop."
        ),
        process_now=True,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "context",
            "--project",
            "project-a",
            "--task",
            "fix cli context retry storm",
        ],
    )

    assert "[Error Memory - L3]" in output
    assert "Trigger condition: timeout repeats twice" in output
    assert "Fix: add bounded backoff" in output
    assert "Forbidden anti-pattern: immediate retry loop" in output


def test_cli_raw_logs_output_is_project_scoped(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation("project-a", "conv-1", "user", "Bug: cli auth raw log", process_now=False)
    service.append_conversation("project-b", "conv-2", "user", "Bug: cli billing raw log", process_now=False)

    output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "raw-logs", "--project", "project-a"],
    )
    logs = json.loads(output)

    assert len(logs) == 1
    assert logs[0]["project_id"] == "project-a"
    assert "cli auth raw log" in logs[0]["content"]
    assert "cli billing raw log" not in output


def test_cli_jobs_output_is_project_scoped(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    project_a_raw_id = service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: cli auth job",
        process_now=False,
    )
    project_b_raw_id = service.append_conversation(
        "project-b",
        "conv-2",
        "user",
        "Bug: cli billing job",
        process_now=False,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "jobs", "--project", "project-a"],
    )
    jobs = json.loads(output)

    assert len(jobs) == 1
    assert jobs[0]["project_id"] == "project-a"
    assert jobs[0]["payload"]["raw_log_ids"] == [project_a_raw_id]
    assert project_b_raw_id not in jobs[0]["payload"]["raw_log_ids"]


def test_cli_retrieve_applies_module_type_and_memory_type_filters(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: refresh token before retry",
        metadata={"module": "auth", "type": "api"},
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: export invoice after settlement",
        metadata={"module": "billing", "type": "batch"},
        process_now=True,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "retrieve",
            "--project",
            "project-a",
            "--query",
            "refresh token retry",
            "--module",
            "auth",
            "--tag-type",
            "api",
            "--type",
            "solution",
        ],
    )
    results = json.loads(output)

    assert results
    assert all(result["project_id"] == "project-a" for result in results)
    assert all(result["memory_type"] == "solution" for result in results)
    assert any("refresh token" in result["title"] for result in results)
    assert "invoice" not in output


def test_cli_retrieve_layer_filter_limits_results(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: cli layer filter error",
        body="error: cli layer filter error\ncontext: test\nroot_cause: setup\nfix: none\nanti_pattern: expose filtered L3",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
    )
    service.append_conversation("project-a", "conv-2", "assistant", "Solution: cli layer filter fix", process_now=True)

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "retrieve",
            "--project",
            "project-a",
            "--query",
            "cli layer filter",
            "--layer",
            "l1",
        ],
    )
    results = json.loads(output)

    assert results
    assert all(result["layer"] == "L1" for result in results)
    assert any("cli layer filter fix" in result["title"] for result in results)
    assert "cli layer filter error" not in output


def test_cli_append_async_process_enqueues_worker(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "append",
            "--project",
            "project-a",
            "--conversation",
            "conv-1",
            "--role",
            "user",
            "--content",
            "Bug: cli async process should layer",
            "--async-process",
        ],
    )
    raw_id = json.loads(output)["raw_log_id"]
    verifier = MemoryService(db_path)
    raw_log = verifier.store.list_raw_logs(project_id="project-a")[0]
    memories = verifier.store.list_memories("project-a", include_global_l2=False)

    assert verifier.store.count_jobs("project-a", status="done") == 1
    assert raw_log.id == raw_id
    assert raw_log.processed_at is not None
    assert any(memory.layer == Layer.L3 for memory in memories)


def test_cli_append_enqueue_worker_drains_layering_before_exit(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "append",
            "--project",
            "project-a",
            "--conversation",
            "conv-1",
            "--role",
            "user",
            "--content",
            "Bug: cli enqueue worker should layer before exit",
            "--enqueue-worker",
        ],
    )
    raw_id = json.loads(output)["raw_log_id"]
    verifier = MemoryService(db_path)
    raw_log = verifier.store.list_raw_logs(project_id="project-a")[0]
    memories = verifier.store.list_memories("project-a", include_global_l2=False)

    assert raw_log.id == raw_id
    assert raw_log.processed_at is not None
    assert verifier.store.count_jobs("project-a", status="done") == 1
    assert any(memory.layer == Layer.L3 for memory in memories)


def test_cli_append_process_now_layers_immediately(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "append",
            "--project",
            "project-a",
            "--conversation",
            "conv-1",
            "--role",
            "user",
            "--content",
            "Bug: cli process now should layer immediately",
            "--process-now",
        ],
    )
    raw_id = json.loads(output)["raw_log_id"]
    verifier = MemoryService(db_path)
    raw_log = verifier.store.list_raw_logs(project_id="project-a")[0]
    memories = verifier.store.list_memories("project-a", include_global_l2=False)

    assert raw_log.id == raw_id
    assert raw_log.processed_at is not None
    assert verifier.store.count_jobs("project-a", status="done") == 1
    assert any(memory.layer == Layer.L3 for memory in memories)


def test_cli_append_metadata_json_preserves_l0_metadata(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "append",
            "--project",
            "project-a",
            "--conversation",
            "conv-1",
            "--role",
            "assistant",
            "--content",
            "Solution: cli metadata belongs to raw audit log",
            "--metadata-json",
            '{"tool":"codex","module":"auth","turn":3}',
        ],
    )
    raw_id = json.loads(output)["raw_log_id"]
    verifier = MemoryService(db_path)
    raw_log = verifier.store.list_raw_logs(project_id="project-a")[0]

    assert raw_log.id == raw_id
    assert raw_log.metadata == {"tool": "codex", "module": "auth", "turn": 3}
    assert raw_log.processed_at is None
    assert verifier.store.count_jobs("project-a", status="pending") == 1


def test_module_and_type_tag_filters_limit_retrieval(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: module:auth type:api token refresh fails",
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "user",
        "Bug: module:billing type:batch invoice export fails",
        process_now=True,
    )

    results = service.retrieve(
        "project-a",
        "token refresh fails",
        modules=["auth"],
        type_tags=["api"],
        limit=10,
    )

    assert results
    assert all("module:auth" in result.item.tags for result in results)
    assert all("type:api" in result.item.tags for result in results)
    assert not any("billing" in result.item.body for result in results)


def test_metadata_module_type_and_tags_participate_in_retrieval_filters(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: refresh token before retry",
        metadata={"module": "auth", "type": "api", "tags": ["security"]},
        process_now=True,
    )
    service.append_conversation(
        "project-a",
        "conv-2",
        "assistant",
        "Solution: export invoice after settlement",
        metadata={"module": "billing", "type": "batch", "tags": ["finance"]},
        process_now=True,
    )

    results = service.retrieve(
        "project-a",
        "refresh token retry",
        tags=["security"],
        modules=["auth"],
        type_tags=["api"],
        limit=10,
    )

    assert results
    assert all("security" in result.item.tags for result in results)
    assert all("module:auth" in result.item.tags for result in results)
    assert all("type:api" in result.item.tags for result in results)
    assert not any("invoice" in result.item.body for result in results)


def test_error_memory_extracts_structured_fields(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        (
            "Error: retry loop never exits. "
            "Context: worker queue drains slowly. "
            "Trigger: retry count exceeds three. "
            "Root cause: missing max-attempt guard. "
            "Fix: stop after three attempts. "
            "Anti-pattern: unbounded retries."
        ),
        process_now=True,
    )

    error = next(memory for memory in service.store.list_memories("project-a") if memory.layer == Layer.L3)

    assert "root_cause: missing max-attempt guard" in error.body
    assert "trigger_condition: retry count exceeds three" in error.body
    assert "fix: stop after three attempts" in error.body
    assert "anti_pattern: unbounded retries" in error.body
    assert error.metadata["error_memory"] == {
        "error": "retry loop never exits",
        "context": "worker queue drains slowly",
        "trigger_condition": "retry count exceeds three",
        "root_cause": "missing max-attempt guard",
        "fix": "stop after three attempts",
        "anti_pattern": "unbounded retries",
    }


def test_error_memory_extracts_chinese_structured_fields(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        (
            "\u9519\u8bef: \u652f\u4ed8\u56de\u8c03\u91cd\u590d\u6263\u6b3e. "
            "\u4e0a\u4e0b\u6587: \u7f51\u5173\u8d85\u65f6\u540e\u91cd\u8bd5. "
            "\u89e6\u53d1\u6761\u4ef6: \u540c\u4e00\u8ba2\u5355\u6536\u5230\u4e24\u6b21\u56de\u8c03. "
            "\u6839\u56e0: \u7f3a\u5c11\u5e42\u7b49\u952e\u7ea6\u675f. "
            "\u4fee\u590d\u65b9\u6848: \u6309\u8ba2\u5355\u53f7\u5199\u5165\u552f\u4e00\u7d22\u5f15. "
            "\u53cd\u6a21\u5f0f: \u76f4\u63a5\u91cd\u653e\u56de\u8c03\u5904\u7406."
        ),
        process_now=True,
    )

    error = next(memory for memory in service.store.list_memories("project-a") if memory.layer == Layer.L3)
    context = service.build_context("project-a", "\u4fee\u590d\u652f\u4ed8\u56de\u8c03\u91cd\u590d\u6263\u6b3e")

    assert error.metadata["error_memory"] == {
        "error": "\u652f\u4ed8\u56de\u8c03\u91cd\u590d\u6263\u6b3e",
        "context": "\u7f51\u5173\u8d85\u65f6\u540e\u91cd\u8bd5",
        "trigger_condition": "\u540c\u4e00\u8ba2\u5355\u6536\u5230\u4e24\u6b21\u56de\u8c03",
        "root_cause": "\u7f3a\u5c11\u5e42\u7b49\u952e\u7ea6\u675f",
        "fix": "\u6309\u8ba2\u5355\u53f7\u5199\u5165\u552f\u4e00\u7d22\u5f15",
        "anti_pattern": "\u76f4\u63a5\u91cd\u653e\u56de\u8c03\u5904\u7406",
    }
    assert "Trigger condition: \u540c\u4e00\u8ba2\u5355\u6536\u5230\u4e24\u6b21\u56de\u8c03" in context
    assert "Forbidden anti-pattern: \u76f4\u63a5\u91cd\u653e\u56de\u8c03\u5904\u7406" in context


def test_runtime_records_every_message_as_l0(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    runtime = CodexMemoryRuntime(service)

    raw_ids = runtime.record_conversation(
        "project-a",
        "conv-1",
        [
            ConversationMessage(role="user", content="Need a fix for cache bug"),
            ConversationMessage(role="assistant", content="Solution: add cache version key"),
        ],
        process_now=True,
    )

    raw_logs = service.store.list_raw_logs(project_id="project-a")
    assert len(raw_ids) == 2
    assert [log.content for log in raw_logs] == ["Need a fix for cache bug", "Solution: add cache version key"]


def test_runtime_can_leave_layering_jobs_pending_for_external_worker(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    runtime = CodexMemoryRuntime(service)

    runtime.record_conversation(
        "project-a",
        "conv-1",
        [ConversationMessage(role="user", content="Bug: external worker owns layering")],
        process_now=False,
    )

    assert service.store.count_jobs("project-a", status="pending") == 1
    assert service.store.list_raw_logs(project_id="project-a")[0].processed_at is None


def test_runtime_prepare_answer_context_processes_project_pending_memories_before_rag(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    runtime = CodexMemoryRuntime(service)
    runtime.record_conversation(
        "project-a",
        "conv-1",
        [ConversationMessage(role="assistant", content="Solution: use cache version key before lookup")],
        process_now=False,
    )
    runtime.record_conversation(
        "project-b",
        "conv-2",
        [ConversationMessage(role="assistant", content="Solution: billing export private backlog")],
        process_now=False,
    )

    context = runtime.prepare_answer_context("project-a", "cache version key")

    assert "use cache version key" in context
    assert "billing export private backlog" not in context
    assert service.store.count_jobs("project-a", status="done") == 1
    assert service.store.count_jobs("project-b", status="pending") == 1


def test_processing_job_listing_is_project_scoped(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    project_a_raw_id = service.append_conversation(
        "project-a", "conv-1", "user", "Bug: auth job visible", process_now=False
    )
    project_b_raw_id = service.append_conversation(
        "project-b", "conv-2", "user", "Bug: billing job hidden", process_now=False
    )

    jobs = service.list_processing_jobs("project-a")

    assert len(jobs) == 1
    assert jobs[0]["project_id"] == "project-a"
    assert jobs[0]["payload"]["raw_log_ids"] == [project_a_raw_id]
    assert project_b_raw_id not in jobs[0]["payload"]["raw_log_ids"]


def test_l0_append_creates_durable_layering_job(tmp_path):
    service = MemoryService(tmp_path / "memory.db")

    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: durable queue should process this later",
    )

    assert service.store.count_jobs("project-a", status="pending") == 1
    assert service.store.list_raw_logs(project_id="project-a")[0].processed_at is None
    assert service.process_pending_memories() == 2
    assert service.store.count_jobs("project-a", status="done") == 1
    assert service.store.list_raw_logs(project_id="project-a")[0].processed_at is not None


def test_failed_layering_jobs_can_be_retried(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: retry failed layering job",
        process_now=False,
    )
    job_id = service.store.mark_layering_jobs_running("project-a")[0]
    service.store.fail_jobs([job_id], "temporary classifier failure")

    retried = service.retry_failed_layering_jobs("project-a")
    created = service.process_pending_memories()

    assert retried == 1
    assert created == 2
    assert service.store.count_jobs("project-a", status="done") == 1
    assert service.store.list_raw_logs(project_id="project-a")[0].processed_at is not None


def test_cli_retry_failed_resets_project_failed_jobs(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: cli retry failed layering job",
        process_now=False,
    )
    job_id = service.store.mark_layering_jobs_running("project-a")[0]
    service.store.fail_jobs([job_id], "temporary classifier failure")

    output = run_cli(monkeypatch, capsys, ["--db", str(db_path), "retry-failed", "--project", "project-a"])
    result = json.loads(output)
    verifier = MemoryService(db_path)
    jobs = verifier.list_processing_jobs("project-a")

    assert result["retried"] == 1
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["error"] is None


def test_pending_processing_does_not_consume_failed_job_raw_logs(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    failed_raw_id = service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: failed job must stay unprocessed",
        process_now=False,
    )
    failed_job_id = service.store.mark_layering_jobs_running("project-a")[0]
    service.store.fail_jobs([failed_job_id], "temporary failure")
    pending_raw_id = service.append_conversation(
        "project-a",
        "conv-2",
        "user",
        "Bug: pending job can process",
        process_now=False,
    )

    created = service.process_pending_memories()
    raw_logs = {raw.id: raw for raw in service.store.list_raw_logs(project_id="project-a")}

    assert created == 2
    assert raw_logs[failed_raw_id].processed_at is None
    assert raw_logs[pending_raw_id].processed_at is not None
    assert service.store.count_jobs("project-a", status="failed") == 1
    assert service.store.count_jobs("project-a", status="done") == 1


def test_pending_processor_continues_after_project_failure(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    processor = LayeringProcessor(store, classifier=FailingLayeringClassifier())
    failing_raw_id = store.append_raw_log("project-a", "conv-1", "user", "Bug: boom during layering")
    succeeding_raw_id = store.append_raw_log("project-b", "conv-2", "assistant", "Solution: continue after classifier outage")

    created = processor.process_pending()
    project_a_raw = store.list_raw_logs(project_id="project-a")[0]
    project_b_raw = store.list_raw_logs(project_id="project-b")[0]
    project_b_memories = store.list_memories("project-b", include_global_l2=False)

    assert created == 1
    assert project_a_raw.id == failing_raw_id
    assert project_a_raw.processed_at is None
    assert project_b_raw.id == succeeding_raw_id
    assert project_b_raw.processed_at is not None
    assert store.count_jobs("project-a", status="failed") == 1
    assert store.count_jobs("project-b", status="done") == 1
    assert any("continue after classifier outage" in memory.body for memory in project_b_memories)


def test_stale_running_layering_jobs_can_be_reset_and_processed(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    stale_raw_id = service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: stale running job should recover",
        process_now=False,
    )
    stale_job_id = service.store.mark_layering_jobs_running("project-a")[0]
    fresh_raw_id = service.append_conversation(
        "project-a",
        "conv-2",
        "user",
        "Bug: fresh running job should stay running",
        process_now=False,
    )
    fresh_job_id = service.store.mark_layering_jobs_running("project-a")[0]
    with service.store.connect() as connection:
        connection.execute(
            "UPDATE processing_jobs SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (stale_job_id,),
        )

    reset = service.reset_stale_running_layering_jobs("project-a", older_than_minutes=1)
    created = service.process_pending_memories()
    jobs = {job["id"]: job for job in service.list_processing_jobs("project-a")}
    raw_logs = {raw.id: raw for raw in service.store.list_raw_logs(project_id="project-a")}

    assert reset == 1
    assert created == 2
    assert jobs[stale_job_id]["status"] == "done"
    assert jobs[fresh_job_id]["status"] == "running"
    assert raw_logs[stale_raw_id].processed_at is not None
    assert raw_logs[fresh_raw_id].processed_at is None


def test_cli_reset_stale_running_resets_only_timed_out_jobs(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    stale_raw_id = service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: cli stale running job should recover",
        process_now=False,
    )
    stale_job_id = service.store.mark_layering_jobs_running("project-a")[0]
    fresh_raw_id = service.append_conversation(
        "project-a",
        "conv-2",
        "user",
        "Bug: cli fresh running job should stay running",
        process_now=False,
    )
    fresh_job_id = service.store.mark_layering_jobs_running("project-a")[0]
    with service.store.connect() as connection:
        connection.execute(
            "UPDATE processing_jobs SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (stale_job_id,),
        )

    output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "reset-stale-running", "--project", "project-a", "--older-than-minutes", "1"],
    )
    result = json.loads(output)
    verifier = MemoryService(db_path)
    jobs = {job["id"]: job for job in verifier.list_processing_jobs("project-a")}
    raw_logs = {raw.id: raw for raw in verifier.store.list_raw_logs(project_id="project-a")}

    assert result["reset"] == 1
    assert jobs[stale_job_id]["status"] == "pending"
    assert jobs[fresh_job_id]["status"] == "running"
    assert raw_logs[stale_raw_id].processed_at is None
    assert raw_logs[fresh_raw_id].processed_at is None


def test_cli_process_consumes_all_pending_l0_jobs(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: cli process should create error memory",
        process_now=False,
    )

    output = run_cli(monkeypatch, capsys, ["--db", str(db_path), "process"])
    result = json.loads(output)
    verifier = MemoryService(db_path)
    raw_log = verifier.store.list_raw_logs(project_id="project-a")[0]
    memories = verifier.store.list_memories("project-a", include_global_l2=False)

    assert result["created"] == 2
    assert verifier.store.count_jobs("project-a", status="done") == 1
    assert raw_log.processed_at is not None
    assert any(memory.layer == Layer.L3 for memory in memories)


def test_cli_process_job_runs_schedulable_layering_once(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: cli process job should create error memory",
        process_now=False,
    )

    output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "process-job", "--iterations", "1", "--interval", "0"],
    )
    reports = json.loads(output)
    verifier = MemoryService(db_path)

    assert reports == [{"created": 2}]
    assert verifier.store.count_jobs("project-a", status="done") == 1
    assert any(memory.layer == Layer.L3 for memory in verifier.store.list_memories("project-a"))


def test_layering_job_runner_processes_pending_l0_jobs(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "user",
        "Bug: scheduled worker should create error memory",
        process_now=False,
    )
    runner = LayeringJobRunner(service, interval_seconds=0)

    reports = runner.run_iterations(2)

    assert reports == [{"created": 2}, {"created": 0}]
    assert service.store.count_jobs("project-a", status="done") == 1
    assert any(memory.layer == Layer.L3 for memory in service.store.list_memories("project-a"))


def test_project_can_rebuild_derived_memories_from_l0_without_deleting_l3(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "assistant", "Solution: rebuild working memory", process_now=True)
    service.append_conversation("project-a", "conv-2", "user", "Bug: rebuild should preserve error", process_now=True)
    service.store.delete_project_derived_memories("project-a", [Layer.L1, Layer.L2])

    report = service.rebuild_project_from_l0("project-a")
    memories = service.store.list_memories("project-a", include_global_l2=False)

    assert report["created"] >= 2
    assert any(memory.layer == Layer.L1 and "rebuild working" in memory.body for memory in memories)
    assert any(memory.layer == Layer.L3 and "rebuild should preserve error" in memory.body for memory in memories)
    events = service.store.list_governance_events("project-a")
    assert events[-1]["event_type"] == "rebuild_project_from_l0"
    assert events[-1]["metadata"]["created"] == report["created"]


def test_cli_rebuild_reconstructs_project_memory_from_l0(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation("project-a", "conv-1", "assistant", "Solution: cli rebuild working memory", process_now=True)
    service.append_conversation("project-a", "conv-2", "user", "Bug: cli rebuild should preserve error", process_now=True)
    service.store.delete_project_derived_memories("project-a", [Layer.L1, Layer.L2])

    output = run_cli(monkeypatch, capsys, ["--db", str(db_path), "rebuild", "--project", "project-a"])
    report = json.loads(output)
    verifier = MemoryService(db_path)
    memories = verifier.store.list_memories("project-a", include_global_l2=False)
    events = verifier.store.list_governance_events("project-a")

    assert report["created"] >= 2
    assert any(memory.layer == Layer.L1 and "cli rebuild working" in memory.body for memory in memories)
    assert any(memory.layer == Layer.L3 and "cli rebuild should preserve error" in memory.body for memory in memories)
    assert events[-1]["event_type"] == "rebuild_project_from_l0"
    assert events[-1]["metadata"]["created"] == report["created"]


def test_reflection_writes_auditable_summary_report(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation(
        "project-a",
        "conv-1",
        "assistant",
        "Solution: isolate database writes",
        process_now=True,
    )

    report = service.run_reflection("project-a")
    reports = service.list_reflection_reports("project-a")

    assert report["report_id"] == reports[0]["id"]
    assert "Reflection summary for project project-a" in reports[0]["summary"]
    assert reports[0]["metrics"]["promoted"] == report["promoted"]


def test_cli_reflect_writes_project_scoped_report(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation("project-a", "conv-1", "assistant", "Solution: cli reflect auth rule", process_now=True)
    service.append_conversation("project-b", "conv-2", "assistant", "Solution: cli reflect billing rule", process_now=True)

    reflect_output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "reflect", "--project", "project-a"],
    )
    report = json.loads(reflect_output)
    reports_output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "reports", "--project", "project-a"],
    )
    reports = json.loads(reports_output)

    assert report["report_id"] == reports[0]["id"]
    assert reports[0]["project_id"] == "project-a"
    assert "Reflection summary for project project-a" in reports[0]["summary"]
    assert "project-b" not in reports_output


def test_cli_reflect_job_runs_scheduled_reflection_for_projects(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation("project-a", "conv-1", "assistant", "Solution: cli reflect job auth rule", process_now=True)
    service.append_conversation("project-b", "conv-2", "assistant", "Solution: cli reflect job billing rule", process_now=True)

    output = run_cli(
        monkeypatch,
        capsys,
        [
            "--db",
            str(db_path),
            "reflect-job",
            "--project",
            "project-a",
            "--project",
            "project-b",
            "--iterations",
            "1",
            "--interval",
            "0",
        ],
    )
    reports = json.loads(output)
    verifier = MemoryService(db_path)

    assert len(reports) == 1
    assert set(reports[0]) == {"project-a", "project-b"}
    assert verifier.list_reflection_reports("project-a")
    assert verifier.list_reflection_reports("project-b")


def test_reflection_job_runner_runs_multiple_projects_once(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "assistant", "Solution: isolate auth writes", process_now=True)
    service.append_conversation("project-b", "conv-2", "assistant", "Solution: isolate billing writes", process_now=True)
    runner = ReflectionJobRunner(service, ["project-a", "project-b"], interval_seconds=0)

    reports = runner.run_iterations(1)

    assert len(reports) == 1
    assert set(reports[0]) == {"project-a", "project-b"}
    assert service.list_reflection_reports("project-a")
    assert service.list_reflection_reports("project-b")


def test_l3_error_memory_is_not_deleted_or_decayed_by_reflection(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: duplicate one",
        body="error: same outage\ncontext: deploy\nroot_cause: bad flag\nfix: rollback\nanti_pattern: blind deploy",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
        weight=3.0,
    )
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: duplicate two",
        body="error: same outage\ncontext: deploy\nroot_cause: bad flag\nfix: rollback\nanti_pattern: blind deploy",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
        weight=3.0,
    )

    service.run_reflection("project-a")
    errors = service.store.list_memories("project-a", layers=[Layer.L3], include_global_l2=False)

    assert len(errors) == 2
    assert all(error.weight == 3.0 for error in errors)


def test_delete_memories_never_deletes_l3_even_if_requested(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    memory_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L3,
        title="Error: protected",
        body="error: protected\ncontext: test\nroot_cause: guard\nfix: none\nanti_pattern: deleting L3",
        tags=["error", "anti-pattern"],
        memory_type="error",
        source_log_ids=[],
    )

    deleted = service.store.delete_memories([memory_id], allowed_layers=[Layer.L3])

    assert deleted == 0
    assert service.store.get_memory(memory_id) is not None


def test_delete_memories_does_not_delete_l2_by_default(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    memory_id = service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L2,
        title="Knowledge: default delete protects L2",
        body="Stable knowledge should require explicit delete scope.",
        tags=["knowledge"],
        memory_type="knowledge",
        source_log_ids=[],
    )

    default_deleted = service.store.delete_memories([memory_id])
    explicit_deleted = service.store.delete_memories([memory_id], allowed_layers=[Layer.L2])

    assert default_deleted == 0
    assert explicit_deleted == 1


def test_retrieval_uses_injected_embedding_backend(tmp_path):
    backend = CountingEmbeddingBackend()
    service = MemoryService(tmp_path / "memory.db", embedding_backend=backend)
    service.append_conversation("project-a", "conv-1", "assistant", "Solution: alpha matching token", process_now=True)

    results = service.retrieve("project-a", "alpha")

    assert results
    assert backend.calls > 0


def test_reflection_uses_injected_embedding_backend_for_clustering(tmp_path):
    backend = CountingEmbeddingBackend()
    service = MemoryService(tmp_path / "memory.db", embedding_backend=backend)
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: first",
        body="same cluster",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
    )
    service.store.upsert_memory(
        project_id="project-a",
        layer=Layer.L1,
        title="Working: second",
        body="same cluster",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
    )

    service.run_reflection("project-a")

    assert backend.calls > 0


def test_dense_embedding_vectors_are_supported():
    backend = DenseTestEmbeddingBackend()

    score = cosine_similarity("left", "right", backend=backend)

    assert 0.49 < score < 0.51


def test_cached_embedding_backend_reuses_vectors():
    backend = CountingEmbeddingBackend()
    cached = CachedEmbeddingBackend(backend)

    cached.similarity("alpha", "alpha")
    cached.similarity("alpha", "alpha")

    assert backend.calls == 0
    assert backend.embed_calls == 1
    assert len(cached._cache) == 1


def test_project_audit_export_is_project_scoped(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    service.append_conversation("project-a", "conv-1", "user", "Bug: auth export check", process_now=True)
    service.append_conversation("project-b", "conv-2", "user", "Bug: billing must stay private", process_now=True)

    audit = service.export_project_audit("project-a")

    assert audit["project_id"] == "project-a"
    assert len(audit["raw_logs"]) == 1
    assert "auth export" in audit["raw_logs"][0]["content"]
    assert "billing" not in json_dump(audit)


def test_cli_export_output_is_project_scoped(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"
    service = MemoryService(db_path)
    service.append_conversation("project-a", "conv-1", "user", "Bug: cli auth export check", process_now=True)
    service.append_conversation("project-b", "conv-2", "user", "Bug: cli billing export check", process_now=False)

    output = run_cli(
        monkeypatch,
        capsys,
        ["--db", str(db_path), "export", "--project", "project-a"],
    )
    audit = json.loads(output)

    assert audit["project_id"] == "project-a"
    assert all(row["project_id"] == "project-a" for row in audit["raw_logs"])
    assert all(row["project_id"] == "project-a" for row in audit["memories"])
    assert all(row["project_id"] == "project-a" for row in audit["processing_jobs"])
    assert "cli auth export check" in output
    assert "cli billing export check" not in output


def test_project_audit_export_includes_memory_versions(tmp_path):
    service = MemoryService(tmp_path / "memory.db")
    message = "Solution: versioned audit trail"
    service.append_conversation("project-a", "conv-1", "assistant", message, process_now=True)
    service.append_conversation("project-a", "conv-2", "assistant", message, process_now=True)
    service.store.upsert_memory(
        project_id="project-b",
        layer=Layer.L1,
        title="Working: private other project",
        body="private other project version",
        tags=["working"],
        memory_type="solution",
        source_log_ids=[],
    )

    audit = service.export_project_audit("project-a")

    assert "memory_versions" in audit
    assert [version["version"] for version in audit["memory_versions"]] == [1, 2]
    assert not any("other project" in version["body"] for version in audit["memory_versions"])


def test_health_status_reports_required_tables_and_foreign_keys(tmp_path):
    service = MemoryService(tmp_path / "memory.db")

    status = service.health_status()

    assert status["ok"] is True
    assert status["integrity_check"] == "ok"
    assert status["foreign_keys_enabled"] is True
    assert status["missing_tables"] == []
    assert "raw_logs" in status["required_tables"]
    assert "memories" in status["row_counts"]


def test_cli_health_outputs_database_status(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "memory.db"

    output = run_cli(monkeypatch, capsys, ["--db", str(db_path), "health"])
    status = json.loads(output)

    assert status["ok"] is True
    assert status["foreign_keys_enabled"] is True
    assert status["missing_tables"] == []
    assert "raw_logs" in status["required_tables"]
    assert "memories" in status["row_counts"]


def json_dump(value):
    import json

    return json.dumps(value, ensure_ascii=False)
