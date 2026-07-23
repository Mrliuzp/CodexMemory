from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from codex_memory.config import Settings
from codex_memory.db_models import Base


config = context.config
environment_url = os.environ.get("CODEX_MEMORY_DATABASE_URL")
configured_url = config.get_main_option("sqlalchemy.url")
default_url = "postgresql+psycopg://codex_memory:codex_memory@127.0.0.1:5432/codex_memory"
if environment_url and configured_url == default_url:
    config.set_main_option("sqlalchemy.url", environment_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
