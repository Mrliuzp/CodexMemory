# codex-memory V1.1 Implementation Plan

> **For agentic workers:** Use the V1.1 executable specification as the binding contract. Each Agent task must follow RED → GREEN → full verification → isolated commit.

**Goal:** Upgrade the existing codex-memory V1 into a backward-compatible V1.1 with durable ingestion, candidate governance, deterministic hybrid retrieval, profile-versioned embeddings, and shadow-only LLM enrichment.

**Architecture:** Keep Hook local JSONL outbox and add a server-side transactional outbox. Persist immutable L0 first, then process candidates, policy, publication, chunks, embeddings, and retrieval asynchronously. Dense retrieval is profile-isolated and always has lexical-only degradation.

**Tech Stack:** Python 3.10+, SQLAlchemy 2, Alembic, FastAPI, PostgreSQL 16, pgvector, SQLite test path, pytest.

## Global Constraints

- Append never calls embedding or LLM synchronously.
- UNIQUE(project_id,event_key); same hash is idempotent, different hash is HTTP 409 plus audit.
- Dense failure is lexical-only fallback; local-token vectors never query remote vector indexes.
- V1.1 flags default false.
- LLM writes candidates only; server owns project_id, scope, review, and publish fields.
- Evidence must be verified against immutable L0.
- Shadow results never enter Search or Context.
- Old V1 tables/API remain for two full release cycles.

### Task 0: Audit and specification

Files: docs/CODEX_MEMORY_V1_1_EXECUTABLE_SPEC.md, IMPLEMENTATION_STATUS.md. Run baseline static_check and pytest. Commit: 7cefe06.

### Task 1: Additive database schema

Files: db_models.py, alembic/versions/0003+ migrations, tests/test_v1_schema.py or new migration tests. Add flags, policies, outbox, jobs, attempts, candidates, evidence, policy results, profiles, chunks, V1.1 vector table, lexical/audit tables. Preserve legacy memory_embeddings. Commit separately.

### Task 2: Append contract

Files: v1_service.py, http_api.py, v1_schemas.py, db_models.py, Hook tests. Write immutable L0 plus outbox in one transaction; return 201/200/409; retain Hook local outbox. Commit separately.

### Task 3: Worker framework

Files: new worker/outbox modules, models, services, admin endpoint tests. Add SKIP LOCKED claim, lease, heartbeat, sweeper, backoff, exception classification, job idempotency and dead/retry operations. Commit separately.

### Task 4: Lexical and context retrieval

Files: retrieval modules, v1_service/http schemas, lexical migrations, retrieval tests. Add scope/status filtering, simple/code/Chinese token/trigram retrieval, RRF constants, L3 priority, global quota, context budget and degraded metadata. Commit separately.

### Task 5: Embedding profiles

Files: embedding provider modules, vector store/retriever, profile APIs, migrations and tests. Add query/document methods, batch/capability validation, profile-isolated vectors/indexes, backfill, shadow retrieval, canary/rollback. Commit separately.

### Task 6: Candidate and policy pipeline

Files: candidate/policy modules, classifier integration, models, APIs and tests. Convert rule classification to candidate output; verify evidence and publish with version/relation/audit. Commit separately.

### Task 7: LLM shadow enrichment

Files: provider-neutral LLM adapter, ErrorMemoryExtractor, redaction/policy, schemas and tests. Shadow-only, strict schema, abstain, evidence, timeout/cost budget and prompt injection defense. Commit separately.

### Task 8: MCP and Admin surface

Files: v1 MCP, HTTP API, auth, schemas and tests. Add Search/Context/Admin/job/profile/candidate/replay/review contracts without exposing shadow candidates. Commit separately.

### Task 9: Verification and fault injection

Files: unit/integration/concurrency/fault tests and status. Verify all acceptance criteria, migration compatibility, isolation, profile separation, worker crash/lease recovery and remote failures. Commit separately.

### Task 10: Flags and release

Files: feature flag/policy modules, deployment docs, status and release tests. Implement project canary 1/10/50/100%, rollback, metrics, compatibility and two-cycle retention. Commit separately.
