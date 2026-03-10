"""Database engine, session management, and the :class:`DbManager` CRUD helper.

Key design decisions:

* **Connection pooling** — Uses :class:`~sqlalchemy.pool.QueuePool` with
  ``pool_pre_ping=True`` so that warm Lambda containers reuse connections
  while stale ones are transparently replaced.
* **Automatic retry** — The :func:`_with_retry` decorator retries any
  :class:`~sqlalchemy.exc.OperationalError` up to ``_MAX_RETRIES`` times,
  disposing the engine between attempts to force a fresh connection.
"""

import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote_plus

from sqlalchemy import and_, create_engine, func, pool, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import constants
from db.schemas import BaseClass

_logger = logging.getLogger(__name__)


@dataclass
class DatabaseField:
    name: str
    value: object = None


GLOBAL_ENGINE = None
GLOBAL_SESSION = None
GLOBAL_SCHEMA = None

# Maximum number of times to retry a DB operation on a transient connection error
_MAX_RETRIES = 2


def _build_base_url(include_schema: bool = False) -> tuple[str, dict]:
    """Build MySQL URL and connect_args for get_engine (no schema or with schema)."""
    host = os.environ[constants.DATABASE_HOST]
    user = quote_plus(os.environ[constants.ADMIN_DATABASE_USER])
    passwd = quote_plus(os.environ[constants.ADMIN_DATABASE_PASSWORD])
    schema = os.environ.get(constants.ADMIN_DATABASE_SCHEMA, "syncbot")
    path = f"/{schema}" if include_schema else ""
    db_url = f"mysql+pymysql://{user}:{passwd}@{host}:3306{path}?charset=utf8mb4"
    connect_args: dict = {}
    if not constants.LOCAL_DEVELOPMENT:
        ca_path = "/etc/pki/tls/certs/ca-bundle.crt"
        try:
            ssl_ctx = ssl.create_default_context(cafile=ca_path)
        except (OSError, ssl.SSLError):
            ssl_ctx = ssl.create_default_context()
        connect_args["ssl"] = ssl_ctx
    return db_url, connect_args


def drop_and_init_db() -> None:
    """Drop the database and reinitialize from db/init.sql. All data is lost.

    Called from the "Reset Database" UI button (gated by ENABLE_DB_RESET).
    Resets GLOBAL_ENGINE and GLOBAL_SESSION so the next get_engine() uses a fresh DB.
    """
    global GLOBAL_ENGINE, GLOBAL_SESSION, GLOBAL_SCHEMA

    _logger.critical(
        "DB RESET: dropping database and reinitializing from init.sql. All data will be lost."
    )

    schema = os.environ.get(constants.ADMIN_DATABASE_SCHEMA, "syncbot")
    url_no_db, connect_args = _build_base_url(include_schema=False)
    engine_no_db = create_engine(url_no_db, connect_args=connect_args, pool_pre_ping=True)

    with engine_no_db.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{schema}`"))
        conn.execute(text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4"))
        conn.commit()

    engine_no_db.dispose()

    url_with_db, connect_args = _build_base_url(include_schema=True)
    engine_with_db = create_engine(url_with_db, connect_args=connect_args, pool_pre_ping=True)

    init_path = Path(__file__).resolve().parent.parent.parent / "db" / "init.sql"
    if not init_path.exists():
        _logger.error("drop_and_init_db: init.sql not found at %s", init_path)
        engine_with_db.dispose()
        return

    sql = init_path.read_text()
    # Strip line comments and split into statements
    lines = []
    for line in sql.splitlines():
        if "--" in line:
            line = line[: line.index("--")].strip()
        else:
            line = line.strip()
        if line:
            lines.append(line)
    combined = " ".join(lines)
    statements = [s.strip() for s in combined.split(";") if s.strip()]

    with engine_with_db.connect() as conn:
        for stmt in statements:
            if stmt:
                conn.execute(text(stmt))
        conn.commit()

    engine_with_db.dispose()

    GLOBAL_ENGINE = None
    GLOBAL_SESSION = None
    GLOBAL_SCHEMA = None
    _logger.info("drop_and_init_db: database %s dropped and reinitialized from init.sql", schema)


def get_engine(echo: bool = False, schema: str = None):
    """Return the global SQLAlchemy engine, creating it on first call.

    Uses QueuePool with pool_pre_ping so that stale connections (common
    in Lambda warm containers) are detected and replaced transparently.
    """
    global GLOBAL_ENGINE, GLOBAL_SCHEMA

    target_schema = schema or os.environ[constants.ADMIN_DATABASE_SCHEMA]

    if target_schema == GLOBAL_SCHEMA and GLOBAL_ENGINE is not None:
        return GLOBAL_ENGINE

    host = os.environ[constants.DATABASE_HOST]
    user = quote_plus(os.environ[constants.ADMIN_DATABASE_USER])
    passwd = quote_plus(os.environ[constants.ADMIN_DATABASE_PASSWORD])

    db_url = f"mysql+pymysql://{user}:{passwd}@{host}:3306/{target_schema}?charset=utf8mb4"

    connect_args: dict = {}
    if not constants.LOCAL_DEVELOPMENT:
        ca_path = "/etc/pki/tls/certs/ca-bundle.crt"
        try:
            ssl_ctx = ssl.create_default_context(cafile=ca_path)
        except (OSError, ssl.SSLError):
            ssl_ctx = ssl.create_default_context()
        connect_args["ssl"] = ssl_ctx

    GLOBAL_ENGINE = create_engine(
        db_url,
        echo=echo,
        poolclass=pool.QueuePool,
        pool_size=1,
        max_overflow=1,
        pool_recycle=3600,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    GLOBAL_SCHEMA = target_schema
    return GLOBAL_ENGINE


def get_session(echo: bool = False, schema: str = None):
    if GLOBAL_SESSION:
        return GLOBAL_SESSION
    engine = get_engine(echo=echo, schema=schema)
    return sessionmaker(bind=engine)()


def close_session(session):
    """Close the session (return the connection to the pool)."""
    if session is not None:
        session.close()


T = TypeVar("T")


def _with_retry(fn):
    """Decorator that retries a DB operation on transient OperationalErrors.

    Relies on ``pool_pre_ping=True`` to replace stale connections between
    retries.  Only disposes the engine after all retries are exhausted to
    avoid disrupting other in-flight queries sharing the pool.
    """

    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except OperationalError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    _logger.warning(f"DB operation {fn.__name__} failed (attempt {attempt + 1}), retrying: {exc}")
                else:
                    _logger.error(f"DB operation {fn.__name__} failed after {_MAX_RETRIES + 1} attempts")
                    global GLOBAL_ENGINE
                    if GLOBAL_ENGINE is not None:
                        GLOBAL_ENGINE.dispose()
        raise last_exc

    wrapper.__name__ = fn.__name__
    return wrapper


class DbManager:
    @staticmethod
    @_with_retry
    def get_record(cls: T, id, schema=None) -> T:
        session = get_session(schema=schema)
        try:
            x = session.query(cls).filter(cls.get_id() == id).first()
            if x:
                session.expunge(x)
            return x
        finally:
            session.rollback()
            close_session(session)

    @staticmethod
    @_with_retry
    def find_records(cls: T, filters, schema=None) -> list[T]:
        session = get_session(schema=schema)
        try:
            records = session.query(cls).filter(and_(*filters)).all()
            for r in records:
                session.expunge(r)
            return records
        finally:
            session.rollback()
            close_session(session)

    @staticmethod
    @_with_retry
    def count_records(cls: T, filters, schema=None) -> int:
        session = get_session(schema=schema)
        try:
            return session.query(func.count(cls.id)).filter(and_(*filters)).scalar() or 0
        finally:
            session.rollback()
            close_session(session)

    @staticmethod
    @_with_retry
    def find_join_records2(left_cls: T, right_cls: T, filters, schema=None) -> list[tuple[T]]:
        session = get_session(schema=schema)
        try:
            records = session.query(left_cls, right_cls).join(right_cls).filter(and_(*filters)).all()
            session.expunge_all()
            return records
        finally:
            session.rollback()
            close_session(session)

    @staticmethod
    @_with_retry
    def find_join_records3(
        left_cls: T, right_cls1: T, right_cls2: T, filters, schema=None, left_join=False
    ) -> list[tuple[T]]:
        session = get_session(schema=schema)
        try:
            records = (
                session.query(left_cls, right_cls1, right_cls2)
                .select_from(left_cls)
                .join(right_cls1, isouter=left_join)
                .join(right_cls2, isouter=left_join)
                .filter(and_(*filters))
                .all()
            )
            session.expunge_all()
            return records
        finally:
            session.rollback()
            close_session(session)

    @staticmethod
    @_with_retry
    def update_record(cls: T, id, fields, schema=None):
        session = get_session(schema=schema)
        try:
            session.query(cls).filter(cls.get_id() == id).update(fields, synchronize_session="fetch")
            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)

    @staticmethod
    @_with_retry
    def update_records(cls: T, filters, fields, schema=None):
        session = get_session(schema=schema)
        try:
            session.query(cls).filter(and_(*filters)).update(fields, synchronize_session="fetch")
            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)

    @staticmethod
    @_with_retry
    def create_record(record: BaseClass, schema=None) -> BaseClass:
        session = get_session(schema=schema)
        try:
            session.add(record)
            session.flush()
            session.expunge(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)
        return record

    @staticmethod
    @_with_retry
    def create_records(records: list[BaseClass], schema=None):
        session = get_session(schema=schema)
        try:
            session.add_all(records)
            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)

    @staticmethod
    @_with_retry
    def delete_record(cls: T, id, schema=None):
        session = get_session(schema=schema)
        try:
            session.query(cls).filter(cls.get_id() == id).delete()
            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)

    @staticmethod
    @_with_retry
    def delete_records(cls: T, filters, schema=None):
        session = get_session(schema=schema)
        try:
            session.query(cls).filter(and_(*filters)).delete()
            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)

