# 数据迁移、诊断与管理观测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 SQLite 与 outbox 数据安全迁移到 PostgreSQL，并提供可复核的迁移报告、统一诊断和后台运行状态视图。

**Architecture:** 迁移器以只读方式盘点和备份来源，再通过显式适配器将 L0、会话、派生记忆、来源关系和审计写入 PostgreSQL。每条导入数据携带来源指纹并服从幂等约束。`doctor` 和 Admin API 读取聚合状态，不直接修改领域表。

**Tech Stack:** Python 3.10+、sqlite3 backup API、SQLAlchemy 2、Alembic、PostgreSQL、FastAPI、Vue 3、Vitest、Playwright、pytest、Docker Compose。

## Global Constraints

- 迁移前必须停止自动写入或进入维护模式。
- 源 SQLite 文件只读打开，迁移过程不得修改或删除源文件。
- 每个备份和迁移清单必须记录 SHA-256、schema 版本、表计数和时间。
- 无法确定项目归属的数据进入问题清单，不得写入默认项目。
- PostgreSQL 切换失败时，必须先导出切换后新增事件再执行回滚。
- Admin 状态接口只返回计数、时间、状态和摘要，不返回 Token 或默认返回消息全文。
- 所有迁移与管理操作产生审计记录。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `src/codex_memory/migration_inventory.py` | 识别 SQLite、PostgreSQL 和本地 outbox 来源 |
| `src/codex_memory/migration_backup.py` | 使用 SQLite backup API 创建只读备份与清单 |
| `src/codex_memory/migration_import.py` | 分实体导入、来源指纹和幂等处理 |
| `src/codex_memory/migration_verify.py` | 计数、关系、抽样和检索校验 |
| `src/codex_memory/operations_service.py` | 为 doctor 和 Admin API 聚合运行状态 |
| `src/codex_memory/cli.py` | `inventory`、`backup`、`migrate`、`verify-migration` 命令 |
| `src/codex_memory/admin/api.py` | 只读系统状态、归档状态、积压和死信 API |
| `apps/admin-web/src/views/SystemStatusView.vue` | 服务与归档状态页面 |
| `apps/admin-web/src/api.js` | 状态 API 客户端 |
| `apps/admin-web/src/router.js` | 状态页面路由 |
| `alembic/versions/0012_global_http_operations.py` | 来源指纹、迁移批次和归档状态表 |
| `tests/test_migration_*.py` | 盘点、备份、导入、校验和回滚测试 |
| `tests/test_operations_api.py` | Admin 状态权限与脱敏契约 |
| `apps/admin-web/src/views/SystemStatusView.test.js` | 状态页面组件测试 |
| `apps/admin-web/e2e/system-status.spec.js` | 管理后台端到端验收 |

### Task 1: 迁移元数据与来源指纹 schema

**Files:**
- Modify: `src/codex_memory/db_models.py`
- Create: `alembic/versions/0012_global_http_operations.py`
- Create: `tests/test_migration_schema.py`

**Interfaces:**
- Consumes: Alembic revision `0011_v12_admin_scopes`。
- Produces: `MigrationBatchRow`、`MigrationIssueRow`、`ArchiveStatusRow` 和消息来源指纹索引。

- [ ] **Step 1: 写 schema 失败测试**

```python
def test_operations_schema_has_migration_and_archive_tables(engine) -> None:
    create_schema(engine)
    names = set(inspect(engine).get_table_names())
    assert {"migration_batches", "migration_issues", "archive_status"} <= names


def test_message_source_fingerprint_is_unique_per_project(engine) -> None:
    indexes = inspect(engine).get_indexes("messages")
    assert any(
        item["unique"] and item["column_names"] == ["project_id", "source_fingerprint"]
        for item in indexes
    )
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_schema.py -q`

Expected: FAIL，新表和字段不存在。

- [ ] **Step 3: 定义模型**

```python
class MigrationBatchRow(TimestampedRow, Base):
    __tablename__ = "migration_batches"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="inventory")
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class MigrationIssueRow(TimestampedRow, Base):
    __tablename__ = "migration_issues"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("migration_batches.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
```

`MessageRow` 增加可空 `source_fingerprint`；`ArchiveStatusRow` 以项目为唯一键，记录最近用户事件、最近助手事件、最近成功、最近失败和 pending/dead-letter 计数。

- [ ] **Step 4: 实现可升级和可降级迁移**

Alembic upgrade 增加三张表、字段和索引；downgrade 先删除索引和字段，再删除三张表。SQLite 使用 batch migration，PostgreSQL 使用普通 `ALTER TABLE`。

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_schema.py tests/test_v12_scope.py -q`

Expected: PASS。

- [ ] **Step 5: 提交迁移 schema**

```powershell
git add src/codex_memory/db_models.py alembic/versions/0012_global_http_operations.py tests/test_migration_schema.py
git commit -m "feat: add migration and archive status schema"
```

### Task 2: 来源盘点与安全备份

**Files:**
- Create: `src/codex_memory/migration_inventory.py`
- Create: `src/codex_memory/migration_backup.py`
- Create: `tests/test_migration_inventory.py`
- Create: `tests/test_migration_backup.py`

**Interfaces:**
- Consumes: SQLite 路径、outbox 根目录和 PostgreSQL session factory。
- Produces: `SourceManifest`、`inventory_source(path)`、`backup_sqlite(source, destination)`。

- [ ] **Step 1: 写盘点测试**

```python
def test_inventory_records_hash_schema_and_counts(legacy_db: Path) -> None:
    manifest = inventory_source(legacy_db)
    assert manifest.sha256 == sha256_file(legacy_db)
    assert manifest.tables["raw_logs"] == 2
    assert manifest.schema_family == "legacy-layered"
    assert str(legacy_db) not in manifest.public_dict()["source_path_hash"]


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unknown.db"
    sqlite3.connect(path).execute("CREATE TABLE other(id INTEGER)").connection.close()
    with pytest.raises(UnsupportedSourceError):
        inventory_source(path)
```

- [ ] **Step 2: 写 SQLite backup 一致性测试**

```python
def test_backup_uses_consistent_sqlite_snapshot(legacy_db: Path, tmp_path: Path) -> None:
    destination = tmp_path / "backup" / "memory.db"
    result = backup_sqlite(legacy_db, destination)
    assert destination.exists()
    assert result.sha256 == sha256_file(destination)
    assert result.source_sha256 == sha256_file(legacy_db)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_inventory.py tests/test_migration_backup.py -q`

Expected: FAIL，盘点与备份模块不存在。

- [ ] **Step 4: 实现只读盘点和 backup API**

SQLite 使用 URI `file:<path>?mode=ro` 打开来源；通过 `sqlite_master` 和 `PRAGMA table_info` 区分 `legacy-layered` 与 `v1-relational`。备份使用：

```python
with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as source_db:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)
```

清单只保存路径 Hash，不在 Admin API 返回绝对路径。

- [ ] **Step 5: 运行测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_inventory.py tests/test_migration_backup.py -q`

Expected: PASS。

```powershell
git add src/codex_memory/migration_inventory.py src/codex_memory/migration_backup.py tests/test_migration_inventory.py tests/test_migration_backup.py
git commit -m "feat: inventory and back up legacy memory stores"
```

### Task 3: 分实体幂等导入

**Files:**
- Create: `src/codex_memory/migration_import.py`
- Create: `tests/test_migration_import.py`

**Interfaces:**
- Consumes: `SourceManifest`、只读 SQLite、目标 session factory 和显式项目映射。
- Produces: `MigrationImporter.import_batch() -> ImportReport`。

- [ ] **Step 1: 写 L0 与会话导入测试**

```python
def test_imports_raw_logs_with_stable_fingerprint(legacy_db, target_factory, project) -> None:
    report = MigrationImporter(target_factory).import_batch(
        source=legacy_db,
        project_map={"legacy-project": "erp"},
    )
    assert report.messages.created == 2
    assert report.sessions.created == 1

    repeated = MigrationImporter(target_factory).import_batch(
        source=legacy_db,
        project_map={"legacy-project": "erp"},
    )
    assert repeated.messages.created == 0
    assert repeated.messages.duplicates == 2
```

- [ ] **Step 2: 写派生数据和问题清单测试**

```python
def test_imports_memories_versions_sources_and_audits(legacy_db, target_factory, project) -> None:
    report = MigrationImporter(target_factory).import_batch(legacy_db, {"legacy-project": "erp"})
    assert report.memories.created > 0
    assert report.memory_versions.created > 0
    assert report.memory_sources.created > 0
    assert report.audit_events.created > 0


def test_unmapped_project_creates_issue_without_writing(legacy_db, target_factory) -> None:
    report = MigrationImporter(target_factory).import_batch(legacy_db, {})
    assert report.issues.by_code["unmapped_project"] > 0
    assert report.messages.created == 0
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_import.py -q`

Expected: FAIL，导入器不存在。

- [ ] **Step 4: 实现来源指纹和导入顺序**

来源指纹固定为：

```python
def source_fingerprint(source_sha256: str, table: str, source_id: str) -> str:
    value = f"{source_sha256}:{table}:{source_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()
```

导入顺序固定为：项目校验、会话、消息、Memory、MemoryVersion、MemorySource、Candidate、Job、Outbox、Audit。每个实体使用独立 savepoint；单条失败产生 `MigrationIssueRow`，不回滚无关成功记录。

- [ ] **Step 5: 实现角色和层级映射**

角色只接受 `user`、`assistant`、`system`；未知角色进入问题清单。旧 `L0` 进入 `messages`，旧 `L1/L2/L3` 保留层级和状态；无法证明来源关系的派生记忆进入 `candidate` 状态并标记 `metadata.migration_review_required=true`。

- [ ] **Step 6: 运行导入测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_import.py tests/test_v1_schema.py tests/test_v11_schema.py -q`

Expected: PASS。

```powershell
git add src/codex_memory/migration_import.py tests/test_migration_import.py
git commit -m "feat: import legacy memory data idempotently"
```

### Task 4: 迁移校验、CLI 与回滚清单

**Files:**
- Create: `src/codex_memory/migration_verify.py`
- Modify: `src/codex_memory/cli.py`
- Create: `tests/test_migration_verify.py`
- Create: `docs/v1.2/data-unification-runbook.md`

**Interfaces:**
- Consumes: 来源清单、目标数据库和迁移批次 ID。
- Produces: `verify_migration() -> VerificationReport` 和四个 CLI 命令。

- [ ] **Step 1: 写校验报告测试**

```python
def test_verification_reports_counts_relations_and_samples(imported_fixture) -> None:
    report = verify_migration(imported_fixture.source, imported_fixture.factory, imported_fixture.batch_id)
    assert report.counts_match is True
    assert report.broken_foreign_keys == 0
    assert report.duplicate_fingerprints == 0
    assert report.sample_mismatches == []
    assert report.ready_to_cutover is True
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_verify.py -q`

Expected: FAIL，校验器不存在。

- [ ] **Step 3: 实现校验器**

校验器必须检查：来源与目标实体计数、项目映射、会话消息关系、MemorySource、唯一来源指纹、20 条确定性抽样内容 Hash、每项目一次 `context` 检索和迁移问题数量。任何未解决的 `error` 级问题使 `ready_to_cutover=false`。

- [ ] **Step 4: 增加 CLI 命令**

```text
codex-memory inventory --source <sqlite> --json
codex-memory backup --source <sqlite> --destination <directory>
codex-memory migrate --source <backup> --project-map <json> --dry-run
codex-memory migrate --source <backup> --project-map <json> --apply
codex-memory verify-migration --batch-id <id> --json
```

`--apply` 必须要求已经存在完整备份清单；没有 `--apply` 时不得写入目标数据库。

- [ ] **Step 5: 编写切换和回滚手册**

手册包括维护模式、备份、dry-run、apply、校验、Compose 切换、观察期、回滚前增量导出和旧 SQLite 只读归档命令。

- [ ] **Step 6: 运行测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_migration_inventory.py tests/test_migration_backup.py tests/test_migration_import.py tests/test_migration_verify.py -q`

Expected: PASS。

```powershell
git add src/codex_memory/migration_verify.py src/codex_memory/cli.py tests/test_migration_verify.py docs/v1.2/data-unification-runbook.md
git commit -m "feat: verify and operate memory data migration"
```

### Task 5: 统一运行状态服务和 Admin API

**Files:**
- Create: `src/codex_memory/operations_service.py`
- Modify: `src/codex_memory/admin/api.py`
- Create: `tests/test_operations_api.py`

**Interfaces:**
- Consumes: PostgreSQL session factory 和 `archive_status`、Job、Outbox、Migration 表。
- Produces: `GET /api/admin/v1/system/status` 和 `GET /api/admin/v1/projects/{project_key}/archive-status`。

- [ ] **Step 1: 写权限与脱敏测试**

```python
def test_system_status_requires_admin(client, viewer_token) -> None:
    response = client.get("/api/admin/v1/system/status", headers=_auth(viewer_token))
    assert response.status_code == 403


def test_archive_status_is_project_scoped(client, erp_token) -> None:
    response = client.get("/api/admin/v1/projects/other/archive-status", headers=_auth(erp_token))
    assert response.status_code == 403


def test_status_response_does_not_expose_secrets(client, admin_token) -> None:
    payload = client.get("/api/admin/v1/system/status", headers=_auth(admin_token)).json()
    text = json.dumps(payload)
    assert "CODEX_MEMORY_MCP_TOKEN" not in text
    assert "postgresql+psycopg" not in text
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_operations_api.py -q`

Expected: FAIL，路由不存在。

- [ ] **Step 3: 实现聚合服务**

`OperationsService.system_status()` 返回数据库 dialect、schema revision、MCP 最近探测、Worker 最近心跳、pending Job、server outbox、迁移批次和死信计数。`project_archive_status(project_key)` 返回最近用户/助手归档时间、最近失败摘要、pending 和 dead-letter 计数。

- [ ] **Step 4: 添加只读路由和审计**

系统状态要求 `admin`；项目归档状态要求 `project:read` 且通过 project grant。每次访问记录 `admin_audit_events`，响应沿用 `data`、`meta`、`request_id` 契约。

- [ ] **Step 5: 运行 Admin 回归并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_operations_api.py tests/test_admin_p0_contract.py tests/test_admin_p0_security.py -q`

Expected: PASS。

```powershell
git add src/codex_memory/operations_service.py src/codex_memory/admin/api.py tests/test_operations_api.py
git commit -m "feat: expose memory operations status"
```

### Task 6: Admin Web 系统状态页面

**Files:**
- Modify: `apps/admin-web/src/api.js`
- Modify: `apps/admin-web/src/router.js`
- Create: `apps/admin-web/src/views/SystemStatusView.vue`
- Create: `apps/admin-web/src/views/SystemStatusView.test.js`
- Create: `apps/admin-web/e2e/system-status.spec.js`

**Interfaces:**
- Consumes: Admin 系统状态与项目归档状态 API。
- Produces: `/system-status` 管理页面。

- [ ] **Step 1: 写组件失败测试**

```javascript
it('显示服务、归档和迁移状态', async () => {
  vi.spyOn(api, 'getSystemStatus').mockResolvedValue({
    database: 'ok', mcp: 'ok', worker: 'ok', pending_jobs: 2,
    server_outbox: 1, dead_letters: 0, latest_migration: 'completed'
  })
  const wrapper = mount(SystemStatusView)
  await flushPromises()
  expect(wrapper.text()).toContain('运行状态')
  expect(wrapper.text()).toContain('待处理任务')
  expect(wrapper.text()).toContain('2')
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npm test --prefix apps/admin-web -- SystemStatusView.test.js`

Expected: FAIL，组件不存在。

- [ ] **Step 3: 实现 API 客户端和路由**

```javascript
export const getSystemStatus = () => request('/api/admin/v1/system/status')
export const getProjectArchiveStatus = projectKey =>
  request(`/api/admin/v1/projects/${encodeURIComponent(projectKey)}/archive-status`)
```

路由 meta 要求管理员权限；侧边导航使用现有图标库中的 `Activity` 图标和中文标签“运行状态”。

- [ ] **Step 4: 实现状态页面**

页面使用紧凑表格和状态标记展示 API、MCP、数据库、Worker、待处理任务、服务端 outbox、死信和最近迁移。错误状态提供“刷新”图标按钮，不在页面内显示 Token、连接串或消息全文。

- [ ] **Step 5: 增加 Playwright 测试并运行前端回归**

Playwright 登录后访问 `/system-status`，断言状态区域可见、无横向溢出、401 会回到登录页、Viewer 访问时显示 403 页面。

Run: `npm test --prefix apps/admin-web`

Expected: PASS。

Run: `npm run build --prefix apps/admin-web`

Expected: 构建成功。

- [ ] **Step 6: 提交状态页面**

```powershell
git add apps/admin-web/src/api.js apps/admin-web/src/router.js apps/admin-web/src/views/SystemStatusView.vue apps/admin-web/src/views/SystemStatusView.test.js apps/admin-web/e2e/system-status.spec.js
git commit -m "feat: add memory operations status page"
```

### Task 7: 扩展 doctor 与最终端到端验收

**Files:**
- Modify: `src/codex_memory/doctor.py`
- Modify: `tests/test_doctor.py`
- Create: `docs/v1.2/global-http-final-acceptance.md`

**Interfaces:**
- Consumes: 全部服务、全局 Codex 配置、项目门禁、outbox 和迁移批次。
- Produces: 最终验收报告和发布门禁。

- [ ] **Step 1: 扩展 doctor 测试**

```python
def test_doctor_reports_all_global_http_components(fake_probes, enabled_project) -> None:
    report = run_doctor(enabled_project, env={"CODEX_MEMORY_MCP_TOKEN": "set"}, probes=fake_probes)
    assert report["services"] == {
        "postgres": "ok", "api": "ok", "mcp": "ok", "worker": "ok", "admin_web": "ok"
    }
    assert report["outbox"]["pending"] == 0
    assert report["migration"]["ready"] is True
    assert report["overall"] == "ok"
```

- [ ] **Step 2: 实现完整诊断聚合**

doctor 增加 Compose 服务、Alembic revision、pgvector、MCP 认证、API Token、最近归档、pending/dead-letter、最近迁移和 Admin Web 探测。`--json` 不返回敏感值；文本模式使用中文建议。

- [ ] **Step 3: 运行全部自动测试**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: 全部 PASS。

Run: `npm test --prefix apps/admin-web`

Expected: 全部 PASS。

Run: `npm run build --prefix apps/admin-web`

Expected: 构建成功。

- [ ] **Step 4: 执行最终部署验收**

Run: `docker compose up -d --build`

Run: `codex-memory doctor --cwd "G:\Codex Project\20260703-codex-memory-system" --json`

Expected: `overall` 为 `ok`，五个服务均为 `ok`，pending 和 dead-letter 为 0，迁移状态为 ready/completed。

- [ ] **Step 5: 执行真实 Codex 任务验收**

在已启用项目创建新任务，提出需要历史信息的问题并完成一轮回答。验证 Admin Web 的记录页面同时出现用户和助手消息；在未启用临时项目执行一轮任务，验证无新记录。

- [ ] **Step 6: 记录证据并提交**

验收文档记录版本、命令、结果计数、已知限制和回滚点，不记录真实 Token 或消息正文。

```powershell
git add src/codex_memory/doctor.py tests/test_doctor.py docs/v1.2/global-http-final-acceptance.md
git commit -m "test: complete global HTTP memory acceptance"
```
