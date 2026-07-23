# codex-memory V1 项目交接说明

更新时间：2026-07-12  
仓库：G:/Codex Project/20260703-codex-memory-system  
最新提交：670336e fix: complete v1 deployment verification

## 项目目标

为 Codex 提供项目级长期记忆：

~~~text
Codex Hook -> HTTP API -> PostgreSQL + pgvector
Codex MCP -> HTTP API
反思 Worker -> L1/L2/L3 知识沉淀
~~~

## 已完成能力

### 1. 记忆分层和本地能力

- L0 原始对话完整保存。
- 自动分类为 L1 工作记忆、L2 稳定知识、L3 错误与反模式。
- L3 结构化记录错误、上下文、触发条件、根因、修复方案和反模式。
- RAG 上下文按 L3、L2、L1 优先级返回。
- 支持项目、模块、类型、标签和层级过滤。
- 支持 embedding 后端、缓存、反思、衰减、版本、审计、失败重试和导出。
- SQLite 本地模式继续保留。

### 2. PostgreSQL/pgvector V1 存储

已实现 SQLAlchemy ORM 和 Alembic 迁移，包含：

- projects
- sessions
- messages
- memories
- memory_embeddings
- memory_sources
- memory_relations
- memory_versions
- audit_logs
- api_keys

PostgreSQL 使用 pgvector/pgvector:pg16。迁移会创建 vector 扩展、向量索引和 memory_relations 表。当前数据库迁移版本为 0002_memory_relations。

### 3. 鉴权和项目隔离

- Bearer Token 认证。
- 数据库只保存 Token 哈希。
- 支持 append、read、memory_write、reflect、admin 权限。
- API Key 绑定项目。
- 未认证返回 401，跨项目访问返回 403。
- 普通客户端不能直接写入 L2。

### 4. V1 HTTP API

API 容器内部端口为 8000；Compose 部署不发布该端口，对外统一经 `http://127.0.0.1:5174/api/` 访问。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | /api/v1/append | 幂等追加 L0 消息 |
| POST | /api/v1/context | 获取 L3/L2/L1 上下文 |
| POST | /api/v1/search | 项目范围检索 |
| POST | /api/v1/memory | 写入 L1 候选知识 |
| POST | /api/v1/reflect | 执行反思 |
| GET | /api/v1/health | 检查 API、数据库和向量扩展 |

相同 event_key 重复提交只保存一条消息，并返回 duplicate。

### 5. Codex Hook 自动记录

文件：

- .codex/hooks.json
- .codex/scripts/append_user.py
- .codex/scripts/append_assistant.py
- .codex/scripts/hook_common.py

功能：

- UserPromptSubmit 自动保存用户消息。
- Stop 自动保存助手最终消息。
- 根据 cwd 和 CODEX_MEMORY_PROJECT_MAP 解析项目。
- API 不可用时写入 JSONL outbox。
- 下次 Hook 执行时重放 outbox。
- 文件锁保证并发安全。
- outbox 不保存明文 Token。
- 用户消息提交后自动请求上下文。

### 6. MCP

Streamable HTTP 地址：

~~~text
http://127.0.0.1:8001/mcp
~~~

工具：

- build_context
- retrieve_memory
- record_outcome
- health

MCP 通过 HTTP API 调用，不直接访问数据库。

### 7. 反思 Worker

- 查询 active 项目。
- 处理消息并生成 L1/L3。
- 跨多个会话重复出现的知识可提升为 L2。
- L3 错误永久保留。
- 默认每天本地时间 02:00 执行。
- 支持 --once 单次执行。

### 8. Docker Compose

服务：

- postgres：PostgreSQL + pgvector。
- api：Alembic、bootstrap、FastAPI。
- mcp：HTTP MCP。
- worker：反思任务。

已完成：

- PostgreSQL 和 API 健康检查。
- MCP/Worker 等待 API 健康后启动，避免迁移竞态。
- .dockerignore。
- bootstrap 自动创建首个项目和 API Token。
- pgdata 命名卷持久化。

## 已验证结果

最后一轮真实验收：

- 自动化测试：120 passed。
- Docker 镜像构建成功。
- Compose 配置校验成功。
- API 健康返回 status=ok、database=ok、vector=ok。
- PostgreSQL 迁移版本为 0002_memory_relations。
- append 幂等通过。
- 401 未认证和 403 跨项目隔离通过。
- 反思生成 L3、L2、L1 上下文通过。
- 真实 MCP 客户端发现四个工具并成功调用 build_context。
- 真实 Hook 已将用户和助手消息写入 PostgreSQL。
- Worker 启动没有 UndefinedTable 或迁移竞态错误。
- Git 工作区已提交且干净。

## 当前运行状态

代码和部署实现已完成。

但本会话结束前再次检查时，Docker Engine 已停止，docker compose ps 无法连接 dockerDesktopLinuxEngine。上一次验收使用的是临时环境变量，没有保存项目 .env，因此下个窗口需要重新配置环境后启动。

## 下个窗口第一步

~~~powershell
cd "G:\Codex Project\20260703-codex-memory-system"
Copy-Item .env.example .env
~~~

编辑 .env：

~~~dotenv
POSTGRES_PASSWORD=<随机数据库密码>
CODEX_MEMORY_DATABASE_URL=postgresql+psycopg://codex:<同一个数据库密码>@postgres:5432/codex_memory
CODEX_MEMORY_SERVICE_TOKEN=<随机高强度 Token>
CODEX_MEMORY_BOOTSTRAP_PROJECT_KEY=<项目标识>
CODEX_MEMORY_BOOTSTRAP_PROJECT_NAME=<项目名称>
~~~

启动和检查：

~~~powershell
docker info
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:5174/api/v1/health
docker compose logs -f api mcp worker
~~~

不要执行 docker compose down -v，否则会删除 pgdata 数据卷。

## 下一步建议

### P0：团队可用和服务器部署

1. 启动 Docker Engine，创建正式 .env。
2. 使用反向代理和 HTTPS 暴露 API/MCP。
3. 为每个项目或同事创建独立 API Token。
4. 为每个 Codex 项目配置 CODEX_MEMORY_PROJECT_MAP、API 地址和 Token。
5. 在同事环境安装 Hook 和 MCP 配置。
6. 用真实 Codex 对话验证用户消息、助手消息和 outbox 重放。

### P1：生产管理

1. 增加项目和 API Key 管理 CLI/API。
2. 增加 Token 轮换、吊销和过期策略。
3. 增加 PostgreSQL 备份、恢复演练和迁移回滚。
4. 增加 API 指标、告警和 Worker 失败通知。
5. 增加 PostgreSQL 集成测试或 Testcontainers CI。

### P2：真正使用 pgvector 做语义检索

当前 V1 已有 pgvector 扩展、向量列和索引，但 V1MemoryService.search_memories() 主要使用文本词项匹配。下一步应：

1. 接入 OpenAI 兼容或本地 embedding provider。
2. 在记忆创建和反思时写入 memory_embeddings。
3. 在 /api/v1/search 中执行 pgvector 余弦距离查询。
4. 合并 L3 优先级、标签过滤、时间衰减和使用次数权重。
5. 增加 PostgreSQL 召回质量和项目隔离集成测试。

### P3：知识治理

1. 增加 L1 -> L2 人工审核入口。
2. 增加记忆合并、版本比较和反模式关联管理。
3. 增加团队知识管理后台。
4. 增加全局 L2 的显式审批和审计。

## 开发注意事项

- 不要 git reset --hard。
- 不要删除 pgdata 卷。
- ORM 改动必须配套 Alembic 增量迁移。
- API 权限改动必须补 401、403 和跨项目测试。
- Hook 改动必须保留 outbox、并发锁和 Token 不落盘。
- Compose 改动必须保留 PostgreSQL -> API 迁移 -> MCP/Worker 健康依赖。
- 部署完成必须同时提供 compose ps、health、MCP 调用和测试证据。

