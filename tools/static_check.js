const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");

const requiredFiles = [
  "pyproject.toml",
  "README.md",
  "SPEC_COVERAGE.md",
  "REQUIREMENTS_AUDIT.md",
  "src/codex_memory/__init__.py",
  "src/codex_memory/classifier.py",
  "src/codex_memory/cli.py",
  "src/codex_memory/context.py",
  "src/codex_memory/embedding.py",
  "src/codex_memory/jobs.py",
  "src/codex_memory/models.py",
  "src/codex_memory/processor.py",
  "src/codex_memory/reflection.py",
  "src/codex_memory/retrieval.py",
  "src/codex_memory/runtime.py",
  "src/codex_memory/service.py",
  "src/codex_memory/storage.py",
  "tests/test_memory_system.py",
];

const requiredMarkers = {
  "pyproject.toml": [
    "面向 Codex 智能体的项目级分层记忆与 RAG 上下文构建器。",
  ],
  "src/codex_memory/storage.py": [
    "CREATE TABLE IF NOT EXISTS raw_logs",
    "CREATE TABLE IF NOT EXISTS processing_jobs",
    "CREATE TABLE IF NOT EXISTS memories",
    "CREATE TABLE IF NOT EXISTS memory_versions",
    "CREATE TABLE IF NOT EXISTS reflection_reports",
    "CREATE TABLE IF NOT EXISTS governance_events",
    "CHECK (project_id IS NOT NULL OR layer = 'L2')",
    "PRAGMA foreign_keys = ON",
    "PRAGMA integrity_check",
    "REQUIRED_TABLES",
    "def health_status",
    "def list_raw_logs(self, project_id: str",
    "list_pending_layering_projects",
    "complete_jobs",
    "retry_failed_layering_jobs",
    "reset_stale_running_layering_jobs",
    "list_job_raw_log_ids",
    "list_raw_logs_by_ids",
    "allowed_layers",
    "allowed_layers or [Layer.L1]",
    "if layer != Layer.L3",
    "add_governance_event",
    "list_governance_events",
    "decay_stale_l1",
    "unused_days: int = 30",
    "updated_at <= datetime('now', ?)",
    "project_id: str | None = None",
    "list_memory_versions",
    "idx_memory_versions_memory",
    "delete_project_derived_memories",
    "only L2 knowledge can be stored without project_id",
    "project_id TEXT NOT NULL",
    "project_id TEXT",
  ],
  "src/codex_memory/classifier.py": [
    "Layer.L3",
    "Layer.L1",
    "final conclusion",
    "standard",
    "design",
    "root_cause",
    "anti_pattern",
    "trigger_condition",
    "error_memory",
    "DEBUG_WORDS",
    "TEMPORARY_WORDS",
    "_working_type",
    "metadata.get(\"module\")",
    "metadata.get(\"type\")",
    "_error_fields",
    "_extract_field",
    "\\u53cd\\u6a21\\u5f0f",
  ],
  "src/codex_memory/embedding.py": [
    "class EmbeddingBackend",
    "class LocalTokenEmbeddingBackend",
    "class HttpJsonEmbeddingBackend",
    "class CachedEmbeddingBackend",
    "def similarity",
    "cosine_similarity",
  ],
  "src/codex_memory/retrieval.py": [
    "embedding_backend",
    "modules",
    "type_tags",
    "LAYER_PRIORITY",
    "result.priority_score",
    "memory_types",
  ],
  "src/codex_memory/context.py": [
    "[Project Context]",
    "[Error Memory - L3]",
    "[Knowledge Base - L2]",
    "[Working Memory - L1]",
    "[Current Task]",
    "_format_error_group",
    "Trigger condition",
    "Forbidden anti-pattern",
  ],
  "src/codex_memory/runtime.py": [
    "record_conversation",
    "prepare_answer_context",
    "process_pending",
    "process_project_pending_memories",
    "enqueue_async",
    "modules",
    "type_tags",
    "layers",
    "memory_types",
  ],
  "src/codex_memory/service.py": [
    "process_now: bool = False",
    "promote_to_global_l2",
    "export_project_audit",
    "rebuild_project_from_l0",
    "rebuild derived memories from L0 source of truth",
    "health_status",
    "list_raw_logs",
    "list_processing_jobs",
    "process_project_pending_memories",
    "retry_failed_layering_jobs",
    "reset_stale_running_layering_jobs",
    "memory_versions",
    "enqueue_async",
    "drain_async_processor",
    "modules",
    "type_tags",
    "layers",
    "memory_types",
  ],
  "src/codex_memory/cli.py": [
    "promote-global",
    "--process-now",
    "--async-process",
    "--metadata-json",
    "parse_json_object",
    "export",
    "reflect-job",
    "process-job",
    "raw-logs",
    "jobs",
    "retry-failed",
    "reset-stale-running",
    "rebuild",
    "health",
    "--enqueue-worker",
    "--module",
    "--tag-type",
    "--layer",
    "--type",
    "--skip-pending",
    "process_project_pending_memories(args.project)",
  ],
  "src/codex_memory/processor.py": [
    "mark_layering_jobs_running",
    "process_pending",
    "list_job_raw_log_ids",
    "list_raw_logs_by_ids",
    "retry_failed",
    "reset_stale_running",
    "except Exception",
    "def drain",
    "task_done",
    "rebuild_project_from_l0",
    "complete_jobs",
    "item.metadata",
  ],
  "src/codex_memory/jobs.py": [
    "class LayeringJobRunner",
    "class ReflectionJobRunner",
    "run_once",
    "run_iterations",
    "run_forever",
  ],
  "src/codex_memory/reflection.py": [
    "promote_frequent_l1",
    "synthesize_stable_rules",
    "Synthesized stable rules",
    "cluster_similar",
    "if item.layer != Layer.L3",
    "decay_stale_l1",
    "project_id=project_id",
    "generate_summary",
    "add_reflection_report",
  ],
  "tests/test_memory_system.py": [
    "test_l0_raw_log_is_preserved_and_layered",
    "test_l0_raw_log_preserves_metadata_for_audit",
    "test_service_raw_log_listing_is_project_scoped_for_audit",
    "test_cli_raw_logs_output_is_project_scoped",
    "test_cli_append_metadata_json_preserves_l0_metadata",
    "memory.memory_type == \"solution\"",
    "test_l0_raw_log_does_not_directly_participate_in_retrieval_or_context",
    "test_project_isolation_blocks_l1_and_l3_cross_project_retrieval",
    "test_l3_error_memory_is_first_in_retrieval_even_when_l1_has_stronger_semantic_match",
    "test_l3_error_memory_injects_forbidden_anti_pattern",
    "test_cli_context_includes_global_l2_without_other_project_private_memory",
    "test_context_layer_filter_limits_injected_memory",
    "test_context_memory_type_filter_limits_injected_memory",
    "test_l1_working_memory_covers_problem_debug_code_and_temporary_types",
    "test_general_conversation_becomes_low_weight_l1_or_is_ignored",
    "test_only_l2_can_be_global_without_project_id",
    "test_layer_filter_limits_retrieval",
    "test_parse_layer_accepts_lowercase_cli_values",
    "test_parse_json_object_accepts_cli_metadata",
    "test_parse_json_object_rejects_non_object_metadata",
    "test_cli_context_processes_pending_l0_before_output",
    "test_cli_context_skip_pending_keeps_l0_out_of_injected_memory",
    "test_cli_context_applies_module_type_and_memory_type_filters",
    "test_cli_context_layer_filter_limits_injected_memory",
    "test_cli_context_injects_structured_error_memory",
    "test_cli_retrieve_applies_module_type_and_memory_type_filters",
    "test_cli_retrieve_layer_filter_limits_results",
    "test_cli_append_async_process_enqueues_worker",
    "test_cli_append_enqueue_worker_drains_layering_before_exit",
    "test_cli_append_process_now_layers_immediately",
    "test_module_and_type_tag_filters_limit_retrieval",
    "test_metadata_module_type_and_tags_participate_in_retrieval_filters",
    "error.metadata[\"error_memory\"]",
    "test_error_memory_extracts_chinese_structured_fields",
    "test_project_l2_can_be_promoted_to_global_l2_with_governance_event",
    "test_cli_promote_global_creates_governed_cross_project_l2",
    "test_l2_knowledge_covers_final_conclusion_standard_and_design",
    "test_global_l2_promotion_rejects_cross_project_memory",
    "test_runtime_records_every_message_as_l0",
    "test_runtime_can_leave_layering_jobs_pending_for_external_worker",
    "test_runtime_prepare_answer_context_processes_project_pending_memories_before_rag",
    "test_processing_job_listing_is_project_scoped",
    "test_cli_jobs_output_is_project_scoped",
    "test_l0_append_creates_durable_layering_job",
    "test_failed_layering_jobs_can_be_retried",
    "test_cli_retry_failed_resets_project_failed_jobs",
    "test_pending_processing_does_not_consume_failed_job_raw_logs",
    "test_pending_processor_continues_after_project_failure",
    "test_stale_running_layering_jobs_can_be_reset_and_processed",
    "test_cli_reset_stale_running_resets_only_timed_out_jobs",
    "test_cli_process_consumes_all_pending_l0_jobs",
    "test_cli_process_job_runs_schedulable_layering_once",
    "test_layering_job_runner_processes_pending_l0_jobs",
    "test_project_can_rebuild_derived_memories_from_l0_without_deleting_l3",
    "test_cli_rebuild_reconstructs_project_memory_from_l0",
    "rebuild_project_from_l0",
    "test_reflection_writes_auditable_summary_report",
    "test_cli_reflect_writes_project_scoped_report",
    "test_reflection_synthesizes_stable_rules_from_repeated_l1",
    "test_reflection_synthesizes_stable_rules_after_similarity_clustering",
    "test_reflection_job_runner_runs_multiple_projects_once",
    "test_cli_reflect_job_runs_scheduled_reflection_for_projects",
    "test_l3_error_memory_is_not_deleted_or_decayed_by_reflection",
    "test_delete_memories_never_deletes_l3_even_if_requested",
    "test_delete_memories_does_not_delete_l2_by_default",
    "test_reflection_decays_only_stale_l1",
    "test_reflection_deletes_only_stale_low_value_l1",
    "test_reflection_lifecycle_changes_are_project_scoped",
    "test_retrieval_prefers_newer_memory_when_scores_are_otherwise_similar",
    "test_retrieval_increases_access_count_and_weight",
    "test_retrieval_uses_injected_embedding_backend",
    "test_dense_embedding_vectors_are_supported",
    "test_project_audit_export_is_project_scoped",
    "test_cli_export_output_is_project_scoped",
    "test_project_audit_export_includes_memory_versions",
    "test_health_status_reports_required_tables_and_foreign_keys",
    "test_cli_health_outputs_database_status",
  ],
};

const mojibakeMarkers = [
  "\u9286",
  "\u9207",
  "\u93c9",
  "\u6d93",
  "\u93c8",
  "\u6d91",
  "\u93b9",
  "\u95c2",
  "\u59ab",
  "\u6fb6",
];

const forbiddenMarkers = {
  "src/codex_memory/retrieval.py": [
    "raw_logs",
    "list_raw_logs",
    "list_raw_logs_by_ids",
  ],
  "src/codex_memory/context.py": [
    "raw_logs",
    "list_raw_logs",
    "list_raw_logs_by_ids",
  ],
};

const invariantChecks = [
  {
    relative: "src/codex_memory/retrieval.py",
    name: "layer priority order",
    check: (text) => /LAYER_PRIORITY\s*=\s*\{Layer\.L3:\s*3\.0,\s*Layer\.L2:\s*2\.0,\s*Layer\.L1:\s*1\.0\}/.test(text),
  },
  {
    relative: "src/codex_memory/classifier.py",
    name: "structured error memory fields",
    check: (text) => ["error", "context", "trigger_condition", "root_cause", "fix", "anti_pattern"].every((field) =>
      text.includes(`"${field}"`)
    ),
  },
  {
    relative: "src/codex_memory/storage.py",
    name: "projectless memory limited to L2",
    check: (text) =>
      text.includes("CHECK (project_id IS NOT NULL OR layer = 'L2')") &&
      text.includes("if project_id is None and layer != Layer.L2"),
  },
  {
    relative: "src/codex_memory/service.py",
    name: "append defaults to durable pending jobs",
    check: (text) => text.includes("process_now: bool = False"),
  },
  {
    relative: "src/codex_memory/reflection.py",
    name: "reflection clusters before stable rule synthesis",
    check: (text) =>
      text.indexOf("merged = self.cluster_similar(project_id)") >= 0 &&
      text.indexOf("merged = self.cluster_similar(project_id)") <
        text.indexOf("synthesized = self.synthesize_stable_rules(project_id)"),
  },
];

let failures = 0;

for (const relative of requiredFiles) {
  const file = path.join(root, relative);
  if (!fs.existsSync(file)) {
    console.error(`missing file: ${relative}`);
    failures += 1;
  }
}

for (const [relative, markers] of Object.entries(requiredMarkers)) {
  const file = path.join(root, relative);
  if (!fs.existsSync(file)) {
    continue;
  }
  const text = fs.readFileSync(file, "utf8");
  for (const marker of markers) {
    if (!text.includes(marker)) {
      console.error(`missing marker in ${relative}: ${marker}`);
      failures += 1;
    }
  }
}

for (const [relative, markers] of Object.entries(forbiddenMarkers)) {
  const file = path.join(root, relative);
  if (!fs.existsSync(file)) {
    continue;
  }
  const text = fs.readFileSync(file, "utf8");
  for (const marker of markers) {
    if (text.includes(marker)) {
      console.error(`forbidden marker in ${relative}: ${marker}`);
      failures += 1;
    }
  }
}

for (const invariant of invariantChecks) {
  const file = path.join(root, invariant.relative);
  if (!fs.existsSync(file)) {
    continue;
  }
  const text = fs.readFileSync(file, "utf8");
  if (!invariant.check(text)) {
    console.error(`invariant failed in ${invariant.relative}: ${invariant.name}`);
    failures += 1;
  }
}

for (const relative of requiredFiles.filter((name) => name.endsWith(".md") || name.endsWith(".py") || name.endsWith(".toml") || name.endsWith(".js"))) {
  const text = fs.readFileSync(path.join(root, relative), "utf8");
  if (text.includes("\uFFFD")) {
    console.error(`replacement character found in ${relative}`);
    failures += 1;
  }
  for (const marker of mojibakeMarkers) {
    if (text.includes(marker)) {
      console.error(`possible mojibake marker found in ${relative}: ${marker}`);
      failures += 1;
    }
  }
  if (relative.endsWith(".py")) {
    const lines = text.split(/\r?\n/);
    lines.forEach((line, index) => {
      if (!line.includes("help=")) {
        return;
      }
      const quoteCount = (line.match(/"/g) || []).length;
      if (quoteCount % 2 !== 0) {
        console.error(`unbalanced double quote on help line ${relative}:${index + 1}`);
        failures += 1;
      }
    });
  }
}

if (failures > 0) {
  process.exitCode = 1;
} else {
  console.log("static_check: ok");
}
