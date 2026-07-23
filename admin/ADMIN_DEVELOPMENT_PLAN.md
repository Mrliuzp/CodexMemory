# Codex Memory 管理后台 — 开发计划文档

版本：1.0
日期：2026-07-15
状态：规划阶段（Phase 0）

---

## 1. 项目概述

本文档定义了 **20260703-codex-memory-system** 项目下 `admin/` 目录的管理后台系统开发路线图。管理后台的目标是为 Codex Memory 提供完整的可视化运维与管理能力，覆盖项目监控、作业管理、记忆审查、系统配置、审计追踪与数据浏览。

---

## 2. 现有系统评估

### 2.1 双重前端现状

当前项目存在**两套独立的管理前端实现**，需要整合为统一架构：

| 维度 | `admin/` (原生 JS SPA) | `apps/admin-web/` (Vue 3 + Element Plus) |
|------|----------------------|----------------------------------------|
| **状态** | 功能性可用，10+ 页面完整 | 开发中，4 个主页面 + 登录页 |
| **技术栈** | Vanilla JS + Fetch API | Vue 3 + Vue Router + Pinia + Element Plus |
| **后端** | `admin/main.py` 内嵌 FastAPI 路由 | 独立开发，需要单独部署 |
| **认证** | 无（Bearer token 直通） | 用户名/密码登录，JWT Token |
| **页面** | 仪表盘、项目、作业、候选、开关、配置、记忆、日志、令牌、审计 | Dashboard, Projects, Records, SystemStatus, Login |
| **API 前缀** | `/api/admin/` | `/api/admin/v1/`（需统一） |
| **数据绑定** | 已匹配后端返回格式 | 部分页面（Records）列定义与后端不匹配 |

### 2.2 后端 `admin/main.py` 现状

| 端点 | 状态 | 说明 |
|------|------|------|
| `GET /api/admin/health` | 完成 | 系统健康检查 + 数据库/向量状态 |
| `GET /api/admin/stats` | 完成 | 仪表盘统计（项目、作业、候选、记忆分布） |
| `GET /api/admin/projects` | 完成 | 项目列表 |
| `GET /api/admin/projects/{id}` | 完成 | 项目详情（含功能开关与处理策略） |
| `GET /api/admin/jobs` | 完成 | 作业列表（支持 project_id/status/job_type 过滤） |
| `POST /api/admin/jobs/{id}/retry` | 完成 | 重试死信或等待重试作业 |
| `GET /api/admin/candidates` | 完成 | 候选记忆列表（默认隐藏 shadow） |
| `POST /api/admin/candidates/{id}/review` | 完成 | 批准/拒绝候选记忆 |
| `GET /api/admin/profiles` | 完成 | 嵌入配置列表 |
| `POST /api/admin/profiles` | 完成 | 创建嵌入配置 |
| `GET/PUT /api/admin/flags/{project_id}` | 完成 | 读取/更新项目功能开关 |
| `GET /api/admin/memories` | 完成 | 记忆列表（支持搜索、过滤、分页） |
| `GET /api/admin/logs` | 完成 | L0 原始日志（支持角色过滤、分页） |
| `GET /api/admin/token-usage` | 完成 | 令牌用量统计 |
| `GET /api/admin/audit-logs` | 完成 | 安全审计日志 |
| SPA 回退 `/{path:path}` | 完成 | 为前端 SPA 提供 index.html |

### 2.3 已知问题

1. **API 路径不一致**：Vue 前端使用 `/api/admin/v1/` 前缀，但后端路由是 `/api/admin/`，导致 404
2. **登录端点缺失**：Vue 前端需要 `POST /api/admin/v1/login`，后端无此路由
3. **Dashboard 端点缺失**：Vue 前端调用 `GET /api/admin/v1/dashboard`，后端无此路由
4. **System Status 端点缺失**：Vue 前端调用 `GET /api/admin/v1/system/status`，后端无此路由
5. **Records 页面数据不匹配**：Records 定义了 7 种数据类型（candidates/memories/jobs/raw-records/outbox-events/retrieval-audits/audit-events），但后端只实现了部分端点
6. **认证机制未统一**：后端 `admin/main.py` 使用 Bearer token（从 `codex_memory.auth` 导入），但 Vue 前端尚未正确集成
7. **CORS 配置**：开发时前后端分离（Vite 5174 + Uvicorn 8500），需要 CORS 中间件
8. **中文乱码**：现有 CSS/JS 文件中的中文字符在 UTF-8 读取时显示为乱码
9. **维护模式缺失**：全局 HTTP 服务切换需要维护模式门禁（TDD 测试已写但未实现）

### 2.4 整体项目缺失能力

1. **管理后台认证系统** — 无登录页/权限体系（后端 `admin/main.py` 直接暴露）
2. **实时监控与告警** — 无 WebSocket 推送、无告警通知
3. **数据导入导出** — 批量导入历史项目知识、导出为 JSON/CSV
4. **视觉图表仪表盘** — 当前仅数字指标卡片，无趋势图/饼图/柱状图
5. **操作日志可视化** — 审计日志仅有表格展示
6. **用户管理** — 多管理员账户与角色权限
7. **配置持久化** — `admin/main.py` 硬编码数据库连接，无法通过 UI 修改
8. **多语言支持** — 当前仅有中文硬编码
9. **单元测试覆盖** — `admin/` 后端无测试，`apps/admin-web/` 仅有一个路由测试

---

## 3. 统一架构愿景

```
用户浏览器
    |
    v
+------------------------------------+
|  Vue 3 + Element Plus SPA          |
|  (apps/admin-web/src/)             |
+---------------+--------------------+
                | HTTP /api/admin/*
                v
+------------------------------------+
|  admin/main.py (FastAPI)           |
|  认证路由 /login /me               |
|  业务路由 /dashboard /projects ... |
|  中间件 CORS + Auth + Log          |
+---------------+--------------------+
                | SQLAlchemy
                v
+------------------------------------+
|  codex_memory (src/codex_memory/)  |
+---------------+--------------------+
                |
                v
+------------------------------------+
|  PostgreSQL + pgvector             |
+------------------------------------+
```

### 3.1 决策原则

- **使用 Vue 3 + Element Plus** 作为统一前端框架，淘汰 `admin/static/` 下的原生 JS SPA
- **后端路由统一为 `/api/admin/`**，前端通过 Vite proxy 或 nginx 转发
- **认证使用 JWT Token**，`POST /api/admin/login` 换取 token
- **前后端合并部署**：生产环境由 nginx 提供静态文件 + 反向代理 API
- **渐进式替换**：保留 `admin/static/` 直到 Vue SPA 完全覆盖所有页面

---

## 4. 阶段规划

### Phase 0 — 现状摸底与计划 (当前)

| 任务 | 状态 | 说明 |
|------|------|------|
| 0.1 评估现有 admin 系统 | 完成 | 本文件 |
| 0.2 评估 apps/admin-web 系统 | 完成 | 本文件 |
| 0.3 编写开发计划文档 | 完成 | 本文件 |
| 0.4 创建建议的目录结构 | 待办 | Phase 1 开始前建立 |
| 0.5 识别缺失 MCP/Skill | 待办 | 见第 6 节 |

### Phase 1 — 修复现有问题 (P0 可编码任务)

**目标**：让 `apps/admin-web` Vue 前端能在开发环境中正常连接 `admin/main.py` 后端，所有 P0 页面可访问。

#### 1.1 后端：新增认证端点

- 创建 `POST /api/admin/login` — 接受 `{username, password}`，验证凭据，返回 JWT token
- 创建 `GET /api/admin/me` — 返回当前用户信息及权限列表
- 使用 `python-jose` 或内置 `hmac` + `hashlib` 实现 JWT
- Token 有效期默认 24 小时

依赖：`admin/main.py` 已有 `from codex_memory.auth import authenticate_bearer`

#### 1.2 后端：新增 Dashboard 端点

- 创建 `GET /api/admin/dashboard` — 聚合仪表盘数据：
  - 原始记录数（MessageRow）
  - 候选项数（MemoryCandidateRow）
  - 记忆数（MemoryRow）
  - 作业数（ProcessingJobRow）
  - 待处理作业数
  - 7 天趋势（新增记录/记忆）
  - 按项目聚合的概览

#### 1.3 后端：新增 System Status 端点

- 创建 `GET /api/admin/system/status` — 返回系统运行状态：
  - `database: "ok" | "error"`
  - `migration_schema: "ok" | "pending"`
  - `pending_jobs`: 待处理作业数
  - `server_outbox`: 待投递事件数
  - `dead_letters`: 死信事件数
  - `latest_migration`: 最新迁移版本
  - `maintenance`: 维护模式状态（计划 Phase 2）

#### 1.4 后端：新增缺失的 Records 端点

- 创建 `GET /api/admin/raw-records` — 原始消息记录（类似 `/api/admin/logs` 但按 MessageRow 字段对齐）
- 创建 `GET /api/admin/outbox-events` — Outbox 事件列表（OutboxEventRow）
- 创建 `GET /api/admin/retrieval-audits` — 检索审计列表（RetrievalAuditRow）
- 创建 `GET /api/admin/audit-events` — 审计事件列表（SecurityAuditRow，已有 `/api/admin/audit-logs`）

所有端点支持 `page` 和 `page_size` 参数。

#### 1.5 后端：添加中间件 / 全局配置

- 添加 CORSMiddleware 允许开发跨域（allow_origins=["*"]）
- 添加请求日志中间件（structlog 或标准 logging）
- 捕获 V1 的全局异常处理器，返回统一 JSON 错误格式

#### 1.6 前端：修复 API 路径和认证集成

- 修改 `apps/admin-web/src/api.js` 中 base 从 `/api/admin/v1` 改为 `/api/admin`
- 修复 LoginView.vue 调用 adminLogin() 的路径（应为 POST /api/admin/login）
- 修复 DashboardView.vue 使用真实的 dashboard 数据
- 修复 SystemStatusView.vue 使用真实的 system/status 数据
- 在 api.js 中添加缺失的端点函数

#### 1.7 前端：修复 Records 页面数据绑定

- 检查每个 Records 视图定义的列名与实际后端返回的 JSON key 是否一致
- candidates：后端返回 "candidates" 字段 -> 前端应映射为 result.data
- memories：后端返回 "memories" 数组和 "total" -> 前端需适配
- raw-records：后端 /api/admin/logs 返回 "logs" -> 前端应映射
- outbox-events、retrieval-audits、audit-events：新端点需确保返回格式一致

#### 1.8 后端：统一响应格式

创建响应包装器，将所有列表返回统一为：
```json
{ "data": [...], "total": N, "page": 1, "page_size": 50 }
```

#### 1.9 部署：Vite 代理配置

在 `apps/admin-web/vite.config.js` 中添加 dev server proxy 到 `http://127.0.0.1:8500`

### Phase 2 — 运维能力增强

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 2.1 维护模式门禁 | P0 | 实现 POST /api/admin/maintenance 切换（TDD 测试已准备） |
| 2.2 作业批量操作 | P1 | 批量重试、批量取消、作业清理 |
| 2.3 记忆手动管理 | P1 | 编辑、删除、提升/降级记忆 |
| 2.4 项目配置 UI | P1 | 项目级别功能开关、处理策略的可视化管理 |
| 2.5 嵌入 Profile 管理 | P2 | 激活/停用/回滚、金丝雀发布状态可视化 |
| 2.6 令牌预算管理 | P2 | 每日配额设置、超限告警、预算可视化 |

### Phase 3 — 数据操作能力

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 3.1 历史项目知识导入 | P1 | 批量导入历史项目记忆（JSON/CSV/Codex export） |
| 3.2 数据导出 | P2 | 项目数据导出为 JSON/Markdown/CSV |
| 3.3 数据清理与归档 | P2 | 按时间/项目清理旧数据、归档到冷存储 |
| 3.4 迁移管理 UI | P2 | Alembic 迁移状态查看、手动触发迁移 |

### Phase 4 — 视觉与监控增强

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 4.1 图表仪表盘 | P1 | 使用 ECharts 或 Chart.js 添加趋势图、分布图 |
| 4.2 WebSocket 实时推送 | P2 | 作业状态变更、新候选通知 |
| 4.3 告警规则配置 | P3 | 作业失败率超限、令牌预算超限时通知 |
| 4.4 系统拓扑图 | P3 | 服务依赖关系、容器状态可视化 |

### Phase 5 — 用户与权限

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 5.1 多管理员账户 | P2 | 注册、角色分配（admin/operator/viewer） |
| 5.2 API Key 管理 UI | P2 | 创建、吊销、权限范围配置的界面 |
| 5.3 操作审计可视化 | P2 | 审计日志的筛选、搜索、详情查看 |
| 5.4 会话管理 | P3 | 查看活跃会话、强制登出 |

### Phase 6 — 生产化与文档

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 6.1 后端单元测试 | P1 | admin/ 后端路由测试、认证测试、错误路径测试 |
| 6.2 E2E 测试 | P2 | Playwright/Cypress 端到端测试 |
| 6.3 Docker Compose 集成 | P1 | admin-web 容器加入 docker-compose.yml |
| 6.4 CI/CD 流水线 | P2 | GitHub Actions 自动构建与部署 |
| 6.5 管理员操作手册 | P2 | 中文管理员文档 |
| 6.6 API 文档 | P2 | OpenAPI/Swagger 文档完善 |

---

## 5. 目录结构建议

整合后的 admin/ 目录结构：

```
admin/
  __init__.py
  main.py             FastAPI 应用入口 + 所有路由
  auth.py             认证逻辑 (login, JWT, permission check)
  models.py           Pydantic 请求/响应模型
  middleware.py       CORS, 日志, 异常处理中间件
  routes/
    __init__.py
    auth.py           认证路由 /login, /me
    dashboard.py      仪表盘路由
    projects.py       项目管理路由
    jobs.py           作业路由
    candidates.py     候选记忆路由
    profiles.py       嵌入配置路由
    flags.py          功能开关路由
    memories.py       记忆管理路由
    records.py        只读数据路由
    system.py         系统状态路由
    token_usage.py    令牌用量路由
    audit.py          审计日志路由
  static/             淘汰中（保留作为回退）
    index.html
    css/
    js/
  start-admin.ps1     启动脚本
  ADMIN_DEVELOPMENT_PLAN.md  本开发计划文档
  tests/
    test_auth.py
    test_routes.py
    test_integration.py
```

---

## 6. MCP 与 Skill 配置

### 6.1 已配置项

| 组件 | 路径 | 状态 | 用途 |
|------|------|------|------|
| AGENTS.md | 项目根目录 | 配置 | 定义 CODEX_MEMORY_AUTO_LOG=required |
| codex-memory MCP | 全局安装 | 可用 | Codex 通过 MCP 工具写入/检索记忆 |
| codex-memory-auto-log Skill | 全局安装 | 可用 | 自动归档对话记录 |
| PROJECT_CONSTRAINTS.md | 项目根目录 | 配置 | 中文约束、编码规范、提交流程 |

### 6.2 建议新增 MCP

| MCP | 用途 | 优先级 |
|-----|------|--------|
| codex-memory-admin | 管理后台 API 的 MCP 桥接，允许 Codex 直接调用 admin 操作 | P1 |
| codex-memory-monitor | 监控指标的 MCP 查询接口（作业状态、系统健康） | P2 |

### 6.3 建议新增 Skill

| Skill | 用途 | 优先级 |
|-------|------|--------|
| codex-memory-admin-guide | 管理员操作指南，告诉 Codex 如何通过 admin API 管理记忆 | P0 |
| codex-memory-import | 历史项目知识导入工作流，自动解析并批量写入 | P1 |
| codex-memory-troubleshoot | 运维排障指南，包含常见错误排查步骤 | P2 |

### 6.4 自动归档配置说明

若要开启自动归档，需确保项目根目录的 AGENTS.md 包含：

```
CODEX_MEMORY_AUTO_LOG=required
CODEX_MEMORY_PROJECT_ID=20260703-codex-memory-system
CODEX_MEMORY_MCP_SERVER=codex-memory
```

注意：这些参数中的 PROJECT_ID 和 MCP_SERVER 可在不同项目中按需修改。

---

## 7. 风险与依赖

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 后端 admin/main.py 与 codex_memory 紧密耦合 | 修改主库可能影响 admin | 通过 SessionFactory 注入解耦 |
| Vue 前端与后端响应格式不兼容 | 重复的数据映射工作 | Phase 1.8 统一响应格式 |
| 认证安全 | 管理后台暴露敏感数据 | 最小权限原则，JWT 过期策略 |
| 项目已存在两套前端 | 维护负担翻倍 | Phase 1 后明确淘汰方案 |
| 工作树分支与主线不同步 | 功能碎片 | 完成 Phase 1 后合并回主线 |

---

## 8. 验收标准

### Phase 1 验收

- POST /api/admin/login 返回 JWT token
- GET /api/admin/me 返回当前用户信息
- GET /api/admin/dashboard 返回仪表盘数据
- GET /api/admin/system/status 返回系统状态
- Vue 前端开发模式连接 admin 后端无 404
- Records 页面 7 种数据类型均可正确显示
- 登录页面可正常登录并跳转
- 后端测试通过

### Phase 2-6 验收（每阶段独立）

- 所有新增端点有测试覆盖
- 前端所有页面无控制台错误
- Docker Compose 集成验证
- 管理员操作手册更新

---

## 9. 建议优先执行顺序

### 立即执行（Phase 1）

1. 1.5 后端添加 CORS 中间件 → 解决 fetch 跨域错误
2. 1.1 新增认证端点 → 解决登录 404
3. 1.2 + 1.3 新增 Dashboard 和 System Status 端点 → 解决两处 404
4. 1.9 配置 Vite Proxy → 让前端开发环境正常运行
5. 1.6 修复前端 API 路径和认证集成 → 让前端所有页面可访问
6. 1.4 + 1.7 新增 Records 端点 + 修复数据绑定 → 完整数据浏览能力
7. 1.8 统一后端响应格式 → 减少后续数据映射工作

### Phase 2 优先级

8. 2.1 维护模式门禁（TDD 测试已准备）
9. 2.2 作业批量操作
10. 2.3 + 2.4 记忆手动管理 + 项目配置 UI

---

## 10. 附录：Vue 前端与后端端点映射

| Vue 前端调用 | 对应后端端点 | 状态 |
|--------------|-------------|------|
| POST /v1/login | POST /api/admin/login | 新建 |
| GET /v1/me | GET /api/admin/me | 新建 |
| GET /v1/dashboard | GET /api/admin/dashboard | 新建 |
| GET /v1/system/status | GET /api/admin/system/status | 新建 |
| GET /v1/projects | GET /api/admin/projects | 已有 |
| GET /v1/projects/{key}/archive-status | GET /api/admin/projects/{key}/archive-status | 新建 |
| GET /v1/candidates | GET /api/admin/candidates | 已有 |
| GET /v1/memories | GET /api/admin/memories | 已有 |
| GET /v1/jobs | GET /api/admin/jobs | 已有 |
| GET /v1/raw-records | GET /api/admin/raw-records | 新建 |
| GET /v1/outbox-events | GET /api/admin/outbox-events | 新建 |
| GET /v1/retrieval-audits | GET /api/admin/retrieval-audits | 新建 |
| GET /v1/audit-events | GET /api/admin/audit-events | 新建 |
| - | POST /api/admin/maintenance | Phase 2 |
| - | PUT /api/admin/projects/{id}/flags | 已有 |
| - | POST /api/admin/profiles | 已有 |
| - | POST /api/admin/candidates/{id}/review | 已有 |
| - | POST /api/admin/jobs/{id}/retry | 已有 |

---

## 11. 审批记录

| 版本 | 日期 | 变更说明 | 状态 |
|------|------|----------|------|
| 1.0 | 2026-07-15 | 首次建立，覆盖 Phase 0-6 | 草稿 |

