# codex-memory V1.3.0 异步闭环可执行开发规格

## 1. 文档目的

本文档把 V1.3 优化方案收敛为可以直接拆分、开发和验收的 V1.3.0 规格。

V1.3.0 只解决一个问题：

> 让已经写入 L0 的消息，在无人值守条件下可靠地经过 Outbox、Processing Job、Worker、Candidate、Policy、Memory 和 Embedding，最终可以被下一次 Context Retrieval 使用。

本文档不扩展历史资料导入、不实现 `codex-memory init`，也不开放新的 LLM 自动发布能力。它们分别属于 V1.3.1 和 V1.3.2。

## 2. 当前基线与差距

当前仓库已经具备以下基础：

- Append API 可以在同一事务内写入 L0 和服务端 Outbox。
- 已有 `outbox_events`、`processing_jobs`、`job_attempts`、Candidate、Evidence、Policy 和 Embedding Profile 表。
- 已有 PostgreSQL `FOR UPDATE SKIP LOCKED`、租约、心跳字段、重试和 Dead 状态的基础实现。
- 已有规则 Candidate Handler、Embedding Handler、Publish Handler 和 LLM ErrorMemoryExtractor shadow 能力。
- 已有项目 Scope、审计、Admin Job 查询和部分 Replay 能力。

V1.3.0 必须补齐以下缺口：

1. Worker 主循环必须真正调用 Dispatcher 和 Job Executor。
2. Event、Job、Handler、Result 的关系必须固定，不能由各模块自行解释。
3. Outbox 必须有完整的领取、派发、完成、重试、Dead 和 Replay 状态迁移。
4. Job 和 Handler 必须使用统一的幂等键。
5. Worker 异常退出后，其他 Worker 必须能够接管过期租约。
6. Compose 启动后必须自动消费任务，不再依赖手工运行 `run_v11_once`。
7. 必须有端到端故障测试，而不仅是单元测试。

当前实现映射：

| 当前位置 | V1.3.0 处理 |
| --- | --- |
| `src/codex_memory/v1_service.py` | 保留 Append 事务边界，补充统一 Outbox 幂等字段 |
| `src/codex_memory/v11_worker.py` | 演进为统一 Dispatcher、Claim、Lease 和 Terminal 状态实现 |
| `src/codex_memory/v11_handlers.py` | 迁移为标准 Handler 接口 |
| `src/codex_memory/worker.py` | 增加常驻异步主循环，保留旧 `run_once` 兼容入口 |
| `docker-compose.yml` | 将 `worker` 改为常驻轮询模式 |
| `src/codex_memory/http_api.py` | 增加 Outbox 管理、取消、过期租约恢复和 Worker 健康接口 |
| `src/codex_memory/admin/api.py` | 补充只读观测和审计查询 |

## 3. 版本边界

### 3.1 V1.3.0 范围

- 服务端 transactional Outbox。
- Outbox Dispatcher。
- Processing Job Claim、Lease、Heartbeat、Retry、Dead 和 Replay。
- 单进程 Worker Runtime，内部包含 Dispatcher、Job Executor 和 Reflection Scheduler。
- `message.appended.v1` 到 Candidate 的规则处理。
- Candidate Policy 到 Memory Publish 的已有流程接入。
- Memory Embedding Job 的异步执行。
- Worker 心跳、健康检查、结构化日志和基础指标。
- 项目级幂等、跨项目隔离和审计。
- SQLite 单元测试和 PostgreSQL 并发/故障测试。

### 3.2 V1.3.0 非范围

- `import_batches`、Source Document、Document Chunk 和 Parser Framework。
- PDF、DOCX、XLSX、PPTX、ZIP、Git 导入。
- `codex-memory init`、Credential Store 和自动 Hook 安装。
- 新增 LLM 分类模型或让 LLM 直接发布正式 Memory。
- 多 Worker Docker 服务拆分。
- 多租户、Workspace Scope 和跨项目自动提升 Global Memory。

### 3.3 兼容要求

- V1.3.0 数据库迁移必须是 additive migration，不删除旧 V1/V1.1/V1.2 数据。
- `server_outbox_enabled=false` 时，保留现有 V1 兼容路径。
- 新增 `async_pipeline_v13_enabled` 项目开关，默认关闭，按项目 Canary 启用。
- 旧的 `run_once`、`run_v11_once` 和已有 Admin API 不得直接删除；可以标记为兼容入口或转发到新 Runtime。
- 原始 L0 永远是事实来源，Worker 只能创建或更新派生 Candidate/Memory，不能改写原始消息。

## 4. 核心模型：Event、Job、Result

### 4.1 Event 定义

Event 表示“某件事情已经发生”。它是事实通知，不是执行命令。

示例：

```json
{
  "event_type": "message.appended.v1",
  "aggregate_type": "conversation_message",
  "aggregate_id": 123,
  "project_key": "demo",
  "payload_version": "v1",
  "idempotency_key": "demo.message.appended.msg-123.v1",
  "payload": {
    "message_id": 123,
    "event_key": "session-1:turn-1:user"
  }
}
```

Event 必须在产生事实的事务中写入 Outbox。任何远端 Provider、LLM、Embedding 或 Worker 都不能参与 Append 事务。

### 4.2 Job 定义

Job 表示“系统需要执行一个动作”。同一个 Event 可以产生多个不同 Job。

示例：

```json
{
  "job_type": "extract_memory_candidate",
  "source_type": "message",
  "source_id": 123,
  "project_key": "demo",
  "handler_version": "memory-extractor-v1",
  "idempotency_key": "demo.extract_memory_candidate.msg-123.memory-extractor-v1"
}
```

Job 必须能独立重试、取消和 Replay。Handler 不能假设同一 Job 只会执行一次。

### 4.3 Result 定义

Result 是 Job 的业务结果，写入 Candidate、Memory、Embedding 或审计记录，并在同一事务内推进 Job 和关联 Event 的终态。

Result 不以“内存返回值”作为唯一事实来源。业务写入必须提交后，Job 才能标记为 `succeeded`。

## 5. Event-Job 矩阵

V1.3.0 固定以下事件和任务。未列入的事件可以保留为预留类型，但不能被默认 Worker 消费。

| Event | Job | Handler | V1.3.0 状态 |
| --- | --- | --- | --- |
| `message.appended.v1` | `extract_memory_candidate` | `ExtractMemoryCandidateHandler` | 启用 |
| `message.appended.v1` | `extract_error_memory` | `ExtractErrorMemoryHandler` | 仅 shadow，默认不发布 |
| `memory.created.v1` | `generate_embedding` | `GenerateEmbeddingHandler` | 启用 |
| `candidate.accepted.v1` | `publish_memory` | `PublishMemoryHandler` | 按项目 Policy 启用 |
| `reflection.requested.v1` | `reflect_project` | `ReflectProjectHandler` | 定时调度 |
| `memory.published.v1` | `rebuild_search_document` | `RebuildSearchDocumentHandler` | 预留/可复用现有索引流程 |
| `document.imported.v1` | `parse_document` | — | V1.3.1 预留，不消费 |
| `document.parsed.v1` | `chunk_document` | — | V1.3.1 预留，不消费 |

`message.appended.v1` 至少需要生成 `extract_memory_candidate`。Error Memory 任务可以与规则处理并行，但 LLM 只允许产生 shadow Candidate，不允许直接改写 `memories`。

## 6. 统一幂等 Key 规范

新增 `IdempotencyKeyBuilder`，所有 Event、Job、Embedding 和 Replay 必须通过它生成幂等键。禁止在 Handler 中自行拼接不透明字符串。

### 6.1 通用格式

```text
{project_key}.{operation}.{source_type}.{source_id}.{version}
```

其中：

- `project_key` 是服务端确认的稳定项目标识，不使用本地绝对路径。
- `source_id` 使用稳定业务 ID，不使用数据库自增 ID 作为唯一业务语义。
- `version` 必须包含 Handler、Parser、Prompt 或 Embedding Profile 版本。
- 输入字段先做 UTF-8、大小写和分隔符规范化。

### 6.2 固定示例

| 场景 | 幂等键 |
| --- | --- |
| L0 消息候选 | `demo.extract_memory_candidate.message.msg-123.memory-extractor-v1` |
| 错误记忆 shadow | `demo.extract_error_memory.message.msg-123.error-extractor-v1` |
| 发布 Memory | `demo.publish_memory.candidate.candidate-456.policy-v1` |
| Embedding | `demo.generate_embedding.memory-version.mv-789.profile-profile-1.hash-abc` |
| Replay | 保持原业务幂等键，增加 `replay_attempt_id`，不创建新的业务结果 |

### 6.3 数据库约束

数据库的唯一性以 `project_id` 为边界：

```sql
UNIQUE (project_id, idempotency_key)
```

Outbox 的唯一键防止同一业务事件重复入队；Job 的唯一键防止 Dispatcher 或 Admin Replay 重复创建同一动作。

`content_hash` 只用于内容冲突检测，不替代幂等键。同一幂等键提交不同内容时，必须产生冲突审计并拒绝静默覆盖。

## 7. 数据库迁移设计

建议新增：

```text
alembic/versions/0012_v13_async_contracts.py
```

如果当前分支已经存在编号冲突，必须以 `alembic heads` 的实际结果为准，不得强行使用重复 revision。

### 7.1 Outbox 增量字段

```sql
ALTER TABLE outbox_events ADD COLUMN idempotency_key VARCHAR(255);
ALTER TABLE outbox_events ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5;
ALTER TABLE outbox_events ADD COLUMN completed_at TIMESTAMP NULL;
ALTER TABLE outbox_events ADD COLUMN replay_count INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX uq_outbox_project_idempotency
ON outbox_events(project_id, idempotency_key);
```

迁移旧数据时，使用 `legacy.outbox.{id}` 回填 `idempotency_key`。PostgreSQL 可以在回填后增加非空约束；SQLite 使用兼容的重建表策略。

保留现有 `next_attempt_at`、`locked_by`、`lease_expires_at` 和错误字段，避免重复表达同一语义。

### 7.2 Processing Job 增量字段

```sql
ALTER TABLE processing_jobs ADD COLUMN source_type VARCHAR(64);
ALTER TABLE processing_jobs ADD COLUMN source_id VARCHAR(255);
ALTER TABLE processing_jobs ADD COLUMN handler_version VARCHAR(128);
ALTER TABLE processing_jobs ADD COLUMN idempotency_key VARCHAR(255);
ALTER TABLE processing_jobs ADD COLUMN error_class VARCHAR(64);
ALTER TABLE processing_jobs ADD COLUMN cancelled_at TIMESTAMP NULL;
ALTER TABLE processing_jobs ADD COLUMN cancel_reason TEXT NULL;

CREATE UNIQUE INDEX uq_jobs_project_type_idempotency
ON processing_jobs(project_id, job_type, idempotency_key);
```

现有 `job_key` 保留为兼容字段，但新代码不得把全局唯一的 `job_key` 作为业务幂等契约。新建 Job 时必须同时写入 `project_id`、`job_type` 和 `idempotency_key`。

### 7.3 Job Attempt 增量字段

```sql
ALTER TABLE job_attempts ADD COLUMN error_class VARCHAR(64);
ALTER TABLE job_attempts ADD COLUMN finished_reason VARCHAR(128);

CREATE UNIQUE INDEX uq_job_attempt_number
ON job_attempts(job_id, attempt_no);
```

### 7.4 Worker 心跳表

新增轻量的 `worker_instances` 表，用于健康检查和租约诊断：

```sql
CREATE TABLE worker_instances (
    worker_id VARCHAR(128) PRIMARY KEY,
    role VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    current_job_id BIGINT NULL REFERENCES processing_jobs(id) ON DELETE SET NULL,
    started_at TIMESTAMP NOT NULL,
    stopped_at TIMESTAMP NULL,
    metadata JSON NOT NULL DEFAULT '{}'
);

CREATE INDEX ix_worker_instances_heartbeat
ON worker_instances(status, last_seen_at);
```

SQLite 和 PostgreSQL 的 JSON 默认值、时间类型和外键行为必须分别通过迁移测试验证。

### 7.5 迁移原则

- Migration `upgrade` 和 `downgrade` 都必须可执行。
- 已有 V1.1/V1.2 数据不得删除或重写业务内容。
- 已存在的旧 `processing_jobs` 必须可被新 Worker 识别；无法补齐业务幂等键的旧任务标记为 `legacy` 并只允许一次兼容处理。
- 所有状态迁移和管理员操作保留 `security_audits` 或现有审计表记录。

## 8. 状态机

### 8.1 Outbox 状态

正式状态：

```text
pending
   ↓ claim
leased
   ↓ job created
dispatched
   ↓ job succeeded
completed
```

异常路径：

```text
leased ── transient error ──→ retry_wait ── time reached ──→ pending
leased ── lease expired ────→ pending
leased ── permanent error ──→ dead
retry_wait ── max attempts ──→ dead
```

规则：

- `leased` 必须带 `locked_by` 和 `lease_expires_at`。
- `dispatched` 表示 Job 已经以幂等方式创建，不表示业务处理成功。
- 只有关联 Job 成功后，Outbox 才能变成 `completed`。
- Outbox 死信不得物理删除，Replay 只重置状态并增加 `replay_count`。
- 过期租约恢复必须是批量、原子、可审计的。

### 8.2 Processing Job 状态

V1.3.0 正式状态统一为：

```text
pending → running → succeeded
             │
             ├── retryable error → retry_wait → pending
             ├── permanent error → dead
             ├── max attempts ───→ dead
             ├── lease expired ──→ pending 或 dead
             └── admin cancel ───→ cancelled
```

不再新增含义模糊的 `failed` 状态。永久失败使用 `dead`，错误分类写入 `error_class`、`error_code` 和 `last_error_message`。

### 8.3 状态迁移权限

| 迁移 | 执行者 |
| --- | --- |
| `pending → running` | Worker Claim |
| `running → succeeded` | 持有有效租约的 Worker |
| `running → retry_wait` | 持有有效租约的 Worker |
| `running → dead` | Worker 或过期租约 Sweeper |
| `running → cancelled` | Admin，必须有权限和 reason |
| `dead/retry_wait → pending` | Admin Replay/Retry，必须写审计 |

任何没有有效 `locked_by`、已经过期的 Worker 都不能提交业务结果。

## 9. Worker Runtime

### 9.1 V1.3.0 部署形态

V1.3.0 使用一个常驻 `worker` 进程，内部包含三个角色：

```text
worker
├── Outbox Dispatcher
├── Job Executor
└── Reflection Scheduler
```

暂不拆分为四个 Docker 服务。V1.4 再根据吞吐量和故障域拆分为 `worker-dispatcher`、`worker-memory`、`worker-import`、`worker-reflection`。

### 9.2 主循环

建议入口：

```python
def run_async_once(runtime: WorkerRuntime) -> WorkerCycleResult:
    recovered = runtime.sweep_expired_leases()
    dispatched = runtime.dispatch_outbox()
    processed = runtime.process_jobs()
    runtime.publish_heartbeat()
    return WorkerCycleResult(
        recovered=recovered,
        dispatched=dispatched,
        processed=processed,
    )
```

常驻循环：

```python
while not runtime.stop_requested:
    run_async_once(runtime)
    runtime.run_due_reflection_schedule()
    runtime.sleep(poll_interval_seconds=2)
```

建议默认参数：

| 参数 | 默认值 |
| --- | ---: |
| claim batch size | 10 |
| lease duration | 60 秒 |
| heartbeat interval | 20 秒 |
| poll interval | 2 秒 |
| general max attempts | 5 |
| embedding max attempts | 4 |
| max backoff | 30 分钟 |
| graceful shutdown timeout | 30 秒 |

### 9.3 Claim 协议

PostgreSQL 必须使用：

```sql
SELECT id
FROM processing_jobs
WHERE status IN ('pending', 'retry_wait')
  AND next_attempt_at <= NOW()
  AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
ORDER BY priority DESC, created_at ASC, id ASC
FOR UPDATE SKIP LOCKED
LIMIT :batch_size;
```

领取后在同一事务中更新：

```text
status = running
locked_by = worker_id
locked_at = now
heartbeat_at = now
lease_expires_at = now + lease_duration
attempt_count = attempt_count + 1
```

同时写入一条 `job_attempts`，并保证 `(job_id, attempt_no)` 唯一。

### 9.4 心跳与失租约

- 长任务每 20 秒刷新一次心跳。
- 心跳更新必须检查 `job_id`、`locked_by`、`status=running` 和租约未过期。
- Handler 执行完成后，提交业务结果前必须再次检查租约归属。
- 丢失租约的 Worker 只能记录 `abandoned`，不能发布 Memory 或标记 Job 成功。
- Sweeper 将过期 Job 重置为 `pending` 或 `dead`，并结束当前 Attempt 为 `abandoned`。

### 9.5 优雅退出

收到 SIGINT/SIGTERM 后：

1. 停止领取新 Job。
2. 在最多 30 秒内等待当前 Handler 完成。
3. 无法完成的 Job 不提交成功结果，等待租约到期后由其他 Worker 接管。
4. 写入 `worker_instances.stopped_at` 和结构化退出日志。

## 10. Handler 接口

统一接口建议如下：

```python
class JobHandler(Protocol):
    job_type: str
    handler_version: str

    def validate(self, claim: JobClaim) -> None:
        ...

    def execute(self, claim: JobClaim, context: HandlerContext) -> HandlerResult:
        ...

    def compensate(self, claim: JobClaim, error: Exception) -> None:
        ...

    def classify_error(self, error: Exception) -> ErrorClassification:
        ...
```

### 10.1 Handler 约束

- `validate` 只检查输入结构、项目归属和前置条件，不做远端调用。
- `execute` 必须可重复执行，业务写入使用幂等键。
- `compensate` 只处理本次 Handler 产生的临时资源，不删除原始 L0。
- `classify_error` 必须返回 `retryable`、`permanent`、`lease_lost` 或 `cancelled`。
- Handler 不得直接改变 `project_id`、Scope、审核状态或 Global Publish 权限。
- LLM 只能生成 Candidate/Shadow 结果，不能直接写正式 Memory。

### 10.2 V1.3.0 Handler

| Handler | 输入 | 输出 |
| --- | --- | --- |
| `ExtractMemoryCandidateHandler` | Message | Memory Candidate + Evidence |
| `ExtractErrorMemoryHandler` | Message | Shadow Candidate 或 abstain 结果 |
| `PublishMemoryHandler` | Accepted Candidate | Memory、Memory Version、`memory.created.v1` |
| `GenerateEmbeddingHandler` | Memory Version/Chunk | Profile-scoped Embedding |
| `ReflectProjectHandler` | Project | Reflection Report 和受 Policy 控制的派生结果 |

## 11. 错误分类与重试

### 11.1 可重试错误

- HTTP 429。
- HTTP 500、502、503、504。
- 数据库短暂连接失败或事务死锁。
- Embedding Provider 超时。
- 网络连接中断。
- Worker 在提交前意外退出。
- 临时租约冲突。

### 11.2 不可重试错误

- JSON Schema 校验失败。
- 项目、Message、Candidate 或 Memory 不存在。
- Embedding 维度不匹配。
- 权限或 Policy 拒绝。
- 不支持的任务类型。
- 非法的项目边界或 Scope。
- 发现不可接受的敏感内容或输入安全问题。

### 11.3 重试公式

```text
delay = min(base_delay × 2^(attempt_count - 1) + jitter, max_delay)
```

要求：

- `jitter` 使用可复现测试注入器，生产使用随机值。
- 达到 `max_attempts` 后进入 `dead`。
- 每次重试写入 `job_attempts` 和结构化日志。
- 不因重试而重新创建新的 Candidate 或 Memory 版本，除非业务幂等键发生了合法版本变化。

## 12. Replay、Retry 与 Cancel

### 12.1 Replay

Replay 是“重新执行同一个业务 Job”，不是创建新的业务事实。

规则：

1. Admin 必须提交 `reason`。
2. 保留原 Event、Job、Candidate 和审计记录。
3. 将 Job 从 `dead` 或 `retry_wait` 重置为 `pending`。
4. 清理旧租约，增加 `replay_count`，保留原 `idempotency_key`。
5. 重新执行必须再次命中幂等约束。
6. 记录 `before_state`、`after_state`、操作者、Request ID 和 reason。

### 12.2 Retry

Retry 只允许作用于 `dead` 或 `retry_wait` Job。永久错误不应通过 Retry 绕过 Policy；管理员重新尝试前必须能看到错误分类。

### 12.3 Cancel

Cancel 只允许取消尚未完成的 Job。正在执行的 Job 设置取消标记，由 Handler 在安全边界退出；不能强行删除 Job 或 Outbox 行。

## 13. API 增补

### 13.1 Append

保留现有：

```text
POST /api/v1/append
```

行为：

- L0 Message 和 Outbox 必须在一个数据库事务中提交。
- API 成功只代表 L0/Outbox 已持久化，不等待 Worker 完成。
- 重复 `event_key` 且正文相同返回 `duplicate`。
- 重复 `event_key` 且正文不同返回 409 `event_key_conflict`。

### 13.2 Admin Job/Outbox

新增或统一以下接口：

```text
GET  /api/admin/v1/outbox
GET  /api/admin/v1/outbox/{id}
POST /api/admin/v1/outbox/{id}/replay

GET  /api/admin/v1/jobs
GET  /api/admin/v1/jobs/{id}
POST /api/admin/v1/jobs/{id}/retry
POST /api/admin/v1/jobs/{id}/cancel
POST /api/admin/v1/jobs/{id}/replay
POST /api/admin/v1/jobs/reset-stale
```

所有写接口必须要求 Admin 权限和 `reason`，并记录审计。已有兼容路径可以保留，但必须在响应中标注 API 版本。

### 13.3 Health

```text
GET /api/v1/health
```

至少返回：

```json
{
  "status": "ok",
  "database": "ok",
  "outbox": "ok",
  "worker": {
    "status": "healthy",
    "last_heartbeat_age_seconds": 2,
    "active_jobs": 1
  },
  "pending_jobs": 0,
  "dead_jobs": 0,
  "degraded": false
}
```

Worker 心跳超过阈值时，API 仍可返回 `status=degraded`，但不能把 L0 Append 判定为失败。

## 14. 指标与日志

V1.3.0 先实现应用内指标接口或结构化计数，指标名称固定为：

```text
codex_memory_outbox_pending
codex_memory_outbox_dead
codex_memory_jobs_pending
codex_memory_jobs_running
codex_memory_jobs_dead
codex_memory_job_duration_seconds
codex_memory_job_retry_total
codex_memory_worker_heartbeat_age
codex_memory_candidate_created_total
codex_memory_candidate_published_total
codex_memory_embedding_failure_total
```

每条 Job 日志至少包含：

```json
{
  "request_id": "req_xxx",
  "project_id": 1,
  "job_id": 10,
  "job_type": "extract_memory_candidate",
  "attempt_no": 2,
  "worker_id": "worker-abc",
  "handler_version": "memory-extractor-v1",
  "status": "succeeded",
  "duration_ms": 120
}
```

不得记录 Token、完整 Authorization Header 或未脱敏的远端 Prompt/Response。

告警条件：

- `Dead Job > 0`。
- 最老 Pending Job 超过 10 分钟。
- Worker 心跳超过 2 个 lease duration。
- Outbox Pending 持续增长。
- Embedding 失败率超过项目阈值。

## 15. Docker Compose 调整

V1.3.0 保留一个 `worker` 服务，示意配置：

```yaml
services:
  worker:
    build: .
    command:
      [
        "python",
        "-m",
        "codex_memory.worker",
        "--mode",
        "async",
        "--poll-interval",
        "2",
        "--reflection-schedule",
        "02:00"
      ]
    environment:
      CODEX_MEMORY_DATABASE_URL: ${CODEX_MEMORY_DATABASE_URL}
      CODEX_MEMORY_WORKER_ROLE: async
      CODEX_MEMORY_WORKER_LEASE_SECONDS: 60
      CODEX_MEMORY_WORKER_HEARTBEAT_SECONDS: 20
      CODEX_MEMORY_WORKER_BATCH_SIZE: 10
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped
```

要求：

- `worker` 启动后持续轮询，不再只等待每天 02:00。
- `--once` 继续支持本地测试和运维诊断。
- `worker` 的健康状态不能再固定返回 `unknown`。
- API、MCP、Admin Web 的启动顺序保持不变。
- 开发环境可以将所有 Worker 角色运行在一个进程中，生产环境预留角色参数。

## 16. 测试规格

### 16.1 Schema 测试

新增 `tests/test_v13_async_schema.py`：

- SQLite Migration 从当前 head 升级成功。
- PostgreSQL Migration 从当前 head 升级成功。
- Outbox `(project_id, idempotency_key)` 唯一。
- Job `(project_id, job_type, idempotency_key)` 唯一。
- Job Attempt `(job_id, attempt_no)` 唯一。
- 旧 V1.1/V1.2 数据仍可读取。
- Downgrade 不破坏旧表结构。

### 16.2 正常闭环

新增 `tests/test_v13_async_pipeline.py`：

```text
Append
  → L0 + Outbox
  → Dispatcher
  → Job
  → Candidate
  → Policy
  → Memory
  → Embedding
  → Retrieval
```

验收：Append 成功后，Worker 启动且无人工命令时，10 秒内可以查询到可检索 Memory；Embedding 失败时至少保留 Lexical 可用性。

### 16.3 幂等测试

新增 `tests/test_v13_idempotency.py`：

- 同一个 Append 并发 8 次只产生一条 L0 和一条 Outbox。
- 同一个 Outbox Replay 100 次只产生一个逻辑 Job。
- 同一个 Job 执行 100 次只产生一个 Candidate/Memory 结果。
- 同一个 Embedding Job 重复执行不产生重复向量。
- 同一 `event_key` 不同正文返回 409，并记录审计。

### 16.4 故障测试

新增 `tests/test_v13_worker_recovery.py`：

- Worker 在执行 50% 时退出，租约过期后新 Worker 接管并完成。
- 数据库短暂断开后 Job 进入 Retry，恢复后继续执行。
- Embedding Provider 返回 503 时按退避策略重试，超过上限进入 Dead。
- JSON Schema、权限和维度错误不进行无限重试。
- Dead Job 可以通过 Admin Replay 恢复。
- Cancel 后 Handler 不得发布新 Memory。

### 16.5 项目隔离与安全

- 项目 A 的 Event、Job、Candidate、Memory 和 Embedding 不得被项目 B 查询。
- Replay 必须校验项目权限。
- Handler 不得根据 Payload 中的 project_key 越权切换项目。
- 日志、Outbox 和 Job 响应不包含 Token。
- Global L2 仍必须通过现有 reviewer/reason 治理路径。

### 16.6 Compose 端到端测试

验证：

1. `docker compose up` 后无需手工运行 Worker 命令。
2. Append 后自动产生 Candidate 和 Memory。
3. `docker compose restart worker` 后任务继续处理。
4. `docker compose down` 不删除 L0、Outbox 或 Job 数据。
5. API Health 可以反映 Worker 心跳和 Pending/Dead 数量。

## 17. Agent 任务拆分

每个 Agent 只修改自己的职责范围，完成后运行对应测试并提交独立 commit。禁止 Agent 修改其他 Agent 未完成的接口。

### Agent 1：Async Schema

范围：

- `0012_v13_async_contracts.py`。
- `v13_models.py` 或现有模型的增量字段。
- 幂等唯一索引、Worker 心跳表。
- SQLite/PostgreSQL Migration 测试。

不修改 API、Handler 和 Worker 行为。

### Agent 2：Idempotency and Event Contract

范围：

- `idempotency.py`。
- Event 类型常量和 Event-Job Matrix。
- Append Outbox 幂等键写入。
- 冲突检测和兼容旧 Job Key。

### Agent 3：Dispatcher and Lease Runtime

范围：

- Outbox 状态迁移。
- Dispatcher Claim、Job 创建和 Outbox Ack。
- Processing Job Claim、Heartbeat、Lease Sweeper。
- Replay/Retry/Cancel 的领域服务。

### Agent 4：Handler Pipeline

范围：

- 标准 `JobHandler` 接口。
- Message Candidate、Error Shadow、Publish、Embedding、Reflection Handler 适配。
- Handler 错误分类和幂等写入。

### Agent 5：Worker Entry and Observability

范围：

- `worker.py` 常驻循环。
- Worker 心跳。
- 结构化日志、指标和 Health 响应。
- `--once`、`--mode async` 和优雅退出。

### Agent 6：Admin API and Compose

范围：

- Outbox/Job 查询、Retry、Replay、Cancel、Reset Stale。
- 审计和权限校验。
- `docker-compose.yml` 和运行文档。

### Agent 7：End-to-End Reliability Tests

范围：

- 正常闭环。
- 并发幂等。
- Worker 退出恢复。
- Provider 503/429。
- Dead Replay。
- 项目隔离和 Compose 验收。

Agent 7 不修改生产实现，除非发现阻断验收的明确缺陷，并须记录变更原因。

## 18. 实施顺序

推荐依赖顺序：

```text
Agent 1 Schema
    ↓
Agent 2 Idempotency/Event
    ↓
Agent 3 Dispatcher/Lease
    ↓
Agent 4 Handler Pipeline
    ↓
Agent 5 Worker Runtime/Health
    ↓
Agent 6 Admin/Compose
    ↓
Agent 7 End-to-End Tests
```

在 Agent 1 完成前，不应修改业务 Handler；在 Agent 3 完成前，不应启用 `async_pipeline_v13_enabled`；在 Agent 7 完成前，不应将开关提升到 100%。

## 19. 发布与回滚

### 19.1 发布

1. 运行迁移并验证旧数据可读。
2. 部署兼容代码，保持 `async_pipeline_v13_enabled=false`。
3. 启动常驻 Worker，确认心跳正常。
4. 对测试项目开启 1% Canary。
5. 验证 Pending、Dead、Retry、Duplicate 和 Retrieval 指标。
6. 按 10% → 50% → 100% 提升项目开关。

### 19.2 回滚

- 关闭项目 `async_pipeline_v13_enabled`，不删除已写入的 L0、Outbox、Job 和 Memory。
- 保留 Worker 以继续处理已提交任务，或明确暂停并保留租约恢复能力。
- 代码回滚不能回滚已经提交的业务结果；需要通过现有 Memory Version 和治理流程处理。
- Migration downgrade 只在确认没有使用新增字段的活动任务后执行。

## 20. V1.3.0 完成定义

只有同时满足以下条件，才可以宣布 V1.3.0 完成：

1. Append 成功后，Worker 无需人工触发即可在 10 秒内处理正常消息。
2. L0、Outbox 和 Job 在 Worker 停止时不丢失。
3. Worker 强制退出并恢复租约后，任务可以由新 Worker 接管。
4. 同一个 Event Replay 100 次不会产生重复 Candidate 或 Memory。
5. 429、503、数据库短暂故障能够按策略重试。
6. 永久错误不会无限重试，并能在 Admin 中查看和 Replay。
7. Outbox、Job 和 Worker 状态可查询、可审计、可告警。
8. 项目隔离测试通过，Global L2 治理边界不被绕过。
9. Compose 启动后不需要手工执行 `run_v11_once` 或一次性 Worker 命令。
10. 全量后端测试、前端测试、前端构建和 `tools/static_check.js` 通过。

完成以上条件后，才进入 V1.3.1 Knowledge Import Pipeline，不在 V1.3.0 中继续扩展功能范围。
