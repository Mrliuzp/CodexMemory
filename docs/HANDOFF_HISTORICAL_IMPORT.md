# 历史知识导入模块交接文档

## 文档信息

- 项目：`20260703-codex-memory-system`
- 交接日期：2026-07-18
- 当前状态：功能实现与本地部署验收完成，改动尚未提交或推送
- 交接范围：历史知识导入（V1.3.1）及其管理端、存储、数据库迁移和运行验证

## 当前任务目标

建设一套可在管理端使用的历史知识导入能力，将 Markdown、TXT、JSON/JSONL、SQL、源码/代码、PDF、DOCX 和 ZIP 等历史资料安全地导入记忆系统，并满足以下约束：

- 采用批次、文件、问题和处理任务的异步生命周期，支持进度查询、取消、重试、回滚和人工审核。
- 导入内容先形成候选记忆，经过治理和审核后才能发布为正式记忆。
- 保证项目、知识范围和会话之间的隔离，保留来源、版本和审计信息。
- 在 Docker Compose 的 API、Worker 和 Admin Web 中可运行，并以 PostgreSQL 作为共享生产数据源。

## 已完成的事项

### 后端与数据模型

- 完成 V1.3.1 导入治理、生命周期、文件/问题、范围 ID、对象存储和分片上传迁移：`0014` 至 `0021`。
- 完成 `ImportBatch`、`ImportFile`、`ImportIssue`、候选记忆和处理任务的关联模型及状态流转。
- 将导入实体的 `scope_id` 统一为真实的 `knowledge_scopes.id`，并为已发布的 `memories` 补充真实范围 ID。
- 保留来源消息、来源文件和记忆版本，支持按项目和范围追溯。

### API、Worker 与管理端

- 提供批次创建、文件上传、启动处理、状态/进度查询、取消、重试、回滚和审核发布接口。
- 提供跨请求、跨进程的分片上传接口：开始上传、提交分片、查询分片状态、完成上传。
- Worker 使用 Outbox → Processing Job → Worker 流程处理导入任务，支持失败重试和问题记录。
- Admin Web 已接入导入页面，能够展示批次进度、文件状态、问题、候选记忆，并执行审核、取消、重试和回滚。

### 解析、安全与去重

- 已实现 Markdown、TXT、JSON/JSONL、SQL、源码/代码、PDF、DOCX 和 ZIP 解析。
- PDF 支持页级文本提取、页标题和页级定位信息；DOCX 支持段落、表格等内容提取。
- ZIP 增加路径穿越、文件数量、单文件大小、总大小和嵌套深度限制。
- 对疑似 Prompt Injection 的内容隔离为问题，不直接进入正式记忆；候选层提供常见凭据脱敏。
- 支持内容哈希和业务键去重，重复导入不会重复生成同一批记忆。

### 存储、部署与文档

- 新增可配置的文件系统对象存储后端；数据库存储后端保留为本地/测试实现。
- Docker Compose 的 API 与 Worker 共享 `importdata` 卷，并通过 `IMPORT_STORAGE_BACKEND`、`IMPORT_STORAGE_PATH` 配置对象存储。
- 已更新 `README.md` 和 `IMPLEMENTATION_STATUS.md`，说明支持格式、分片上传、存储后端和当前边界。

### 验证结果

- 后端全量测试：`206 passed, 1 skipped, 19 warnings`。
- 前端测试：`1 passed`；生产构建通过。
- 静态检查：`node tools/static_check.js` 返回 `static_check: ok`。
- UTF-8 检查通过：4572 个文件中无替换字符文件。
- `git diff --check` 通过；`docker compose config --quiet` 通过。
- Docker 部署验证通过：Admin Web `5175` 返回 200，API 健康检查为 `status=ok`，PostgreSQL、向量、Outbox 和 Worker 均正常。
- 数据库当前迁移版本为 `0021_v131_memory_scope (head)`；`knowledge_scopes`、`import_files`、`import_upload_parts` 和 `import_issues` 表存在，导入批次范围字段为 `BIGINT`，文件内容字段可为空。

## 遇到的问题与处理

### 已处理问题

- Windows 环境下默认临时目录权限会导致测试初次失败，改用受控可写临时目录后全量测试稳定通过。
- PostgreSQL 的 `alembic_version.version_num` 长度限制导致过长迁移 revision 启动失败，已将新增 revision ID 缩短并完成重建验证。
- 既有 Docker 部署中宿主机 `5174`/`8001` 被其他服务占用，本项目使用 `5175`/`8002` 验证，避免停止用户已有容器。
- SQLite Worker 曾存在独立文件导致缺少表的问题，当前 Compose 已统一使用共享 PostgreSQL；本地遗留 SQLite 服务和数据库已清理。

### 当前风险与边界

- SQLite 下的 `V11JobWorker.claim_jobs` 仍依赖事务竞争避免重复领取，曾出现过一次并发重复 claim；后续应在真实并发数据库和压力测试中继续观察。
- 目前没有 S3/MinIO 等远程对象存储适配器；文件系统后端适合本地和单机部署，生产环境需要补充远程存储、生命周期清理和权限策略。
- PDF 当前以页级文本和偏移定位为主，复杂版式、图片、扫描件 OCR 和视觉保真仍属于后续增强。
- 工作区改动尚未提交或推送，不能视为远程分支已同步。
- 本轮自动记忆 MCP 的 `build_context` 和 `append_message` 均返回 HTTP 502，因此本轮记录未能写入，不能声称已完成自动归档。

## 下一步计划

1. 在负责人确认后，审阅当前工作区差异，按功能范围分批暂存并创建提交，再推送到远程分支。
2. 推送后创建或更新代码审查/PR，重点审查迁移兼容性、范围隔离、候选发布边界和 Worker 并发行为。
3. 为生产部署补充 S3/MinIO 对象存储适配器、对象生命周期清理、访问权限和失败恢复策略。
4. 增加大文件分片上传、断点续传、并发 Worker、ZIP 安全限制和批次回滚的压力测试及端到端测试。
5. 提升 PDF 处理能力：扫描件 OCR、图片/表格抽取、复杂版式保留和更精确的页级来源定位。
6. 在管理端完善审核筛选、问题聚合、批次审计导出和导入历史检索，并补充用户操作指引。
7. 每次后续改动后重新执行后端测试、前端测试/构建、静态检查、UTF-8 检查、迁移检查和 Compose 健康检查。

## 交接验证命令

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
docker compose exec -T api alembic current
python -m pytest -q
Push-Location apps/admin-web
npm run test -- --run
npm run build
Pop-Location
```

管理端入口：<http://127.0.0.1:5175/imports>

