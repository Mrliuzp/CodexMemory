from __future__ import annotations

import os

from .api_client import MemoryApiClient
from .mcp_auth import StaticTokenVerifier
from .mcp_server import create_v1_server


def main() -> None:
    client = MemoryApiClient(os.environ["CODEX_MEMORY_API_URL"], os.environ["CODEX_MEMORY_API_TOKEN"])
    create_v1_server(
        client,
        host=os.environ.get("CODEX_MEMORY_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("CODEX_MEMORY_MCP_PORT", "8001")),
        token_verifier=StaticTokenVerifier(os.environ["CODEX_MEMORY_MCP_TOKEN"]),
    ).run(transport="streamable-http")


if __name__ == "__main__":
    main()
