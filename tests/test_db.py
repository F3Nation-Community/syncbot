"""Unit tests for ``syncbot/db`` connection pooling and retry logic."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("ADMIN_DATABASE_USER", "root")
os.environ.setdefault("ADMIN_DATABASE_PASSWORD", "test")
os.environ.setdefault("ADMIN_DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

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
            "DATABASE_HOST": "localhost",
            "ADMIN_DATABASE_USER": "root",
            "ADMIN_DATABASE_PASSWORD": "test",
            "ADMIN_DATABASE_SCHEMA": "syncbot",
        },
    )
    def test_engine_uses_queue_pool(self):
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
