from __future__ import annotations

import os
from dataclasses import dataclass


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
            raise ValueError("CODEX_MEMORY_DEPLOYMENT_MODE \u5fc5\u987b\u662f development\u3001test \u6216 production")
        if self.deployment_mode == "production" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("\u751f\u4ea7\u73af\u5883\u5fc5\u987b\u4f7f\u7528 PostgreSQL")
