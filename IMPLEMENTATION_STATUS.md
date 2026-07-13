# codex-memory V1.1 implementation status

Updated: 2026-07-12

## Current state

The V1.1 vertical slice is **fully implemented and verified on both SQLite and PostgreSQL**. The original V1 behavior is preserved when the V1.1 schema is absent.

## Completed and verified

- Append API (V1.1): transactional L0 + outbox, project-scoped event_key idempotency, hash conflict 409, occurred_at, 201/200/409 responses, V1 compatibility.
- Outbox dispatcher and job worker: idempotent dispatch, claim, lease, heartbeat, retry backoff, dead state, expired-lease recovery.
- Lexical retrieval: project/global scope modes, layer/type filters, deterministic RRF metadata, retrieval audit, context token budgets.
- Embedding profiles: deterministic local vectors, immutable metadata, chunk backfill, dimension validation, profile isolation.
- Dense retrieval: profile-aware RRF hybrid search, lexical degraded fallback with reason.
- Candidate policy: immutable L0 evidence verification, scope validation, default-off publish flag, governance audit.
- ErrorMemoryExtractor: shadow-only, secret redaction, prompt injection detection, strict schema, no direct formal memory writes.
- Admin API: jobs/list/retry, candidates (shadow hidden by default), review/approve/reject, replay, profile creation/activation/backfill, flag updates, expanded health.
- Project policy service: feature flags (all default off), canary profile 1/10/50/100%, rollback with previous active preservation, audit on every change.
- Production job handlers: message.appended.v1 routed to candidate creation, handler error classification (permanent vs retryable), run_v11_once entry point.
- Provider adapter: embed_documents with remote policy checks, allowed provider list, local fallback, TimeoutError classification.
- Provider budget tracking: DailyTokenUsageRow, per-project daily token budgets, BudgetExceededError, wrapped backend integration, migration 0010.
- Canary migration (0009): guarded additive columns, SQLite batch-ALTER downgrade.
- Concurrency/fault-injection tests: 8-thread append idempotence, 4-worker job claim non-duplication, lease expiry recovery, cross-project isolation, handler error classification, full pipeline.
- MCP context tool accepts V1.1 filter parameters.

## SQLite verification

- `static_check: ok`, `pytest -q`: **162 passed**, 2 warnings (Alembic config deprecation only).
- 13 new V1.1 test modules with 42+ targeted tests.

## Docker Compose / PostgreSQL verification (2026-07-12)

- Docker Engine v29.6.1 + Docker Compose v5.2.0: all 4 services (postgres, api, mcp, worker) built and deployed successfully.
- Alembic migrations up to 0010 ran against PostgreSQL 16 + pgvector.
- Health endpoint shows V1.1 extended fields: `outbox: ok`, `lexical: "available"`, `vector_profile: "ok"`.
- All V1.1 API features verified: append, duplicate, 409 conflict, search, context, admin auth.
- **FOR UPDATE SKIP LOCKED verified**: 15 outbox events atomically claimed via PostgreSQL SKIP LOCKED; lease sweep recovers all expired-running jobs; retry-wait jobs re-claimed by second worker. No duplicate dispatch or duplicate claim observed.