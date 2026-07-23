# codex-memory V1.1 实施状态

更新时间：2026-07-12

## 当前状态

V1.1 纵向能力切片已在 SQLite 和 PostgreSQL 上完整实现并验证。未启用 V1.1 数据库结构时，原有 V1 行为保持不变。

## 已完成并验证

- Append API（V1.1）：事务内写入 L0 与 Outbox，项目级 `event_key` 幂等，哈希冲突返回 409，支持 `occurred_at`、201/200/409 响应和 V1 兼容。
- Outbox 分发器和任务 Worker：幂等分发、领取、租约、心跳、重试退避、死信状态和过期租约恢复。
- 词法检索：项目级/全局作用域模式、层级/类型筛选、确定性 RRF 元数据、检索审计和上下文令牌预算。
- Embedding Profile：确定性本地向量、不可变元数据、分块回填、维度校验和 Profile 隔离。
- 稠密检索：面向 Profile 的 RRF 混合检索，以及带原因说明的词法降级回退。
- 候选策略：不可变 L0 证据校验、作用域校验、默认关闭的发布开关和治理审计。
- ErrorMemoryExtractor：仅用于 shadow、秘密脱敏、提示注入检测、严格 Schema，不直接写入正式记忆。
- Admin API：任务列表/重试、候选记忆（默认隐藏 shadow）、审核/批准/拒绝、回放、Profile 创建/激活/回填、开关更新和扩展健康检查。
- 项目策略服务：功能开关（默认全部关闭）、1/10/50/100% Canary、保留上一个生效版本的回滚机制，以及每次变更的审计。
- 生产任务处理器：将 `message.appended.v1` 路由到候选记忆创建，进行处理器错误分类（永久失败/可重试），并提供 `run_v11_once` 入口。
- Provider 适配器：带远程策略检查的 `embed_documents`、允许的 Provider 列表、本地回退和 `TimeoutError` 分类。
- Provider 预算跟踪：`DailyTokenUsageRow`、按项目的每日令牌预算、`BudgetExceededError`、包装后的后端集成和迁移 0010。
- Canary 迁移（0009）：受保护的增量列和 SQLite 批量 ALTER 降级。
- 并发/故障注入测试：8 线程 Append 幂等、4 Worker 任务领取不重复、租约过期恢复、跨项目隔离、处理器错误分类和完整流水线。
- MCP context 工具接受 V1.1 筛选参数。

## SQLite 验证

- `static_check: ok`，`pytest -q`：**162 passed**，2 个警告（仅 Alembic 配置弃用警告）。
- 新增 13 个 V1.1 测试模块，共 42 个以上的定向测试。

## Docker Compose / PostgreSQL 验证（2026-07-12）

- Docker Engine v29.6.1 + Docker Compose v5.2.0：4 个服务（postgres、api、mcp、worker）均已成功构建和部署。
- Alembic 迁移已在 PostgreSQL 16 + pgvector 上执行至 0010。
- 健康检查端点显示 V1.1 扩展字段：`outbox: ok`、`lexical: "available"`、`vector_profile: "ok"`。
- 已验证全部 V1.1 API 能力：append、duplicate、409 conflict、search、context、admin auth。
- **已验证 `FOR UPDATE SKIP LOCKED`**：通过 PostgreSQL SKIP LOCKED 原子领取 15 个 Outbox 事件；租约清理恢复全部过期运行中任务；第二个 Worker 重新领取 retry-wait 任务。未观察到重复分发或重复领取。