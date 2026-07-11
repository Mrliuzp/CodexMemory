# 需求审计

本文档将系统目标映射到具体的实现证据。

## L0 原始日志

- 证据：`src/codex_memory/storage.py`
- 模式：`raw_logs`
- API：`MemoryStore.append_raw_log`、`MemoryStore.list_raw_logs`
- 服务/CLI 审计：`MemoryService.list_raw_logs`、`codex-memory raw-logs --project <project_id>`
- CLI 元数据：`codex-memory append --metadata-json <object>` 会在 L0 上保留结构化审计元数据。
- 元数据审计：原始日志列表和项目审计导出都会保留原始的 L0 元数据对象。
- 重建：`MemoryService.rebuild_project_from_l0`、`codex-memory rebuild`
- 重建审计：`governance_events.event_type = rebuild_project_from_l0`
- RAG 边界：在进入 `memories` 之前，原始日志不会被检索或上下文注入读取。
- 静态边界检查：如果 `retrieval.py` 或 `context.py` 引用了原始日志 API，`tools/static_check.js` 会失败。
- 测试：`test_l0_raw_log_is_preserved_and_layered`、`test_l0_raw_log_preserves_metadata_for_audit`、`test_service_raw_log_listing_is_project_scoped_for_audit`、`test_cli_raw_logs_output_is_project_scoped`、`test_cli_append_metadata_json_preserves_l0_metadata`、`test_parse_json_object_accepts_cli_metadata`、`test_parse_json_object_rejects_non_object_metadata`、`test_l0_raw_log_does_not_directly_participate_in_retrieval_or_context`、`test_runtime_records_every_message_as_l0`
- 备注：原始日志列表需要显式提供 `project_id`。

## 分层记忆

- 证据：`src/codex_memory/models.py`、`src/codex_memory/classifier.py`、`src/codex_memory/processor.py`
- 层级：`Layer.L1`、`Layer.L2`、`Layer.L3`
- L2 覆盖范围：最终结论、标准、规范、设计决策、最佳实践和架构笔记都会路由到 `memory_type = knowledge`。
- 流程：L0 行 -> `processing_jobs` -> `LayeringProcessor` -> `memories`
- 异步默认值：`MemoryService.append_conversation` 默认 `process_now=False`，因此 L0 写入会创建持久化的待处理任务。
- 显式处理：`process_now=True` 或 `codex-memory append --process-now` 会立即清空当前项目的待处理分层任务。
- 异步控制：`enqueue_async=True` 会为长期运行的环境启动进程内 worker；`codex-memory append --enqueue-worker` 和 `codex-memory append --async-process` 会在命令退出前清空已入队的工作。
- L1 覆盖范围：问题描述、调试笔记、代码片段、临时结论和解决方案都会作为不同的工作记忆 `memory_type` 保存。
- 多标签分层：一条同时包含 bug 记录和修复方案的日志，会同时创建 L3 错误记忆和 L1 解决方案记忆。
- 测试：`test_l0_raw_log_is_preserved_and_layered`、`test_l0_append_creates_durable_layering_job`、`test_cli_append_process_now_layers_immediately`、`test_cli_append_async_process_enqueues_worker`、`test_cli_append_enqueue_worker_drains_layering_before_exit`、`test_l1_working_memory_covers_problem_debug_code_and_temporary_types`、`test_general_conversation_becomes_low_weight_l1_or_is_ignored`、`test_l2_knowledge_covers_final_conclusion_standard_and_design`

## 项目隔离

- 证据：`src/codex_memory/storage.py`、`src/codex_memory/retrieval.py`
- 原始日志：`list_raw_logs(project_id=...)`
- 检索：`list_memories(project_id=...)`
- 全局例外：仅 `project_id IS NULL AND layer = 'L2'`
- 存储不变量：`CHECK (project_id IS NOT NULL OR layer = 'L2')`
- 静态不变量：`tools/static_check.js` 验证 schema 和写入保护只允许无项目的 L2。
- 测试：`test_l0_raw_log_listing_requires_project_scope`、`test_project_isolation_blocks_l1_and_l3_cross_project_retrieval`、`test_global_l2_is_available_without_exposing_other_project_memory`、`test_cli_context_includes_global_l2_without_other_project_private_memory`、`test_only_l2_can_be_global_without_project_id`

## 全局 L2 治理

- 证据：`src/codex_memory/service.py`、`src/codex_memory/storage.py`
- API：`MemoryService.promote_to_global_l2`
- 治理日志：`governance_events`
- 测试：`test_project_l2_can_be_promoted_to_global_l2_with_governance_event`、`test_cli_promote_global_creates_governed_cross_project_l2`、`test_global_l2_promotion_rejects_cross_project_memory`

## RAG 检索

- 证据：`src/codex_memory/retrieval.py`、`src/codex_memory/embedding.py`
- 嵌入：可插拔 `EmbeddingBackend`
- 本地后端：`LocalTokenEmbeddingBackend`
- 生产桥接：`HttpJsonEmbeddingBackend`
- 缓存：`CachedEmbeddingBackend`
- 排序：先按层级优先级，再按语义分数、时效性分数和自适应权重
- 静态不变量：`tools/static_check.js` 验证 L3 > L2 > L1 的优先级值。
- 元数据过滤器：L0 的 `metadata.tags`、`metadata.module` 和 `metadata.type` 会提升为检索标签。
- 测试：`test_memory_type_filter_limits_retrieval`、`test_layer_filter_limits_retrieval`、`test_parse_layer_accepts_lowercase_cli_values`、`test_cli_retrieve_applies_module_type_and_memory_type_filters`、`test_cli_retrieve_layer_filter_limits_results`、`test_module_and_type_tag_filters_limit_retrieval`、`test_metadata_module_type_and_tags_participate_in_retrieval_filters`、`test_l3_error_memory_is_first_in_retrieval_even_when_l1_has_stronger_semantic_match`、`test_retrieval_prefers_newer_memory_when_scores_are_otherwise_similar`、`test_retrieval_uses_injected_embedding_backend`、`test_dense_embedding_vectors_are_supported`、`test_cached_embedding_backend_reuses_vectors`

## 上下文注入

- 证据：`src/codex_memory/context.py`、`src/codex_memory/runtime.py`
- 必须顺序：项目上下文、L3、L2、L1、当前任务
- L3 格式：结构化错误会注入 `Trigger condition` 和 `Forbidden anti-pattern`
- 过滤器：上下文构建支持标签、模块、类型标签、层级和记忆类型。
- 运行时钩子：`CodexMemoryRuntime.prepare_answer_context`
- 答案时分层：`prepare_answer_context` 会在上下文检索之前调用 `MemoryService.process_project_pending_memories`。
- CLI 答案时分层：除非使用 `--skip-pending`，否则 `codex-memory context` 会调用 `process_project_pending_memories`。
- 测试：`test_l3_error_memory_is_prioritized_in_context`、`test_l3_error_memory_injects_forbidden_anti_pattern`、`test_cli_context_injects_structured_error_memory`、`test_cli_context_includes_global_l2_without_other_project_private_memory`、`test_context_layer_filter_limits_injected_memory`、`test_context_memory_type_filter_limits_injected_memory`、`test_runtime_prepare_answer_context_processes_project_pending_memories_before_rag`、`test_cli_context_processes_pending_l0_before_output`、`test_cli_context_skip_pending_keeps_l0_out_of_injected_memory`、`test_cli_context_applies_module_type_and_memory_type_filters`、`test_cli_context_layer_filter_limits_injected_memory`

## 自动分层

- 证据：`src/codex_memory/storage.py`、`src/codex_memory/processor.py`、`src/codex_memory/jobs.py`、`src/codex_memory/cli.py`
- 持久化任务：`processing_jobs`
- 任务可见性：`MemoryService.list_processing_jobs`、`codex-memory jobs`
- 手动 worker 入口：`MemoryService.process_pending_memories`
- 任务载荷范围：待处理过程只消费由标记为运行中的任务所引用的原始日志。
- 失败隔离：`process_pending` 会把失败项目的任务标记为失败，并继续处理其他待处理项目。
- 可调度 worker：`LayeringJobRunner`、`codex-memory process-job`
- 失败恢复：`MemoryService.retry_failed_layering_jobs`、`codex-memory retry-failed`
- 运行中超时恢复：`MemoryService.reset_stale_running_layering_jobs`、`codex-memory reset-stale-running`
- CLI：`codex-memory process`
- 测试：`test_l0_append_creates_durable_layering_job`、`test_processing_job_listing_is_project_scoped`、`test_cli_jobs_output_is_project_scoped`、`test_failed_layering_jobs_can_be_retried`、`test_cli_retry_failed_resets_project_failed_jobs`、`test_pending_processing_does_not_consume_failed_job_raw_logs`、`test_pending_processor_continues_after_project_failure`、`test_stale_running_layering_jobs_can_be_reset_and_processed`、`test_cli_reset_stale_running_resets_only_timed_out_jobs`、`test_cli_process_consumes_all_pending_l0_jobs`、`test_cli_process_job_runs_schedulable_layering_once`、`test_layering_job_runner_processes_pending_l0_jobs`、`test_project_can_rebuild_derived_memories_from_l0_without_deleting_l3`、`test_cli_rebuild_reconstructs_project_memory_from_l0`

## 合并与历史

- 证据：`src/codex_memory/storage.py`、`src/codex_memory/reflection.py`
- 精确重复合并：`MemoryStore.upsert_memory`
- 相似聚类：`ReflectionEngine.cluster_similar`
- 历史：`memory_versions`
- 导出：`MemoryStore.list_memory_versions`、`MemoryService.export_project_audit`
- 测试：`test_duplicate_memories_keep_version_history`

## 反思引擎

- 证据：`src/codex_memory/reflection.py`、`src/codex_memory/jobs.py`
- 提升：`promote_frequent_l1`
- 稳定规则合成：`synthesize_stable_rules`
- 聚类：`cluster_similar`
- 顺序：`cluster_similar` 先于 `synthesize_stable_rules` 运行，因此相似的 L1 记录可以先合并成多来源候选，再进行 L2 合成。
- 清理：`delete_low_value_l1` 只删除过期且低价值的 L1。
- 项目范围：`run(project_id)` 会把 `project_id` 传入衰减和清理流程。
- 报告：`reflection_reports`
- 可调度任务：`ReflectionJobRunner`、`codex-memory reflect-job`
- 测试：`test_reflection_promotes_frequently_used_l1_and_decays_working_memory`、`test_reflection_synthesizes_stable_rules_from_repeated_l1`、`test_reflection_synthesizes_stable_rules_after_similarity_clustering`、`test_reflection_lifecycle_changes_are_project_scoped`、`test_reflection_writes_auditable_summary_report`、`test_cli_reflect_writes_project_scoped_report`、`test_reflection_job_runner_runs_multiple_projects_once`、`test_cli_reflect_job_runs_scheduled_reflection_for_projects`

## 错误驱动学习

- 证据：`src/codex_memory/classifier.py`、`src/codex_memory/context.py`、`src/codex_memory/retrieval.py`
- 结构化字段：`error`、`context`、`trigger_condition`、`root_cause`、`fix`、`anti_pattern`
- 机器可读对象：`metadata.error_memory`
- 优先级：检索会先按 `Layer.L3` 的优先级排序，再排低层级。
- 提示注入：`ContextBuilder` 会渲染 `Forbidden anti-pattern`
- 测试：`test_error_memory_extracts_structured_fields`、`test_error_memory_extracts_chinese_structured_fields`、`test_l3_error_memory_is_prioritized_in_context`、`test_l3_error_memory_injects_forbidden_anti_pattern`、`test_cli_context_injects_structured_error_memory`

## 生命周期

- 证据：`src/codex_memory/storage.py`、`src/codex_memory/reflection.py`
- L1 衰减：`decay_l1`
- 仅对过期 L1 衰减：`decay_stale_l1`
- 按项目范围的反思生命周期：`decay_stale_l1(project_id=...)`、`delete_low_value_l1(project_id=...)`
- 高使用度提升：`increment_access`
- L2 稳定：没有衰减路径，也没有默认删除路径
- L3 永久在线：不参与聚类删除和清理
- 存储层保护：`delete_memories` 会过滤掉 `Layer.L3`
- 完整性：每个 SQLite 连接都会启用 `PRAGMA foreign_keys = ON`
- 测试：`test_reflection_promotes_frequently_used_l1_and_decays_working_memory`、`test_reflection_decays_only_stale_l1`、`test_reflection_deletes_only_stale_low_value_l1`、`test_reflection_lifecycle_changes_are_project_scoped`、`test_retrieval_increases_access_count_and_weight`、`test_delete_memories_does_not_delete_l2_by_default`、`test_l3_error_memory_is_not_deleted_or_decayed_by_reflection`、`test_delete_memories_never_deletes_l3_even_if_requested`

## 验证状态

- 本地检查通过：`node .\tools\static_check.js`
- 运行时检查被阻塞：`python -m pytest`
- Python 阻塞原因：当前 `python.exe` 解析为 Microsoft Store 别名，在这个环境中无法执行。
- 健康 API：`MemoryService.health_status`
- 健康 CLI：`codex-memory health`
- 健康检查：SQLite `PRAGMA integrity_check`、`PRAGMA foreign_keys`、所需表和行数
- 测试：`test_health_status_reports_required_tables_and_foreign_keys`、`test_cli_health_outputs_database_status`

## 审计导出

- 证据：`src/codex_memory/service.py`
- API：`MemoryService.export_project_audit`
- CLI：`codex-memory export --project <project_id>`
- 包含：原始日志、记忆、记忆版本、处理任务、反思报告、治理事件
- 测试：`test_project_audit_export_is_project_scoped`、`test_cli_export_output_is_project_scoped`、`test_project_audit_export_includes_memory_versions`、`test_project_can_rebuild_derived_memories_from_l0_without_deleting_l3`、`test_cli_rebuild_reconstructs_project_memory_from_l0`
