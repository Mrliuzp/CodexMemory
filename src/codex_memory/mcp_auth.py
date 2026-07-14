from __future__ import annotations

import hmac

from mcp.server.auth.provider import AccessToken, TokenVerifier


MCP_REQUIRED_SCOPES = ["memory:read", "memory:append"]


class StaticTokenVerifier(TokenVerifier):
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
            scopes=MCP_REQUIRED_SCOPES,
            subject="codex-user",
        )