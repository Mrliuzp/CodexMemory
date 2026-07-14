from __future__ import annotations

import pytest


def test_production_rejects_sqlite() -> None:
    from codex_memory.config import Settings

    settings = Settings(database_url="sqlite:///memory-v1.db", deployment_mode="production")

    with pytest.raises(ValueError, match="\u751f\u4ea7\u73af\u5883\u5fc5\u987b\u4f7f\u7528 PostgreSQL"):
        settings.validate_runtime()


def test_development_allows_sqlite() -> None:
    from codex_memory.config import Settings

    Settings(database_url="sqlite:///memory-v1.db", deployment_mode="development").validate_runtime()
