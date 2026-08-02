import asyncio

import pytest

from codex_memory.mcp_auth import StaticTokenVerifier


def test_static_token_verifier_accepts_exact_token() -> None:
    verifier = StaticTokenVerifier("mcp-secret")

    access = asyncio.run(verifier.verify_token("mcp-secret"))

    assert access is not None
    assert access.client_id == "codex-memory-client"
    assert access.scopes == ["memory:read", "memory:append"]


def test_static_token_verifier_rejects_wrong_token() -> None:
    assert asyncio.run(StaticTokenVerifier("mcp-secret").verify_token("wrong")) is None


@pytest.mark.parametrize("token", ["", "change-me", "change-me-now"])
def test_static_token_verifier_rejects_placeholder_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="CODEX_MEMORY_MCP_TOKEN"):
        StaticTokenVerifier(token)


def test_v1_mcp_factory_configures_bearer_authentication() -> None:
    from codex_memory.mcp_server import create_v1_server

    server = create_v1_server(object(), token_verifier=StaticTokenVerifier("mcp-secret"))

    assert server.settings.auth is not None
    assert str(server.settings.auth.resource_server_url) == "http://127.0.0.1:8001/mcp"
    assert server.settings.auth.required_scopes == ["memory:read", "memory:append"]

def test_v1_mcp_main_configures_static_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    from codex_memory.api import v1_mcp

    captured: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str) -> None:
            captured["transport"] = transport

    def create_server(client: object, **kwargs: object) -> FakeServer:
        captured["client"] = client
        captured.update(kwargs)
        return FakeServer()

    monkeypatch.setattr(v1_mcp, "MemoryApiClient", lambda url, token: (url, token))
    monkeypatch.setattr(v1_mcp, "create_v1_server", create_server)
    monkeypatch.setenv("CODEX_MEMORY_API_URL", "http://api.example")
    monkeypatch.setenv("CODEX_MEMORY_API_TOKEN", "api-secret")
    monkeypatch.setenv("CODEX_MEMORY_MCP_TOKEN", "mcp-secret")

    v1_mcp.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8001
    assert isinstance(captured["token_verifier"], StaticTokenVerifier)
    assert captured["transport"] == "streamable-http"
