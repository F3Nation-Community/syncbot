"""Unit tests for ``syncbot/db`` connection pooling, retry logic, and backend parity (MySQL/SQLite)."""

import contextlib
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from db import _MAX_RETRIES, _with_retry

# -----------------------------------------------------------------------
# _with_retry decorator
# -----------------------------------------------------------------------


class TestWithRetry:
    def test_success_no_retry(self):
        call_count = 0

        @_with_retry
        def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert fn() == "ok"
        assert call_count == 1

    def test_retries_on_operational_error(self):
        call_count = 0

        @_with_retry
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count <= _MAX_RETRIES:
                raise OperationalError("statement", {}, Exception("connection lost"))
            return "recovered"

        assert fn() == "recovered"
        assert call_count == _MAX_RETRIES + 1

    def test_exhausts_retries_raises(self):
        @_with_retry
        def fn():
            raise OperationalError("statement", {}, Exception("connection lost"))

        with pytest.raises(OperationalError):
            fn()

    def test_non_operational_error_not_retried(self):
        call_count = 0

        @_with_retry
        def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a db error")

        with pytest.raises(ValueError):
            fn()
        assert call_count == 1


# -----------------------------------------------------------------------
# Engine creation uses QueuePool
# -----------------------------------------------------------------------


class TestEngineConfig:
    @patch.dict(
        os.environ,
        {
            "DATABASE_BACKEND": "mysql",
            "DATABASE_HOST": "localhost",
            "DATABASE_USER": "root",
            "DATABASE_PASSWORD": "test",
            "DATABASE_SCHEMA": "syncbot",
        },
        clear=False,
    )
    def test_engine_uses_queue_pool_mysql(self):
        from sqlalchemy.pool import QueuePool

        import db as db_mod
        from db import get_engine

        old_engine = db_mod.GLOBAL_ENGINE
        old_schema = db_mod.GLOBAL_SCHEMA
        engine = None
        try:
            db_mod.GLOBAL_ENGINE = None
            db_mod.GLOBAL_SCHEMA = None
            engine = get_engine(schema="test_schema_unique")
            assert isinstance(engine.pool, QueuePool)
        finally:
            if engine:
                engine.dispose()
            db_mod.GLOBAL_ENGINE = old_engine
            db_mod.GLOBAL_SCHEMA = old_schema

    @patch.dict(
        os.environ,
        {
            "DATABASE_BACKEND": "postgresql",
            "DATABASE_HOST": "localhost",
            "DATABASE_USER": "root",
            "DATABASE_PASSWORD": "test",
            "DATABASE_SCHEMA": "syncbot",
        },
        clear=False,
    )
    def test_engine_uses_queue_pool_postgresql(self):
        from sqlalchemy.pool import QueuePool

        import db as db_mod
        from db import get_engine

        old_engine = db_mod.GLOBAL_ENGINE
        old_schema = db_mod.GLOBAL_SCHEMA
        engine = None
        try:
            db_mod.GLOBAL_ENGINE = None
            db_mod.GLOBAL_SCHEMA = None
            engine = get_engine(schema="test_schema_unique_pg")
            assert isinstance(engine.pool, QueuePool)
        finally:
            if engine:
                engine.dispose()
            db_mod.GLOBAL_ENGINE = old_engine
            db_mod.GLOBAL_SCHEMA = old_schema

    @patch.dict(
        os.environ,
        {
            "DATABASE_BACKEND": "sqlite",
            "DATABASE_URL": "sqlite:///:memory:",
        },
        clear=False,
    )
    def test_engine_uses_null_pool_sqlite(self):
        from sqlalchemy.pool import NullPool

        import db as db_mod
        from db import get_engine

        old_engine = db_mod.GLOBAL_ENGINE
        old_schema = db_mod.GLOBAL_SCHEMA
        engine = None
        try:
            db_mod.GLOBAL_ENGINE = None
            db_mod.GLOBAL_SCHEMA = None
            engine = get_engine()
            assert isinstance(engine.pool, NullPool)
        finally:
            if engine:
                engine.dispose()
            db_mod.GLOBAL_ENGINE = old_engine
            db_mod.GLOBAL_SCHEMA = old_schema


# -----------------------------------------------------------------------
# Backend parity: SQLite bootstrap and required vars
# -----------------------------------------------------------------------


class TestBackendParity:
    @pytest.mark.parametrize("sqlite_url", ["sqlite:///test_bootstrap.db"])
    @patch.dict(os.environ, {"DATABASE_BACKEND": "sqlite"}, clear=False)
    def test_sqlite_initialize_database_creates_tables(self, sqlite_url):
        import db as db_mod
        from db import get_engine, initialize_database

        os.environ["DATABASE_URL"] = sqlite_url
        old_engine = db_mod.GLOBAL_ENGINE
        old_schema = db_mod.GLOBAL_SCHEMA
        try:
            db_mod.GLOBAL_ENGINE = None
            db_mod.GLOBAL_SCHEMA = None
            initialize_database()
            engine = get_engine()
            insp = inspect(engine)
            assert insp.has_table("workspaces")
            assert insp.has_table("alembic_version")
            assert insp.has_table("slack_bots")
        finally:
            if db_mod.GLOBAL_ENGINE:
                db_mod.GLOBAL_ENGINE.dispose()
            db_mod.GLOBAL_ENGINE = old_engine
            db_mod.GLOBAL_SCHEMA = old_schema
            if "DATABASE_URL" in os.environ and "test_bootstrap" in os.environ["DATABASE_URL"]:
                with contextlib.suppress(Exception):
                    (__import__("pathlib").Path("test_bootstrap.db")).unlink(missing_ok=True)

    def test_get_required_db_vars_mysql_without_url(self):
        with patch.dict(os.environ, {"DATABASE_BACKEND": "mysql"}, clear=False):
            if "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]
            from constants import get_required_db_vars

            required = get_required_db_vars()
            assert "DATABASE_HOST" in required
            assert "DATABASE_USER" in required
            assert "DATABASE_PASSWORD" in required
            assert "DATABASE_SCHEMA" in required

    def test_get_required_db_vars_sqlite(self):
        with patch.dict(os.environ, {"DATABASE_BACKEND": "sqlite"}, clear=False):
            from constants import get_required_db_vars

            required = get_required_db_vars()
            assert required == ["DATABASE_URL"]

    def test_get_required_db_vars_postgresql_without_url(self):
        with patch.dict(
            os.environ,
            {"DATABASE_BACKEND": "postgresql"},
            clear=False,
        ):
            if "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]
            from constants import get_required_db_vars

            required = get_required_db_vars()
            assert "DATABASE_HOST" in required
            assert "DATABASE_USER" in required
            assert "DATABASE_PASSWORD" in required
            assert "DATABASE_SCHEMA" in required

    def test_default_database_backend_is_mysql(self):
        import importlib

        import constants as c

        old = os.environ.pop("DATABASE_BACKEND", None)
        try:
            importlib.reload(c)
            assert c.get_database_backend() == "mysql"
        finally:
            if old is not None:
                os.environ["DATABASE_BACKEND"] = old
            else:
                os.environ.setdefault("DATABASE_BACKEND", "mysql")
            importlib.reload(c)
