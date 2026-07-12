from __future__ import annotations


def test_v1_mcp_factory_configures_streamable_http_endpoint() -> None:
    from codex_memory.mcp_server import create_v1_server

    server = create_v1_server(object(), host="0.0.0.0", port=8001)

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 8001
