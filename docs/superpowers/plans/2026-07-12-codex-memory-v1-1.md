# codex-memory V1.1 实施计划

> **执行要求：** 以 V1.1 可执行规格作为绑定契约。每个 Agent 任务都必须遵循 RED → GREEN → 完整验证 → 独立提交。

**目标：** 在保持向后兼容的前提下，将现有 codex-memory V1 升级为具备持久化采集、候选治理、确定性混合检索、Profile 版本化 Embedding 和仅 shadow LLM 增强能力的 V1.1。

**架构：** 保留 Hook 本地 JSONL Outbox，并增加服务端事务 Outbox。先持久化不可变 L0，再异步处理候选、策略、发布、分块、Embedding 和检索。稠密检索按 Profile 隔离，并始终支持仅词法检索的降级模式。

**技术栈：** Python 3.10+、SQLAlchemy 2、Alembic、FastAPI、PostgreSQL 16、pgvector、SQLite 测试路径、pytest。

## 全局约束

- Append 不得同步调用 Embedding 或 LLM。
- `UNIQUE(project_id,event_key)`；相同哈希按幂等处理，不同哈希返回 HTTP 409 并写入审计。
- 稠密检索失败时只能回退到词法检索；本地 Token 向量不能查询远程向量索引。
- V1.1 功能开关默认关闭。
- LLM 只能写入候选；服务端负责 `project_id`、作用域、审核和发布字段。
- 证据必须依据不可变 L0 验证。
- Shadow 结果不得进入 Search 或 Context。
- 旧 V1 表/API 至少保留两个完整发布周期。

### 任务 0：审计与规格

文件：`docs/CODEX_MEMORY_V1_1_EXECUTABLE_SPEC.md`、`IMPLEMENTATION_STATUS.md`。运行基线 `static_check` 和 pytest。提交：`7cefe06`。

### 任务 1：增量数据库结构

文件：`db_models.py`、`alembic/versions/0003+` 迁移、`tests/test_v1_schema.py` 或新的迁移测试。增加开关、策略、Outbox、任务、尝试、候选、证据、策略结果、Profile、分块、V1.1 向量表、词法/审计表；保留历史 `memory_embeddings`。独立提交。

### 任务 2：Append 契约

文件：`v1_service.py`、`http_api.py`、`v1_schemas.py`、`db_models.py`、Hook 测试。一个事务中写入不可变 L0 和 Outbox；返回 201/200/409；保留 Hook 本地 Outbox。独立提交。

### 任务 3：Worker 框架

文件：新的 Worker/Outbox 模块、模型、服务和管理端点测试。增加 SKIP LOCKED 领取、租约、心跳、清理器、退避、异常分类、任务幂等和 dead/retry 操作。独立提交。

### 任务 4：词法与上下文检索

文件：检索模块、V1 service/HTTP Schema、词法迁移和检索测试。增加作用域/状态筛选、简单文本/代码/中文 Token/Trigram 检索、RRF 常量、L3 优先级、全局配额、上下文预算和降级元数据。独立提交。

### 任务 5：Embedding Profile

文件：Embedding Provider 模块、向量存储/检索器、Profile API、迁移和测试。增加 query/document 方法、批量/能力校验、Profile 隔离的向量/索引、回填、shadow 检索、Canary 和回滚。独立提交。

### 任务 6：候选与策略流水线

文件：候选/策略模块、分类器集成、模型、API 和测试。将规则分类输出转换为候选；通过版本/关系/审计完成证据校验和发布。独立提交。

### 任务 7：LLM Shadow 增强

文件：与 Provider 无关的 LLM 适配器、ErrorMemoryExtractor、脱敏/策略、Schema 和测试。只允许 Shadow、严格 Schema、abstain、证据、超时/成本预算和提示注入防护。独立提交。

### 任务 8：MCP 与管理后台

文件：V1 MCP、HTTP API、认证、Schema 和测试。增加 Search/Context/Admin/任务/Profile/候选/replay/review 契约，但不暴露 Shadow 候选。独立提交。

### 任务 9：验证与故障注入

文件：单元/集成/并发/故障测试和状态文档。验证全部验收标准、迁移兼容性、隔离性、Profile 分离、Worker 崩溃/租约恢复和远程失败。独立提交。

### 任务 10：开关与发布

文件：功能开关/策略模块、部署文档、状态和发布测试。实现项目级 1/10/50/100% Canary、回滚、指标、兼容性和两个周期的保留策略。独立提交。