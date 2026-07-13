# V1.2 Domain Boundaries

| Boundary | Owns | Does not own |
| --- | --- | --- |
| Admin Web | Navigation, table rendering, URL query state, token input | Authorization decisions, data writes |
| Admin API | Authentication, project/Scope checks, pagination, redaction, read DTOs | Import, publishing, retry, replay |
| Query Service | Read models for raw records, candidates, memories, jobs, outbox, retrieval and audit data | Mutating V1.1 aggregates |
| Scope projection | `knowledge_scopes` and the logical `default` projection for legacy project data | Rewriting legacy records |
| V1.1 runtime | Append, process, retrieve, publish and worker behavior | Admin Web presentation |

P0 permits only `GET` endpoints. Write workflows are explicitly deferred to P1 and must have separate permissions, audit records, idempotency and feature gates.
