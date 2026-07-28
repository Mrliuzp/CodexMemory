# CodexMemory 版本路线与 V1.4 可信任务执行报告蓝图

更新时间：2026-07-29

本文是 CodexMemory 后续版本的唯一实施蓝图。README 与交接文档只链接到本文，不复制或另行解释本版本规格；实现、测试、PR 描述和验收均以本文为准。

## 版本路线冻结

| 版本 | 目标 | 状态 |
| --- | --- | --- |
| V1.4 | 可信任务执行报告：可审计的 Hook 事件、Git 变更清单、确定性报告和只读管理界面 | 本次实施 |
| V1.5 | Contract Registry | 未启动，明确不属于 V1.4 |
| V1.6 | 通用 Evidence Platform 与 Provenance Graph | 未启动，明确不属于 V1.4 |
| V1.7 | 多 Agent 精确归因、Legal Hold 与 Deployment Binding | 未启动，明确不属于 V1.4 |
| V1.8 | AST 反向提取、知识图谱与多 Provider | 未启动，明确不属于 V1.4 |

## V1.4 目标与边界

V1.4 将一次 Codex session 建模为一个 `TaskRun`，保存可重放的执行事件、Git 基线与变更清单，并由 Worker 生成完全确定性的 checkpoint/final 报告。报告是项目级 L1 `task_report` 投影，但不产生 L2/L3，也不调用 LLM。

本版本不实现 Contract Registry、LLM 报告、通用 Evidence Platform、Provenance Graph、多 Agent 精确归因、Legal Hold、Deployment Binding、AST 反向提取、知识图谱、多 Provider、自动发布或 Artifact Store。报告不提供重生成、纠错、重新归因、删除等写操作。

## 持久化与迁移

新增 Alembic 修订 `0022_v14_task_execution_reports`，以前置合并修订 `0022_merge_heads` 为 `down_revision`；其发布编号为 V1.4 的 0022。迁移必须支持全新库升级、既有 `0021_v131_memory_scope` 路径升级至 V1.4，以及 V1.4 回退。迁移增加下列表，所有实体均通过内部 `project_id` 隔离，并设置必要的外键、唯一约束和索引。

| 表 | 核心职责 | 幂等/隔离要求 |
| --- | --- | --- |
| `task_runs` | 一个 session 的运行、状态、Git 基线、结束时间和当前报告版本 | `(project_id, session_key)` 唯一 |
| `task_events` | 顺序化 Hook 事件、脱敏后的载荷摘要、内容 hash | `(task_run_id, event_key)` 唯一；同 key 同 hash 返回既有记录，不同 hash 冲突 |
| `task_reports` | checkpoint/final 的确定性报告及 revision | `(task_run_id, revision)` 唯一；同一运行按版本追加 |
| `task_file_changes` | 每份报告关联的确定性 ChangeManifest 文件变化 | 由报告 revision 与路径/变更序号唯一定位 |

`task_events` 的写入与服务端 `outbox_events` 必须在同一事务中；失败时两者均不得落库。项目过滤必须在服务端完成，禁止仅依赖调用方过滤。

## 事件 API 与 Worker

新增 `POST /api/v1/task-events`。它复用 append 的 Bearer 鉴权和 `append` 权限，以及项目访问检查；请求包含 `project_key`、session 标识、`event_key`、事件类型和受限元数据。同一 key 与同一规范化 hash 必须幂等；同 key 不同内容返回 HTTP 409。单个事件最大 64 KiB，命令摘要最大 4 KiB，结果摘要最大 8 KiB；截断后的内容必须保留原始长度和 SHA-256，且敏感信息先脱敏后落库。

事件写入成功后，事务内创建可重试的 Outbox 事件。Worker 消费后：

1. 首个 `PreToolUse` 固化 Git 基线。
2. `Stop` 生成 checkpoint 报告与对应 L1 投影。
3. `SessionEnd` 生成 final 报告并关闭运行。
4. 没有 `SessionEnd` 的运行仍可检索 checkpoint，不能被隐藏或伪装成 final。

Worker 重试不得重复创建同一报告 revision 或 L1 投影。报告内容按固定输入、固定排序和固定章节生成，时间仅以事件中已记录的数据呈现，不得调用 LLM 或依赖环境的不稳定输出。

## Hook、采集与 Git 变更清单

Hook 只处理结构化 Hook payload，禁止读取或解析 transcript。`UserPromptSubmit` 创建或复用 `TaskRun`；首次 `PreToolUse` 采集 Git 基线；`PostToolUse` 记录脱敏命令/结果摘要和退出码；`Stop` 写入 checkpoint；`SessionEnd` 写入 final 并关闭运行。Hook 必须在 API 超时、API 失败、本地 outbox 故障和文件锁故障时 fail-open：不得阻止 Codex 主流程，能够入队时复用现有本地 JSONL outbox，不能入队时仅返回可观测错误。

本地状态与锁只能位于用户本地 Codex 目录，严禁写入目标仓库。Git 基线只保存 branch、HEAD、`git status --porcelain`、diff hash 与 untracked 元数据。`Stop` 和 `SessionEnd` 都生成确定性 `ChangeManifest`。若基线本身脏，报告必须标记归因 `uncertain`，不得把既有变更归因于当前运行。清单必须涵盖干净库、脏基线、新增、修改、删除、重命名、untracked 与还原情形；非 Git 目录必须以受限的“不可用”状态完成事件处理。

脱敏采用双重凭证防线：Hook 采集前脱敏，服务端在持久化前再次脱敏。不得持久化原始密钥、令牌、密码或 Authorization 值。

## 确定性报告与 L1 投影

报告固定包含：运行标识与状态、事件时间线、工具执行摘要、Git 基线、ChangeManifest、归因与不确定性、脱敏/截断说明、限制与完整性状态。checkpoint 与 final 必须显式不同；同一个 `TaskRun` 的每次生成形成递增 `revision`。报告与其 L1 投影都使用 `memory_type=task_report`、`level=L1`、项目级 Scope，内容保留报告 ID、运行 ID、revision、状态和可读正文。不得将其提升到 L2 或 L3。

## 只读管理 API 与 Vue 页面

新增只读管理接口：

- `GET /api/admin/v1/task-runs`
- `GET /api/admin/v1/task-runs/{id}`
- `GET /api/admin/v1/task-runs/{id}/reports/{revision}`

它们必须使用现有管理鉴权与项目隔离，列表支持安全的项目筛选和分页。管理端新增“任务报告”只读入口，提供列表、详情、报告版本和变更清单展示，以及 checkpoint/final/不确定性/截断状态。界面文案、注释和文档必须为简体中文；不得提供任何写操作入口。

## 验收矩阵

完成条件至少包括：

1. Hook 五事件单元测试；API 超时、本地 JSONL outbox 与文件锁故障 fail-open 测试。
2. Git 干净/脏基线、新增/修改/删除/重命名/untracked/还原/非 Git 的集成测试。
3. API 幂等、409、权限、项目隔离、事务原子性、Worker 重试、报告 revision 与 L1 投影测试。
4. Vue 测试与生产构建，后端全量测试均以实际 exit code 0 为准；不得以放宽断言掩盖失败。
5. Alembic 全新库升级、0021 至 V1.4、V1.4 回退验证；`git diff --check`；UTF-8/中文约束检查；Compose 健康检查。

任何失败、跳过或未执行项必须在 PR 中如实说明；只有上述必要验收全部通过，才可将 Draft PR 标记为 Ready。
