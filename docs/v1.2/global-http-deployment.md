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
