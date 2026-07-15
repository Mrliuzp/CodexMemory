# Codex Memory 数据统一操作手册

本文档规定历史 SQLite 知识库导入 Codex Memory 的验证流程。来源数据库始终只读；任何 `apply` 操作必须使用由 `backup` 生成且哈希匹配的备份清单。

## 当前能力边界

`migrate --apply` 支持两类目标：

- `--db`：本地 SQLite 验证目标。命令会创建验证所需表，不得用于生产。
- `--database-url`：既有目标数据库。该路径不会创建表；当目标为 PostgreSQL 时，会在写入前检查 Alembic 版本和 pgvector 就绪状态。

历史 L0 对话、会话和来源指纹已可导入并幂等校验。旧记忆、版本关系、维护模式、切换后的增量事件导出以及正式切换编排仍未完成；因此不得仅凭本手册直接完成生产流量切换。

## 准备条件

- 暂停来源项目的新归档写入，或记录冻结时间点。
- 为每个旧项目准备稳定的目标 `project_id` 映射；目标项目必须已在目标库注册。
- PostgreSQL 目标必须先由部署流程执行 Alembic 升级，并确认 pgvector 可用。
- 保留原始 SQLite 文件，不重命名、不覆盖、不删除。
- 在隔离目录中保存清单、备份、命令输出和验收记录；不得记录 Token 或消息正文。

## 盘点与备份

```powershell
$source = 'D:\archive\memory.db'
$backup = 'D:\migration\memory-20260715.db'

codex-memory inventory --source $source --json
codex-memory backup --source $source --destination $backup
```

`backup` 会输出 `manifest` 路径，并在备份文件旁写入 `<backup>.manifest.json`。清单包含来源和备份的 SHA-256；后续导入会校验备份内容没有变化。

## 预演和本地导入验证

```powershell
$target = 'D:\migration\target-verify.db'
$mapping = '{"old-project":"20260703-codex-memory-system"}'
$manifest = 'D:\migration\memory-20260715.db.manifest.json'

codex-memory --db $target migrate --source $backup --project-map $mapping --dry-run
codex-memory --db $target migrate --source $backup --project-map $mapping --backup-manifest $manifest --apply
codex-memory --db $target verify-migration --source $backup --batch-id <batch_id>
```

## PostgreSQL 导入演练

生产目标必须已完成 schema 升级，且目标项目已经注册。将数据库 URL 仅保存在受控环境变量中，不要写入命令记录或验收文档。

```powershell
$targetUrl = $env:CODEX_MEMORY_DATABASE_URL

codex-memory --database-url $targetUrl migrate --source $backup --project-map $mapping --dry-run
codex-memory --database-url $targetUrl migrate --source $backup --project-map $mapping --backup-manifest $manifest --apply
codex-memory --database-url $targetUrl verify-migration --source $backup --batch-id <batch_id>
```

当 `--database-url` 指向 PostgreSQL 时，命令不会自动建表；schema 或 pgvector 未就绪会在导入前被拒绝。仅当验证结果中的 `ready_to_cutover` 为 `true` 时，才允许进入下一阶段验收。以下任一情况必须停止：

- 备份清单缺失、格式不正确或哈希不匹配。
- 项目映射不存在，或映射的目标项目尚未注册。
- `error` 级迁移问题、重复来源指纹，或来源与目标消息数不一致。

重新执行相同 `apply` 是幂等的：已导入的 L0 消息由来源指纹去重。不要为了重试而修改来源 SQLite 或删除目标数据。

## 生产切换前的阻断项

完成导入不等于完成正式切换。维护模式、旧记忆及版本关系的导入、切换后增量事件导出和自动回滚编排尚未实现前，生产流量切换被明确阻断。正式流程至少需要：

1. 停止 API、MCP、Worker 与 Hook 写入，记录冻结时间点。
2. 对来源 SQLite 生成只读备份和清单，对 PostgreSQL 生成可恢复备份。
3. 先执行 dry-run，再执行 PostgreSQL 导入和完整校验。
4. 将 API、MCP、Worker 和 Admin Web 指向统一 PostgreSQL 后进入观察期。
5. 观察期出现问题时，先导出切换后增量事件，再恢复旧部署配置和 PostgreSQL 备份；旧 SQLite 仅保留为只读归档。

## 验收记录

每次演练记录：执行版本、来源与备份 SHA-256、项目映射摘要、批次 ID、计数结果、验证结果、执行人、时间和已知限制。记录中不得包含真实 Token、数据库连接串或对话正文。