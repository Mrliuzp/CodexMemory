# 可靠 Hook、outbox 与幂等归档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将用户和助手最终消息通过项目门禁、服务端幂等和可重放 outbox 可靠归档，且服务中断或重复提交不会丢失或重复。

**Architecture:** 全局 Hook 从标准输入读取 Codex 生命周期事件，使用共享项目解析器确定是否启用，再调用统一 HTTP API。可重试失败写入用户级项目隔离 outbox；重放器按顺序提交并将不可重试事件移入死信。服务端 `(project_id, event_key)` 唯一约束是最终幂等边界。

**Tech Stack:** Python 3.10+、urllib/httpx、JSONL、Windows 文件锁、FastAPI、SQLAlchemy、pytest。

## Global Constraints

- 只归档 `user` 消息和已经发送给用户的最终 `assistant` 消息。
- 不归档隐藏推理、工具中间输出、流式片段或空助手消息。
- 事件键固定为 `codex:{project_id}:{session_id}:{turn_id}:{role}`。
- Token 不得写入 outbox、死信、日志或异常正文。
- 连接错误、超时、429 和 5xx 可重试；400、401、403、404 和 409 不自动无限重试。
- 同事件同内容返回原记录；同事件不同内容返回 409 并生成审计。
- 未启用项目静默跳过；声明错误或已声明但无权限必须产生中文诊断。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `src/codex_memory/hook_events.py` | 校验 Codex Hook 输入并生成稳定事件键 |
| `src/codex_memory/hook_client.py` | 调用 append/context 并分类 HTTP 错误 |
| `src/codex_memory/local_outbox.py` | 锁定、追加、重放、退避和死信 |
| `src/codex_memory/codex_hooks.py` | 编排项目门禁、归档和上下文输出 |
| `src/codex_memory/cli.py` | `hook-user`、`hook-assistant`、`replay-outbox` 命令 |
| `.codex/hooks.json` | 删除项目重复 Hook，改由全局 Hook 负责 |
| `.codex/scripts/*` | 完成迁移后删除旧脚本，避免双写 |
| `tests/test_hook_events.py` | 事件校验和事件键契约 |
| `tests/test_hook_client.py` | HTTP 成功与错误分类 |
| `tests/test_local_outbox.py` | 文件锁、重放、死信和敏感信息契约 |
| `tests/test_hooks.py` | 端到端 Hook 编排行为 |

### Task 1: 标准化 Hook 事件与稳定事件键

**Files:**
- Create: `src/codex_memory/hook_events.py`
- Create: `tests/test_hook_events.py`

**Interfaces:**
- Consumes: Codex `UserPromptSubmit` 或 `Stop` 的 JSON 对象。
- Produces: `HookMessage` 和 `parse_user_event()`、`parse_assistant_event()`。

- [ ] **Step 1: 写事件模型失败测试**

```python
def test_user_event_builds_project_scoped_key() -> None:
    event = parse_user_event(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "prompt": "修改订单"},
        project_id="erp",
    )
    assert event.event_key == "codex:erp:s1:t1:user"
    assert event.role == "user"
    assert event.content == "修改订单"


def test_assistant_event_ignores_empty_message() -> None:
    assert parse_assistant_event(
        {"cwd": "G:/erp", "session_id": "s1", "turn_id": "t1", "last_assistant_message": ""},
        project_id="erp",
    ) is None
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hook_events.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现事件模型**

```python
@dataclass(frozen=True)
class HookMessage:
    project_id: str
    session_id: str
    turn_id: str
    role: Literal["user", "assistant"]
    content: str
    cwd: str

    @property
    def event_key(self) -> str:
        return f"codex:{self.project_id}:{self.session_id}:{self.turn_id}:{self.role}"

    def to_append_payload(self) -> dict[str, Any]:
        return {
            "project_key": self.project_id,
            "session_key": self.session_id,
            "event_key": self.event_key,
            "role": self.role,
            "content": self.content,
            "source": "hook",
            "metadata": {"turn_id": self.turn_id},
        }
```

缺少 `cwd`、`session_id`、`turn_id` 或用户 `prompt` 时抛出 `HookEventError`；助手消息为空时返回 `None`。

- [ ] **Step 4: 运行完整事件测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hook_events.py -q`

Expected: PASS，覆盖缺失字段、非字符串字段和中文内容。

- [ ] **Step 5: 提交事件标准化**

```powershell
git add src/codex_memory/hook_events.py tests/test_hook_events.py
git commit -m "feat: normalize Codex hook events"
```

### Task 2: HTTP 客户端与错误分类

**Files:**
- Create: `src/codex_memory/hook_client.py`
- Create: `tests/test_hook_client.py`

**Interfaces:**
- Consumes: API URL、Bearer Token 和 append/context payload。
- Produces: `HookApiClient.append()`、`HookApiClient.context()`、`RetryableHookError`、`PermanentHookError`。

- [ ] **Step 1: 写错误分类测试**

```python
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_statuses(status: int) -> None:
    with pytest.raises(RetryableHookError):
        _client(MockTransport(lambda request: Response(status))).append(_payload())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_permanent_statuses(status: int) -> None:
    with pytest.raises(PermanentHookError) as caught:
        _client(MockTransport(lambda request: Response(status, json={"error": "rejected"}))).append(_payload())
    assert "secret-token" not in str(caught.value)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hook_client.py -q`

Expected: FAIL，客户端不存在。

- [ ] **Step 3: 实现客户端**

```python
class HookApiClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=3.0,
            transport=transport,
        )

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/v1/append", payload)

    def context(self, project_id: str, task: str) -> dict[str, Any]:
        return self._post("/api/v1/context", {"project_key": project_id, "task": task})

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.post(path, json=payload)
        except (httpx.ConnectError, httpx.TimeoutException) as error:
            raise RetryableHookError("Codex Memory 服务暂时不可用") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableHookError(f"Codex Memory 暂时失败：HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PermanentHookError(f"Codex Memory 拒绝请求：HTTP {response.status_code}")
        return response.json()
```

- [ ] **Step 4: 运行客户端测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hook_client.py -q`

Expected: PASS，覆盖成功、重复、连接错误、超时和敏感值不泄漏。

- [ ] **Step 5: 提交 Hook HTTP 客户端**

```powershell
git add src/codex_memory/hook_client.py tests/test_hook_client.py
git commit -m "feat: classify hook delivery failures"
```

### Task 3: 项目隔离 outbox 与死信

**Files:**
- Create: `src/codex_memory/local_outbox.py`
- Create: `tests/test_local_outbox.py`

**Interfaces:**
- Consumes: `HookMessage.to_append_payload()` 和 `HookApiClient.append()`。
- Produces: `LocalOutbox.enqueue()`、`LocalOutbox.replay()`、`ReplayReport`。

- [ ] **Step 1: 写 outbox 失败测试**

```python
def test_enqueue_never_persists_token(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)
    outbox.enqueue("erp", _payload(), reason="offline")
    text = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "secret-token" not in text
    assert '"project_id": "erp"' in text


def test_replay_keeps_retryable_and_dead_letters_permanent(tmp_path: Path) -> None:
    outbox = LocalOutbox(tmp_path)
    outbox.enqueue("erp", {**_payload(), "event_key": "retry"}, reason="offline")
    outbox.enqueue("erp", {**_payload(), "event_key": "denied"}, reason="offline")

    def send(payload: dict) -> dict:
        if payload["event_key"] == "retry":
            raise RetryableHookError("offline")
        raise PermanentHookError("HTTP 403")

    report = outbox.replay(send)
    assert report.remaining == 1
    assert report.dead_lettered == 1
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_local_outbox.py -q`

Expected: FAIL，`LocalOutbox` 不存在。

- [ ] **Step 3: 实现文件格式和原子操作**

每行使用以下结构：

```python
record = {
    "schema": 1,
    "project_id": project_id,
    "event_key": payload["event_key"],
    "payload": payload,
    "queued_at": datetime.now(timezone.utc).isoformat(),
    "attempts": 0,
    "next_attempt_at": None,
    "last_error": reason,
}
```

文件路径为 `<root>/<project_id>/pending.jsonl` 和 `<root>/<project_id>/dead-letter.jsonl`。Windows 使用 `msvcrt.locking`；重写使用同目录临时文件和 `Path.replace()`。

- [ ] **Step 4: 实现重放和退避**

退避函数固定为：

```python
def retry_delay_seconds(attempts: int) -> int:
    return min(3600, 2 ** min(attempts, 11))
```

成功和 API 返回 `duplicate` 时移除事件；可重试错误增加 `attempts` 和 `next_attempt_at`；永久错误写入死信并从 pending 移除。

- [ ] **Step 5: 运行并发与回归测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_local_outbox.py tests/test_outbox_concurrency.py -q`

Expected: PASS。

- [ ] **Step 6: 提交本地 outbox**

```powershell
git add src/codex_memory/local_outbox.py tests/test_local_outbox.py tests/test_outbox_concurrency.py
git commit -m "feat: add durable project-scoped hook outbox"
```

### Task 4: Hook 编排与 CLI

**Files:**
- Create: `src/codex_memory/codex_hooks.py`
- Modify: `src/codex_memory/cli.py`
- Rewrite: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `ProjectMemoryConfig`、`HookMessage`、`HookApiClient` 和 `LocalOutbox`。
- Produces: `handle_user_prompt(event, env) -> str`、`handle_assistant_stop(event, env) -> HookResult` 和三个 CLI 命令。

- [ ] **Step 1: 写项目门禁与离线编排测试**

```python
def test_disabled_project_does_not_call_api(tmp_path: Path) -> None:
    client = FakeClient()
    result = handle_user_prompt(_event(cwd=tmp_path), _env(), client=client)
    assert result == ""
    assert client.calls == []


def test_enabled_user_event_appends_then_returns_context(tmp_path: Path) -> None:
    _enable(tmp_path, "erp")
    client = FakeClient(context={"long_term_rules": [{"content": "使用领域服务"}]})
    result = handle_user_prompt(_event(cwd=tmp_path), _env(), client=client)
    assert client.calls[0][0] == "append"
    assert "使用领域服务" in result


def test_retryable_failure_is_queued(tmp_path: Path) -> None:
    _enable(tmp_path, "erp")
    client = FakeClient(append_error=RetryableHookError("offline"))
    handle_assistant_stop(_stop_event(cwd=tmp_path), _env(outbox=tmp_path / "outbox"), client=client)
    assert list((tmp_path / "outbox" / "erp").glob("pending.jsonl"))
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hooks.py -q`

Expected: FAIL，新编排器不存在。

- [ ] **Step 3: 实现编排器**

```python
def handle_user_prompt(event: dict[str, Any], env: Mapping[str, str], client: HookApiClient | None = None) -> str:
    config = load_project_memory_config(event["cwd"])
    if not config.enabled:
        return ""
    message = parse_user_event(event, config.project_id or "")
    api = client or HookApiClient(env["CODEX_MEMORY_API_URL"], env["CODEX_MEMORY_API_TOKEN"])
    outbox = LocalOutbox(Path(env["CODEX_MEMORY_OUTBOX_DIR"]))
    try:
        api.append(message.to_append_payload())
        context = api.context(message.project_id, message.content)
        return format_context(context)
    except RetryableHookError as error:
        outbox.enqueue(message.project_id, message.to_append_payload(), str(error))
        return ""
```

永久错误不能进入无限重试 pending；应立即写入死信并向 stderr 输出一行中文诊断。助手流程不请求上下文。

- [ ] **Step 4: 增加 CLI 子命令**

`hook-user` 和 `hook-assistant` 从 stdin 读取一个 JSON 对象；`hook-user` 将上下文写到 stdout，诊断写到 stderr。`replay-outbox` 支持 `--project` 和 `--all`，输出 JSON `ReplayReport`。

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_hooks.py tests/test_hook_events.py tests/test_hook_client.py tests/test_local_outbox.py -q`

Expected: PASS。

- [ ] **Step 5: 提交 Hook 编排**

```powershell
git add src/codex_memory/codex_hooks.py src/codex_memory/cli.py tests/test_hooks.py
git commit -m "feat: orchestrate project-gated conversation archive"
```

### Task 5: 清理项目级重复 Hook 并验证幂等

**Files:**
- Delete: `.codex/hooks.json`
- Delete: `.codex/scripts/append_user.py`
- Delete: `.codex/scripts/append_assistant.py`
- Delete: `.codex/scripts/hook_common.py`
- Modify: `tests/test_v1_service.py`
- Modify: `tests/test_v1_http_api.py`
- Create: `tests/test_archive_end_to_end.py`

**Interfaces:**
- Consumes: 全局 Hook 和 `/api/v1/append`。
- Produces: 无双写的项目配置与端到端幂等证据。

- [ ] **Step 1: 强化服务端内容冲突测试**

```python
def test_same_project_and_event_same_content_is_duplicate(service, principal) -> None:
    first = service.append_message_v11(principal, "erp", "s1", "e1", "user", "内容")
    second = service.append_message_v11(principal, "erp", "s1", "e1", "user", "内容")
    assert first.status == "accepted"
    assert second.status == "duplicate"


def test_same_project_and_event_different_content_conflicts(service, principal) -> None:
    service.append_message_v11(principal, "erp", "s1", "e1", "user", "原内容")
    with pytest.raises(AppendConflictError):
        service.append_message_v11(principal, "erp", "s1", "e1", "user", "不同内容")
```

- [ ] **Step 2: 写中断恢复端到端测试**

测试流程固定为：第一次 Hook 调用连接失败并产生一条 pending；第二次 `replay-outbox` 调用真实 TestClient API；再次重放不增加消息；数据库中该 `event_key` 恰好一条。

- [ ] **Step 3: 删除项目级旧 Hook**

删除四个旧文件，避免全局 Hook 和项目 Hook 同时运行。安装文档说明：全局 Hook 是唯一生命周期入口，`AGENTS.md` 是项目启用入口。

- [ ] **Step 4: 运行归档回归**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_v1_service.py tests/test_v1_http_api.py tests/test_archive_end_to_end.py tests/test_hooks.py -q`

Expected: PASS。

- [ ] **Step 5: 提交重复 Hook 清理**

```powershell
git add -A .codex tests/test_v1_service.py tests/test_v1_http_api.py tests/test_archive_end_to_end.py tests/test_hooks.py
git commit -m "refactor: make global hook the single archive entrypoint"
```

### Task 6: 可靠归档验收

**Files:**
- Create: `docs/v1.2/reliable-archive-acceptance.md`

**Interfaces:**
- Consumes: 本计划全部提交。
- Produces: 可重复的验收记录。

- [ ] **Step 1: 运行完整后端测试**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: 全部 PASS。

- [ ] **Step 2: 执行服务中断测试**

停止 `api`，在已启用项目触发一轮用户和助手消息；确认 outbox 有两条事件。启动 `api` 后运行：

Run: `codex-memory replay-outbox --all`

Expected: `delivered=2`、`remaining=0`、`dead_lettered=0`。

- [ ] **Step 3: 执行重复与隔离测试**

再次运行重放，预期数据库计数不变。切换到未启用临时项目发送消息，预期 outbox 和数据库均不新增。

- [ ] **Step 4: 记录验收并提交**

```powershell
git add docs/v1.2/reliable-archive-acceptance.md
git commit -m "docs: record reliable archive acceptance"
```
