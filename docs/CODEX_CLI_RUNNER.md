# Codex CLI 执行器契约

`codex_memory.codex_cli_runner.CodexCliRunner` 是后台 Worker 的模型判断边界；当前集成通过 `CodexCliDecisionAdapter` 接入 Worker。Runner 仍不接触数据库、Outbox 或任何自动发布流程，候选发布资格由服务器策略门最终决定。

## 调用接口

上游先构造 `CodexCliRequest`：

- `project_key`：明确的单项目标识，不能使用 `*`；
- `task`：要判断的任务说明；
- `context`：已经裁剪好的同项目 JSON 对象；如果带 `context.project_key`，必须与请求项目一致；
- `output_schema`：自包含 JSON Schema，只允许本地 `$ref`；
- `request_id`：可选的外部关联标识。

调用 `CodexCliRunner.run(request)` 后，只会得到 `CodexCliResult.data` 和非敏感运行元数据。结果已经通过 Schema 校验，调用方不得把 `data` 当作命令、SQL、路径或模板再次执行。执行器没有数据库写入能力。

所有失败都抛出 `CodexCliError` 子类，可使用 `error.code` 和 `error.as_dict()` 记录：

- `codex_cli_disabled`：默认状态，未启用时不会启动进程；
- `codex_cli_unavailable` / `codex_cli_authentication_unavailable`：CLI 或认证不可用；
- `codex_cli_timeout`、`codex_cli_output_limit`、`codex_cli_process_error`：进程生命周期或退出失败；
- `codex_cli_invalid_schema`、`codex_cli_output_not_json`、`codex_cli_output_schema_mismatch`：输入 Schema 或最终输出不合格；
- `codex_cli_context_budget_exceeded`、`codex_cli_daily_budget_exceeded`、`codex_cli_concurrency_limit`：本地资源边界拒绝。

Worker 应把这些失败作为任务结果记录，并根据 `retryable` 决定是否重试；不能把失败转换成空的“成功”结果。

## 隔离边界

每次运行都在新的临时目录内写入 Schema 和最终输出文件，结束后清理目录；工作目录不指向项目源码。调用参数是固定的数组，`task` 和 `context` 只通过 stdin 的 UTF-8 JSON 传递，不拼接 shell 字符串。

CLI 固定使用：

```text
codex exec --ephemeral --ignore-user-config --ignore-rules
  --sandbox read-only --ask-for-approval never --skip-git-repo-check
  --color never --output-schema <临时 Schema> --output-last-message <临时输出> -
```

不传入 `--add-dir`、`--yolo`、`--full-auto` 或远程连接参数。子进程使用受限环境变量白名单，并把 `HOME`、`USERPROFILE`、`APPDATA`、`LOCALAPPDATA`、`CODEX_SQLITE_HOME` 和临时目录重定向到本次运行目录；工作目录、Schema、输出和 SQLite 状态永远是本次调用的临时目录，不挂载项目仓库。只有显式配置 `CODEX_MEMORY_CODEX_CLI_AUTH_ROOT` 时，Runner 才从该 Worker 专用根目录的 `.active-generation` 元数据指针选择 `CODEX_HOME`；该根目录必须由 Compose 以只读方式挂载给 Worker，Runner 不会回退到默认用户目录。默认不传递 API key、数据库连接串和 Docker 环境变量，也不会由 API 容器读取认证文件。底层进程 runner 以无 shell 方式启动，超时或输出超限时终止进程树。

解析时只选取最后一条通过 Schema 校验的完整 JSON；所有进程错误摘要都会先脱敏并截断。模型返回内容永远不会触发命令执行或数据库写入。

## 配置

配置类为 `CodexCliSettings`，默认 `enabled=False`。支持环境变量（前缀 `CODEX_MEMORY_CODEX_CLI_`，同时兼容不含中间 `CODEX` 的旧前缀）：

| 配置 | 环境变量 | 默认值 |
| --- | --- | --- |
| 是否启用 | `..._ENABLED` | `false` |
| CLI 路径 | `..._PATH` | `codex` |
| Worker 认证根目录 | `..._AUTH_ROOT` | 空（每次调用不可用认证） |
| Profile | `..._PROFILE` | 空 |
| Model | `..._MODEL` | 空 |
| 单次超时秒数 | `..._TIMEOUT_SECONDS` | `60` |
| 最大并发数 | `..._MAX_CONCURRENCY` | `1` |
| 上下文预算 token | `..._CONTEXT_BUDGET_TOKENS` | `4000` |
| 每日预算 token | `..._DAILY_BUDGET_TOKENS` | `20000` |
| 最大输出字节数 | `..._MAX_OUTPUT_BYTES` | `65536` |

每日预算是进程内 UTC 日账本，`0` 表示不启用本地限制。需要跨 Worker 或重启保持预算时，由后续持久化任务注入兼容的预算实现，不应在本模块直接访问数据库。

## Worker 集成契约

Worker 在同一项目授权和 `decision_engine_enabled` 检查之后，通过 `CodexCliDecisionAdapter` 构造最小 `CodexCliRequest`。adapter 先用 `ModelDecisionOutput` 严格校验并映射建议，Worker 再由 `DecisionService` 记录运行与决策，最后交给 `CandidatePolicyService` 执行项目级发布门禁。失败记录分类错误，不保存原始 prompt、完整 stdout/stderr 或凭据；Runner 不负责自动写入、发布或 provider 路由。

## 认证协调器

认证文件由仓库外的本机协调器维护，API 只接收有限状态：`ready`、`not_logged_in`、`login_in_progress`、`error`。协调器启动交互式 `codex login` 时使用新 generation；启动更换账号前先原子写入 `disabled`，登录成功后再原子替换 `.active-generation`，取消或失败保持禁用。Worker 只读挂载认证根目录，协调器是唯一写入方。

管理后台的入口是“运行监控 → 系统状态”页面。管理员可看到有限登录状态，并使用“开始登录/更换账号”“取消”“重新检查”；页面不显示账号、token、认证文件内容或认证 URL。后端接口为：

- `GET /api/admin/v1/codex-auth/status`（`operations_read`）；
- `POST /api/admin/v1/codex-auth/start`、`/cancel`、`/recheck`（`admin`）。

认证协调器的启动、专用目录和 `CODEX_MEMORY_CODEX_AUTH_COORDINATOR_TOKEN` 只通过本机受控环境提供，不写入仓库、`.env`、数据库或日志。详见 [`docs/CODEX_AUTH_COORDINATOR.md`](CODEX_AUTH_COORDINATOR.md)。候选、决策、自动发布和真实模型调用仍须单独灰度授权。
