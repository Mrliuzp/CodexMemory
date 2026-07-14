# Codex Memory 全局 HTTP 服务实施计划索引

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已批准的全局 HTTP MCP 设计拆成四个可以独立测试、审查、提交和回滚的实施计划。

**Architecture:** PostgreSQL 是唯一生产数据源；FastAPI 提供统一领域 API，独立 MCP 适配器通过受认证的 Streamable HTTP 转发到 API。全局 Skill 和 Hook 只在项目 `AGENTS.md` 明确启用时工作，并通过项目级幂等键和本地 outbox 保证可靠归档。

**Tech Stack:** Python 3.10+、FastAPI、MCP Python SDK、SQLAlchemy 2、Alembic、PostgreSQL 16、pgvector、Docker Compose、PowerShell、Vue 3、Vitest、Playwright、pytest。

## Global Constraints

- 所有新增或修改的用户界面文案、代码注释和 Markdown 文档使用简体中文；技术标识保持原样。
- 生产环境只允许 PostgreSQL；SQLite 只用于测试、开发验证和只读迁移来源。
- 全局 MCP 固定使用 `http://127.0.0.1:8001/mcp`，默认不得绑定局域网接口。
- MCP Token、Admin 凭据和数据库密码不得写入 Git、日志、Skill、`AGENTS.md` 或 outbox。
- 只有 `CODEX_MEMORY_AUTO_LOG=required` 且项目参数完整时才自动归档。
- `CODEX_MEMORY_PROJECT_ID` 是稳定持久身份；目录移动不能改变项目身份。
- Hook、Skill、outbox 重放和人工补录必须服从 `(project_id, event_key)` 幂等约束。
- 每个实现任务遵循测试先行，并以独立 Git 提交结束。

---

## 执行顺序

1. [第一部分：HTTP MCP 与 PostgreSQL 生产基线](2026-07-14-codex-memory-global-http-01-service.md)
2. [第二部分：Codex 全局 MCP、Skill 与项目门禁](2026-07-14-codex-memory-global-http-02-codex-integration.md)
3. [第三部分：可靠 Hook、outbox 与幂等归档](2026-07-14-codex-memory-global-http-03-reliable-archive.md)
4. [第四部分：数据迁移、诊断与管理观测](2026-07-14-codex-memory-global-http-04-migration-operations.md)

四份计划必须按顺序执行。第一部分交付可认证、仅使用 PostgreSQL 的全局 HTTP 服务；第二部分交付可安装的 Codex 全局接入；第三部分将自动归档提升为可重放的生命周期机制；第四部分迁移历史数据并补齐诊断和后台观测。

## 阶段门禁

| 门禁 | 必须满足的证据 |
| --- | --- |
| 第一部分完成 | API、MCP、Worker 和 Admin Web 只连接 PostgreSQL；匿名 MCP 请求返回 401；Compose 健康检查全部通过 |
| 第二部分完成 | `codex mcp get codex-memory` 指向全局 URL；全局 Skill 通过校验；启用和未启用项目行为不同 |
| 第三部分完成 | 中断 API 后事件进入 outbox；恢复后只写入一次；401/403/409 进入可诊断死信 |
| 第四部分完成 | 历史数据迁移报告可核对；`doctor` 全绿；管理后台可查看新归档和积压；端到端回归通过 |

## Git 与审查策略

- 每个任务使用计划中指定的提交信息，不将多个门禁混入一个提交。
- 每份子计划完成后进行一次代码审查和完整回归，再开始下一份。
- 推送远程和创建 PR 时使用 GitHub 插件工作流；本地实现阶段保留小步提交。
- 发现现有工作区中的用户改动时不得回滚，必须调整任务范围与其共存。

## 最终回归命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test --prefix apps/admin-web
npm run build --prefix apps/admin-web
docker compose config
docker compose up -d --build
docker compose ps
```

预期结果：pytest、Vitest 和前端构建全部通过；Compose 配置有效；`postgres`、`api`、`mcp`、`worker` 和 `admin-web` 均运行且健康。
