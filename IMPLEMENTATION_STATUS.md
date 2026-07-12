# codex-memory V1.1 实施状态

更新时间：2026-07-12

## 当前阶段

Agent-0（审计、规格与基线）进行中。

## 基线证据

- 工作树：干净，无未提交改动。
- 当前 HEAD：3bdf718 docs: restore project status entrypoint。
- 静态检查：node .\tools\static_check.js → static_check: ok。
- 测试：.venv Python 执行 pytest -q → 120 passed。
- 现有 V1：SQLite MVP、PostgreSQL/pgvector schema、Hook 本地 JSONL outbox、HTTP API、MCP transport 均已存在。

## 阶段状态

| Agent | 范围 | 状态 | Commit | 验证 |
|---|---|---|---|---|
| Agent-0 | 审计、规格、基线 | in progress | - | baseline recorded |
| Agent-1 | migrations、outbox/jobs/candidates/profile schema | pending | - | - |
| Agent-2 | append transaction、project-scoped idempotency | pending | - | - |
| Agent-3 | outbox dispatcher、worker lease/retry/idempotency | pending | - | - |
| Agent-4 | lexical/dense/RRF/context budget | pending | - | - |
| Agent-5 | embedding profile、chunk、backfill、profile index | pending | - | - |
| Agent-6 | candidate/evidence/policy/publish | pending | - | - |
| Agent-7 | LLM ErrorMemoryExtractor shadow | pending | - | - |
| Agent-8 | MCP/Admin API | pending | - | - |
| Agent-9 | regression/concurrency/fault-injection tests | pending | - | - |
| Agent-10 | flags、canary、rollback、compatibility | pending | - | - |

## 交接规则

每个 Agent 必须先写失败测试并确认 RED，再实现最小改动；完成后运行目标测试、静态检查和全量测试，更新本文件，并提交单独 commit。不得 reset、覆盖或删除其他 Agent 的改动。
