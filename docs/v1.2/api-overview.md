# Admin API P0 Overview

Base path: `/api/admin/v1`

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/me` | Current project and permissions |
| GET | `/dashboard` | Read-only project counters |
| GET | `/projects` | Authorized projects |
| GET | `/projects/{project_key}` | Project detail |
| GET | `/projects/{project_key}/scopes` | Project Scopes |
| GET | `/raw-records` | Redacted raw messages |
| GET | `/candidates` | Redacted candidate memories |
| GET | `/memories` | Redacted accepted memories |
| GET | `/jobs` | Processing jobs |
| GET | `/outbox-events` | Outbox state |
| GET | `/retrieval-audits` | Retrieval audit data |
| GET | `/audit-events` | Security and domain audit data |

List endpoints accept `project_key`, `scope_id`, `page`, `page_size` (1-200), `sort`, and `order` (`asc` or `desc`). Sort fields are allowlisted. The P0 response mapper removes raw and credential-like fields from JSON content.
