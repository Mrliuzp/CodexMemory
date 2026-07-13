# Codex Memory V1.1 Agent-1 数据库结构实施计划

> **面向自动化执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施。本计划使用复选框记录步骤。

**目标：** 增加 V1.1 增量数据库结构、Alembic 迁移、SQLAlchemy 模型和 SQLite 迁移覆盖，同时保持 V1 表和行为不变。

**架构：** 保持历史 V1 `Base` 元数据和 `memory_embeddings` 表兼容。为现有消息/记忆映射增加 3 个 V1.1 增量字段，并在独立的 `V11Base` 元数据中定义新的 V1.1 表，避免迁移 0001 提前创建未来表。按照可执行规格将迁移拆分为 0003-0008，并使用方言感知的 SQLite 类型和受保护的增量操作。

**技术栈：** Python 3.10+、SQLAlchemy 2、Alembic、PostgreSQL/pgvector、SQLite、pytest。

## 全局约束

- 范围只包括增量数据库结构、迁移、SQLAlchemy 模型、迁移测试和实施状态。
- 不修改业务 API、Worker、检索、Embedding Provider 或 LLM 模块。
- 保留旧 V1 表和旧 `memory_embeddings`；为 V1.1 Profile 隔离向量创建 `memory_embedding_vectors`。
- 所有 V1.1 功能开关默认 `false`；处理策略默认为 `fail_closed`，远程 Provider 默认禁用，脱敏默认启用。
- 迁移必须兼容已升级数据库，并可在 SQLite 测试数据库上运行。
- 最终提交前运行定向迁移/模型测试、`node .\tools\static_check.js` 和完整 `.venv` pytest 测试集。

### 任务 1：增加失败的 SQLite 模型和迁移测试

**文件：**
- 新建：`tests/test_v11_schema.py`

- [ ] **步骤 1：** 为所有必需的 V1.1 表名、增量列、默认值、历史表保留和 SQLite 模型插入编写测试。
- [ ] **步骤 2：** 运行 `..\.venv\Scripts\python.exe -m pytest tests/test_v11_schema.py -q`，确认由于 V1.1 模型/表/迁移尚不存在而进入 RED 状态。

### 任务 2：增加增量 SQLAlchemy 映射

**文件：**
- 修改：`src/codex_memory/db_models.py`

- [ ] **步骤 1：** 为 `MessageRow` 增加 `occurred_at`、`ingestion_version`、`conflict_status`；为 `MemoryRow` 增加 `scope`、`source_kind`、`review_status`，并提供 V1 兼容默认值。
- [ ] **步骤 2：** 增加 `V11Base`，以及开关、策略、Outbox/任务/尝试、候选/证据/策略结果、Profile/检索 Profile/分块/向量行、词法文档和审计行模型。
- [ ] **步骤 3：** 运行定向模型测试，并保持新 V1.1 表不进入历史 `Base.metadata` 的行为不变。

### 任务 3：增加受保护的 Alembic 迁移

**文件：**
- 新建：`alembic/versions/0003_v11_additive_columns.py`
- 新建：`alembic/versions/0004_v11_outbox_jobs.py`
- 新建：`alembic/versions/0005_v11_candidates_policy.py`
- 新建：`alembic/versions/0006_v11_embedding_profiles.py`
- 新建：`alembic/versions/0007_v11_lexical_audit.py`
- 新建：`alembic/versions/0008_v11_flags_policies.py`

- [ ] **步骤 1：** 在 0003 中实现受保护的增量列和项目/事件唯一索引，并保留历史全局事件键唯一约束。
- [ ] **步骤 2：** 在 0004 中实现 Outbox、处理任务、尝试记录、索引和检查约束。
- [ ] **步骤 3：** 在 0005 中实现候选、证据和策略结果表。
- [ ] **步骤 4：** 在 0006 中实现 Embedding Profile、项目检索 Profile、分块和 `memory_embedding_vectors`；SQLite 使用 JSON 向量，PostgreSQL 使用 pgvector。
- [ ] **步骤 5：** 在 0007 中实现词法检索文档与检索/安全审计；SQLite 跳过 PostgreSQL 专属的 GIN/TSVECTOR 操作。
- [ ] **步骤 6：** 在 0008 中实现功能开关和处理策略，所有 V1.1 开关默认关闭。
- [ ] **步骤 7：** 通过 `upgrade head` 运行迁移测试，检查列/表/索引/默认值，并降级回 `0002`，确认不删除历史表。

### 任务 4：重构与验证

**文件：**
- 修改：`IMPLEMENTATION_STATUS.md`

- [ ] **步骤 1：** 运行定向测试、静态检查和完整 pytest；只处理 Agent-1 范围内的失败，不修改范围外模块。
- [ ] **步骤 2：** 将 Agent-1 状态更新为 `completed`，记录最终提交哈希、精确测试结果和兼容性决策。
- [ ] **步骤 3：** 检查 `git diff` 和 `git status`，只暂存 Agent-1 文件，并以 `feat: v1.1 add additive schema` 开头的消息提交。
- [ ] **步骤 4：** 提交后重新运行最终验证命令，并报告提交、文件和输出。