# Codex Memory V1.1 Agent-1 Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the V1.1 additive database schema, Alembic migrations, SQLAlchemy models, and SQLite migration coverage while preserving V1 tables and behavior.

**Architecture:** Keep the historical V1 `Base` metadata and `memory_embeddings` table compatible. Add the three V1.1 additive columns to the existing message/memory mappings and define new V1.1 tables in a separate `V11Base` metadata so migration 0001 does not eagerly create future tables. Split the migration into 0003–0008 in the executable-spec order, with dialect-aware SQLite types and guarded additive operations.

**Tech Stack:** Python 3.10+, SQLAlchemy 2, Alembic, PostgreSQL/pgvector, SQLite, pytest.

## Global Constraints

- Only additive database schema, migrations, SQLAlchemy models, migration tests, and implementation status are in scope.
- Do not modify business API, worker, retrieval, embedding provider, or LLM modules.
- Preserve old V1 tables and old `memory_embeddings`; create `memory_embedding_vectors` for V1.1 profile-isolated vectors.
- All V1.1 feature flags default to `false`; processing policy defaults to `fail_closed`, remote providers disabled, and redaction enabled.
- Migrations must be guarded for an already-upgraded database and remain runnable on SQLite test databases.
- Run target migration/model tests, `node .\\tools\\static_check.js`, and the full `.venv` pytest suite before the final commit.

### Task 1: Add failing SQLite model and migration tests

**Files:**
- Create: `tests/test_v11_schema.py`

- [ ] **Step 1: Write tests for all required V1.1 table names, additive columns, defaults, legacy retention, and SQLite model inserts.**
- [ ] **Step 2: Run `..\\.venv\\Scripts\\python.exe -m pytest tests/test_v11_schema.py -q` and confirm RED because V1.1 models/tables/migrations do not exist.

### Task 2: Add additive SQLAlchemy mappings

**Files:**
- Modify: `src/codex_memory/db_models.py`

- [ ] **Step 1: Add `occurred_at`, `ingestion_version`, `conflict_status` to `MessageRow` and `scope`, `source_kind`, `review_status` to `MemoryRow` with V1-compatible defaults.
- [ ] **Step 2: Add `V11Base` and model classes for flags, policies, outbox/jobs/attempts, candidates/evidence/policy results, profiles/retrieval profiles/chunks/vector rows, lexical documents, and audit rows.
- [ ] **Step 3: Run the focused model test and keep the legacy `Base.metadata` behavior unchanged for new V1.1 tables.

### Task 3: Add guarded Alembic migrations

**Files:**
- Create: `alembic/versions/0003_v11_additive_columns.py`
- Create: `alembic/versions/0004_v11_outbox_jobs.py`
- Create: `alembic/versions/0005_v11_candidates_policy.py`
- Create: `alembic/versions/0006_v11_embedding_profiles.py`
- Create: `alembic/versions/0007_v11_lexical_audit.py`
- Create: `alembic/versions/0008_v11_flags_policies.py`

- [ ] **Step 1: Implement guarded additive columns and the project/event unique index in 0003; retain the historical global event-key unique constraint.
- [ ] **Step 2: Implement outbox, processing jobs, attempts, indexes, and checks in 0004.
- [ ] **Step 3: Implement candidate, evidence, and policy result tables in 0005.
- [ ] **Step 4: Implement embedding profiles, project retrieval profiles, chunks, and `memory_embedding_vectors` in 0006 using JSON vectors on SQLite and pgvector on PostgreSQL.
- [ ] **Step 5: Implement lexical search documents and retrieval/security audits in 0007; omit PostgreSQL-only GIN/TSVECTOR operations on SQLite.
- [ ] **Step 6: Implement feature flags and processing policies in 0008 with all V1.1 flags disabled by default.
- [ ] **Step 7: Run migration tests through `upgrade head`, inspect columns/tables/indexes/defaults, and test downgrade back to `0002` without dropping legacy tables.

### Task 4: Refactor and verify

**Files:**
- Modify: `IMPLEMENTATION_STATUS.md`

- [ ] **Step 1: Run target tests, static check, and full pytest; investigate only Agent-1 failures without changing out-of-scope modules.
- [ ] **Step 2: Update the Agent-1 row to `completed`, record the final commit hash and exact test results, and record any compatibility decisions.
- [ ] **Step 3: Review `git diff` and `git status`, stage only Agent-1 files, and commit with a message beginning `feat: v1.1 add additive schema`.
- [ ] **Step 4: Re-run the final verification commands after the commit and report the commit, files, and outputs.
