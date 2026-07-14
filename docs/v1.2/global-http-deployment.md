# 全局 HTTP 服务部署

本部署将 `postgres`、`api`、`mcp`、`worker` 和 `admin-web` 作为 Docker Compose 常驻服务运行。仅 MCP 与管理后台发布到本机：`http://127.0.0.1:8001/mcp` 和 `http://127.0.0.1:5174`。API 仅在 Compose 内部网络提供给 MCP 与管理后台。

## 准备密钥

在 PowerShell 中生成三个彼此独立的随机值，分别用于数据库密码、服务 Token 和 MCP Token。不要把输出写入终端记录、仓库、Skill 或 `AGENTS.md`。

```powershell
function New-RandomSecret {
  -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char] $_ })
}
$PostgresPassword = New-RandomSecret
$ServiceToken = New-RandomSecret
$McpToken = New-RandomSecret
```

管理员密码与会话密钥也必须使用不同的随机值。生产环境不得保留任何 `change-me-*` 占位符。

## 配置

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，将下列示例占位符替换为刚生成的值，并确保数据库 URL 中的密码与 `POSTGRES_PASSWORD` 相同：

```dotenv
POSTGRES_PASSWORD=change-me-postgres-password
CODEX_MEMORY_DATABASE_URL=postgresql+psycopg://codex:change-me-postgres-password@postgres:5432/codex_memory
CODEX_MEMORY_SERVICE_TOKEN=change-me-service-token
CODEX_MEMORY_MCP_TOKEN=change-me-mcp-token
CODEX_MEMORY_ADMIN_PASSWORD=change-me-admin-password
CODEX_MEMORY_ADMIN_SESSION_SECRET=change-me-admin-session-secret
```

不要提交 `.env`。MCP 客户端使用的 `CODEX_MEMORY_MCP_TOKEN` 与 API 的 `CODEX_MEMORY_SERVICE_TOKEN` 相互独立。

## 启动与检查

```powershell
docker compose up -d --build
docker compose ps
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=5).read().decode())"
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5).read().decode())"
```

浏览器访问 `http://127.0.0.1:5174`。MCP 地址固定为 `http://127.0.0.1:8001/mcp`，并以 `CODEX_MEMORY_MCP_TOKEN` 作为 Bearer Token。`/api/v1/health` 仍保留为兼容检查地址，但不对主机发布。

## 日志与停止

```powershell
docker compose logs --tail 200 api mcp worker admin-web
docker compose logs -f api mcp worker
docker compose stop
```

`docker compose stop` 会保留 `pgdata` 卷。不要使用 `docker compose down -v`，除非明确需要删除全部持久化数据。

## 备份

先建立备份目录，再从 `pgdata` 对应的 PostgreSQL 服务导出逻辑备份：

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
$BackupFile = "backups/codex-memory-$(Get-Date -Format 'yyyyMMdd-HHmmss').sql"
docker compose exec -T postgres pg_dump -U codex -d codex_memory | Out-File -Encoding utf8 $BackupFile
```

定期在独立环境校验备份可恢复，并保护备份文件的访问权限。

## MCP Token 轮换

停止 MCP 后生成新值，更新 `.env` 和用户环境变量，再仅重建 MCP 服务：

```powershell
docker compose stop mcp
$NewMcpToken = 'change-me-new-mcp-token'
# 将 .env 中的 CODEX_MEMORY_MCP_TOKEN 替换为 $NewMcpToken。
[Environment]::SetEnvironmentVariable('CODEX_MEMORY_MCP_TOKEN', $NewMcpToken, 'User')
docker compose up -d --force-recreate mcp
```

## 升级与回滚

升级前先完成备份，并确认 `.env` 中的数据库连接、服务 Token 和 MCP Token 已保留。拉取新版本后重建镜像并按依赖顺序启动：

```powershell
git pull --ff-only
docker compose pull
docker compose up -d --build
docker compose ps
```

API 容器启动时会执行 `alembic upgrade head`。迁移会修改持久化的 `pgdata` 数据卷；不要在没有可恢复备份的情况下跳过备份，也不要通过删除卷来解决迁移失败。升级后使用 `/health/live` 和 `/health/ready` 确认服务可用，再让 MCP 客户端恢复连接。

需要回滚时，先停止新版本服务，检出已验证的旧版本并重新构建。数据库迁移通常不能自动降级：只有在该版本提供并验证过对应的 downgrade 时才执行；否则应在隔离环境验证后，从升级前的逻辑备份恢复数据库。回滚期间保留 `.env` 和 `pgdata`，避免执行 `docker compose down -v`。

```powershell
docker compose stop
git checkout <已验证的版本>
docker compose up -d --build
```

## 故障排查

先查看服务状态、健康检查和最近日志；`live` 表示进程存活，`ready` 还会确认依赖已就绪。

```powershell
docker compose ps
docker compose logs --tail 200 postgres api mcp worker admin-web
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=5).read().decode())"
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5).read().decode())"
```

若 MCP 或管理后台无法启动，检查宿主机端口是否已被占用：

```powershell
Get-NetTCPConnection -LocalPort 8001,5174 -ErrorAction SilentlyContinue
```

停止占用进程或修改 Compose 的宿主机端口映射后重新执行 `docker compose up -d`。不要为排障给 API 添加 `ports`；API 应继续只通过 Compose 内部网络的 `api:8000` 提供服务。

若 `ready` 失败或 API/worker 日志出现数据库连接错误，确认 `postgres` 健康、`POSTGRES_PASSWORD` 与 `CODEX_MEMORY_DATABASE_URL` 中的密码一致，并验证 URL 的主机仍是 `postgres`、端口为 `5432`。修正 `.env` 后重建依赖该变量的容器：

```powershell
docker compose up -d --force-recreate api worker mcp
```

MCP 返回 `401` 时，通常是客户端未发送 Bearer Token、Token 已轮换，或使用了错误的 `CODEX_MEMORY_MCP_TOKEN`。MCP 返回 `403` 时，先确认客户端调用的是 `http://127.0.0.1:8001/mcp`，再检查 MCP 使用的 `CODEX_MEMORY_SERVICE_TOKEN` 能否代表已引导的服务身份。修改 Token 后同时更新 `.env`、客户端环境变量并重建 MCP；不要把 API 服务 Token 当作 MCP 客户端 Token 使用。

## 验收记录

**时间：** 2026-07-15

**版本：** Docker Engine 29.6.1；Docker Compose v5.2.0。

本次验收仅在当前 PowerShell 进程中设置数据库、服务和 MCP 的本地测试值；未写入 `.env`，也未记录 Token 或密码。MCP 自动化测试使用固定、非占位的本地测试 Token，本文不记录其值。

```powershell
$env:COMPOSE_PARALLEL_LIMIT = '1'
# 在当前进程中设置本地测试值（值未记录）。
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8001/mcp -Method Post -ContentType 'application/json' -Body '{}' -SkipHttpErrorCheck

@"
import anyio, httpx, json, os
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    headers = {"Authorization": f"Bearer {os.environ['CODEX_MEMORY_MCP_TOKEN']}"}
    async with httpx.AsyncClient(headers=headers) as client:
        async with streamable_http_client(
            "http://127.0.0.1:8001/mcp", http_client=client
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("health", {})
    assert not result.isError
    payload = json.loads(next(item.text for item in result.content if hasattr(item, "text")))
    print(payload["status"])

anyio.run(main)
"@ | ..venvScriptspython.exe -

Invoke-RestMethod http://127.0.0.1:5174/api/v1/health
.\.venv\Scripts\python.exe -m pytest tests/test_compose_contract.py tests/test_v1_mcp_transport.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

结果摘要：

- 采用 `COMPOSE_PARALLEL_LIMIT=1` 的 `docker compose up -d --build` 构建成功。Docker Hub 鉴权超时后，使用 AWS 官方公共 ECR 提供的相同镜像完成本地标记；未改变 Compose 配置。
- `admin-web`、`api`、`mcp`、`postgres` 和 `worker` 均为 Up，`api` 与 `postgres` 为 healthy。`admin-web` 仅发布 `127.0.0.1:5174`，`mcp` 仅发布 `127.0.0.1:8001`，API 没有宿主机端口。
- `POST http://127.0.0.1:8001/mcp` 未携带 Authorization 返回 HTTP 401。
- 使用 `$env:CODEX_MEMORY_MCP_TOKEN` 的官方 MCP 客户端成功完成 `initialize` 和 `health` 工具调用，返回 `status=ok`。
- `GET http://127.0.0.1:5174/api/v1/health` 返回 `status=ok`、`database=ok`、`vector=ok`。
- 焦点测试结果为 `11 passed`；全量结果为 `199 passed, 1 skipped`，并有 11 条既有弃用警告。
- 前端构建曾因本地 `node_modules` 进入构建上下文而失败；`apps/admin-web/.dockerignore` 现排除依赖、产物和本地缓存。
