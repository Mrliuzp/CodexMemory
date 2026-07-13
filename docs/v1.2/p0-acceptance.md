# P0 Acceptance Checklist

- [x] Admin API is mounted at `/api/admin/v1` without changing `/api/v1/*` behavior.
- [x] Every P0 read route requires a Bearer token and returns a `WWW-Authenticate: Bearer` challenge when absent.
- [x] Project and Scope checks return structured 403 errors.
- [x] List responses have stable pagination metadata and request IDs.
- [x] Sort fields are allowlisted and page size is capped at 200.
- [x] Candidate and memory content is redacted before serialization.
- [x] P0 routes expose no publish, review, retry, replay, import, or upload command.
- [x] Scope migration preserves legacy project-level records and creates a default projection.
- [x] Admin Web builds as a Vue/Vite/Element Plus application with dashboard, project and read-only data views.

Verification commands:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd apps\admin-web
npm run build
```
- [x] Admin Web has a production Nginx container with SPA fallback and `/api` proxying.
