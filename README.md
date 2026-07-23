# Codex 记忆系统

Codex 记忆系统（`codex-memory-system`）是面向 Codex 和其他智能体的项目级长期记忆服务。它把对话原文、可复用经验、稳定知识和错误反模式分层保存，再通过 HTTP API、MCP、CLI 和 Codex Hook 提供项目隔离的检索与上下文注入能力。

当前仓库包含：

- V1/V1.1 核心记忆服务：PostgreSQL、pgvector、FastAPI、Bearer Token、Alembic、Outbox、Worker 和审计治理。
- 本地开发模式：PostgreSQL 16 + pgvector、CLI、FastAPI、Worker 和 stdio/HTTP MCP。
- V1.2 管理后台：Vue 3、Vite、Element Plus、Pinia 和 Vue Router。
- V1.3.1 历史知识导入：异步批次、文件与问题生命周期、分片上传、对象存储、候选审核和回滚。

最新本地验收基线为 PostgreSQL 后端 `116 passed, 1 skipped`、前端 `1 passed`、Alembic 全新数据库升级到 `0021_v131_memory_scope (head)`；当前工作区改动尚未提交或推送。

## 一、项目现在具有什么能力

### 1. 记忆分层与知识沉淀

系统把输入和派生内容分为四层：

| 层级 | 内容 | 用途 |
| --- | --- | --- |
| L0 | 不经筛选的用户、助手和系统原始消息 | 保留事实来源，支持重放、审计和重建 |
| L1 | 问题、解决方案、代码片段、调试笔记和临时结论 | 支持当前项目的短期工作 |
| L2 | 稳定工程规则、最佳实践、架构知识和经审核的全局知识 | 跨任务复用长期经验 |
| L3 | 错误、根因、触发条件、修复方案和反模式 | 在类似问题再次出现时优先提醒，避免重复踩坑 |

系统支持从 L0 自动生成候选记忆，保留 `memory_versions`、来源关系、审计事件和反思报告；重复记忆可以合并，过期或低价值的 L1 可以衰减，稳定且高频使用的经验可以进入 L2。L3 会以结构化字段和可读正文保存。

### 2. 项目隔离与安全治理

- L0、L1、L3 默认严格按 `project_key` 隔离。
- 当前作用域模型支持项目级和全局级；只有经过明确审核和理由记录的 L2 才能提升为全局知识。
- HTTP API 使用 Bearer Token，Token 以哈希形式保存，并按项目和权限控制 `append`、`read`、`memory_write`、`reflect`、`admin` 等操作。
- 访问不存在的项目、越权读取其他项目或缺少权限时返回相应的 401/403，不依赖调用方自行过滤。
- 候选记忆、发布决策、任务重试、Profile 切换和治理操作都会写入审计记录。

### 3. 可靠写入与异步处理

V1.1 的写入链路用于解决“消息丢失、重复写入和异步任务失控”：

- `/api/v1/append` 支持项目范围的 `event_key` 幂等；相同事件重复提交不会产生重复 L0 消息，正文不一致时返回 409 冲突。
- L0 消息和服务端 transactional Outbox 在同一事务中写入，后续处理不阻塞原始消息落库。
- Outbox 分发和任务 Worker 支持领取、租约、心跳、重试退避、死信状态以及过期租约恢复。
- 原始 Hook 还提供本地 JSONL outbox；API 暂时不可用时先落本地，后续 Hook 再重放，并通过文件锁保证并发安全。
- 失败任务可在管理 API 中查询、重试或通过 replay 重新生成处理任务。

### 4. 检索与上下文构建

- 支持按项目、作用域、层级、记忆类型过滤。
- 支持词法检索、确定性 RRF 混合排序、L3 优先、上下文令牌预算和检索审计。
- Embedding Profile 记录 Provider、模型、维度、分块和内容规范化版本；不同 Profile 的向量相互隔离，可回填、灰度切换和回滚。
- 稠密检索是可选能力；稠密检索不可用时返回词法降级结果，不应阻塞上下文构建。
- `/api/v1/context` 只注入正式可用的记忆；shadow、candidate、draft、needs_review 和 rejected 内容不会直接进入生产上下文。
- 生成的上下文包含 `[Project Context]`、`[Error Memory - L3]`、`[Knowledge Base - L2]`、`[Working Memory - L1]` 和 `[Current Task]` 等分区，便于模型区分事实、规则、工作信息和当前任务。

### 5. Codex 与应用接入

- HTTP API：用于消息追加、记忆检索、上下文构建、反思、健康检查和管理操作。
- MCP：提供 `append_message`、`retrieve_memory`、`build_context`、`health` 工具；生产部署通过独立 Streamable HTTP MCP 服务访问统一 API。
- Codex Hook：`UserPromptSubmit` 记录用户消息并请求上下文，`Stop` 记录助手最终消息。
- CLI：支持 `init`、`status`、`doctor`、`hook install/uninstall`、`import`，以及通过正式 V1 API 执行 `append`、`retrieve`、`context`、`reflect` 和 `health`。
- Knowledge Import Pipeline：支持 Markdown、TXT、JSON/JSONL、SQL、常见源码文件、PDF、DOCX 和 ZIP；通过异步批次上传、Outbox/Worker 解析、进度、取消、重试和审核治理，资料先进入 Reference Layer、分块和待审核候选，不直接发布为正式 Memory。
- Python 集成：`CodexMemoryRuntime` 提供记录完整轮次和准备答案上下文的集成边界。

项目 Python 代码已按职责拆分到 `domain`、`persistence`、`api`、`pipelines` 和 `entrypoints`；旧的文件数据库运行时与兼容转发层已删除。详细说明见 [项目目录结构](docs/PROJECT_STRUCTURE.md)。

### 6. 管理与运行观测

管理后台保留 V1.2 的只读观测能力，并在 V1.3.1 中增加受权限、审计、幂等和状态机约束的历史导入操作，支持：

- Dashboard、授权项目和作用域。
- 脱敏后的原始记录、候选记忆和已接受记忆。
- Processing Job、Outbox、检索审计和安全/领域审计事件。
- 系统状态、数据库迁移版本、待处理任务、Outbox 和死信数量。
- 导入批次、文件、问题、进度、候选审核、取消、重试和回滚。

## 二、这些能力解决了什么问题

项目主要解决智能体在长期协作中的五类问题：

1. 对话结束后经验丢失：L0 保留原始事实，L1/L2/L3 将经验结构化，后续任务可以重新检索。
2. 相似错误反复出现：L3 保存根因、触发条件、修复和反模式，并在答案前优先注入。
3. 多项目记忆串线：所有查询和写入都在服务端执行项目/作用域授权，避免把 A 项目的内容泄露给 B 项目。
4. Hook 或网络短暂失败导致消息丢失：客户端本地 outbox、服务端 Outbox、幂等键和任务重试共同保证可靠处理。
5. 自动生成知识不受控：原始消息不可变，派生内容经过候选、证据、策略和审核链路；全局知识不会因为一次普通请求被直接发布。

## 三、项目边界

### 已明确支持的范围

- 项目级长期记忆、知识分层、检索、上下文构建和错误学习。
- PostgreSQL 16 + pgvector 的本地开发与服务化部署；不再支持文件数据库。
- FastAPI、独立 MCP 服务、常驻异步 Worker，以及包含历史导入治理能力的管理后台。
- 项目级与全局级 Scope；全局知识必须经过治理流程。

### 当前不承诺的范围

- 这不是通用的自主知识库，也不会替代人工判断、代码审查或生产变更审批。
- L0 是事实来源；LLM 或规则只允许生成可追溯的派生候选，不能改写原始消息，也不能自行改变项目、Scope、审核或发布状态。
- `ErrorMemoryExtractor` 当前只用于 shadow 评估；不能把它理解为已经开放的全自动 LLM 分类、事实拆分或知识综合流水线。
- 稠密检索和远程 Embedding Provider 是可配置增强能力，不保证任何 Provider 的可用性、召回质量或跨模型分数可比较；默认应保留词法降级路径。
- 当前没有内置 HTTPS 反向代理、Token 轮换/吊销、完整多租户计费、备份恢复编排、告警体系或高可用数据库集群。这些需要由部署环境补齐。
- Compose 中的 `worker` 以常驻异步循环消费 Outbox/Processing Job，并在同一进程中按计划运行反思任务；V1.3.0 已不要求手工执行一次性 Worker 命令。
- 不以 Redis、Celery 或其他外部队列为运行前提；如果接入，需要自行设计容量、重试、数据驻留和故障恢复策略。

## 四、如何使用

### 1. 本地安装

要求 Python 3.10 或更高版本：

```powershell
cd "G:\Codex Project\20260703-codex-memory-system"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Windows 也可以使用项目脚本。首次执行会准备本地 Python 虚拟环境并安装项目：

```powershell
.\start-local.ps1 health
.\start-local.ps1 append --project demo --conversation c1 --role user --content "Bug: auth token refresh throws an exception"
.\start-local.ps1 context --project demo --task "Fix auth token refresh"
```

不带参数执行 `start-local.ps1` 会启动本地 HTTP 服务，默认地址为 `http://127.0.0.1:8000`；服务必须能够通过 `CODEX_MEMORY_DATABASE_URL` 连接已迁移的 PostgreSQL，可通过 `CODEX_MEMORY_HTTP_HOST` 和 `CODEX_MEMORY_HTTP_PORT` 修改监听地址。

### 2. CLI 示例

下面示例通过正式 V1 API 访问 PostgreSQL。请先设置 API 地址和项目 Token：

```powershell
$env:CODEX_MEMORY_API_URL = "http://127.0.0.1:8000"
$env:CODEX_MEMORY_API_TOKEN = "<项目 API Token>"

python -m codex_memory.cli append `
  --project demo --conversation c1 --role user `
  --content "Bug: migration fails after retry" --process-now

python -m codex_memory.cli retrieve `
  --project demo --query "migration retry bug"

python -m codex_memory.cli context `
  --project demo --task "Fix migration retry bug"

python -m codex_memory.cli reflect --project demo
python -m codex_memory.cli health
```

V1.3.2 接入命令示例：

```powershell
python -m codex_memory.cli init --project demo --project-root . --api-url http://127.0.0.1:8000 --token '<项目 API Token>' --install-hook
python -m codex_memory.cli status --project-root .
python -m codex_memory.cli doctor --project-root .
python -m codex_memory.cli hook uninstall --project-root .
```

本地或诊断场景使用 V1.3.1 导入命令时，需要连接已经执行 Alembic 迁移的数据库：

$env:CODEX_MEMORY_DATABASE_URL = "postgresql+psycopg://codex:change-me@127.0.0.1:5432/codex_memory"
```powershell
python -m codex_memory.cli import --project demo .\docs\guide.md .\schema.sql
```

导入内容按 ImportBatch、ImportFile、ImportIssue、Processing Job 和候选记忆生命周期保存，并保留来源、版本与 Scope；正式 Memory 仍需审核和既有治理流程。

Docker 部署后默认访问 <http://127.0.0.1:5174/imports> 使用管理端导入历史资料。页面支持创建批次、上传资料、启动异步解析、查看进度和候选、批准发布、拒绝、取消、重试和批次软回滚；导入内容不会绕过审核直接写入正式 Memory。支持 Markdown、TXT、JSON/JSONL、SQL、源码、PDF、DOCX 和 ZIP；大文件可跨请求分片上传，原文可使用数据库或文件系统对象存储；疑似 Prompt Injection 内容会被隔离，常见凭据会在候选层脱敏。本机 Admin Web 因端口占用使用 `5175`，MCP 已恢复正式端口 `8001`。

文件系统对象存储需要让 API 和 Worker 使用相同路径；Compose 通过共享 `importdata` 卷满足这一要求：

```powershell
$env:IMPORT_STORAGE_BACKEND = "filesystem"
$env:IMPORT_STORAGE_PATH = ".\.codex-import-storage"
```


`append` 通过正式 API 写入 PostgreSQL 和 transactional Outbox；异步任务由常驻 Worker 消费，失败任务通过管理 API 或管理后台治理。

### 3. HTTP API

本地 PostgreSQL API：

```powershell
python -m codex_memory.cli serve --host 127.0.0.1 --port 8000
```

生产版 API 使用 `/api/v1` 和 Bearer Token：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/append` | 幂等追加 L0 消息 |
| POST | `/api/v1/search` | 按项目/Scope 检索记忆 |
| POST | `/api/v1/context` | 构建答案前上下文 |
| POST | `/api/v1/memory` | 写入 L1 候选知识 |
| POST | `/api/v1/reflect` | 对项目执行反思 |
| GET | `/api/v1/health` | 检查数据库、Outbox 和向量状态 |

管理 API 的正式命名空间统一为 `/api/admin/v1/*`。登录并通过项目、Scope 和权限校验后，可查询运行状态和审计数据，也可执行历史导入、取消、重试、回滚与候选审核等受控写操作。现行契约以 `docs/PROJECT_STATUS_AND_NEXT_STEPS.md` 和 `docs/PROJECT_HANDOFF.md` 为准。

### 4. MCP

本地 stdio MCP：

```powershell
python -m codex_memory.cli mcp
```

HTTP MCP 客户端通过以下地址访问部署后的服务：

```text
http://127.0.0.1:8001/mcp
```

MCP 适配器通过认证的 HTTP API 访问数据库，不直接绕过 API 读写数据库。

### 5. Codex Hook

仓库内的 `.codex/hooks.json` 已声明 `UserPromptSubmit` 和 `Stop` 两个 Hook。使用前需要在运行 Codex 的环境中配置：

```powershell
$env:CODEX_MEMORY_PROJECT_MAP='{"G:/Codex Project/20260703-codex-memory-system":"20260703-codex-memory-system"}'
$env:CODEX_MEMORY_API_URL='http://127.0.0.1:8000'
$env:CODEX_MEMORY_API_TOKEN='<项目 API Token>'
# 可选：覆盖默认临时目录中的 JSONL outbox
$env:CODEX_MEMORY_OUTBOX_PATH='C:\codex-memory\outbox.jsonl'
```

`CODEX_MEMORY_PROJECT_MAP` 的键是仓库路径，值是服务端项目键。不要把 Token 写入 Hook outbox、提交到仓库或放入公开日志。

## 五、如何部署

### 推荐方式：Docker Compose

要求 Docker Desktop 或 Docker Engine + Compose。Compose 会启动 `postgres`、`api`、`mcp`、`worker` 和 `admin-web` 五个服务，其中 PostgreSQL 使用 `pgvector/pgvector:pg16`，数据库数据保存在 `pgdata` 命名卷。

1. 创建环境文件：

```powershell
cd "G:\Codex Project\20260703-codex-memory-system"
Copy-Item .env.example .env
```

2. 编辑 `.env`，至少修改以下值：

```dotenv
POSTGRES_PASSWORD=<随机数据库密码>
CODEX_MEMORY_DATABASE_URL=postgresql+psycopg://codex:<同一个数据库密码>@postgres:5432/codex_memory
CODEX_MEMORY_SERVICE_TOKEN=<随机高强度 Token>
CODEX_MEMORY_BOOTSTRAP_PROJECT_KEY=<项目键>
CODEX_MEMORY_BOOTSTRAP_PROJECT_NAME=<项目名称>
CODEX_MEMORY_ADMIN_PASSWORD=<随机管理员密码>
CODEX_MEMORY_ADMIN_SESSION_SECRET=<随机会话密钥>
CODEX_MEMORY_ADMIN_PROJECT_KEY=<后台默认项目键>
```

`CODEX_MEMORY_SERVICE_TOKEN` 会作为引导项目的 API Token 和 MCP 服务 Token。首次部署使用的值应当是随机高强度值；示例文件中的默认值只适用于本地演示，不适用于生产环境。

3. 构建并启动：

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

4. 访问服务：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| API | `http://127.0.0.1:8000` | V1/V1.1 HTTP API |
| MCP | `http://127.0.0.1:8001/mcp` | Streamable HTTP MCP |
| 管理后台 | `http://127.0.0.1:5174` | 管理观测与 V1.3.1 历史导入 |
| PostgreSQL | Compose 内部 `postgres:5432` | 不建议直接暴露到宿主机 |

API 容器启动时会执行 `alembic upgrade head`，然后幂等执行 bootstrap 创建首个项目和 Token。部署后可以查看日志：

```powershell
docker compose logs -f api mcp worker admin-web
```

### 生产部署必须补齐的工作

- 使用反向代理和 HTTPS，不直接把内部服务暴露到公网。
- 为不同项目或调用方创建独立 Token，并规划轮换、吊销和过期策略。
- 配置 PostgreSQL 备份、恢复演练、迁移监控和 `pgdata` 持久化保护。
- 监控 `/api/v1/health`、processing job、Outbox、retry/dead 状态和 Worker 日志。
- 根据数据驻留要求决定是否启用远程 Embedding Provider，并配置脱敏、超时、预算和降级策略。
- 不要执行 `docker compose down -v`，否则会删除 `pgdata` 数据卷。

## 六、开发验证

按仓库约束执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node tools/static_check.js
npm test --prefix apps/admin-web
npm run build --prefix apps/admin-web
git diff --check
```

需要 PostgreSQL 迁移覆盖时，额外设置 `CODEX_MEMORY_POSTGRES_TEST_URL` 后运行对应测试。Docker Compose 配置可用 `docker compose config` 检查。

## 相关文档

- [项目状态与下一步](docs/PROJECT_STATUS_AND_NEXT_STEPS.md)：当前实现、现行契约、默认端口和文档优先级。
- [V1.3.1 历史导入交接](docs/HANDOFF_HISTORICAL_IMPORT.md)：导入能力、迁移、验收证据、风险和后续计划。
- [V1.1 执行规格](docs/CODEX_MEMORY_V1_1_EXECUTABLE_SPEC.md)：API、Outbox、Worker、检索、Embedding Profile 和治理契约。
- [V1.2 架构](docs/v1.2/architecture.md)：管理观测界面的边界和请求模型。
- [V1.2 管理 API 概览](docs/v1.2/api-overview.md)：后台查询接口。
- [项目交接说明](docs/PROJECT_HANDOFF.md)：项目总体状态、现行契约和历史 V1 验收快照。
- [项目约束](PROJECT_CONSTRAINTS.md)：语言、编码和提交前检查要求。
