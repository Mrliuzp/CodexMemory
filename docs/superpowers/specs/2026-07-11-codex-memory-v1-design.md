# Codex Memory V1 设计

## 目标

在保留现有 SQLite 本地开发能力的前提下，将 `codex-memory` 升级为可部署、可认证、可被多个 Codex 客户端共享的项目级记忆服务。V1 的闭环是：Codex Hook 自动采集原始对话，HTTP API 持久化，MCP 检索并构建上下文，后台反思沉淀 L1/L2/L3 记忆。

## 范围

V1 交付 PostgreSQL 16、pgvector、SQLAlchemy、Alembic、版本化 HTTP API、Bearer Token 认证、Streamable HTTP MCP、Codex Hook 脚本、Cron 风格后台反思任务和 Docker Compose。Redis 与 Celery 保留为可选部署能力，不作为 V1 运行前提。

V1 不交付管理 Web UI、知识图谱可视化、自动代码扫描、跨项目自动提升全局知识，或由普通 Codex 工具触发的 L2 提升。

## 架构

```text
Codex Hook ----> HTTP API ----> SQLAlchemy ----> PostgreSQL + pgvector
     |                |                |
     |                |                +--> L0 消息、L1/L2/L3 和记忆版本
     |                +--> 认证、项目授权、审计与 outbox 重试
     |
Codex MCP -----> Streamable HTTP MCP -----> HTTP API
                                              |
Cron Worker ---------------------------------+--> 分层、反思、去重
```

API 是唯一写入入口。MCP 不直接访问数据库，而是以服务账户调用 API。这样 Hook、MCP、Worker 的认证、审计和项目隔离规则一致。

## 存储模型

使用 `projects`、`sessions`、`messages`、`memories`、`memory_embeddings`、`memory_sources`、`memory_relations`、`memory_versions`、`audit_logs` 和 `api_keys`。

`projects.project_key` 全局唯一。`sessions` 使用 `UNIQUE(project_id, session_key)`，同一个会话键可在不同项目复用。

`messages` 是不可变 L0：`project_id`、`session_id`、`role`、`content`、`source`、`event_key`、`content_hash`、`created_at` 均不可更新。`event_key` 使用 `session_key:turn_id:role`，或在缺少 `turn_id` 时由 Hook 生成 UUID；它是唯一幂等键。`content_hash` 不唯一，只用于聚类和分析，防止相同文本在独立真实轮次中被丢弃。

`memories.level` 仅允许 `L1`、`L2`、`L3`。`memories.status` 允许 `candidate`、`active`、`deprecated`、`rejected`。L1 是候选经验，L2 是已沉淀知识，L3 是结构化错误和反模式。`memory_sources` 以外键关联记忆与原始消息，替代数组字段，确保可审计与可查询。每次可见记忆更新都写入 `memory_versions`。

`memory_embeddings.embedding` 使用由 `CODEX_MEMORY_EMBEDDING_DIMENSION` 固定的 `vector(n)` 列；使用 OpenAI 兼容 1536 维模型时设为 1536，使用本地模型时必须在首次迁移前设为该模型的实际维度。PostgreSQL 创建余弦距离 HNSW 索引。SQLite 开发模式不创建向量表索引，继续使用当前本地 token 嵌入后端。

## 项目隔离与认证

每个 API Key 只绑定一个项目和一组权限（`read`、`append`、`memory_write`、`reflect`、`admin`）。请求使用 `Authorization: Bearer <token>`；数据库只保存 token 哈希。除管理员服务账户外，服务端拒绝令牌与请求 `project_key` 不匹配的请求。

全局 L2 不在 V1 的普通 API 和 MCP 工具中写入。提升动作只允许 Worker 或管理员服务账户执行，并写入审计日志，包含审批人、原因、源记忆和目标记忆。

## HTTP API

所有端点位于 `/api/v1`。

- `POST /append`：仅在已存在且令牌已授权的项目中创建或复用会话并追加一条 L0 消息。项目只能通过管理员流程预先创建。相同 `event_key` 返回既有记录，状态为 `duplicate`。
- `POST /context`：按照 L3、L2、L1 的固定优先级返回紧凑 JSON 上下文及来源记忆 ID。
- `POST /search`：按项目、层级、类型、标签和语义查询检索记忆。
- `POST /memory`：创建 L1 候选经验；拒绝客户端直接写入 L2。
- `POST /reflect`：仅接受 Worker/管理员令牌，用于项目反思。
- `GET /health`：返回 API、数据库、迁移版本和向量扩展状态。

旧的非版本化 `/append`、`/retrieve` 和 `/context` 在兼容期保留为本地开发别名，并在文档中标记为弃用。新客户端只使用 `/api/v1`。

## Hook 自动采集

仓库级 `.codex/hooks.json` 配置 `UserPromptSubmit` 和 `Stop`。脚本从 stdin 读取 Codex Hook JSON，而不是读取终端参数：前者读取 `prompt`、`session_id`、`turn_id` 与 `cwd`；后者读取 `last_assistant_message`、`session_id`、`turn_id` 与 `cwd`。

Hook 根据受控的 `CODEX_MEMORY_PROJECT_MAP` 配置将 `cwd` 解析为 `project_key`，不接受用户消息中的项目名。它调用 `/api/v1/append`，超时为三秒。网络或服务失败时，Hook 以独占文件锁和原子追加把完整请求写入本地 JSONL outbox；每次新 Hook 执行前先重放 outbox。相同 `event_key` 使重放安全。`Stop` 没有助手消息时不写入消息。

`UserPromptSubmit` 还调用 `/api/v1/context`，只将固定长度的 L3/L2/L1 摘要输出为 Hook additional context，避免一次注入完整对话或阻塞用户交互。

## MCP 服务

MCP 同时支持本地 STDIO 与部署后的 Streamable HTTP。它暴露四个工具：

- `build_context(project, task)`：调用 API 上下文端点。
- `retrieve_memory(project, query, filters)`：调用 API 搜索端点。
- `record_outcome(project, type, content)`：调用 API 创建 L1 候选经验。
- `health()`：调用 API 健康端点。

MCP 服务器初始化指令要求优先读取 `build_context`，但不把模型工具选择视作 L0 采集保证；L0 保证来自 Hook。

## 后台任务

Worker 以 Cron 调度。默认每天 02:00 对活跃项目执行反思：处理待分层 L0、合并明显重复的 L1、生成候选经验、对独立会话与来源数满足阈值的条目沉淀 L2、衰减陈旧 L1，并记录反思报告。

频率阈值只统计独立 `session_id` 和来源消息，不把同一会话中的重试或重复文本视为多次验证。L3 永不自动删除。Worker 重试可幂等，且每一次失败都会写入审计日志。

## 部署

Docker Compose 包含 `postgres`、`api`、`mcp` 和 `worker`。PostgreSQL 使用 `pgvector/pgvector:pg16`，数据使用命名卷持久化。`api` 暴露 8000，`mcp` 暴露 Streamable HTTP 端口 8001。服务密钥、数据库密码和管理员令牌只通过 `.env` 传入，`.env` 不提交。

Redis/Celery 作为 `async` Compose profile，可在反思负载增长后启用；默认 Cron worker 不依赖 Redis。

## 验收标准

1. 重复提交同一 `event_key` 只保存一条 L0。
2. 不同项目的令牌不能读取、写入或搜索彼此的内容。
3. L0 更新和删除请求被拒绝，并保留审计记录。
4. L3 在 `/api/v1/context` 中始终先于 L2 和 L1 返回。
5. `record_outcome` 只能创建 L1，不能直接创建 L2。
6. Hook 的用户与助手输入能写入 API；API 暂时不可用时可通过 outbox 重放。
7. Docker Compose 启动后，health、迁移状态、pgvector 和 Streamable HTTP MCP 均可用。
8. SQLite 本地模式的现有测试保持通过；PostgreSQL 集成测试覆盖新 API、认证和向量检索。
