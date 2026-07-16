from __future__ import annotations

from .config import Settings
from .db import create_engine_from_url, create_session_factory
from .http_api import create_v1_app


settings = Settings.from_env()
app = create_v1_app(create_session_factory(create_engine_from_url(settings.database_url)))
