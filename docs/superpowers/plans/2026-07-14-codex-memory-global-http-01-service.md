# 全局 HTTP MCP 与 PostgreSQL 生产基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付只绑定本机、Bearer Token 认证、只使用 PostgreSQL 的 Codex Memory HTTP API 与 Streamable HTTP MCP。

**Architecture:** 保留现有 `api` 和 `mcp` 进程边界。`mcp` 是无状态适配器，通过内部服务 Token 调用 `api`；外部 Codex Token 由 MCP SDK 的 `TokenVerifier` 校验。所有持久化和领域规则只发生在 FastAPI 与 PostgreSQL 中。

**Tech Stack:** Python 3.10+、FastAPI、MCP Python SDK 1.28+、SQLAlchemy 2、Alembic、PostgreSQL 16、pgvector、Docker Compose、pytest、httpx。

## Global Constraints

- 生产环境 `CODEX_MEMORY_DEPLOYMENT_MODE=production` 时只接受 `postgresql+psycopg://` 数据库 URL。
- 对外地址固定为 `127.0.0.1:8001/mcp`；API 和管理后台端口同样只绑定 `127.0.0.1`。
- MCP 外部 Token 与 API 内部服务 Token 分离，二者均不得使用占位符。
- 所有健康接口不得返回 Token、密码或完整数据库 URL。
- 保持 `/api/v1/*` 和 `/api/admin/v1/*` 现有契约兼容。
- 每个任务先写失败测试，再写最小实现，并创建独立提交。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `src/codex_memory/config.py` | 解析部署模式、数据库和服务地址，执行生产配置校验 |
| `src/codex_memory/runtime_health.py` | 生成 live/ready 状态，不泄露敏感配置 |
| `src/codex_memory/mcp_auth.py` | 实现 MCP SDK `TokenVerifier` |
| `src/codex_memory/mcp_server.py` | 暴露 `append_message`、检索、上下文和健康工具 |
| `src/codex_memory/v1_mcp.py` | 组装受认证的 Streamable HTTP MCP 进程 |
| `src/codex_memory/http_api.py` | 暴露 `/health/live`、`/health/ready` 和 V1 API |
| `docker-compose.yml` | 常驻服务、回环地址绑定、健康检查和重启策略 |
| `.env.example` | 无真实密钥的部署变量契约 |
| `tests/test_runtime_config.py` | 生产数据库和占位符校验 |
| `tests/test_v1_mcp_auth.py` | MCP Token 成功和拒绝场景 |
| `tests/test_v1_mcp_server.py` | MCP 工具到 V1 API 的映射 |
| `tests/test_v1_health.py` | live/ready 契约 |
| `tests/test_compose_contract.py` | Compose 网络、端口和重启契约 |

### Task 1: 生产配置门禁与健康检查

**Files:**
- Modify: `src/codex_memory/config.py`
- Create: `src/codex_memory/runtime_health.py`
- Modify: `src/codex_memory/http_api.py`
- Modify: `src/codex_memory/v1_app.py`
- Create: `tests/test_runtime_config.py`
- Modify: `tests/test_v1_health.py`

**Interfaces:**
- Consumes: `Settings.from_env()` 和 SQLAlchemy `sessionmaker`。
- Produces: `Settings.validate_runtime() -> None`、`build_readiness(session_factory) -> dict[str, str]`、`GET /health/live`、`GET /health/ready`。

- [ ] **Step 1: 写生产数据库门禁失败测试**

```python
def test_production_rejects_sqlite() -> None:
    from codex_memory.config import Settings

    settings = Settings(database_url="sqlite:///memory-v1.db", deployment_mode="production")

    with pytest.raises(ValueError, match="生产环境必须使用 PostgreSQL"):
        settings.validate_runtime()


def test_development_allows_sqlite() -> None:
    from codex_memory.config import Settings

    Settings(database_url="sqlite:///memory-v1.db", deployment_mode="development").validate_runtime()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runtime_config.py -q`

Expected: FAIL，提示 `Settings` 没有 `deployment_mode` 或 `validate_runtime`。

- [ ] **Step 3: 实现配置门禁**

在 `Settings` 中增加字段和校验：

```python
@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///memory-v1.db"
    embedding_dimension: int = 1536
    deployment_mode: str = "development"

    def validate_runtime(self) -> None:
        if self.deployment_mode not in {"development", "test", "production"}:
            raise ValueError("CODEX_MEMORY_DEPLOYMENT_MODE 必须是 development、test 或 production")
        if self.deployment_mode == "production" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("生产环境必须使用 PostgreSQL")
```

`from_env()` 同时读取 `CODEX_MEMORY_DEPLOYMENT_MODE`。`v1_app.py` 在创建 Engine 前调用 `settings.validate_runtime()`。

- [ ] **Step 4: 写 live/ready 契约测试**

```python
def test_liveness_does_not_probe_database() -> None:
    client = TestClient(create_v1_app(_factory()))
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_reports_database_and_schema() -> None:
    response = TestClient(create_v1_app(_factory())).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    assert response.json()["schema"] in {"ok", "development"}
    assert "database_url" not in response.json()
```

- [ ] **Step 5: 实现健康检查并运行测试**

`runtime_health.py` 提供纯函数：

```python
def build_readiness(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
        dialect = session.get_bind().dialect.name
        schema = "ok" if inspect(session.get_bind()).has_table("projects") else "missing"
        vector = "not-applicable"
        if dialect == "postgresql":
            vector = "ok" if session.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'" )).first() else "missing"
        return {"status": "ok" if schema == "ok" else "degraded", "database": "ok", "schema": schema, "vector": vector}
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runtime_config.py tests/test_v1_health.py -q`

Expected: PASS。

- [ ] **Step 6: 提交配置与健康检查**

```powershell
git add src/codex_memory/config.py src/codex_memory/runtime_health.py src/codex_memory/http_api.py src/codex_memory/v1_app.py tests/test_runtime_config.py tests/test_v1_health.py
git commit -m "feat: enforce production runtime configuration"
```

### Task 2: MCP Bearer Token 认证

**Files:**
- Create: `src/codex_memory/mcp_auth.py`
- Modify: `src/codex_memory/mcp_server.py`
- Modify: `src/codex_memory/v1_mcp.py`
- Create: `tests/test_v1_mcp_auth.py`

**Interfaces:**
- Consumes: `mcp.server.auth.provider.TokenVerifier`、`AccessToken` 和环境变量 `CODEX_MEMORY_MCP_TOKEN`。
- Produces: `StaticTokenVerifier.verify_token(token) -> AccessToken | None` 和受保护的 `/mcp`。

- [ ] **Step 1: 写 TokenVerifier 单元测试**

```python
@pytest.mark.asyncio
async def test_static_token_verifier_accepts_exact_token() -> None:
    verifier = StaticTokenVerifier("mcp-secret")
    access = await verifier.verify_token("mcp-secret")
    assert access is not None
    assert access.client_id == "codex-memory-client"
    assert access.scopes == ["memory:read", "memory:append"]


@pytest.mark.asyncio
async def test_static_token_verifier_rejects_wrong_token() -> None:
    assert await StaticTokenVerifier("mcp-secret").verify_token("wrong") is None
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_v1_mcp_auth.py -q`

Expected: FAIL，无法导入 `StaticTokenVerifier`。

- [ ] **Step 3: 实现恒定时间 Token 校验**

```python
class StaticTokenVerifier:
    def __init__(self, expected_token: str) -> None:
        if not expected_token or expected_token.startswith("change-me"):
            raise ValueError("CODEX_MEMORY_MCP_TOKEN 必须使用非占位符值")
        self.expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.expected_token):
            return None
        return AccessToken(
            token="verified",
            client_id="codex-memory-client",
            scopes=["memory:read", "memory:append"],
            subject="codex-user",
        )
```

- [ ] **Step 4: 将校验器注入 FastMCP**

调整工厂签名：

```python
def create_v1_server(
    api_client: Any,
    host: str = "127.0.0.1",
    port: int = 8001,
    token_verifier: TokenVerifier | None = None,
) -> FastMCP:
    return FastMCP(
        "Codex Memory V1 MCP",
        host=host,
        port=port,
        stateless_http=True,
        token_verifier=token_verifier,
    )
```

`v1_mcp.main()` 必须读取 `CODEX_MEMORY_MCP_TOKEN` 并构造 `StaticTokenVerifier`。默认 host 改为 `127.0.0.1`。

- [ ] **Step 5: 运行认证和原 MCP 测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_v1_mcp_auth.py tests/test_v1_mcp_server.py tests/test_v1_mcp_transport.py -q`

Expected: PASS。

- [ ] **Step 6: 提交 MCP 认证**

```powershell
git add src/codex_memory/mcp_auth.py src/codex_memory/mcp_server.py src/codex_memory/v1_mcp.py tests/test_v1_mcp_auth.py
git commit -m "feat: protect HTTP MCP with bearer authentication"
```

### Task 3: 补齐 MCP 对话归档工具

**Files:**
- Modify: `src/codex_memory/mcp_server.py`
- Modify: `tests/test_v1_mcp_server.py`
- Modify: `tests/test_v1_http_api.py`

**Interfaces:**
- Consumes: `MemoryApiClient.post(path, payload)` 和 `/api/v1/append`。
- Produces: MCP 工具 `append_message(project, session, event, role, content, occurred_at=None, source="skill", metadata=None)`。

- [ ] **Step 1: 写工具映射失败测试**

```python
def test_append_message_calls_v1_append_endpoint() -> None:
    client = FakeApiClient()
    tool = _tool(create_v1_server(client), "append_message")

    result = tool(
        project="erp",
        session="s1",
        event="codex:erp:s1:t1:user",
        role="user",
        content="修改订单",
    )

    assert result["path"] == "/api/v1/append"
    assert result["payload"]["project_key"] == "erp"
    assert result["payload"]["event_key"] == "codex:erp:s1:t1:user"
    assert result["payload"]["source"] == "skill"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_v1_mcp_server.py::test_append_message_calls_v1_append_endpoint -q`

Expected: FAIL，工具 `append_message` 不存在。

- [ ] **Step 3: 实现最小 MCP 工具**

```python
@server.tool()
def append_message(
    project: str,
    session: str,
    event: str,
    role: Literal["user", "assistant", "system"],
    content: str,
    occurred_at: str | None = None,
    source: str = "skill",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return api_client.post(
        "/api/v1/append",
        {
            "project_key": project,
            "session_key": session,
            "event_key": event,
            "role": role,
            "content": content,
            "occurred_at": occurred_at,
            "source": source,
            "metadata": metadata or {},
        },
    )
```

- [ ] **Step 4: 运行 MCP 与 append 幂等测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_v1_mcp_server.py tests/test_v1_http_api.py -q`

Expected: PASS，并保持同事件同内容返回 `duplicate`、同事件不同内容返回 409。

- [ ] **Step 5: 提交归档工具**

```powershell
git add src/codex_memory/mcp_server.py tests/test_v1_mcp_server.py tests/test_v1_http_api.py
git commit -m "feat: expose conversation append over MCP"
```

### Task 4: 加固 Compose 常驻服务

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `apps/admin-web/nginx.conf`
- Modify: `tests/test_compose_contract.py`
- Create: `docs/v1.2/global-http-deployment.md`

**Interfaces:**
- Consumes: `postgres`、`api`、`mcp`、`worker`、`admin-web` 镜像。
- Produces: 本机端口 `8001/mcp`、`5174` 管理后台和容器健康状态。

- [ ] **Step 1: 写 Compose 安全契约测试**

```python
def test_compose_binds_public_ports_to_loopback() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8001:8001"' in compose
    assert '"127.0.0.1:5174:80"' in compose
    assert '"8000:8000"' not in compose


def test_compose_uses_restart_and_production_mode() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("restart: unless-stopped") >= 5
    assert "CODEX_MEMORY_DEPLOYMENT_MODE: production" in compose
    assert "CODEX_MEMORY_MCP_TOKEN" in compose
```

- [ ] **Step 2: 运行契约测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compose_contract.py -q`

Expected: FAIL，当前端口未绑定回环地址且没有重启策略。

- [ ] **Step 3: 修改 Compose**

实施以下精确约束：

```yaml
api:
  restart: unless-stopped
  environment:
    CODEX_MEMORY_DEPLOYMENT_MODE: production
  expose:
    - "8000"

mcp:
  restart: unless-stopped
  environment:
    CODEX_MEMORY_MCP_HOST: 0.0.0.0
    CODEX_MEMORY_MCP_PORT: 8001
    CODEX_MEMORY_MCP_TOKEN: ${CODEX_MEMORY_MCP_TOKEN}
  ports:
    - "127.0.0.1:8001:8001"

admin-web:
  restart: unless-stopped
  ports:
    - "127.0.0.1:5174:80"
```

`postgres`、`worker` 同样设置 `restart: unless-stopped`；`.env.example` 使用 `change-me-*` 占位符且不包含真实值。Nginx 继续通过 Compose 内部网络访问 `api:8000`。

- [ ] **Step 4: 验证 Compose 配置与契约**

Run: `docker compose config`

Expected: Exit 0，输出五个服务且无未解析的必需变量。

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compose_contract.py -q`

Expected: PASS。

- [ ] **Step 5: 编写中文部署文档**

文档必须给出：生成三个独立密钥、填写 `.env`、启动、健康检查、查看日志、停止、备份卷和 Token 轮换命令。示例仅使用 `change-me-*`。

- [ ] **Step 6: 提交 Compose 加固**

```powershell
git add docker-compose.yml .env.example apps/admin-web/nginx.conf tests/test_compose_contract.py docs/v1.2/global-http-deployment.md
git commit -m "ops: harden global HTTP service deployment"
```

### Task 5: 第一阶段集成门禁

**Files:**
- Modify: `tests/test_v1_mcp_transport.py`
- Modify: `docs/v1.2/global-http-deployment.md`

**Interfaces:**
- Consumes: 本计划所有提交。
- Produces: 第一阶段验收证据。

- [ ] **Step 1: 增加真实 HTTP MCP 认证冒烟测试**

测试启动临时 MCP ASGI 应用或子进程，并验证：无 Authorization 返回 401；错误 Token 返回 401；正确 Token 可执行 `health`。测试 Token 固定为测试值，不读取开发者真实环境变量。

- [ ] **Step 2: 运行后端回归**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: 全部 PASS。

- [ ] **Step 3: 构建并启动 Compose**

Run: `docker compose up -d --build`

Expected: 镜像构建成功。

Run: `docker compose ps`

Expected: `postgres`、`api`、`mcp`、`worker`、`admin-web` 均为 running；具备健康检查的服务为 healthy。

- [ ] **Step 4: 执行端口与健康验证**

Run: `Invoke-RestMethod http://127.0.0.1:8001/mcp -Method Post -ContentType 'application/json' -Body '{}' -SkipHttpErrorCheck`

Expected: HTTP 401。

Run: `Invoke-RestMethod http://127.0.0.1:5174/api/v1/health`

Expected: `status` 为 `ok`，`database` 为 `ok`，`vector` 为 `ok`。

- [ ] **Step 5: 记录验收并提交**

将实际命令、版本和结果摘要写入部署文档的“验收记录”章节。

```powershell
git add tests/test_v1_mcp_transport.py docs/v1.2/global-http-deployment.md
git commit -m "test: verify global HTTP service baseline"
```
