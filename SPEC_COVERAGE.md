# 规范覆盖

## 已实现

- L0 原始日志
  - `raw_logs` 会存储未过滤的对话消息，并包含 `project_id`、`conversation_id`、角色、内容、元数据、时间戳和处理状态。
  - `append` CLI 支持 `--metadata-json`，因此审计元数据会随 L0 行一起保留。
  - `list_raw_logs` 和 `raw-logs` CLI 命令提供按项目范围的 L0 审计访问。
  - 原始日志不会被检索查询，而是作为处理和审计的事实来源。
  - `tools/static_check.js` 强制要求检索和上下文注入代码不能引用原始日志 API。
  - `rebuild_project_from_l0` 可以从 L0 重建项目派生记忆。
  - 重建操作会写入可审计的 `rebuild_project_from_l0` 治理事件。

- L1/L2/L3 分层记忆
  - `memories.layer` 支持 `L1`、`L2` 和 `L3`。
  - `MemoryClassifier` 会把错误路由到 L3，把稳定规则路由到 L2，把问题/调试/代码/临时/解决方案/对话片段路由到 L1。
  - L2 分类覆盖最终结论、标准、规范、设计决策、最佳实践和架构笔记。
  - 一条同时包含错误和修复方案的 L0 记录，会生成 L3 错误记忆和 L1 工作解决方案记忆。

- 项目隔离
  - 所有原始日志和项目记忆都绑定到 `project_id`。
  - 公共原始日志列表路径需要显式提供 `project_id`。
  - 检索始终通过项目过滤器进行查询。
  - 全局 L2 通过 `project_id = NULL` 表示；只有全局 L2 可以跨项目边界。
  - `memories` 表和 `MemoryStore.upsert_memory` 会拒绝无项目的 L1/L3 记录。
  - 项目 L2 只能通过显式的治理提升事件变成全局 L2。

- RAG 检索
  - 检索使用可插拔的 `EmbeddingBackend`。
  - 默认后端使用确定性的本地 token 向量和余弦相似度。
  - 支持稠密数值向量。
  - `HttpJsonEmbeddingBackend` 可以通过 HTTP JSON 调用生产嵌入服务。
  - `CachedEmbeddingBackend` 会减少重复的嵌入调用。
  - 排序规则先按层级优先级，再按语义分数、时效性和自适应权重。
  - 支持标签、模块、类型标签、层级和 `memory_type` 过滤。
  - L0 的 `tags`、`module`、`type` 和 `type_tag` 元数据字段会成为供检索过滤使用的记忆标签。

- 上下文注入
  - `ContextBuilder` 会输出所需的章节顺序：
    `[Project Context]`、`[Error Memory - L3]`、`[Knowledge Base - L2]`、`[Working Memory - L1]`、`[Current Task]`。
  - 带结构化错误元数据的 L3 条目会以显式的 `Trigger condition` 和 `Forbidden anti-pattern` 行呈现。
  - 除非使用 `--skip-pending`，`context` CLI 命令会在检索前处理所请求项目的待处理 L0 分层任务。

- 自动分层
  - `LayeringProcessor` 会把未处理的 L0 行转换为结构化记忆项。
  - 每次 L0 append 都会创建一条持久化的 `processing_jobs` 记录。
  - 服务默认使用持久化待处理任务，并支持通过 `process_now=True` 立即处理。
  - CLI 默认使用持久化待处理任务，并支持通过 `--process-now` 立即处理。
  - 通过 `enqueue_async=True` 可以显式启用进程内后台队列处理；CLI 的 `--enqueue-worker` 和 `--async-process` 会在命令退出前清空已入队的工作。
  - `list_processing_jobs` 和 `jobs` CLI 命令公开按项目范围的任务状态。
  - `process_pending_memories` 和 `process` CLI 命令会消费待处理分层任务。
  - `LayeringJobRunner` 和 `process-job` CLI 命令提供可调度的外部 L0 分层。
  - 待处理任务处理会把失败项目的任务标记为失败，并继续处理其他待处理项目。
  - 失败的 L0 分层任务可以通过 `retry_failed_layering_jobs` 和 `retry-failed` CLI 命令重置为待处理。
  - 处于运行中且已过时的 L0 分层任务可以通过 `reset_stale_running_layering_jobs` 和 `reset-stale-running` CLI 命令重置为待处理。
  - `rebuild` CLI 命令会从 L0 重建项目派生记忆。

- 去重与历史
  - upsert 会合并重复的项目/层级/类型/标题记忆。
  - 每次写入都会在 `memory_versions` 中记录一行。
  - 项目审计导出包含 `memory_versions`。
  - 反思聚类可以合并非常相似的记忆并删除重复行。

- 反思引擎
  - 将频繁检索到的 L1 记忆提升到 L2。
  - 在从重复、合并或多来源 L1 记忆合成稳定 L2 规则之前，先对相似的 L1/L2 记忆进行聚类。
  - 对相似性聚类使用同样可插拔的嵌入后端。
  - 衰减 L1 权重。
  - 只删除过期且低价值的 L1 行。
  - 生命周期变更仅作用于所请求的 `project_id`。
  - 写入带有计数和工程摘要文本的可审计反思报告。
  - `ReflectionJobRunner` 和 `reflect-job` CLI 命令提供可调度的离线执行。

- 错误驱动学习
  - L3 错误记录包含 `error`、`context`、`trigger_condition`、`root_cause`、`fix` 和 `anti_pattern` 字段。
  - 同样的字段也会以机器可读的 `metadata.error_memory` 对象形式持久化。
  - 源文本中的显式字段会被提取到结构化错误正文中。
  - L3 拥有最高检索优先级，并会在注入上下文中最先出现，同时突出反模式。

- 生命周期管理
  - L1 只有在过期后才会衰减并可被清理。
  - L1 清理只删除同时满足过期和低价值的记忆。
  - 反思衰减和清理按项目范围执行。
  - 检索访问会增加权重并更新时效性。
  - L2 保持稳定，不会被默认删除路径移除。
  - L3 不会被衰减、聚类删除或清理路径删除。
  - 即使显式请求 L3，`delete_memories` 也绝不会删除它。
  - 每个 SQLite 连接都会启用外键，因此版本历史会遵循记忆删除规则。

- 运行时集成
  - `CodexMemoryRuntime.record_conversation` 会把一次轮次中的每条消息都捕获到 L0。
  - `CodexMemoryRuntime.prepare_answer_context` 是答案生成前的 RAG 钩子。
  - `prepare_answer_context` 会在检索和上下文注入之前处理所请求项目的待处理 L0 分层任务。

- 治理与审计
  - `promote_to_global_l2` 需要项目所有权、L2 层级、reviewer 和 reason。
  - `governance_events` 会记录全局 L2 提升决策。
  - `governance_events` 会记录 L0 重建操作。
  - `raw-logs` 提供按项目范围的原始日志，便于直接审计和分析。
  - `export_project_audit` 会导出按项目范围的原始日志、记忆、记忆版本、任务、反思报告和治理事件。

- 运行健康
  - `MemoryService.health_status` 会报告 SQLite 完整性、外键约束、所需表、缺失表和行数。
  - `health` CLI 命令会以 JSON 形式输出相同状态。

## 已知后续工作

- 为生产中使用的嵌入服务，在 `HttpJsonEmbeddingBackend` 周围添加面向具体提供方的辅助工具。
- 为没有显式包含根因、修复和反模式的日志，添加可选的 LLM 辅助分类。
- 增加更丰富的管理工具，用于记忆审查、手动提升和全局 L2 治理。
