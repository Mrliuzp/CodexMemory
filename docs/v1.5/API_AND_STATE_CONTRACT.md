# V1.5 API 与状态契约

## 资源与状态

服务属于一个项目。Revision 以服务内从 1 开始的连续 `revision_number` 标识，状态只允许 `proposed`、`published`、`superseded`。Revision 的规范化文档、`content_hash`、校验结果、Markdown 和操作索引一经创建不可修改。

`POST /api/admin/v1/contract-services` 创建服务；`GET /api/admin/v1/contract-services` 列表服务；`GET /api/admin/v1/contract-services/{service_id}` 返回服务及其 Revision 摘要。写操作要求既有管理员权限，所有读写均由服务端实施项目隔离。

`POST /api/admin/v1/contract-services/{service_id}/revisions` 使用 multipart 的 `file` 字段上传文件。成功响应返回 `data` 中的完整 Revision；同一服务同一 `content_hash` 重复上传返回既有 Revision 并在 `meta` 明示已复用。文件名扩展名、编码、大小和内容都必须满足冻结规格。

`GET /api/admin/v1/contract-services/{service_id}/revisions/{revision_number}` 返回不可变 Revision、校验结果、warnings、操作摘要、规范化文档和 Markdown。Markdown 固定排序、固定标题层级和固定换行，禁止插入当前时间或环境相关数据。

`POST /api/admin/v1/contract-services/{service_id}/revisions/{revision_number}/publish` 的 JSON 请求体为 `{ "expected_content_hash": "<sha256>" }`。哈希不匹配返回冲突；发布已发布 Revision 是幂等成功；发布其他 Revision 时必须在同一事务内完成状态切换。任何错误都不得留下部分 `superseded` 或部分 `published` 状态。

## OpenAPI 校验与告警

归一化输出固定为 `openapi: 3.1.0` 和 `profile_version: v1`。接受 `3.0.3`、`3.1.x`；拒绝 Swagger 2.0、3.0.0–3.0.2、3.2、外部 `$ref`、`callbacks`、`webhooks`、`links`。支持 nullable、组合 Schema、discriminator、multipart 与循环本地 `$ref` 的安全解析。

每项 operation 均要求非空且 Revision 内唯一的 `operationId`。同一 `method + path` 的 `operationId` 变更为校验错误；同一 `operationId` 的路由变更是允许保存的 `route_changed` warning。错误和 warning 均作为结构化数据返回，不能仅写入日志。

## 通用响应

所有 V1.5 管理接口复用既有响应格式：成功为 `{ "data": ..., "meta": ..., "request_id": ... }`，失败也必须保留 `meta` 与 `request_id`。不得另建响应包裹格式或让调用方承担项目隔离判断。
