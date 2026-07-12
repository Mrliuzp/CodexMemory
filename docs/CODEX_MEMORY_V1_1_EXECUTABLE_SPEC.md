# codex-memory V1.1 可执行开发规格

状态：开发基线已冻结（2026-07-12）

本文件是 V1.1 的执行契约。除非后续提交显式修改本文件，Agent 不得自行改变这里定义的字段、状态、默认值或兼容策略。

## 0. 不可违反的约束

- Hook 只负责机械采集 L0；/api/v1/append 只同步完成鉴权、幂等、L0 和 transactional outbox 写入。
- append 同步链路不得调用 embedding、LLM、摘要、去重或远端重试。
- Hook 本地 JSONL outbox 保留；它与服务端 transactional outbox 分别解决 API 不可达和异步任务不丢失。
- L0 messages 是不可变事实源；派生记忆必须能够从 L0 重建。
- UNIQUE(project_id, event_key)；相同 key/hash 返回幂等成功；相同 key/不同 hash 返回 409 并写审计。
- remote dense 失败只能降级到 lexical-only；不得使用 local-token 向量查询 remote 向量索引。
- 不同 embedding profile 不得进入同一个 ANN 索引，也不得比较不同 profile 的原始相似度分数。
- level 表示抽象层级，scope 表示作用域；V1.1 只支持 project 和 global。
- LLM 只能生成 candidate；不能决定 project_id、scope、review/publish 状态，也不能直接写 memories。
- evidence 必须由服务端在原始消息上验证；不可验证候选不得发布。
- 所有 V1.1 功能开关默认关闭；shadow candidate 和 shadow retrieval 不进入 Context Builder。
- 不删除 V1 旧表；至少保留两个完整发布周期后，才允许归档旧索引或旧列。

## 1. V1 基线与兼容策略

当前 V1 使用 SQLAlchemy/Alembic、PostgreSQL 16 + pgvector，并保留 SQLite 测试路径。物理表 messages 继续作为逻辑 messages_l0，避免重命名破坏审计和外部工具。

当前差距：

- messages.event_key 当前是全局唯一，需改为项目内唯一并保留 content_hash 冲突检测。
- memory_embeddings 当前对 memory_id 唯一且固定为 vector(1536)，需改为 profile/chunk 维度。
- V1 没有服务端 outbox、job attempt/lease、candidate/evidence/policy 表。
- V1 search 是应用层关键词匹配，需增加 PostgreSQL lexical 索引和 dense/lexical 融合。
- V1 reflection 会直接写正式记忆，需迁移为 candidate → policy → publish。

迁移只新增或兼容性调整：先建新结构、回填、双读/双写和验证，再切换 feature flag；禁止先删除旧表、旧列、旧索引或旧 API。

## 2. 目标数据库 DDL

以下为 PostgreSQL 目标结构。主键沿用当前项目 BIGINT；SQLite 测试适配为 JSON/整数。V1.1 逻辑表名 messages_l0 对应现有物理表 messages。

### 2.1 项目策略和功能开关

SQL：

    CREATE TABLE project_feature_flags (
        project_id BIGINT PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
        memory_v11_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        server_outbox_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        lexical_retrieval_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        dense_retrieval_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        embedding_profile_v2_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        llm_shadow_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        candidate_publish_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE project_processing_policies (
        project_id BIGINT PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
        remote_embedding_allowed BOOLEAN NOT NULL DEFAULT FALSE,
        remote_llm_allowed BOOLEAN NOT NULL DEFAULT FALSE,
        redaction_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        failure_mode VARCHAR(20) NOT NULL DEFAULT 'fail_closed'
            CHECK (failure_mode IN ('fail_closed', 'fail_open')),
        allowed_embedding_providers JSONB NOT NULL DEFAULT '[]'::jsonb,
        allowed_llm_providers JSONB NOT NULL DEFAULT '[]'::jsonb,
        data_residency_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

生产默认是全部关闭、fail_closed。只能由管理员或 bootstrap 显式开启，不能由用户日志或 LLM 修改。

### 2.2 不可变 L0 与服务端 Outbox

SQL：

    ALTER TABLE messages
        ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS ingestion_version VARCHAR(32) NOT NULL DEFAULT 'v1',
        ADD COLUMN IF NOT EXISTS conflict_status VARCHAR(20) NOT NULL DEFAULT 'none'
            CHECK (conflict_status IN ('none', 'conflict'));

    CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_project_event_key
        ON messages(project_id, event_key);

    CREATE INDEX IF NOT EXISTS ix_messages_project_created
        ON messages(project_id, created_at, id);

    CREATE TABLE outbox_events (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        aggregate_type VARCHAR(64) NOT NULL,
        aggregate_id BIGINT NOT NULL,
        event_type VARCHAR(128) NOT NULL,
        payload_version VARCHAR(32) NOT NULL,
        payload JSONB NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        status VARCHAR(20) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'dispatched', 'retry_wait', 'dead')),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        locked_by VARCHAR(128),
        locked_at TIMESTAMPTZ,
        lease_expires_at TIMESTAMPTZ,
        last_error_code VARCHAR(64),
        last_error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        dispatched_at TIMESTAMPTZ,
        UNIQUE(project_id, event_type, aggregate_id, payload_version)
    );

    CREATE INDEX ix_outbox_claim
        ON outbox_events(status, next_attempt_at, priority DESC, created_at, id);

### 2.3 Processing Jobs 与尝试记录

SQL：

    CREATE TABLE processing_jobs (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        outbox_event_id BIGINT REFERENCES outbox_events(id) ON DELETE RESTRICT,
        job_type VARCHAR(128) NOT NULL,
        aggregate_type VARCHAR(64) NOT NULL,
        aggregate_id BIGINT NOT NULL,
        job_key VARCHAR(255) NOT NULL UNIQUE,
        payload_version VARCHAR(32) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'running', 'retry_wait', 'succeeded', 'dead', 'cancelled')),
        priority INTEGER NOT NULL DEFAULT 0,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
        locked_by VARCHAR(128),
        locked_at TIMESTAMPTZ,
        lease_expires_at TIMESTAMPTZ,
        heartbeat_at TIMESTAMPTZ,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_error_code VARCHAR(64),
        last_error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ
    );

    CREATE TABLE job_attempts (
        id BIGSERIAL PRIMARY KEY,
        job_id BIGINT NOT NULL REFERENCES processing_jobs(id) ON DELETE CASCADE,
        attempt_no INTEGER NOT NULL,
        worker_id VARCHAR(128) NOT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        ended_at TIMESTAMPTZ,
        outcome VARCHAR(20) NOT NULL DEFAULT 'running'
            CHECK (outcome IN ('running', 'succeeded', 'retry_wait', 'dead', 'cancelled')),
        error_code VARCHAR(64),
        error_message TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        UNIQUE(job_id, attempt_no)
    );

    CREATE INDEX ix_jobs_claim
        ON processing_jobs(status, next_attempt_at, priority DESC, created_at, id);

    CREATE INDEX ix_jobs_project_status
        ON processing_jobs(project_id, status, created_at);

### 2.4 Scope、Candidate、Evidence 与 Policy

项目记忆必须 scope=project 且 project_id 非空；全局记忆必须 scope=global、project_id 为空且 level=L2。

SQL：

    ALTER TABLE memories
        ADD COLUMN IF NOT EXISTS scope VARCHAR(20) NOT NULL DEFAULT 'project'
            CHECK (scope IN ('project', 'global')),
        ADD COLUMN IF NOT EXISTS source_kind VARCHAR(32) NOT NULL DEFAULT 'rule'
            CHECK (source_kind IN ('rule', 'llm', 'human', 'reflection')),
        ADD COLUMN IF NOT EXISTS review_status VARCHAR(20) NOT NULL DEFAULT 'accepted'
            CHECK (review_status IN ('shadow', 'candidate', 'needs_review', 'accepted', 'rejected', 'superseded'));

    ALTER TABLE memories ADD CONSTRAINT ck_memory_scope_project
        CHECK ((scope = 'project' AND project_id IS NOT NULL)
            OR (scope = 'global' AND project_id IS NULL AND level = 'L2'));

    CREATE TABLE memory_candidates (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        source_message_id BIGINT REFERENCES messages(id) ON DELETE RESTRICT,
        task_type VARCHAR(64) NOT NULL
            CHECK (task_type IN ('classify', 'error_extract', 'fact_split', 'knowledge_synth')),
        level VARCHAR(10) NOT NULL CHECK (level IN ('L1', 'L2', 'L3')),
        scope VARCHAR(20) NOT NULL DEFAULT 'project' CHECK (scope IN ('project', 'global')),
        memory_type VARCHAR(50) NOT NULL,
        title VARCHAR(300),
        content JSONB NOT NULL,
        model VARCHAR(128),
        prompt_version VARCHAR(64),
        classifier_version VARCHAR(64),
        model_confidence DOUBLE PRECISION,
        status VARCHAR(20) NOT NULL DEFAULT 'generated'
            CHECK (status IN ('generated', 'validating', 'rejected', 'needs_review', 'approved', 'published', 'superseded')),
        abstain BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        published_memory_id BIGINT REFERENCES memories(id) ON DELETE RESTRICT
    );

    CREATE TABLE candidate_evidence (
        id BIGSERIAL PRIMARY KEY,
        candidate_id BIGINT NOT NULL REFERENCES memory_candidates(id) ON DELETE CASCADE,
        message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
        start_char INTEGER NOT NULL CHECK (start_char >= 0),
        end_char INTEGER NOT NULL CHECK (end_char > start_char),
        quoted_text TEXT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        UNIQUE(candidate_id, message_id, start_char, end_char)
    );

    CREATE TABLE candidate_policy_results (
        id BIGSERIAL PRIMARY KEY,
        candidate_id BIGINT NOT NULL REFERENCES memory_candidates(id) ON DELETE CASCADE,
        policy_version VARCHAR(64) NOT NULL,
        decision VARCHAR(20) NOT NULL CHECK (decision IN ('reject', 'needs_review', 'approve', 'publish')),
        reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        checks JSONB NOT NULL,
        reviewer VARCHAR(128),
        reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX ix_candidates_project_status ON memory_candidates(project_id, status, created_at);
    CREATE INDEX ix_evidence_message ON candidate_evidence(message_id);

项目 L2 提升为 global L2 时新建 memories 行，并在 memory_relations 写 derived_from；不修改原项目记忆。

### 2.5 Chunk、Embedding Profile 与可变维度向量

SQL：

    CREATE TABLE embedding_profiles (
        id BIGSERIAL PRIMARY KEY,
        name VARCHAR(128) NOT NULL UNIQUE,
        provider VARCHAR(128) NOT NULL,
        model VARCHAR(128) NOT NULL,
        model_revision VARCHAR(128),
        dimension INTEGER NOT NULL CHECK (dimension BETWEEN 1 AND 8192),
        similarity_metric VARCHAR(20) NOT NULL CHECK (similarity_metric IN ('cosine', 'inner_product', 'l2')),
        normalization VARCHAR(20) NOT NULL CHECK (normalization IN ('none', 'l2')),
        query_input_mode VARCHAR(32) NOT NULL DEFAULT 'default',
        document_input_mode VARCHAR(32) NOT NULL DEFAULT 'default',
        max_batch_size INTEGER NOT NULL DEFAULT 32,
        max_inputs_per_request INTEGER NOT NULL DEFAULT 32,
        max_tokens_per_input INTEGER NOT NULL DEFAULT 8192,
        chunker_version VARCHAR(64) NOT NULL,
        content_normalization_version VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'backfilling', 'shadow', 'canary', 'active', 'retired')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        retired_at TIMESTAMPTZ
    );

    CREATE TABLE project_retrieval_profiles (
        project_id BIGINT PRIMARY KEY REFERENCES projects(id) ON DELETE RESTRICT,
        active_embedding_profile_id BIGINT REFERENCES embedding_profiles(id) ON DELETE RESTRICT,
        fallback_mode VARCHAR(20) NOT NULL DEFAULT 'lexical_only'
            CHECK (fallback_mode IN ('lexical_only', 'unavailable')),
        hybrid_search_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        global_result_limit INTEGER NOT NULL DEFAULT 3,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE memory_chunks (
        id BIGSERIAL PRIMARY KEY,
        memory_id BIGINT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        memory_version INTEGER NOT NULL,
        chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
        content TEXT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        start_char INTEGER NOT NULL DEFAULT 0,
        end_char INTEGER NOT NULL DEFAULT 0,
        chunker_version VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(memory_id, memory_version, chunk_index)
    );

    CREATE TABLE memory_embeddings (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        memory_id BIGINT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        chunk_id BIGINT NOT NULL REFERENCES memory_chunks(id) ON DELETE CASCADE,
        embedding_profile_id BIGINT NOT NULL REFERENCES embedding_profiles(id) ON DELETE RESTRICT,
        embedding vector NOT NULL,
        dimension INTEGER NOT NULL CHECK (dimension > 0),
        content_hash CHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(chunk_id, embedding_profile_id)
    );

    CREATE INDEX ix_memory_embeddings_profile_chunk
        ON memory_embeddings(embedding_profile_id, project_id, memory_id);

vector 不指定维度以容纳多个 profile；每个 profile 的 ANN 索引使用固定维度表达式和 profile 条件。例如 1536 profile：

    CREATE INDEX ix_memory_embeddings_profile_1536_hnsw
    ON memory_embeddings USING hnsw
        ((embedding::vector(1536)) vector_cosine_ops)
    WHERE embedding_profile_id = <profile_id> AND dimension = 1536;

索引创建由 profile admin/backfill 命令完成，不能在 API 请求内动态创建。写入时服务端校验 vector_dims(embedding)=embedding_profiles.dimension。SQLite 使用 JSON 向量和 Python 测试实现，不声称提供 ANN。

### 2.6 Lexical、审计与安全

SQL：

    CREATE TABLE memory_search_documents (
        memory_id BIGINT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
        project_id BIGINT REFERENCES projects(id) ON DELETE RESTRICT,
        scope VARCHAR(20) NOT NULL CHECK (scope IN ('project', 'global')),
        normalized_text TEXT NOT NULL,
        search_vector TSVECTOR,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX ix_memory_search_project_scope ON memory_search_documents(project_id, scope, memory_id);
    CREATE INDEX ix_memory_search_vector ON memory_search_documents USING gin(search_vector);
    CREATE INDEX ix_memory_search_trgm ON memory_search_documents USING gin(normalized_text gin_trgm_ops);

    CREATE TABLE retrieval_audits (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        query_hash CHAR(64) NOT NULL,
        retrieval_mode VARCHAR(32) NOT NULL,
        degraded BOOLEAN NOT NULL DEFAULT FALSE,
        degraded_reason VARCHAR(128),
        profile_id BIGINT REFERENCES embedding_profiles(id) ON DELETE RESTRICT,
        parameters JSONB NOT NULL,
        result_ids JSONB NOT NULL,
        latency_ms INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE security_audits (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT REFERENCES projects(id) ON DELETE RESTRICT,
        event_type VARCHAR(64) NOT NULL,
        subject_type VARCHAR(64),
        subject_id VARCHAR(128),
        reason_code VARCHAR(64),
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

Lexical 默认组合为 PostgreSQL simple/代码 token、应用层确定性中文 token 和 pg_trgm。V1.1 不强制 zhparser；它可以作为可选增强，但不能成为 lexical fallback 的唯一依赖。

## 3. 安全迁移顺序

1. 0003_v11_additive_columns：增加 messages 的 occurred/version/conflict 字段；增加 memories 的 scope/source/review 字段；回填并验证。
2. 0004_v11_outbox_jobs：创建 outbox、processing_jobs、job_attempts 和索引；不改变旧 processing 路径。
3. 0005_v11_candidates_policy：创建 candidates、evidence、policy_results。
4. 0006_v11_embedding_profiles：创建 profiles、project settings、chunks、variable-dimension embeddings；保留旧向量。
5. 0007_v11_lexical_audit：启用 pg_trgm（若有权限），创建 search documents、GIN/GiST 索引和审计表。
6. 0008_v11_flags_policies：创建项目 flags/policies，全部默认关闭。
7. 0009_v11_backfill：为已有 memories 建 chunks、search documents 和 legacy profile 映射；只读验证，不切 active。
8. 0010_v11_indexes：按 profile 创建固定维度 partial HNSW 索引；索引失败不得影响 L0/lexical。
9. 0011_v11_switch：仅在人工批准项目上启用 flags；旧表、旧列和旧索引至少保留两个发布周期。

每个 migration 必须幂等、具有反向迁移；数据破坏型 rollback 必须显式拒绝而非静默删除。迁移后执行 schema health、行数、外键、唯一键和索引检查。

## 4. API 契约

### 4.1 Append

POST /api/v1/append，需要 append 权限。

请求字段：project_key、session_key、event_key、role、content、occurred_at、source、metadata。首次返回 HTTP 201：status=accepted、message_id、event_id；相同 key/hash 返回 HTTP 200：status=duplicate、message_id；相同 key/不同 hash 返回 HTTP 409：error=event_key_conflict、audit_id。403 为权限失败，422 为字段失败，503 仅表示数据库事务无法完成。

服务端事务只写 L0 与 outbox；任何远端依赖失败不得使已提交 L0 回滚。

### 4.2 Search

POST /api/v1/search，需要 read 权限。

请求字段：project_key、query、scope_mode、layers、memory_types、limit、include_audit。scope_mode 为 project_only、project_and_global、global_only，默认 project_and_global。响应必须包含 retrieval_mode、degraded、degraded_reason、profile_id、parameters 和结果。结果包含 memory_id、level、scope、content、rank、rrf_score、source_ids；不返回不可比较的跨模式 raw score 作为排名依据。

### 4.3 Context

POST /api/v1/context，需要 read 权限。请求包含 Search 字段以及 task、context_budget_tokens（默认 4000，最大 12000）、skip_pending。只读取 published/accepted 正式记忆；shadow、candidate、draft、needs_review、rejected 一律不进入 Context Builder。响应包含 context、source_ids、retrieval 降级元数据和 budget 使用情况。

### 4.4 Admin

Admin API 需要独立权限且仍强制 project access：

- GET /api/v1/admin/jobs?project_key=&status=&limit=：任务和最近尝试。
- POST /api/v1/admin/jobs/{id}/retry：dead/retry_wait 重新排队并写审计。
- GET /api/v1/admin/candidates?project_key=&status=：候选、证据和策略结果。
- POST /api/v1/admin/candidates/{id}/review：{decision, reviewer, reason}。
- POST /api/v1/admin/profiles：创建不可变 embedding profile。
- POST /api/v1/admin/profiles/{id}/backfill：只创建回填任务。
- POST /api/v1/admin/projects/{key}/profile：切换 canary/active profile，记录回滚信息。
- POST /api/v1/admin/replay：按项目、时间窗和 job type 生成重放任务。
- GET /api/v1/health：返回 database、outbox、worker、lexical、vector profile 状态。

## 5. Outbox 与 Job 状态机

Outbox：pending → dispatched；失败走 pending/retry_wait → retry_wait → pending，超过上限进入 dead。

Job：pending → running → succeeded；失败走 running → retry_wait → pending；不可恢复错误进入 dead；人工可以 dead → pending；任务可以被 Admin 取消为 cancelled。

running 不能长期停留；lease 过期由 sweeper 转为 retry_wait 或 dead。每个事件与 job 通过 event_id、job_type、aggregate_id、payload_version 形成稳定 job key，重复 dispatch 命中已有 job。事件类型固定为 message.appended.v1、memory.candidate_requested.v1、memory.published.v1、memory.embedding_requested.v1、memory.reindex_requested.v1。

## 6. Worker 领取、lease、重试与幂等

PostgreSQL 领取必须使用 FOR UPDATE SKIP LOCKED：选择 status 为 pending/retry_wait、next_attempt_at 到期且 lease 为空或过期的任务，按 priority DESC、created_at、id 排序，单批 10 条；同一事务更新为 running、写入 worker_id、locked_at、heartbeat_at、60 秒 lease，并递增 attempt_count。

Worker 每 20 秒 heartbeat，lease 60 秒。连续两次 heartbeat 失败不得提交结果。任务执行前创建 job_attempt；提交结果时必须检查 locked_by 和 lease，失去 lease 的 worker 只能记录 abandoned，不得发布记忆。

可重试：连接失败、超时、408、429、500/502/503/504、数据库暂时不可用。不可重试：schema 无效、项目不存在、权限失败、evidence 无法验证、profile 维度不匹配、策略拒绝。不确定错误默认重试一次后转人工复核。

退避固定为 10s、60s、300s、1800s，第五次失败进入 dead；可加确定性 0–10% jitter，测试时注入零 jitter。Handler 幂等键：source_message_id+task_type+classifier_version、source_message_id+task_type+prompt_version+input_hash、memory_id+version+chunk_index、chunk_id+embedding_profile_id。publish 使用 candidate 行锁和 published id。

## 7. Retrieval Engine 固定参数

    lexical_top_k: 30
    dense_top_k: 30
    rrf_k: 60
    final_top_k: 12
    global_limit: 3
    max_chunks_per_memory: 2
    context_budget_tokens: 4000
    min_candidate_score: 0.05

Lexical 采用 simple/代码 token、确定性中文 token、pg_trgm 三路候选；Dense 只使用 active profile。融合公式为 RRF(d)=Σ 1/(rrf_k+rank_i(d))，不融合 cosine 与 lexical raw score。先过滤 project/scope/status，再 RRF；仅对达到最低相关性门槛的结果应用 L3 > L2 > L1 priority。之后按 logical memory 去重、每 memory 最多 2 chunks、global 最多 3 条、最后按 4000 token budget 截断。

## 8. Candidate、Evidence、Policy、Review、Publish

Candidate 状态：generated → validating → approved → published；validating 也可到 rejected 或 needs_review；needs_review 可到 approved/rejected；published 可到 superseded。

Policy Engine 必须计算 schema_valid、evidence_valid、scope_valid、project_access_valid、conflict_check、feature_enabled。LLM confidence 只能作为输入，不得单独触发发布。

Evidence 坐标统一为 Unicode code point 半开区间 [start_char,end_char)；存 message id、start/end、quoted text、content hash。服务端检查消息 hash 未变且 content[start:end] 等于 quoted_text。不通过则 needs_review/rejected，不得 publish。

第一阶段 LLM 只运行 ErrorMemoryExtractor shadow；后续依次开放低置信度分类、错误字段补全、FactSplitter，最后才开放 KnowledgeSynthesizer。Global publish 必须额外 reviewer/reason，普通 worker 没有该权限。

## 9. Agent 粒度连续任务

每个 Agent：先写失败测试并确认 RED → 最小实现 → 目标测试/全量测试 → 更新 IMPLEMENTATION_STATUS.md → 单独 commit。Agent 不得修改其他未授权阶段。

1. Agent-0 审计与规格：审计仓库、保存本规格与状态，记录 static_check: ok 和 120 passed。
2. Agent-1 迁移与基础表：实现 additive migrations、flags、policies、outbox/jobs/candidates/profile/chunk/search/audit 表及迁移测试。
3. Agent-2 Append API：实现同事务 L0+outbox、项目内幂等、hash 冲突 409、201/200/409 契约和 Hook 兼容。
4. Agent-3 Outbox/Worker：实现 dispatcher、SKIP LOCKED、lease、heartbeat、sweeper、错误分类、backoff、handler 幂等和 retry Admin API。
5. Agent-4 Lexical/Context：实现 FTS/trgm/中文 token、scope、RRF、L3、global 配额、budget、degraded 元数据。
6. Agent-5 Embedding Profile：实现 embed_query/embed_documents、能力校验、profile immutable、variable vector、partial HNSW、batch/backfill、active 切换；失败只 lexical fallback。
7. Agent-6 Candidate/Policy：规则 classifier 改为候选输出，实现 evidence、policy、publish、version 和 relation。
8. Agent-7 LLM Shadow：实现 provider-neutral adapter、ErrorMemoryExtractor schema、脱敏、prompt injection 防护、证据、abstain、预算和 shadow candidate。
9. Agent-8 MCP/Admin：扩展 Search/Context/Admin/MCP，默认不返回 shadow/candidate，增加 profile/job/candidate/replay/review。
10. Agent-9 测试与故障注入：补单元、迁移、API、并发、lease、重试、远端故障、DB 断连、LLM timeout、越权和 profile 隔离。
11. Agent-10 灰度发布：实现默认关闭、1%/10%/50%/100% canary、指标、profile rollback、两个发布周期兼容。

## 10. 测试与量化验收

每阶段运行 node .\\tools\\static_check.js、项目 .venv Python 的 pytest -q、目标测试、迁移 upgrade/downgrade、并发和故障注入测试。必须满足：

- append 同 key/hash 只产生一条 L0；同 key/different hash 返回 409。
- 数据库、embedding、LLM 故障均不丢 L0。
- 8 个并发 worker 无重复 publish、无永久 running job。
- lease 过期可恢复，退避和错误分类准确。
- dense 失败 100% 返回 lexical fallback 和 degraded reason。
- project A 不能读取 project B；global 只返回 published global。
- profile dimension/index/query 不混用。
- shadow/candidate 不出现在 Search/Context。
- evidence 伪造、偏移错误、hash 变化均不可 publish。
- profile 回滚后使用旧 active profile。
- 迁移幂等、旧 V1 API 可用、旧表保留两个发布周期。

固定回放集记录 Recall@K/NDCG，不比较不同 profile raw cosine。运行指标包含 p95 append/search latency、outbox backlog、retry/dead rate、dense fallback rate、embedding cost、LLM shadow acceptance/abstain rate。

## 11. 灰度、回滚与旧 V1 兼容

默认 flags：

    memory_v11_enabled: false
    server_outbox_enabled: false
    lexical_retrieval_enabled: false
    dense_retrieval_enabled: false
    embedding_profile_v2_enabled: false
    llm_shadow_enabled: false
    candidate_publish_enabled: false

灰度顺序为单测试项目 → 1% → 10% → 50% → 100%；每一步至少观察一个完整 retry window 和发布周期。回滚优先关闭 flag、恢复旧 active profile、暂停新 job，不回滚数据库数据；dead job 保留。

旧 /api/v1/append、/search、/context 在 flags 关闭时保持 V1 行为；flags 开启后只增加字段，不删除旧字段。旧 MemoryRow、MemorySourceRow、MemoryVersionRow、旧 memory_embeddings 和旧 CLI 保留至少两个完整发布周期。
