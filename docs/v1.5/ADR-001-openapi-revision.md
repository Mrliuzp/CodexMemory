# ADR-001：V1.5 OpenAPI Revision 的权威边界

## 状态

已接受，V1.5 实施中。

## 决策

V1.5 采用 `backend_authoritative`。数据库中每个服务的 Revision 为不可变记录；上传只创建 `proposed` Revision，管理员以携带 `expected_content_hash` 的显式请求发布。单个服务同一时间最多一个 `published` Revision；成功发布在同一事务内将此前 `published` Revision 转为 `superseded`。

服务的接口身份以 Revision 内 `operationId` 为逻辑索引。相同路由改 `operationId` 是不连续的接口身份变化，必须拒绝；同一 `operationId` 改路由允许，但产生 `route_changed` warning。内容哈希基于归一化后的 OpenAPI 文档，重复上传不得创建新 Revision。

## 输入与安全边界

只接受本地 UTF-8 JSON/YAML 文件，允许 BOM，最大 2 MiB。服务器同步完成解析、校验、归一化、Markdown 生成和持久化；不访问 URL，不引入后台 Worker、Outbox 或部署绑定。只允许本地 `$ref`，拒绝外部引用及 `callbacks`、`webhooks`、`links`。

接受 OpenAPI `3.0.3` 和 `3.1.x`，内部仅保存无损归一化的 OpenAPI `3.1.0`，`profile_version` 固定为 `v1`。无法无损归一化、结构深度超过 64、operations 超过 500 的文档一律拒绝。

## 后果

该选择确保发布可审核、版本可追溯，且不会让网络输入、异步执行或消费者分析扩大 V1.5 的攻击面和交付范围。PHP AST、代码生成、Mock、Breaking CI、LLM/Embedding、Memory 投影、消费者图与自动发布延后至后续版本。
