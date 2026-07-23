# V1.2 迁移策略

迁移 `0011_v12_admin_scopes` 采用增量方式创建 `knowledge_scopes`，增加唯一约束 `(project_id, scope_key)`，并通过外键关联 `projects`。每个已有项目都会获得一个 `default` 作用域投影。已有项目级记录保留原字段，不做重写。

该迁移在运行层面具备幂等性：已完成迁移的数据库再次升级时不会创建重复的默认作用域。降级只删除新增表。PostgreSQL 和 SQLite 均受支持；由于部署环境可能没有全局启用 SQLite 外键，SQLite 会额外创建显式触发器来保护外键。