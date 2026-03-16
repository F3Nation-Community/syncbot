"""Alembic env: use SyncBot's engine from db.get_engine(). Run from project root with syncbot on PYTHONPATH."""

import sys
from pathlib import Path

# Project root (db/alembic/env.py -> db -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env when running via CLI (alembic upgrade head)
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from db import get_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use SyncBot's engine (from env vars / DATABASE_URL). Do not use sqlalchemy.url from alembic.ini.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_engine().url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
