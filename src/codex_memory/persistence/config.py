from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://codex_memory:codex_memory@127.0.0.1:5432/codex_memory"
    embedding_dimension: int = 1536

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            database_url=os.environ.get("CODEX_MEMORY_DATABASE_URL", cls.database_url),
            embedding_dimension=int(os.environ.get("CODEX_MEMORY_EMBEDDING_DIMENSION", "1536")),
        )
        if not settings.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("CODEX_MEMORY_DATABASE_URL 必须使用 PostgreSQL")
        return settings
