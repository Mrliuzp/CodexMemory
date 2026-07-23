# codex-memory 项目状态与下一步

更新时间：2026-07-18

这是项目状态交接入口文件。当前状态以以下文档为准：

- [PROJECT_HANDOFF.md](./PROJECT_HANDOFF.md)：项目总体状态、现行契约和下一步。
- [HANDOFF_HISTORICAL_IMPORT.md](./HANDOFF_HISTORICAL_IMPORT.md)：V1.3.1 历史知识导入的详细实现、验收证据和风险。

## 文档优先级

发生表述冲突时，按以下顺序解释：

1. 最新交接文档代表当前实现和运行状态。
2. 版本规格代表对应版本的目标和约束；后续版本已经明确变更的内容，以后续交接为准。
3. `docs/superpowers/plans/` 是历史实施记录，不作为当前接口、迁移编号或运行状态的权威来源。

## 当前基线

- V1.3.1 历史知识导入、管理端、对象存储、分片上传、治理和审核流程已经完成本地实现与部署验收。
- 当前工作区改动尚未提交或推送，不能视为远程分支已经同步。
- 后端全量测试：`206 passed, 1 skipped, 19 warnings`。
- 前端测试：`1 passed`；生产构建通过。
- 静态检查、UTF-8 检查、`git diff --check` 和 Compose 配置检查通过。
- PostgreSQL 当前迁移版本：`0021_v131_memory_scope (head)`。
- Compose 验证中 API、PostgreSQL、向量、Outbox、Worker 和 Admin Web 均正常。

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

当前本机 Admin Web 因 `5174` 被占用而使用 `5175`；MCP Docker 映射已恢复正式端口 `8001`，与 Codex MCP 配置一致。

## 下一步

1. 审阅当前工作区差异，按功能范围分批暂存并提交。
2. 推送后创建或更新代码审查/PR。
3. 重点复核迁移兼容性、Scope 回填、候选发布边界和 Worker 并发行为。
4. 补充 S3/MinIO 对象存储、生命周期清理、压力测试和复杂 PDF/OCR 能力。
5. 每次后续改动后重新执行后端测试、前端测试与构建、静态检查、UTF-8 检查、迁移检查和 Compose 健康检查。
