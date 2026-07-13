# codex-memory V1.2 开发计划

## 文档信息

- 目标版本：V1.2
- 主题：管理控制台与历史知识导入
- 状态：方案 A 已确认，等待计划文档审阅
- 日期：2026-07-13
- 前置版本：V1.1

## 1. 执行摘要

V1.2 将 codex-memory 从提供记忆写入、检索与异步处理能力的服务，扩展为一个受治理的知识管理控制面。交付物分为两部分：

1. 独立部署的 Vue 管理端与 FastAPI Admin API，用于查看、审查、调试和受控运维。
2. 将历史项目材料转为可追溯 Candidate、经审查后发布为正式 Memory 的导入管线。

V1.2 不创建绕开领域规则的数据库管理工具。任何状态变更都必须经由 Command Service、V1.1 领域服务或新的 Import Domain，并以 Outbox、Job 和审计记录保证可重试、可追踪和可回滚。

本计划覆盖六个阶段。P0 等同于 Phase 1：先交付只读管理控制台基座；Phase 1 是本文件中唯一拆到可直接编码任务粒度的阶段。后续阶段提供完整的范围、依赖、验收与发布门槛，待 P0 完成并复盘后再生成各阶段的细化实施计划。

## 2. 当前基线与约束

### 2.1 已有 V1.1 能力

以下能力已被现有状态文档确认，并是 V1.2 必须复用而非重写的底座：

- 事务性 append、项目级幂等、L0 原始记录和 Outbox。
- Job 的原子领取、租约、心跳、重试、死信与过期恢复。
- Lexical/Dense 混合检索、RRF、项目与全局范围隔离、检索审计和上下文预算。
- 不可变 Embedding Profile、向量回填、Canary、回滚与 Provider 预算。
- Candidate、Evidence、Policy、人工审查、发布开关与治理审计。
- LLM Shadow Extractor、脱敏、Prompt Injection 检测和本地优先策略。
- SQLite 与 PostgreSQL/pgvector 双环境验证。

权威依据为 [IMPLEMENTATION_STATUS.md](/G:/Codex%20Project/20260703-codex-memory-system/IMPLEMENTATION_STATUS.md)。V1.2 的每一项功能必须保持 V1.1 行为兼容：关闭 V1.2 Feature Flag 后，append、检索、MCP 与 Worker 的既有行为不得改变。

### 2.2 现有 Admin 原型的处理原则

仓库中存在未提交的 `admin/` 原型目录。它不构成 V1.2 架构基线，也不允许直接成为生产管理端。Phase 1 的架构核查必须为其输出一项明确结论：

- 可复用的仅限视觉资产、只读查询代码或无领域副作用的通用组件；
- 任何直接更新领域表、没有权限边界、没有审计或缺少 API 契约测试的代码必须重构或废弃；
- 正式前端以 `apps/admin-web` 为唯一交付路径，正式服务以 `/api/admin/v1` 为唯一对外 API 命名空间。

### 2.3 强制约束

- 所有管理查询和导入操作必须绑定 `project_id`；V1.2 新数据必须绑定 `scope_id`。
- V1.1 中的 `scope` 字符串语义与 V1.2 的 `scope_id` 不能混用。Phase 1 必须通过 ADR 定义兼容映射和迁移策略。
- 原始 L0、上传源文件、解析后的源内容和 Evidence 均不可覆盖或物理删除。
- 导入内容默认只能生成 Candidate，不能绕过 Evidence、Policy 和人工审查直接写入正式 Memory。
- 管理端不读取或返回 Provider 密钥明文；检测到凭证的内容在发送给远程 Provider 前必须脱敏或阻断。
- 所有危险写操作必须包含操作者、原因、请求 ID、变更前后摘要、结果和失败原因。
- PostgreSQL 是生产目标；SQLite 必须继续作为单元测试和本地验证环境。

## 3. 方案 A：目标架构

```text
Vue Admin Web (apps/admin-web)
        |
        | HTTPS / JSON
        v
FastAPI Admin API (/api/admin/v1)
  - auth context / project grants
  - read query services
  - command services / audit
        |
        +-----------------------------+
        |                             |
        v                             v
Existing V1.1 Domain Services     Import Domain
Candidate / Memory / Retrieval    Batch / File / Chunk / Issue
Embedding / Flags / Budget        Parser / Normalizer / Dedup
        |                             |
        +-------------+---------------+
                      v
             Outbox / Job / Worker
                      |
          PostgreSQL + Object Storage
```

### 3.1 分层职责

| 层 | 职责 | 禁止事项 |
| --- | --- | --- |
| Admin Web | 登录、路由、查询展示、操作确认、表单校验 | 不持有业务状态机；不拼装数据库更新语句 |
| Admin API | 认证、权限、请求校验、查询 DTO、命令入口、审计关联 | 不直接修改 Candidate、Memory、Job、Outbox 状态字段 |
| Query Service | 按权限、项目、Scope 过滤并组装只读视图 | 不暴露未过滤的跨项目查询 |
| Command Service | 验证权限和前置状态，调用领域服务，创建审计事件 | 不绕过领域状态机 |
| V1.1 Domain | 现有 Candidate、Memory、Embedding、Retrieval、Flag、Budget 规则 | 不感知管理端 UI 细节 |
| Import Domain | 导入批次、文件、解析、切块、安全检查、去重、Candidate 生成、回滚 | 不直接发布正式 Memory |
| Worker | 幂等执行异步工作、分类失败、重试和恢复租约 | 不信任 API 传入的项目或 Scope，必须二次校验 |

### 3.2 目录边界

```text
src/codex_memory/
  admin/
    router.py              # Admin API 路由与依赖注入
    schemas.py             # 请求、响应和分页契约
    auth.py                # 管理身份与当前主体
    permissions.py         # resource:action 与 project grant
    query_service.py       # 只读投影查询
    command_service.py     # 受控命令入口
    pagination.py          # 白名单排序、游标/页码分页
    audit.py               # 管理操作审计
    errors.py              # 标准错误响应
  imports/
    models.py
    schemas.py
    service.py
    state_machine.py
    handlers.py
    storage.py
    parsers/
    chunkers/
    normalizer.py
    deduplicator.py
    redaction.py
    rollback.py
  v11_*.py                 # 保持为 V1.1 领域能力的实现位置

apps/admin-web/
  src/api/                 # 每个 Admin API 资源的客户端
  src/views/               # dashboard、projects、imports 等页面
  src/components/          # 通用表格、详情抽屉、确认弹窗、审计面板
  src/router/              # 路由和权限守卫
  src/stores/              # 会话、权限、URL 查询状态
  src/types/               # API DTO 类型
  tests/                   # Vitest
  e2e/                     # Playwright
```

## 4. 核心数据与状态原则

### 4.1 管理与授权

第一版角色为 `Viewer`、`Reviewer`、`Operator`、`Admin`。权限使用 `resource:action`，并叠加项目授权：角色定义最大权限，`project_grants` 决定可访问的项目集合。所有列表、详情和命令均以当前主体的 grant 作为不可跳过的过滤条件。

Phase 1 将定义并落地最小授权模型：`dashboard:read`、`project:read`、`scope:read`、`raw_memory:read`、`memory:read`、`candidate:read`、`job:read`、`outbox:read`、`retrieval:debug`、`audit:read`。写权限将在 阶段 2-5 按实际命令增加，不能预先开放。

### 4.2 Scope 的兼容策略

V1.2 引入 `knowledge_scopes`，作为项目内部更细粒度的知识隔离单位。V1.1 的既有项目级数据不被重写；管理查询将其投影至每个项目的逻辑 `default` Scope。导入产生的所有新数据必须持有真实 `scope_id`。在正式将 `scope_id` 添加到既有实体前，查询服务必须明确标注投影来源，禁止假装旧记录已具备物理 Scope 归属。

### 4.3 历史导入数据模型

后续阶段将以增量迁移引入以下实体：

- `import_batches`：项目、Scope、创建者、输入方式、状态、统计和回滚信息。
- `import_files`：不可变文件元信息、SHA-256、对象存储定位、解析状态和错误摘要。
- `import_chunks`：规范化内容、来源定位、内容 Hash、安全检查和去重结果。
- `import_issues`：解析、脱敏、注入检测、重复和策略相关问题。
- `import_candidate_links`：导入 Chunk、Candidate、Evidence 与发布 Memory 的可追溯关联。
- `admin_audit_events`：比通用安全审计更完整的受控操作审计投影；可关联既有 `security_audits`，但不替换它。

所有迁移为 guarded additive migration：PostgreSQL 和 SQLite 均可升级、回退并保持 V1.1 服务可运行。

### 4.4 导入状态机

```text
draft -> uploaded -> queued -> processing -> awaiting_review -> completed
                  \-> failed -> retry_wait -> processing
any nonterminal -> cancelled
completed -> rollback_requested -> rolling_back -> rolled_back
                                  \-> rollback_conflict
```

文件和 Chunk 有各自状态，单个文件或 Chunk 的失败不得阻断同批次中无关条目的处理。状态转移只能由 Import Domain 执行，并使用幂等键与 Job 关联。

## 5. 分阶段路线图

### 阶段 1 / P0：只读管理控制台基座

目标：在不开放任何后台业务写操作的前提下，提供可独立部署、具备项目隔离与审计能力的管理控制台。

范围：身份与权限、项目/Scope 投影、Dashboard、原始记录、Candidate、Memory、Job、Outbox、Retrieval Audit 的只读查询；Vue 管理端骨架；分页、排序、筛选、URL 查询状态；测试和部署骨架。

不包含：文件上传、导入命令、Candidate 发布、Job 重试、Outbox Replay、Embedding Backfill、Provider 配置和 Feature Flag 变更。

### 阶段 2：历史知识导入基础管线

目标：实现 Markdown、TXT、JSON、JSONL 和文本粘贴的受控导入。

范围：Import Batch/File/Chunk/Issue 模型、文件存储抽象、解析器、结构化切块、规范化、精确去重、脱敏、Prompt Injection 检测、Candidate 生成、Worker 处理、进度与失败重试。导入必须显式指定项目与 Scope，且只能以 Candidate 结束。

### 阶段 3：审核、发布与回滚

目标：完成历史知识从 Candidate 到正式 Memory 的治理闭环。

范围：批量批准/拒绝、Policy 和 Evidence 展示、正式发布、Embedding 生成、Memory 禁用/恢复、批次回滚与冲突处理、全链路审计。回滚不得静默覆盖发布后的人为修改。

### 阶段 4：高级解析器

目标：扩展历史材料支持范围。

范围：PDF、DOCX、SQL、代码与 ZIP。ZIP 必须防路径穿越、压缩炸弹、过深嵌套和总大小失控；解析结果必须可定位到页码、标题、代码对象或 SQL 对象。

### 阶段 5：受控运维控制面

目标：向有权限的 Operator/Admin 开放已存在能力的受控命令入口。

范围：Job Retry、Outbox Replay、超时锁释放、Embedding Backfill、Provider 连通性测试、项目预算变更、Feature Flag 灰度和回滚。所有危险操作要求二次确认与原因，且经 Command Service 调用 V1.1 领域服务。

### 阶段 6：Retrieval Playground 与质量分析

目标：提供可解释的检索调试和知识质量观测。

范围：Lexical/Dense 候选、RRF 分数、Scope/Policy/Budget 过滤原因、最终上下文预览、Retrieval Audit、命中率、Candidate 转化率、Memory 使用统计和 Shadow 对比。调试请求必须始终在授权项目和 Scope 内执行。

## 6. 阶段 1 / P0 可执行任务清单

每项完成后必须运行本项测试和既有 V1.1 回归；任务按序执行，除明确说明可并行的前端工作外，不跳过依赖。

| 编号 | 工作项 | 主要位置 | 交付与验收 |
| --- | --- | --- | --- |
| P0-01 | 基线保护与复用清单 | `docs/v1.2/`, `tests/` | 记录 V1.1 模型、服务、状态机、API 和现有 `admin/` 原型的复用/淘汰判断；新增回归矩阵，确认 Feature Flag 关闭时 V1.1 行为不变。 |
| P0-02 | ADR 与领域边界 | `docs/v1.2/adr/`, `docs/v1.2/` | ADR 至少覆盖管理端独立部署、查询/命令分离、Scope 兼容、审计、源数据不可变和原型处置；接口边界没有“通用 CRUD 更新”例外。 |
| P0-03 | Admin 身份、角色与项目授权模型 | `src/codex_memory/admin/auth.py`, `permissions.py`, Alembic | 采用现有 Token/Auth 能力或增量扩展，不暴露密钥；实现角色、`resource:action` 和项目 grant；越权查询返回标准化 403。 |
| P0-04 | Scope 投影与迁移基础 | `v12_models.py` 或等价模块、Alembic | 创建 `knowledge_scopes` 与项目 default Scope 的兼容投影；不改写旧记录；SQLite/PostgreSQL 迁移和回退测试通过。 |
| P0-05 | Admin API 基础设施 | `admin/router.py`, `schemas.py`, `pagination.py`, `errors.py` | 建立 `/api/admin/v1`、请求 ID、统一错误格式、身份依赖、分页、白名单排序、最大 page size 200；恶意排序字段和过大分页被拒绝。 |
| P0-06 | 只读 Query Service | `admin/query_service.py` | 实现项目、Scope、L0 原始记录、Candidate、Memory、Job、Outbox、Retrieval Audit、Dashboard 聚合查询；每个查询在 SQL 层强制项目授权过滤。 |
| P0-07 | 管理审计读模型 | `admin/audit.py`, `admin_audit_events` 迁移 | 将请求 ID、主体、资源、查询/访问结果关联至审计视图；P0 只记录必要访问事件和 API 拒绝，不打开业务写命令。 |
| P0-08 | Admin Web 工程骨架 | `apps/admin-web/` | Vue 3 + TypeScript + Vite + Element Plus + Pinia + Router；登录态、Axios 拦截、错误页、权限路由守卫、环境配置、构建脚本可运行。 |
| P0-09 | 只读页面与 URL 状态 | `apps/admin-web/src/views/` | Dashboard、项目/Scope、原始记录、Candidate、Memory、Job、Outbox、Retrieval Audit 页面；筛选、排序、分页和详情抽屉；所有查询条件可由 URL 恢复。 |
| P0-10 | API 契约与权限测试 | `tests/test_admin_*.py` | 覆盖 401、403、跨项目、跨 Scope、分页、排序白名单、空结果与脱敏展示；对所有列表 API 建立 SQLite 与 PostgreSQL 契约测试。 |
| P0-11 | 前端测试与端到端只读流 | `apps/admin-web/tests/`, `e2e/` | Vitest 覆盖 store、API client、权限指令、筛选和错误提示；Playwright 覆盖登录、项目切换、列表过滤、详情查看和越权提示。 |
| P0-12 | 部署、观测与灰度 | `docker-compose.yml`, `docs/v1.2/` | Compose 增加 `admin-web`；生产路由建议 `/admin` 和 `/api/admin/v1`；P0 默认只读、单独 Flag 控制；输出部署手册和 P0 验收报告。 |

### 6.1 Phase 1 API 契约范围

P0 仅增加读接口。建议最小资源集合：

```text
GET /api/admin/v1/me
GET /api/admin/v1/dashboard
GET /api/admin/v1/projects
GET /api/admin/v1/projects/{project_key}
GET /api/admin/v1/projects/{project_key}/scopes
GET /api/admin/v1/raw-records
GET /api/admin/v1/candidates
GET /api/admin/v1/memories
GET /api/admin/v1/jobs
GET /api/admin/v1/outbox-events
GET /api/admin/v1/retrieval-audits
GET /api/admin/v1/audit-events
```

所有列表统一支持 `page`、`page_size`、白名单 `sort`、`order` 和资源特定筛选。响应必须包含数据、分页元数据和请求 ID；不得返回跨项目计数、未授权资源标识或密钥材料。

### 6.2 Phase 1 完成定义

- Admin Web 能独立构建、部署和访问，且默认只读。
- 每个 API 和前端页面按当前主体、项目 grant 和 Scope 规则隔离。
- 列表提供分页、排序、过滤和 URL 状态恢复。
- Admin API 不存在直接更新 V1.1 领域状态的端点或 Repository 通路。
- 原型 `admin/` 的处置在 ADR 中记录，正式部署只使用 `apps/admin-web`。
- SQLite 单元/契约测试、PostgreSQL Compose 集成测试、前端 Vitest 和关键 Playwright 流程全部通过。
- 全部 V1.1 回归通过，且禁用 V1.2/P0 Flag 后不影响 V1.1 API、MCP 与 Worker。

## 7. 后续阶段的命令与安全边界

### 7.1 阶段 2 导入命令

```text
POST /api/admin/v1/import-batches
POST /api/admin/v1/import-batches/{id}/files
POST /api/admin/v1/import-batches/{id}:start
POST /api/admin/v1/import-batches/{id}:cancel
POST /api/admin/v1/import-batches/{id}:retry
```

上传接口只持久化源文件和创建 Job，绝不在 HTTP 请求中同步解析大文件。Worker 根据 `import_batch_id` 重新加载项目、Scope、权限策略和 Feature Flag。文件 SHA-256、Chunk normalized hash、内容定位和安全问题必须保留为可审计记录。

### 7.2 阶段 3-5 危险命令

包括 Candidate 批准/拒绝、Memory 发布/禁用、批次回滚、Job Retry、Outbox Replay、释放锁、Embedding Backfill、Provider 测试、预算更新和 Flag Rollout。每条命令都必须：

1. 验证角色、项目和 Scope 权限。
2. 验证领域状态机允许转移。
3. 记录操作者、原因、request ID、资源、前后摘要和结果。
4. 使用幂等键；异步操作创建 Outbox/Job。
5. 对会破坏运行状态的操作要求二次确认。

## 8. 验证策略

### 8.1 后端测试

- 单元测试：状态机、权限、项目/Scope 隔离、分页、排序、乐观锁、审计、解析、脱敏、注入检测、去重、回滚。
- API 契约测试：所有读 API 的身份、授权、参数边界、DTO 脱敏和分页。
- 集成测试：文件上传到 Candidate、Candidate 到 Memory、Memory 到 Embedding、失败重试、Worker 重领、Outbox 重放和批次回滚。
- 故障注入：Parser 异常、存储不可用、数据库断连、429/500/超时、向量维度错误、Worker 中断、重复派发和回滚中断。

### 8.2 前端与端到端测试

- Vitest：权限守卫、会话状态、API client、查询状态、表格过滤、错误提示和危险操作确认。
- Playwright：登录、项目访问隔离、P0 只读浏览；后续阶段增加上传、导入进度、审查、发布、检索验证与回滚的完整闭环。

### 8.3 发布门槛

每个阶段必须同时满足：新增测试通过、既有 V1.1 回归通过、SQLite 验证通过、PostgreSQL Compose 验证通过、迁移可升级并经测试回退、对应 Feature Flag 默认关闭、审计记录可查询。

## 9. 灰度与部署

部署服务为 `postgres`、`api`、`worker`、`admin-web`；对象存储和反向代理可选。生产路由建议：

```text
/admin              -> Vue 静态文件
/api/admin/v1       -> FastAPI Admin API
```

灰度按以下顺序进行：

1. 内部只读：开放 P0 页面，禁止所有管理写操作。
2. 测试项目导入：仅 Admin，支持小批量 Markdown/TXT/JSON/JSONL，人工审查和批次回滚。
3. 小规模生产导入：受限项目百分比，增加 PDF/DOCX、批量审查和 Retrieval 验证。
4. 全面开放：高级解析、运维命令和质量分析，但仍以独立 Flag 控制能力。

建议环境变量包括 `ADMIN_AUTH_SECRET`、`ADMIN_ALLOWED_ORIGINS`、`IMPORT_STORAGE_BACKEND`、`IMPORT_STORAGE_PATH`、`IMPORT_MAX_FILE_SIZE`、`IMPORT_MAX_BATCH_SIZE`、`IMPORT_MAX_FILES`、`IMPORT_WORKER_CONCURRENCY`、`IMPORT_REMOTE_PROVIDER_ENABLED`、`IMPORT_DEFAULT_REVIEW_MODE` 和 `IMPORT_SECRET_DETECTION_ENABLED`。

## 10. 主要风险与缓解措施

| 风险 | 缓解措施 |
| --- | --- |
| 低质量历史资料污染知识库 | 默认 Candidate、Evidence 可追溯、Policy 校验、人工审查、批次回滚 |
| 跨项目或跨 Scope 泄漏 | API、Query Service、Worker 和测试四层均强制校验；旧记录使用显式 default Scope 投影 |
| 凭证或敏感信息泄漏 | 本地检测/脱敏、远程 Provider 发送前阻断、后台永不返回明文密钥 |
| 大批量导入挤占 V1.1 Worker | 独立 Job 类型/优先级、批量限额、项目并发限制、预算与背压 |
| 后台绕过状态机 | Command Service、禁止通用 Update、契约测试、审计和代码审查门槛 |
| 回滚覆盖人工修改 | 逻辑禁用而非物理删除、版本检查、引用检测与 `rollback_conflict` 人工处理 |
| 原型代码与正式架构混淆 | P0-01/P0-02 强制记录处置决策，正式 CI/Compose 仅包含 `apps/admin-web` |

## 11. 明确非目标

V1.2 不包含 Notion/Confluence/飞书等在线源同步、Git 仓库定时增量同步、原始记录编辑、Embedding Profile 原地修改、绕过 Candidate/Policy 的批量发布、无审核自动进入正式知识库、企业 SSO、复杂 IAM、知识图谱、自动执行导入文件中的命令，或跨项目知识自动共享。

## 12. 文档与后续计划产物

P0 启动前应先创建或更新以下文档：

- `docs/v1.2/architecture.md`
- `docs/v1.2/domain-boundaries.md`
- `docs/v1.2/api-overview.md`
- `docs/v1.2/permission-matrix.md`
- `docs/v1.2/migration-strategy.md`
- `docs/v1.2/p0-acceptance.md`

在本设计获审阅批准后，应使用独立的实施计划将 P0-01 至 P0-12 拆成文件级步骤、测试先行顺序和检查点；在 Phase 1 验收后，再分别为 阶段 2-6 生成同等粒度的实施计划。
