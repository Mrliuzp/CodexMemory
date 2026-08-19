# Worker Codex 认证协调器

本组件只负责让管理员在浏览器完成 Codex CLI 登录，并把一个 Worker 专用认证目录以只读方式提供给实际 CLI。它不读取、打印或向 API 返回账号、token、认证 URL 或认证文件内容。

## 目录与切换边界

选择仓库外的专用根目录，例如 Windows 用户目录下的 `AppData\\Local\\CodexMemory\\worker-codex-home-<date>`。目录由本机协调器读写，由 Worker 以只读 bind mount 使用。根目录中的 `.active-generation` 只保存 `root`、`disabled` 或受限的 `generation-...` 名称，不保存凭据。

切换顺序固定为：

1. 协调器原子写入 `disabled`，旧账号立即不能被 Runner 选择；
2. 创建新的 generation 并启动交互式 `codex login`；
3. 用户在浏览器完成认证；
4. 进程成功退出后原子替换 `.active-generation`；
5. 取消或失败保持 `disabled`，页面只显示有限状态。

Runner 仍在每次判断时创建临时工作目录，使用 `--sandbox read-only`、`--ask-for-approval never`、`--ignore-rules` 和 `--skip-git-repo-check`，不挂载项目仓库；认证根目录是唯一持久只读输入，SQLite/临时文件仍在调用临时目录。

## 启动本机协调器

协调器使用仓库中的 Python 模块启动。以下变量必须由本机受控进程环境提供，不要写入仓库、`.env` 或日志：

- `CODEX_MEMORY_CODEX_AUTH_COORDINATOR_TOKEN`：API 与协调器之间的本地控制 token；
- `CODEX_MEMORY_CODEX_CLI_AUTH_DIR`：同一个仓库外专用认证根目录；
- `CODEX_MEMORY_CODEX_AUTH_COORDINATOR_URL`：Compose API 访问协调器的地址，通常为 `http://host.docker.internal:1456`。

在专用目录已经存在且用户已完成登录时，先运行一次不输出内容的 `codex login status`；协调器本身也只通过退出码判断状态。启动命令示意：

```powershell
$env:CODEX_MEMORY_CODEX_AUTH_COORDINATOR_TOKEN = '<由受控环境注入，不要记录>'
python -m codex_memory.codex_auth `
  --auth-root '<仓库外专用目录>' `
  --bind 0.0.0.0 `
  --port 1456
```

使用认证挂载时，Compose 必须额外加载 `docker-compose.codex-auth.yml`，并在同一受控部署环境中提供 `CODEX_MEMORY_CODEX_CLI_AUTH_DIR`。该 override 只给 Worker 安装 CLI，并把认证根目录 `read_only: true` 挂载；API 只获得协调器 URL/token，不获得认证目录挂载。

Worker 镜像使用官方 `https://chatgpt.com/codex/install.sh`。构建网络若需要代理，只在构建命令环境中提供 `CODEX_BUILD_HTTP_PROXY`、`CODEX_BUILD_HTTPS_PROXY`、`CODEX_BUILD_NO_PROXY`；override 将它们映射为 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` build args。当前 Windows 主机代理必须使用 Docker 可达的 `host.docker.internal` 地址，不能直接使用构建器内部不可达的 `127.0.0.1`。这些 args 不进入 Worker runtime ENV，安装步骤具有进度、重试、超时，并要求官方脚本包含 SHA-256 校验路径。

## 管理后台

登录管理员后打开“运行监控 → 系统状态”。“Codex CLI 登录状态”卡片只显示：已登录、未登录、登录进行中或错误。管理员可点击“开始登录/更换账号”“取消”“重新检查”；只读用户只能看到状态。按钮调用 Admin API，API 再调用协调器，不读认证文件。

在候选/决策/自动发布仍关闭时，登录状态变为 `ready` 不会启动任何模型判断；启用 runner、shadow 或自动发布必须另行完成安全评审和项目级灰度。
