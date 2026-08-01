---
name: codex-memory-sync-contract
description: 整理项目中的 OpenAPI 3.0.3/3.1 接口契约，检查 operationId、证据完整性与 V1.5 Profile，并通过 Codex Memory MCP 创建或复用契约服务、上传 proposed Revision。适用于后端接口完成后沉淀文档、前端 Mock/需求转契约提案、更新已有 API 文档或把 OpenAPI 同步到项目知识库；不用于自动发布契约。
---

# Codex Memory 接口契约同步

把代码、框架导出的 OpenAPI、现有文档和明确需求整理成可审计的契约提案，再交给管理员发布。

## 工作流

1. 从仓库 `AGENTS.md`、环境或用户输入确定 `project`。使用稳定项目键，不使用数据库数字 ID。
2. 使用 `list_contract_services` 查找相关服务；若已知稳定 `service_key`，使用 `get_contract_service` 查看现有 Revision。不要仅凭显示名称创建重复服务。
3. 按以下优先级收集证据：
   - PHP 框架或项目脚本导出的 OpenAPI；
   - 仓库中维护的 OpenAPI JSON/YAML；
   - 已实现的路由、请求验证、响应 DTO、错误结构和测试；
   - 用户明确给出的字段、Mock 与业务约束。
4. 证据不足以确定必填、可空、枚举、鉴权、状态码或错误结构时，保留现有契约或向用户说明缺口；不要编造字段。
5. 整理为 OpenAPI 3.0.3 或 3.1.x，并执行“上传前检查”。
6. 使用 `ensure_contract_service` 幂等确保服务存在。已有服务不会因调用而改名或改描述。
7. 使用 `propose_contract_revision` 上传完整文档字符串和原始文件名。
8. 返回 `service_key`、`revision_number`、`status`、`content_hash`、`operation_count`、`warnings` 和 `reused`，并提醒用户在管理后台审核发布。

## 上传前检查

- 文件扩展名只能是 `.json`、`.yaml` 或 `.yml`，内容采用 UTF-8，总大小不超过 2 MiB。
- 版本只能是 OpenAPI 3.0.3 或 3.1.x。
- 每个 operation 必须有非空、Revision 内唯一且稳定的 `operationId`。
- operation 总数不超过 500，结构深度不超过 64。
- 只使用本地 `$ref`；不得包含 external `$ref`、`callbacks`、`webhooks` 或 `links`。
- 可使用 `oneOf`、`anyOf`、`allOf`、`discriminator`、`nullable`、日期、decimal 和 multipart。
- 示例中不得包含真实 Token、Cookie、密码、私钥、连接串或客户敏感数据。
- 若当前已发布 Revision 的同一 method + path 使用不同 `operationId`，停止上传并修正标识。
- 若相同 `operationId` 改变 method/path，可以上传提案，但必须明确报告 `route_changed` 警告。

## MCP 调用约定

按需调用以下工具：

- `list_contract_services(project, keyword?)`
- `get_contract_service(project, service)`
- `ensure_contract_service(project, service, name?, description?)`
- `propose_contract_revision(project, service, document, filename?)`

`document` 必须是完整 OpenAPI 文本，不是本地文件路径。MCP 服务运行在 Docker 中，不能假设它能读取宿主工作区路径。

## 安全边界

- 只创建 `proposed` Revision，绝不自动发布、覆盖或删除现有 Revision。
- 不使用管理端登录令牌，也不尝试绕过项目隔离。
- 不把普通 Markdown 说明直接当成机器契约；先转换为可验证 OpenAPI。
- MCP 不可用或工具未出现时，停止上传并提示重启 Codex 以重新发现 MCP 工具；仍可先在仓库中整理契约草稿。
- 返回校验错误时，修正文档后重试；不要通过删除关键字段来掩盖错误。