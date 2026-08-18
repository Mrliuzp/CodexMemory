# codex-memory 项目状态与下一步

更新时间：2026-08-19

当前发布状态：V1.6 项目级 L1 记忆发布治理已发布到 `codex/expand-audit-subject-id`，发布提交为 `5636b83`，已推送并完成正式 Compose 部署验证。

这是项目状态交接入口文件。当前状态以以下文档为准：

- [PROJECT_HANDOFF.md](./PROJECT_HANDOFF.md)：项目总体状态、现行契约和下一步。
- [CODEX_MEMORY_BLUEPRINT.md](./CODEX_MEMORY_BLUEPRINT.md)：后续版本路线及 V1.4 可信任务执行报告的唯一规格。
- [HANDOFF_HISTORICAL_IMPORT.md](./HANDOFF_HISTORICAL_IMPORT.md)：V1.3.1 历史知识导入的详细实现、验收证据和风险。
- [v1.6/DECISION_WORKER_INTEGRATION.md](./v1.6/DECISION_WORKER_INTEGRATION.md)：V1.6 决策 Worker、发布门禁和人工纠错契约。

`PROJECT_HANDOFF.md` 和 `v1.5/V1.5_FINALIZATION_HANDOFF.md` 保留为历史交接记录；本文件是当前项目状态和发布事实的优先入口。

## 文档优先级

发生表述冲突时，按以下顺序解释：

1. 最新交接文档代表当前实现和运行状态。
2. 版本规格代表对应版本的目标和约束；后续版本已经明确变更的内容，以后续交接为准。
3. `docs/superpowers/plans/` 是历史实施记录，不作为当前接口、迁移编号或运行状态的权威来源。

## 当前基线

- 正式分支为 `codex/expand-audit-subject-id`，当前发布提交为 `5636b83`，本地与 origin 一致。
- 项目级 L1 记忆发布治理已完成：feature flags、默认关闭的 Codex CLI Runner、严格决策契约、CandidatePolicyService 后端门禁、项目阈值、人工纠错和 Admin 观测已发布。
- 自动发布固定要求项目级 L1/project、项目显式开关、证据有效、非重复、无风险、非 abstain；L2、global、L3 不自动发布。
- `CodexCliRunner`、decision engine、候选发布和 auto publish 默认关闭；项目策略默认 `manual_review`、`enabled=false`、`auto_publish_enabled=false`、安全边界为 `L1/project`，初始阈值为 `0.80`。
- 后端完整测试为 `290 passed, 1 skipped`；唯一环境门控的 PostgreSQL migration 测试已单独补跑通过，合计 `291 passed, 0 failed`。
- 前端测试为 6 个文件、28 个 tests；生产构建通过。fake runner/process 测试未调用真实 Codex CLI、账号或模型。
- Alembic 迁移链为 `0024_repair_scope_names → 0025_expand_audit_subject_id → 0026_init_project_flags → 0027_v16_decision_contracts (head)`；fresh、rollback、re-upgrade 均通过。
- 正式部署已完成：构建 `api/worker/mcp/admin-web`，先迁移正式 schema，再仅重建这四个服务；API health 和 Admin Web 200，MCP 未授权边界返回 401，正式 PostgreSQL 与数据卷保留。
- 测试 PostgreSQL 容器、测试卷和测试端口均已清理；未触碰正式 PostgreSQL、`pgdata`、`importdata` 或正式端口。

## 现行契约

- 管理 API 正式命名空间：`/api/admin/v1/`。
- 检索 API：`POST /api/v1/search`。
- 外部 Event 和 API 使用 `project_key`；数据库 `project_id` 保留内部主键语义。
- MCP 使用独立服务，默认地址为 `http://127.0.0.1:8001/mcp`。
- MCP 正式工具：`append_message`、`retrieve_memory`、`build_context`、`health`。
- Append 事务只写入 L0 与服务端 Outbox；Processing Job 由 Dispatcher 后续幂等创建。
- Scope 迁移允许为历史记录回填真实的 `knowledge_scopes.id`，但必须保持项目隔离、来源追溯和审计能力。

## 默认端口与本机覆盖

| 服务 | 正式默认端口 | 说明 |
| --- | ---: | --- |
| API | `8000` | FastAPI、V1 API 和管理 API |
| MCP | `8001` | 独立 Streamable HTTP MCP 服务 |
| Admin Web | `5174` | 管理后台 |

正式 Admin Web 使用 `5174`，MCP 使用 `8001`，PostgreSQL 使用 `5432`；本轮发布后端口和正式数据卷未改变。

## 需用户手动授权/操作

1. 在组织批准的隔离非生产环境中为专用 Codex 服务账号完成人工登录、workspace/RBAC、模型、sandbox、预算和审计权限确认；本轮未登录真实 Codex。
2. 先执行非生产 shadow 验证，再由项目负责人按项目人工开启 `memory_v11_enabled`、`candidate_publish_enabled`、`decision_engine_enabled` 和策略开关；默认关闭值不得直接放宽。
3. 如需历史数据回放，只能按项目小批量、带幂等键、审批原因和审计记录执行 job replay；先观察 `needs_review`、失败和死信。

## 工程后续

1. 建立队列积压、失败/重试、死信、`needs_review`、卡死任务和 Codex CLI 可用性告警。
2. 按项目评审自动发布量、人工推翻率、低置信度率、风险率和证据失败率，再决定是否扩大范围。
3. 补充 shadow/灰度运行手册、回滚步骤和项目策略变更审批记录，持续保持 `L1/project` 门禁。
4. `C:\Users\lzp59\.codex\worktrees\d580\20260703-codex-memory-system` 的 Git worktree 注册项和 `codex/final-local-integration` 分支已删除，但物理目录因 Windows 进程锁定未能删除；需锁释放后手动清理。
5. 其他未合入发布分支的临时 worktree/分支未删除：其提交未通过 `git merge-base --is-ancestor` 祖先检查，或无法证明属于本次发布，保留以避免误删用户工作。
