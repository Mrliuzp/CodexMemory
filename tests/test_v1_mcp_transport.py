from __future__ import annotations

import json

from fastapi.testclient import TestClient


class _HealthApiClient:
    def get(self, path: str) -> dict[str, str]:
        assert path == "/api/v1/health"
        return {"status": "ok"}


def test_v1_mcp_factory_configures_streamable_http_endpoint() -> None:
    from codex_memory.mcp_server import create_v1_server

    server = create_v1_server(object(), host="0.0.0.0", port=8001)

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 8001


def test_v1_mcp_streamable_http_requires_bearer_token_for_health() -> None:
    from codex_memory.mcp_auth import StaticTokenVerifier
    from codex_memory.mcp_server import create_v1_server

    token = "task5-mcp-test-token"
    server = create_v1_server(
        _HealthApiClient(),
        port=8001,
        token_verifier=StaticTokenVerifier(token),
    )
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "health", "arguments": {}},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }

    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8001") as client:
        assert client.post("/mcp", headers=headers, json=request).status_code == 401
        assert (
            client.post(
                "/mcp",
                headers={**headers, "Authorization": "Bearer wrong-token"},
                json=request,
            ).status_code
            == 401
        )

        response = client.post(
            "/mcp",
            headers={**headers, "Authorization": f"Bearer {token}"},
            json=request,
        )

    assert response.status_code == 200
