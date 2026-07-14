from __future__ import annotations

import json
import socket
import threading
import time

import anyio
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class _HealthApiClient:
    def get(self, path: str) -> dict[str, str]:
        assert path == "/api/v1/health"
        return {"status": "ok"}


def test_v1_mcp_factory_configures_streamable_http_endpoint() -> None:
    from codex_memory.mcp_server import create_v1_server

    server = create_v1_server(object(), host="0.0.0.0", port=8001)

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 8001


async def _call_health(url: str, token: str) -> dict[str, str]:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}
    ) as http_client, streamable_http_client(
        url, http_client=http_client
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("health", {})

    assert result.isError is False
    text_items = [item.text for item in result.content if hasattr(item, "text")]
    assert text_items
    return json.loads(text_items[0])


def test_v1_mcp_streamable_http_requires_bearer_token_for_health() -> None:
    from codex_memory.mcp_auth import StaticTokenVerifier
    from codex_memory.mcp_server import create_v1_server

    token = "task5-mcp-test-token"
    app = create_v1_server(
        _HealthApiClient(),
        token_verifier=StaticTokenVerifier(token),
    ).streamable_http_app()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", lifespan="on")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert server.started
    url = f"http://127.0.0.1:{port}/mcp"
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=5) as client:
            assert client.post(url, headers=headers, json={}).status_code == 401
            assert (
                client.post(
                    url,
                    headers={**headers, "Authorization": "Bearer wrong-token"},
                    json={},
                ).status_code
                == 401
            )

        payload = anyio.run(_call_health, url, token)
        assert payload["status"] == "ok"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()

    assert not thread.is_alive()
