# 历史代码退役审计

审计日期：2026-08-02

## 审计范围

本次审计覆盖正式运行入口、管理后台、CLI、历史数据迁移工具和测试。判断依据是 Compose 启动链路、Python 包入口、模块导入关系、前端构建入口和现有测试引用。

## 已退役内容

- 删除根目录 `admin/` 原型后台。正式管理后台仅保留 `apps/admin-web/` 与 `/api/admin/v1`。
- 删除已经脱离 CLI 和运行入口的 SQLite 迁移工具：`migration_backup.py`、`migration_import.py`、`migration_inventory.py`、`migration_verify.py`。
- 删除未被正式入口引用且数据模型已经过期的 `operations_service.py`、`runtime_health.py` 和 `maintenance.py`。
- 删除仅验证上述退役实现、旧 SQLite 存储或已经移除接口的测试。
- 从 CLI 参数解析中移除没有实现路径的历史命令，避免帮助信息继续暴露不可执行能力。
- 删除仍以现行文档口吻描述旧 SQLite 服务和已移除 CLI 的 `SPEC_COVERAGE.md`、`REQUIREMENTS_AUDIT.md` 与 V1.2 数据统一操作手册。

## 明确保留内容

- 保留全部 Alembic 迁移历史。迁移版本是数据库升级链的一部分，不能按普通旧代码删除。
- 保留 `src/codex_memory/admin/`，这是正式 `/api/admin/v1` 路由实现，不是已删除的根目录原型后台。
- 保留 `pipelines/`、`persistence/` 和 `entrypoints/` 中仍由 API、Worker、MCP、Hook 或测试引用的 V1.1、V1.3、V1.4、V1.5 实现。
- 保留顶层 `codex_memory/*.py` 兼容转发层。其用途和边界见 `docs/PROJECT_STRUCTURE.md`。
- 保留 `docs/superpowers/` 中带日期的历史计划，作为决策记录；这些文档不代表当前可执行接口。

## 数据安全边界

本次清理不删除数据库表、历史迁移记录或业务数据。即使某些表当前不再由正式入口写入，也必须另行完成数据保留评估、备份和迁移设计后才能退役。

## 当前正式链路

- 管理后台：`apps/admin-web/`
- 管理 API：`/api/admin/v1`
- 服务入口：`codex_memory.v1_app:app`
- 异步处理：`codex_memory.worker`
- 数据库升级：`alembic upgrade head`

## 审计期间修复

- 修复一条仍调用旧 SQLite `_wal_factory` 的并发测试，使其继续验证正式 PostgreSQL Outbox 并发安全。
- 修复 Worker 对 PostgreSQL 带时区租约时间与旧无时区时间的比较，避免心跳、完成或失败处理抛出时间类型异常。
- 恢复 MCP 的静态 Bearer Token 验证，未配置或使用占位 Token 时启动失败关闭，匿名请求返回 `401`。
- 修复 MCP 启动测试对兼容转发模块的错误 mock，避免测试误启动真实服务并长期等待。
- 删除仍断言已退役 `run_worker_iteration()` 的旧 Worker 入口测试；当前异步运行由 `WorkerRuntime` 测试覆盖。

## 验收要求

- 前端运行 `npm test` 与 `npm run build`。
- 后端运行完整 `pytest`。
- 执行 Alembic 全路径升级测试和 Compose 健康检查。
- 扫描 `?????`、替换字符、退役模块引用和无效 CLI 命令。
- 执行 `git diff --check`。

2026-08-02 实际验收结果：前端 28 个单元测试通过，生产构建通过；后端 236 个测试通过、1 个按环境条件跳过；Compose 服务重建健康；默认 Scope 数据已修复为“默认 Scope”；MCP 匿名请求返回 `401`。
