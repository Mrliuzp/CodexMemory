from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///memory-v1.db"
    embedding_dimension: int = 1536

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("CODEX_MEMORY_DATABASE_URL", cls.database_url),
            embedding_dimension=int(os.environ.get("CODEX_MEMORY_EMBEDDING_DIMENSION", "1536")),
        )
