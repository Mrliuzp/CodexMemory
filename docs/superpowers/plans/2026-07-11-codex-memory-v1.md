# Codex Memory V1 实施计划

> **面向自动化执行者：** 必须使用 `superpowers:subagent-driven-development`，按任务逐项实施。本计划使用复选框记录步骤。

**目标：** 交付 PostgreSQL/pgvector 存储、带认证的 V1 API、Codex Hook 采集、HTTP MCP 工具、反思 Worker、Docker Compose 和自动化验证。

**架构：** SQLAlchemy Repository 作为 V1 持久化边界；FastAPI 负责认证和项目隔离；Hook 与已部署的 MCP 进程调用 API；保留现有 SQLite 代码作为兼容路径。

**技术栈：** Python 3.10+、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、pgvector、FastMCP、httpx、pytest、Docker Compose。

## 全局约束

- PostgreSQL 镜像使用 `pgvector/pgvector:pg16`；SQLite 继续支持历史测试。
- L0 消息只允许追加；`event_key` 唯一，`content_hash` 不设唯一约束。
- Bearer Key 权限包括 `read`、`append`、`memory_write`、`reflect` 和 `admin`。
- 新接口位于 `/api/v1` 下；历史接口保留为兼容别名。
- Context 中 L3 优先于 L2 和 L1；客户端 MCP 只能创建 L1。
- Hook Outbox 不包含 Bearer Token，并支持并发 Hook 执行。

---

### 任务 1：V1 配置与关系数据库结构

**文件：**
- 修改：`pyproject.toml`
- 新建：`src/codex_memory/config.py`
- 新建：`src/codex_memory/db.py`
- 新建：`src/codex_memory/db_models.py`
- 新建：`alembic.ini`、`alembic/env.py`、`alembic/versions/0001_v1_schema.py`
- 测试：`tests/test_v1_schema.py`

**产出：** `Settings.from_env()`、SQLAlchemy engine/session factory，以及 projects、sessions、messages、memories、embeddings、sources、versions、API keys 和 audit logs 的 ORM 行。

- [ ] 编写失败测试：两次插入相同 `event_key` 应断言 `IntegrityError`；使用不同事件键插入相同内容时，两行都应存在。
- [ ] 运行：`.\.venv\Scripts\python.exe -m pytest tests/test_v1_schema.py -v`。预期：由于 V1 结构模块不存在而失败。
- [ ] 实现配置、ORM 元数据和 Alembic 迁移。使用 `UNIQUE(project_id, session_key)`、`UNIQUE(event_key)` 和非唯一 `content_hash`。PostgreSQL 迁移创建 vector 扩展，并依据 `CODEX_MEMORY_EMBEDDING_DIMENSION` 创建 `vector(n)`。
- [ ] 重新运行定向测试，预期通过。
- [ ] 提交：`feat: add v1 database schema`。

### 任务 2：认证与 V1 Repository 服务

**文件：**新建 `src/codex_memory/auth.py`、`v1_repository.py`、`v1_service.py`；测试 `tests/test_v1_auth.py`、`tests/test_v1_service.py`。

**产出：** `Principal(project_key, permissions)`、Token 哈希认证、`ProjectAccessDenied`、`AppendResult(message_id, status)` 和 `V1MemoryService`。

- [ ] 编写重复事件追加返回首个 ID 且状态为 duplicate 的测试，以及错误项目 Token 尝试追加时抛出 `ProjectAccessDenied` 的测试。
- [ ] 实现 `require_permission()`、`append_message()`、`create_l1_memory()`、来源关联和审计写入。不实现消息更新/删除；拒绝直接创建 L2。
- [ ] 重新运行定向测试并提交。

### 任务 3：带认证的版本化 HTTP API

**文件：**修改 `src/codex_memory/http_api.py`；新建 `src/codex_memory/v1_schemas.py`；测试 `tests/test_v1_http_api.py`。

**产出：** `POST /api/v1/append`、`/context`、`/search`、`/memory`、`/reflect` 和 `GET /api/v1/health`。

- [ ] 测试未认证 Append 返回 401、项目不匹配返回 403、重复追加返回 duplicate，以及 `/memory` 使用 L2 返回 422。
- [ ] 增加 Pydantic 请求/响应模型和 Bearer 依赖。按端点要求 `append`、`read`、`memory_write` 或 `reflect` 权限；返回 L3/L2/L1 Context 分区和来源 ID；保留未版本化接口作为已弃用别名。
- [ ] 重新运行定向测试和 `tests/test_http_api.py`，预期通过并提交。

### 任务 4：Hook 采集与持久化 Outbox

**文件：**新建 `.codex/hooks.json`、`.codex/scripts/hook_common.py`、`append_user.py`、`append_assistant.py`；修改 `.gitignore`；测试 `tests/test_hooks.py`。

**产出：** 从 stdin 读取 Hook JSON 的 `UserPromptSubmit` 和 `Stop` 处理器、项目映射解析、V1 Append/Context 调用和 JSONL Outbox 回放。

- [ ] 测试用户事件键 `session:turn:user`、Stop 事件键 `session:turn:assistant`、空助手消息省略、Context 输出和失败请求写入不含 Token 的 Outbox 记录。
- [ ] 实现 3 秒 HTTP 客户端超时、从 `CODEX_MEMORY_PROJECT_MAP` 读取项目映射、加锁原子追加 JSONL、发送前回放和失败记录原子重写。
- [ ] 重新运行定向测试并提交。

### 任务 5：HTTP 后端 MCP 与定时反思 Worker

**文件：**新建 `src/codex_memory/api_client.py`、`worker.py`；修改 `mcp_server.py`、`cli.py`；测试 `tests/test_v1_mcp_server.py`、`tests/test_v1_worker.py`。

**产出：** `build_context`、`retrieve_memory`、`record_outcome`、`health` MCP 工具和 `run_once()` Worker。

- [ ] 测试 `build_context` 调用 `/api/v1/context`、`retrieve_memory` 调用 `/api/v1/search`、`record_outcome` 写入 L1，以及 `run_once()` 每个活动项目只反思一次。
- [ ] 实现带 Bearer Header 的 `MemoryApiClient`；保留本地 SQLite MCP factory；增加 V1 factory 和 CLI 的 streamable-http 主机/端口选项；实现 Worker 失败审计和每日 02:00 调度入口。
- [ ] 重新运行定向测试和 `tests/test_mcp_server.py`，预期通过并提交。

### 任务 6：Docker 部署与最终验证

**文件：**新建 Dockerfile、`docker-compose.yml`、`.env.example`、`tests/test_compose_contract.py`、`tests/test_v1_end_to_end.py`；修改 `README.md`。

**产出：** postgres、api、mcp、worker 四个 Compose 服务，以及本地/服务器启动流程文档。

- [ ] 编写 Compose 契约测试，检查 `pgvector/pgvector:pg16`、API 8000、MCP 8001 和不存在硬编码可用密钥。
- [ ] 运行定向测试，预期由于部署文件和 V1 fixture 不存在而失败。
- [ ] 实现 Dockerfile、Compose 健康依赖、`.env.example` 和端到端 fixture；覆盖 Append、通过 MCP 反思、检索 V1 Context 和来源 ID 校验。
- [ ] 运行 `.venv` 全量 pytest，预期所有单元测试通过。
- [ ] 运行 `docker compose config`，预期 Compose 配置有效；Docker 可用时启动服务，等待 `/api/v1/health`，再运行 PostgreSQL 集成测试。
- [ ] 提交：`feat: add v1 docker deployment and verification`。