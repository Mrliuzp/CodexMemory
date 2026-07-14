# Codex 记忆系统

这个目录包含一个可本地运行的、按项目隔离的 Codex 记忆层 MVP。它用于保留原始对话、提取分层记忆、检索相关知识、优先处理错误学习，并在生成答案之前构建提示注入上下文。

## 已实现能力

- L0 原始日志：不经筛选地存储每一条捕获到的消息。
- L1 工作记忆：存储可检索的问题、解决方案、代码片段、调试笔记和临时结论。
- L2 知识库：存储稳定的工程规则、最佳实践、架构笔记以及可选的全局知识。
- L3 错误记忆：以可读正文和 `metadata.error_memory` 两种形式存储结构化错误记录，包含 `error`、`context`、`trigger_condition`、`root_cause`、`fix` 和 `anti_pattern`。
- 项目隔离：L0、L1 和 L3 都严格按 `project_id` 过滤；只有全局 L2 可以跨项目边界。
- RAG 检索：应用 project/tag/module/type/layer 过滤器，先按层级优先级排序，再结合本地 token 向量相似度、时效性和使用权重。
- 元数据标签：L0 的 `metadata.tags`、`metadata.module` 和 `metadata.type` 会提升为记忆标签，供检索过滤使用。
- 可插拔嵌入：`EmbeddingBackend` 让检索和反思可以使用本地、缓存、密集向量或 HTTP JSON 嵌入后端。
- 上下文注入：输出 `[Project Context]`、`[Error Memory - L3]`、`[Knowledge Base - L2]`、`[Working Memory - L1]` 和 `[Current Task]`；L3 条目会显式注入被禁止的反模式。
- 答案前运行时钩子：`prepare_answer_context` 会在构建 RAG 上下文之前清空当前项目待处理的 L0 分层任务。
- 自动分层：默认把 L0 写入持久化的待处理任务，并支持显式的立即处理、进程内异步队列，以及可调度的外部 worker。
- 持久化处理任务：每次 L0 append 都会在分层开始前创建一条 `processing_jobs` 记录。
- Worker 控制：`process_now=False` 是默认值；`process_now=True` 会立即清空当前项目的持久化任务。`enqueue_async=True` 会为长期运行的环境启动进程内 worker，而 CLI 的 `--async-process` 和 `--enqueue-worker` 会在命令退出前清空已入队的工作。
- 任务可见性：按项目范围输出的 `jobs` 会显示待处理、运行中、完成和失败的 L0 分层任务。
- 失败恢复：失败的 L0 分层任务可以重置为待处理并再次执行。
- 失败隔离：某个项目失败的分层任务会被标记为失败，而不会阻塞其他待处理项目。
- 运行超时恢复：超时的运行中 L0 分层任务可以按项目重置为待处理。
- 重建：可以从 L0 重建项目的 L1/L2，同时保留并合并 L3；重建会写入审计事件。
- 去重与历史：重复记忆会合并，每次更新都会记录到 `memory_versions`。
- 反思引擎：将高频使用的 L1 提升到 L2，在从重复或合并后的 L1 生成稳定规则之前先聚类相似记忆，衰减过期 L1，清理过期且低价值的 L1，并写入摘要报告。
- 运行时钩子：`CodexMemoryRuntime` 提供记录完整轮次和准备答案上下文的集成边界。
- 治理：项目 L2 知识只能通过显式的 reviewer/reason 事件提升为全局 L2。
- 审计导出：按项目范围的原始日志列表和导出包含原始日志、记忆、任务、反思报告和治理事件。
- 版本审计：项目审计导出包含 `memory_versions`，用于历史重建。
- 健康检查：报告 SQLite 完整性、外键约束、所需表和行数。

## 安装

```powershell
cd "G:\Codex Project\20260703-codex-memory-system"
python -m pip install -e .
```

## CLI 用法

向持久队列追加一条原始 L0 消息：

```powershell
python -m codex_memory.cli --db .\memory.db append --project demo --conversation c1 --role user --content "Bug: auth token refresh throws an exception. Fix: refresh before retry."
```

追加一条带审计元数据的原始 L0 消息：

```powershell
python -m codex_memory.cli --db .\memory.db append --project demo --conversation c1 --role user --content "Bug: auth token refresh throws an exception." --metadata-json '{"tool":"codex","source":"cli"}'
```

追加并立即处理当前项目的待处理 L0 任务：

```powershell
python -m codex_memory.cli --db .\memory.db append --project demo --conversation c1 --role user --content "Bug: auth token refresh throws an exception." --process-now
```

检索项目记忆：

```powershell
python -m codex_memory.cli --db .\memory.db retrieve --project demo --query "auth token retry bug"
```

按模块和类型标签过滤：

```powershell
python -m codex_memory.cli --db .\memory.db retrieve --project demo --query "token retry" --module auth --tag-type api
```

按记忆层级过滤：

```powershell
python -m codex_memory.cli --db .\memory.db retrieve --project demo --query "token retry" --layer L3
```

构建提示注入上下文：

```powershell
python -m codex_memory.cli --db .\memory.db context --project demo --task "Fix auth token retry bug"
```

`context` 命令会在检索前处理所请求项目的待处理 L0 分层任务。使用 `--skip-pending` 可以只查看已经分层的记忆。

从特定层级构建提示注入上下文：

```powershell
python -m codex_memory.cli --db .\memory.db context --project demo --task "Fix auth token retry bug" --layer L3
```

从特定记忆类型构建提示注入上下文：

```powershell
python -m codex_memory.cli --db .\memory.db context --project demo --task "Fix auth token retry bug" --type solution
```

运行离线反思：

```powershell
python -m codex_memory.cli --db .\memory.db reflect --project demo
```

运行一次可调度的 L0 分层 worker：

```powershell
python -m codex_memory.cli --db .\memory.db process-job --iterations 1
```

按固定间隔持续运行 L0 分层 worker：

```powershell
python -m codex_memory.cli --db .\memory.db process-job --interval 10 --forever
```

为一个或多个项目运行一次可调度的反思任务：

```powershell
python -m codex_memory.cli --db .\memory.db reflect-job --project demo --project shared --iterations 1
```

按固定间隔持续运行：

```powershell
python -m codex_memory.cli --db .\memory.db reflect-job --project demo --interval 3600 --forever
```

列出反思报告：

```powershell
python -m codex_memory.cli --db .\memory.db reports --project demo
```

列出某个项目的 L0 原始日志：

```powershell
python -m codex_memory.cli --db .\memory.db raw-logs --project demo
```

处理所有待处理的 L0 分层任务：

```powershell
python -m codex_memory.cli --db .\memory.db process
```

列出某个项目的 L0 分层任务：

```powershell
python -m codex_memory.cli --db .\memory.db jobs --project demo
```

重试某个项目失败的 L0 分层任务：

```powershell
python -m codex_memory.cli --db .\memory.db retry-failed --project demo
```

重置某个项目中已过时的运行中 L0 分层任务：

```powershell
python -m codex_memory.cli --db .\memory.db reset-stale-running --project demo --older-than-minutes 30
```

从 L0 重建项目派生记忆：

```powershell
python -m codex_memory.cli --db .\memory.db rebuild --project demo
```

将已批准的项目 L2 知识提升为全局 L2：

```powershell
python -m codex_memory.cli --db .\memory.db promote-global --project demo --memory-id 12 --reviewer lead --reason "applies to every project"
```

导出项目审计数据：

```powershell
python -m codex_memory.cli --db .\memory.db export --project demo
```

检查数据库健康状态：

```powershell
python -m codex_memory.cli --db .\memory.db health
```

## HTTP 服务

使用以下命令启动 API 服务器：

```powershell
python -m codex_memory.cli --db .\memory.db serve --host 0.0.0.0 --port 8000
```

可用端点：

- `GET /health`
- `POST /append`
- `POST /retrieve`
- `POST /context`
## MCP 服务器

使用以下命令启动 MCP 服务器：

```powershell
python -m codex_memory.cli --db .\memory.db mcp
```

这会通过 stdio 暴露同样的 `health`、`append`、`retrieve` 和 `context` 工具。
## Codex 最短操作

给 Codex 接入这套记忆时，按这个顺序做：

1. 用户消息进来后，先调 `append`，`role=user`，`process_now=true`。
2. 需要补充上下文时，先调 `context`，再把结果放进当前任务上下文。
3. 模型回复完成后，再调一次 `append`，`role=assistant`，`process_now=true`。
4. 需要排查或回看时，用 `health`、`retrieve`。

最小约定：同一个对话全程复用同一个 `conversation_id`，并按项目隔离写入 `project_id`。

## 运行时集成

```python
from codex_memory import CodexMemoryRuntime, ConversationMessage, MemoryService

service = MemoryService("memory.db")
runtime = CodexMemoryRuntime(service)

runtime.record_conversation(
    project_id="demo",
    conversation_id="thread-1",
    messages=[
        ConversationMessage(role="user", content="Bug: migration fails"),
        ConversationMessage(role="assistant", content="Fix: add guarded migration"),
    ],
)

context = runtime.prepare_answer_context(
    project_id="demo",
    current_task="Fix migration failure",
)
```

`prepare_answer_context` 会在检索前处理所请求项目的待处理 L0 分层任务。传入 `process_pending=False` 可以只查看已经分层的记忆。

## V1 Docker 部署

V1 部署需要 Docker Desktop、PostgreSQL 16 和 pgvector。首次启动会自动执行 Alembic 迁移，并按 `.env` 中的配置幂等创建首个项目和 API Token。

```powershell
Copy-Item .env.example .env
# 编辑 .env：设置匹配的数据库密码，并分别生成 SERVICE_TOKEN、MCP_TOKEN、管理用户名、管理密码和会话签名密钥
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:5174/api/v1/health
```

服务地址：

- 管理后台与 API 统一入口：`http://127.0.0.1:5174`（API 路径前缀为 `/api`）
- MCP Streamable HTTP：`http://127.0.0.1:8001/mcp`
- 数据库数据：Docker 命名卷 `pgdata`

`.env` 中的 `CODEX_MEMORY_BOOTSTRAP_PROJECT_KEY` 是首个项目标识。`CODEX_MEMORY_SERVICE_TOKEN` 是 MCP 服务访问内部 API 的项目 Token，`CODEX_MEMORY_MCP_TOKEN` 是 Codex 客户端访问 MCP 的独立 Bearer Token，两者不得复用。管理用户名、密码和 `CODEX_MEMORY_ADMIN_SESSION_SECRET` 也必须替换示例值。生产环境请使用随机高强度值，并配置 HTTPS、访问控制和备份策略。

要替换默认的本地嵌入逻辑，传入一个实现了 `embed(text)` 和 `similarity(left, right)` 的对象：

```python
service = MemoryService("memory.db", embedding_backend=my_embedding_backend)
```
