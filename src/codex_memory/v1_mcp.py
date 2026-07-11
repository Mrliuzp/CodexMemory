from __future__ import annotations

import os

from .api_client import MemoryApiClient
from .mcp_server import create_v1_server


def main() -> None:
    client = MemoryApiClient(os.environ["CODEX_MEMORY_API_URL"], os.environ["CODEX_MEMORY_API_TOKEN"])
    create_v1_server(client).run(transport="streamable-http")


if __name__ == "__main__":
    main()
