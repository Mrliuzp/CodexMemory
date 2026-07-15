# 可靠归档验收记录

- 验收日期：2026-07-15
- 验收范围：全局 Hook、项目门禁、本地 outbox、HTTP API、MCP 和 PostgreSQL 最终写入边界。
- 配置边界：本次使用工作树中未提交的随机 `.env`。本文不记录 Token、密码、会话密钥或消息正文。

## 自动化回归

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_codex_install_contract.py tests\test_hooks.py tests\test_hook_client.py tests\test_local_outbox.py tests\test_v1_service.py tests\test_v1_http_api.py -q
```

结果：35 项通过。

## 全局接入

- 用户级 `codex-memory` MCP 已注册为 `http://127.0.0.1:8001/mcp`，使用 `CODEX_MEMORY_MCP_TOKEN`。
- 全局 Skill 与 Hook 已安装。
- 当前项目由 `AGENTS.md` 的三个 `CODEX_MEMORY_*` 参数启用。
- `codex-memory doctor --cwd . --json` 返回 `overall=ok`。

## 服务验收

- Compose 的 PostgreSQL、API、MCP、Worker 和后台页面均处于运行状态。
- `http://127.0.0.1:5174/api/v1/health` 返回 HTTP 200。
- MCP 无 Bearer Token 返回 HTTP 401；携带正确 Token 且同时接受 JSON 与 SSE 的 `initialize` 请求返回 HTTP 200。

## 中断与重放

1. 停止 API 后，通过全局 `hook-user` 与 `hook-assistant` 写入同一轮对话。
2. 项目隔离 outbox 中出现 2 条 pending 记录。
3. 恢复 API 后，首次 `replay-outbox --all` 返回 `delivered=2`、`remaining=0`、`dead_lettered=0`。
4. 再次重放返回 `delivered=0`、`remaining=0`、`dead_lettered=0`。
5. PostgreSQL 中该会话的项目事件键记录数为 2。
6. 在没有启用参数的临时项目触发 Hook，不产生 pending 记录。

## 结论

可靠归档链路已通过离线、重放、幂等和项目隔离验收。生产部署仍需使用受管的非占位 `.env` 凭据替换本地验收配置，并在变更前备份 PostgreSQL 数据卷。
