# Codex Memory 全局接入

## 前置条件

先启动本项目的全局 HTTP 服务：

```powershell
docker compose up -d --build
```

服务就绪后，MCP 地址为 `http://127.0.0.1:8001/mcp`。

## 安装

```powershell
powershell -ExecutionPolicy Bypass -File scripts\set-codex-memory-token.ps1
powershell -ExecutionPolicy Bypass -File scripts\install-codex-memory.ps1
```

安装器会创建 `%USERPROFILE%\.codex\codex-memory-runtime`，先删除同名 `codex-memory` MCP，再以 `CODEX_MEMORY_MCP_TOKEN` 作为 Bearer Token 环境变量重新注册 HTTP MCP；同时安装 Skill 并合并本项目 Hook。Token 不会写入配置文件或命令行。

安装后重启 Codex，并验证注册信息：

```powershell
& "$env:APPDATA\npm\codex.cmd" mcp get codex-memory --json
```

## 项目启用

仅包含以下声明的项目会自动归档：

```text
CODEX_MEMORY_AUTO_LOG=required
CODEX_MEMORY_PROJECT_ID=my-project
CODEX_MEMORY_MCP_SERVER=codex-memory
```

`PROJECT_ID` 与 MCP 名称只能使用小写字母、数字、点、下划线和连字符，最长 64 个字符。缺少、冲突或格式错误时失败关闭，不会写入默认项目。

当前项目已在根目录 `AGENTS.md` 启用。停用时删除三项声明，或将 `CODEX_MEMORY_AUTO_LOG` 改为 `disabled`。

## 诊断

```powershell
codex-memory doctor --cwd "G:\Codex Project\20260703-codex-memory-system" --json
```

退出码：`0` 表示配置和 MCP 服务可用；`1` 表示可恢复警告，例如项目未启用；`2` 表示配置错误、Token 缺失或 MCP 服务不可用。

## 项目改名

项目目录改名不要求修改 `PROJECT_ID`。若要更换项目标识，先在管理后台创建并授权新的项目，再更新 `AGENTS.md`，最后验证 doctor；不要将新项目复用为旧项目标识。

## 卸载

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall-codex-memory.ps1
```

卸载器只删除 `codex-memory` MCP、安装的 Skill、运行环境和带 `codex-memory-runtime` 的 Hook 项，默认保留 Token。只有明确需要删除用户 Token 时才传入 `-RemoveToken`。

> Hook 的 `hook-user` 与 `hook-assistant` 运行时命令将在可靠归档阶段交付；完成该阶段前，请不要把安装结果视为自动归档已生效。