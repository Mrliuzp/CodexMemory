# P0 Permission Matrix

| Actor | Authentication | Project scope | P0 reads | P0 writes |
| --- | --- | --- | --- | --- |
| Project reader | Bearer API key with `read` | Token project only | Yes | No |
| Project admin | Bearer API key with `admin` | Authorized projects | Yes | No in P0 |
| Invalid or inactive token | None | None | No, 401 | No |
| Valid token outside project | Valid | Denied, 403 | No | No |
| Valid token outside Scope | Valid | Denied, 403 | No | No |

Scope checks happen after project authorization and before data queries. Client-provided project or Scope identifiers never broaden the principal's grants.
