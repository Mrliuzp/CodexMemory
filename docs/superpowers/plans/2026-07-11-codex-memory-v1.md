# Codex Memory V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Deliver PostgreSQL/pgvector storage, authenticated V1 API, Codex Hook capture, HTTP MCP tools, reflection worker, Docker Compose, and automated verification.

**Architecture:** SQLAlchemy repositories become the V1 persistence boundary. FastAPI owns authentication and project isolation. Hooks and the deployed MCP process call the API; existing SQLite code remains available as a compatibility path.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, pgvector, FastMCP, httpx, pytest, Docker Compose.

## Global Constraints

- PostgreSQL image: pgvector/pgvector:pg16; SQLite remains usable for legacy tests.
- L0 messages are append-only; event_key is unique, content_hash is not.
- Bearer key permissions are read, append, memory_write, reflect, and admin.
- New endpoints live under /api/v1; legacy endpoints remain compatibility aliases.
- L3 precedes L2 and L1 in context; client MCP can create only L1.
- Hook outbox contains no bearer token and supports concurrent hook executions.

---

### Task 1: V1 configuration and relational schema

**Files:**
- Modify: pyproject.toml
- Create: src/codex_memory/config.py
- Create: src/codex_memory/db.py
- Create: src/codex_memory/db_models.py
- Create: alembic.ini, alembic/env.py, alembic/versions/0001_v1_schema.py
- Test: tests/test_v1_schema.py

**Produces:** Settings.from_env(), SQLAlchemy engine/session factory, ORM rows for projects, sessions, messages, memories, embeddings, sources, versions, API keys, and audit logs.

- [ ] Write failing tests that insert the same event_key twice and assert IntegrityError, then insert identical content under two different event keys and assert both rows exist.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest tests/test_v1_schema.py -v
  Expected: FAIL because V1 schema modules do not exist.
- [ ] Implement configuration, ORM metadata, and Alembic migration. Use UNIQUE(project_id, session_key), UNIQUE(event_key), and a non-unique content_hash. PostgreSQL migration creates the vector extension and vector(n) from CODEX_MEMORY_EMBEDDING_DIMENSION.
- [ ] Re-run the focused test; expected PASS.
- [ ] Commit: feat: add v1 database schema.

### Task 2: Authentication and V1 repository service

**Files:**
- Create: src/codex_memory/auth.py
- Create: src/codex_memory/v1_repository.py
- Create: src/codex_memory/v1_service.py
- Test: tests/test_v1_auth.py, tests/test_v1_service.py

**Produces:** Principal(project_key, permissions), token-hash authentication, ProjectAccessDenied, AppendResult(message_id, status), and V1MemoryService.

- [ ] Write failing tests for duplicate event append returning the first ID with status duplicate, and a mall token attempting to append to erp raising ProjectAccessDenied.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest tests/test_v1_auth.py tests/test_v1_service.py -v
  Expected: FAIL because V1 service classes do not exist.
- [ ] Implement require_permission(), append_message(), create_l1_memory(), source links, and audit writes. Do not implement message update/delete; reject direct L2 creation.
- [ ] Re-run focused tests; expected PASS.
- [ ] Commit: feat: add authenticated v1 memory service.

### Task 3: Authenticated versioned HTTP API

**Files:**
- Modify: src/codex_memory/http_api.py
- Create: src/codex_memory/v1_schemas.py
- Test: tests/test_v1_http_api.py

**Produces:** POST /api/v1/append, /context, /search, /memory, /reflect and GET /api/v1/health.

- [ ] Write failing tests for unauthenticated append returning 401, project mismatch returning 403, duplicate append returning status duplicate, and /memory with level L2 returning 422.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest tests/test_v1_http_api.py -v
  Expected: FAIL because /api/v1 routes do not exist.
- [ ] Add Pydantic request/response models and bearer dependency. Require append, read, memory_write, or reflect permissions per endpoint. Return L3/L2/L1 context sections and source IDs. Preserve unversioned routes as deprecated aliases.
- [ ] Re-run focused tests plus tests/test_http_api.py; expected PASS.
- [ ] Commit: feat: add authenticated v1 http api.

### Task 4: Hook capture and durable outbox

**Files:**
- Create: .codex/hooks.json
- Create: .codex/scripts/hook_common.py
- Create: .codex/scripts/append_user.py
- Create: .codex/scripts/append_assistant.py
- Modify: .gitignore
- Test: tests/test_hooks.py

**Produces:** UserPromptSubmit and Stop handlers reading Hook JSON from stdin, project-map resolution, V1 append/context calls, and JSONL outbox replay.

- [ ] Write failing tests for user event_key session:turn:user, Stop event_key session:turn:assistant, empty assistant omission, context output, and failed request writing a token-free outbox record.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest tests/test_hooks.py -v
  Expected: FAIL because Hook modules do not exist.
- [ ] Implement a three-second HTTP client timeout, project map from CODEX_MEMORY_PROJECT_MAP, atomic locked JSONL append, replay-before-send, and atomic rewrite of failed records.
- [ ] Re-run focused tests; expected PASS.
- [ ] Commit: feat: add codex hook memory capture.

### Task 5: HTTP-backed MCP and scheduled reflection worker

**Files:**
- Create: src/codex_memory/api_client.py
- Modify: src/codex_memory/mcp_server.py
- Modify: src/codex_memory/cli.py
- Create: src/codex_memory/worker.py
- Test: tests/test_v1_mcp_server.py, tests/test_v1_worker.py

**Produces:** build_context, retrieve_memory, record_outcome, health MCP tools and worker run_once().

- [ ] Write failing tests that build_context calls /api/v1/context, retrieve_memory calls /api/v1/search, record_outcome posts level L1, and run_once reflects each active project once.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest tests/test_v1_mcp_server.py tests/test_v1_worker.py -v
  Expected: FAIL because V1 MCP client and worker do not exist.
- [ ] Implement MemoryApiClient with Bearer headers; preserve local SQLite MCP factory; add V1 factory and CLI streamable-http host/port options. Implement worker failure audit and daily 02:00 scheduling entrypoint.
- [ ] Re-run focused tests plus tests/test_mcp_server.py; expected PASS.
- [ ] Commit: feat: add v1 mcp tools and worker.

### Task 6: Docker deployment and final verification

**Files:**
- Create: Dockerfile
- Create: docker-compose.yml
- Create: .env.example
- Create: tests/test_compose_contract.py
- Create: tests/test_v1_end_to_end.py
- Modify: README.md

**Produces:** Compose services postgres, api, mcp, worker; documented local and server startup flow.

- [ ] Write failing Compose contract test checking pgvector/pgvector:pg16, API 8000, MCP 8001, and no hard-coded usable secret.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest tests/test_compose_contract.py tests/test_v1_end_to_end.py -v
  Expected: FAIL because deployment files and V1 fixture do not exist.
- [ ] Implement Dockerfile, Compose health dependencies, .env.example, and end-to-end fixture that appends, reflects, retrieves V1 context through MCP, and checks source IDs.
- [ ] Run: .\\.venv\\Scripts\\python.exe -m pytest
  Expected: all unit tests PASS.
- [ ] Run: docker compose config
  Expected: valid Compose configuration. When Docker is available, start the stack, wait for /api/v1/health, and run PostgreSQL integration tests.
- [ ] Commit: feat: add v1 docker deployment and verification.

