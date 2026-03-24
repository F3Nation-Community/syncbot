"""Alembic env: use SyncBot's engine from db.get_engine().

Run from repo root: ``alembic -c alembic.ini upgrade head``
(with ``syncbot/`` on ``PYTHONPATH`` via ``prepend_sys_path`` in alembic.ini).
"""

import sys
from pathlib import Path

# syncbot/db/alembic/env.py -> syncbot/ (directory that must be on PYTHONPATH for ``import db``)
_SYNCBOT_DIR = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _SYNCBOT_DIR.parent
if str(_SYNCBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_SYNCBOT_DIR))

# Load .env when running via CLI (alembic upgrade head)
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
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
