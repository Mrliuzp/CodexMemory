from __future__ import annotations

import os
from dataclasses import dataclass


PLACEHOLDER_PREFIXES = ("change-me", "change_me")


def is_placeholder_value(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return not normalized or normalized.startswith(PLACEHOLDER_PREFIXES)


def is_placeholder_admin_username(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized == "admin" or is_placeholder_value(value)


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///memory-v1.db"
    embedding_dimension: int = 1536
    deployment_mode: str = "development"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("CODEX_MEMORY_DATABASE_URL", cls.database_url),
            embedding_dimension=int(os.environ.get("CODEX_MEMORY_EMBEDDING_DIMENSION", "1536")),
            deployment_mode=os.environ.get("CODEX_MEMORY_DEPLOYMENT_MODE", cls.deployment_mode),
        )

    def validate_runtime(self) -> None:
        if self.deployment_mode not in {"development", "test", "production"}:
            raise ValueError("CODEX_MEMORY_DEPLOYMENT_MODE 必须是 development、test 或 production")
        if self.deployment_mode != "production":
            return
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("生产环境必须使用 PostgreSQL")

        values = {
            "CODEX_MEMORY_SERVICE_TOKEN": os.environ.get("CODEX_MEMORY_SERVICE_TOKEN"),
            "CODEX_MEMORY_ADMIN_USERNAME": os.environ.get("CODEX_MEMORY_ADMIN_USERNAME"),
            "CODEX_MEMORY_ADMIN_PASSWORD": os.environ.get("CODEX_MEMORY_ADMIN_PASSWORD"),
            "CODEX_MEMORY_ADMIN_SESSION_SECRET": os.environ.get("CODEX_MEMORY_ADMIN_SESSION_SECRET"),
        }
        invalid = [
            name
            for name, value in values.items()
            if (
                is_placeholder_admin_username(value)
                if name == "CODEX_MEMORY_ADMIN_USERNAME"
                else is_placeholder_value(value)
            )
        ]
        mcp_token = os.environ.get("CODEX_MEMORY_MCP_TOKEN")
        if mcp_token is not None and is_placeholder_value(mcp_token):
            invalid.append("CODEX_MEMORY_MCP_TOKEN")
        if invalid:
            variables = "、".join(invalid)
            raise ValueError(f"生产环境变量 {variables} 必须配置非占位符值")
