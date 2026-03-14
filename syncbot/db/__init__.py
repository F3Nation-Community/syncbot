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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote_plus

from sqlalchemy import and_, create_engine, func, inspect, pool, text
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
_DB_INIT_MAX_ATTEMPTS = 15
_DB_INIT_RETRY_SECONDS = 2
_MIGRATION_TABLE = "schema_migrations"
_BASELINE_VERSION = "000_init_sql"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_INIT_SQL_PATH = _PROJECT_ROOT / "db" / "init.sql"
_MIGRATIONS_DIR = _PROJECT_ROOT / "db" / "migrations"


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


def _sql_statements_from_file(sql_path: Path) -> list[str]:
    """Parse a SQL file into executable statements.

    This parser intentionally supports the project's migration style
    (line comments + semicolon-delimited statements).
    """
    sql = sql_path.read_text()
    lines = []
    for line in sql.splitlines():
        if "--" in line:
            line = line[: line.index("--")].strip()
        else:
            line = line.strip()
        if line:
            lines.append(line)
    combined = " ".join(lines)
    return [stmt.strip() for stmt in combined.split(";") if stmt.strip()]


def _execute_sql_file(conn, sql_path: Path) -> None:
    """Execute all statements from *sql_path* using the provided connection."""
    for stmt in _sql_statements_from_file(sql_path):
        conn.execute(text(stmt))


def _ensure_migration_table(engine) -> None:
    """Create the migration tracking table if it does not exist."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(255) PRIMARY KEY,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        )


def _migration_applied(engine, version: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT version FROM schema_migrations WHERE version = :version"),
            {"version": version},
        ).first()
    return row is not None


def _record_migration(engine, version: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO schema_migrations (version)
                VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
                """
            ),
            {"version": version},
        )


def _table_exists(engine, table_name: str) -> bool:
    """Return True if *table_name* exists in the current schema."""
    return inspect(engine).has_table(table_name)


def _ensure_database_exists() -> None:
    """Create the configured schema if it does not already exist."""
    schema = os.environ.get(constants.ADMIN_DATABASE_SCHEMA, "syncbot")
    url_no_db, connect_args = _build_base_url(include_schema=False)
    engine_no_db = create_engine(url_no_db, connect_args=connect_args, pool_pre_ping=True)
    try:
        with engine_no_db.begin() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET utf8mb4"))
    finally:
        engine_no_db.dispose()


def initialize_database() -> None:
    """Initialize schema and apply migrations automatically.

    Behavior:
    - Ensures the target database exists.
    - Creates migration tracking table.
    - Applies ``db/init.sql`` exactly once for fresh databases (or marks it as
      baseline for already-initialized databases).
    - Applies pending SQL migrations from ``db/migrations`` in filename order.
    """
    if not _INIT_SQL_PATH.exists():
        raise FileNotFoundError(f"Missing init.sql at {_INIT_SQL_PATH}")

    for attempt in range(1, _DB_INIT_MAX_ATTEMPTS + 1):
        try:
            _ensure_database_exists()
            engine = get_engine()

            _ensure_migration_table(engine)

            if not _migration_applied(engine, _BASELINE_VERSION):
                if _table_exists(engine, "workspaces"):
                    _logger.info("db_init_baseline_marked", extra={"version": _BASELINE_VERSION})
                    _record_migration(engine, _BASELINE_VERSION)
                else:
                    _logger.info("db_init_start", extra={"file": str(_INIT_SQL_PATH)})
                    with engine.begin() as conn:
                        _execute_sql_file(conn, _INIT_SQL_PATH)
                    _record_migration(engine, _BASELINE_VERSION)
                    _logger.info("db_init_complete", extra={"version": _BASELINE_VERSION})

            if _MIGRATIONS_DIR.exists():
                migration_files = sorted(p for p in _MIGRATIONS_DIR.glob("*.sql") if p.is_file())
                for migration_file in migration_files:
                    version = migration_file.name
                    if _migration_applied(engine, version):
                        continue
                    _logger.info("db_migration_start", extra={"version": version})
                    with engine.begin() as conn:
                        _execute_sql_file(conn, migration_file)
                    _record_migration(engine, version)
                    _logger.info("db_migration_complete", extra={"version": version})

            return
        except Exception as exc:
            if attempt >= _DB_INIT_MAX_ATTEMPTS:
                _logger.error(
                    "db_init_failed",
                    extra={"attempts": _DB_INIT_MAX_ATTEMPTS, "error": str(exc)},
                )
                raise
            _logger.warning(
                "db_init_retrying",
                extra={"attempt": attempt, "max_attempts": _DB_INIT_MAX_ATTEMPTS, "error": str(exc)},
            )
            time.sleep(_DB_INIT_RETRY_SECONDS)


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

    with engine_no_db.begin() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{schema}`"))
        conn.execute(text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4"))

    engine_no_db.dispose()

    url_with_db, connect_args = _build_base_url(include_schema=True)
    engine_with_db = create_engine(url_with_db, connect_args=connect_args, pool_pre_ping=True)

    init_path = _INIT_SQL_PATH
    if not init_path.exists():
        _logger.error("drop_and_init_db: init.sql not found at %s", init_path)
        engine_with_db.dispose()
        return

    with engine_with_db.begin() as conn:
        _execute_sql_file(conn, init_path)

    engine_with_db.dispose()

    GLOBAL_ENGINE = None
    GLOBAL_SESSION = None
    GLOBAL_SCHEMA = None
    # Ensure baseline is re-recorded after reset.
    initialize_database()
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
    def merge_record(record: BaseClass, schema=None) -> BaseClass:
        """Insert or update a record based on its primary key."""
        session = get_session(schema=schema)
        try:
            merged = session.merge(record)
            session.flush()
            session.expunge(merged)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            close_session(session)
        return merged

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

