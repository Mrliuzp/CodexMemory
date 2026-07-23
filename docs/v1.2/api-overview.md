# 管理 API P0 概览

基础路径：`/api/admin/v1`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/me` | 当前项目和权限 |
| GET | `/dashboard` | 只读项目计数 |
| GET | `/projects` | 已授权项目 |
| GET | `/projects/{project_key}` | 项目详情 |
| GET | `/projects/{project_key}/scopes` | 项目作用域 |
| GET | `/raw-records` | 脱敏后的原始消息 |
| GET | `/candidates` | 脱敏后的候选记忆 |
| GET | `/memories` | 脱敏后的已接受记忆 |
| GET | `/jobs` | 处理任务 |
| GET | `/outbox-events` | Outbox 状态 |
| GET | `/retrieval-audits` | 检索审计数据 |
| GET | `/audit-events` | 安全与领域审计数据 |

列表接口接受 `project_key`、`scope_id`、`page`、`page_size`（1-200）、`sort` 和 `order`（`asc` 或 `desc`）。排序字段必须在允许列表中。P0 响应映射器会在 JSON 内容返回前移除原始字段和类似凭据的字段。