# codex-memory V1.1 实施状态

更新时间：2026-07-12

## 当前阶段

Agent-1（迁移与基础表）已完成；Agent-2 准备开始。

## 基线与阶段证据

- Agent-0 commit: 7cefe06，规格和基线已提交。
- 文档计划 commit: 7268a32。
- 静态检查：node .\tools\static_check.js → static_check: ok。
- 全量测试：pytest -q → 123 passed，2 warnings。
- Agent-1 focused tests：pytest tests\test_v11_schema.py -q → 3 passed，2 warnings。
- V1.1 flags 默认关闭；旧 memory_embeddings 表保留。

## 阶段状态

| Agent | 范围 | 状态 | Commit | 验证 |
|---|---|---|---|---|
| Agent-0 | 审计、规格、基线 | completed | 7cefe06 | baseline 120 passed |
| Agent-1 | migrations、outbox/jobs/candidates/profile schema | completed | pending | focused 3 passed; full 123 passed |
| Agent-2 | append transaction、project-scoped idempotency | pending | - | - |
| Agent-3 | outbox dispatcher、worker lease/retry/idempotency | pending | - | - |
| Agent-4 | lexical/dense/RRF/context budget | pending | - | - |
| Agent-5 | embedding profile、chunk、backfill、profile index | pending | - | - |
| Agent-6 | candidate/evidence/policy/publish | pending | - | - |
| Agent-7 | LLM ErrorMemoryExtractor shadow | pending | - | - |
| Agent-8 | MCP/Admin API | pending | - | - |
| Agent-9 | regression/concurrency/fault-injection tests | pending | - | - |
| Agent-10 | flags、canary、rollback、compatibility | pending | - | - |

## Agent-1 交付

新增 V1.1 SQLAlchemy model metadata、Alembic 0003–0008、SQLite migration coverage，以及 Alembic configured URL compatibility fix。V1.1 使用新的 memory_embedding_vectors 逻辑表，旧 memory_embeddings 保留。

## 交接规则

每个 Agent 必须先写失败测试并确认 RED，再实现最小改动；完成后运行目标测试、静态检查和全量测试，更新本文件，并提交单独 commit。不得 reset、覆盖或删除其他 Agent 的改动。
