"""Application constants and startup configuration validation.

This module defines:
1) environment-variable *name* constants, and
2) derived runtime flags computed from ``os.environ``.

It also provides :func:`validate_config` to fail fast on missing
configuration at startup.
"""

import logging
import os

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment-variable name constants
#
# Each value is the *name* of the env var, not its value.  The actual values
# are read from os.environ at runtime.
# ---------------------------------------------------------------------------

SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
SLACK_CLIENT_ID = "SLACK_CLIENT_ID"
SLACK_CLIENT_SECRET = "SLACK_CLIENT_SECRET"
SLACK_BOT_SCOPES = "SLACK_BOT_SCOPES"
SLACK_USER_SCOPES = "SLACK_USER_SCOPES"
SLACK_SIGNING_SECRET = "SLACK_SIGNING_SECRET"
TOKEN_ENCRYPTION_KEY = "TOKEN_ENCRYPTION_KEY"
REQUIRE_ADMIN = "REQUIRE_ADMIN"

# Database: backend-agnostic (postgresql, mysql, or sqlite)
DATABASE_BACKEND = "DATABASE_BACKEND"
DATABASE_URL = "DATABASE_URL"

# Network SQL backends (used when DATABASE_URL is unset)
DATABASE_HOST = "DATABASE_HOST"
DATABASE_PORT = "DATABASE_PORT"
DATABASE_USER = "DATABASE_USER"
DATABASE_PASSWORD = "DATABASE_PASSWORD"
DATABASE_SCHEMA = "DATABASE_SCHEMA"
DATABASE_SSL_CA_PATH = "DATABASE_SSL_CA_PATH"
DATABASE_TLS_ENABLED = "DATABASE_TLS_ENABLED"

# Name of env var that scopes the Reset Database button to one workspace.
ENABLE_DB_RESET = "ENABLE_DB_RESET"

# ---------------------------------------------------------------------------
# Derived runtime flags / computed values
# ---------------------------------------------------------------------------

LOCAL_DEVELOPMENT = os.environ.get("LOCAL_DEVELOPMENT", "false").lower() == "true"

_BOT_TOKEN_PLACEHOLDER = "xoxb-0-0"


def _has_real_bot_token() -> bool:
    """Return *True* if SLACK_BOT_TOKEN looks like a genuine Slack token."""
    token = os.environ.get(SLACK_BOT_TOKEN, "").strip()
    return token.startswith("xoxb-") and token != _BOT_TOKEN_PLACEHOLDER


HAS_REAL_BOT_TOKEN: bool = _has_real_bot_token()

WARNING_BLOCK = "WARNING_BLOCK"

# ---------------------------------------------------------------------------
# User-matching TTLs (seconds)
#
# How long a cached match result is considered "fresh" before re-checking.
# Manual matches never expire and can only be removed via the admin UI.
# ---------------------------------------------------------------------------

MATCH_TTL_EMAIL = 30 * 24 * 3600  # 30 days for email-confirmed matches
MATCH_TTL_NAME = 14 * 24 * 3600  # 14 days for name-based matches
MATCH_TTL_NONE = 90 * 24 * 3600  # 90 days for no-match (team_join handles re-checks)
USER_DIR_REFRESH_TTL = 24 * 3600  # 24 hours per workspace directory refresh
USER_MATCHING_PAGE_SIZE = 40  # max unmatched users shown in the modal

# Refresh button cooldown (seconds) when content hash unchanged
REFRESH_COOLDOWN_SECONDS = 60

SOFT_DELETE_RETENTION_DAYS = int(os.environ.get("SOFT_DELETE_RETENTION_DAYS", "30"))

# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------

SYNCBOT_INSTANCE_ID = "SYNCBOT_INSTANCE_ID"
SYNCBOT_PUBLIC_URL = "SYNCBOT_PUBLIC_URL"
FEDERATION_ENABLED = os.environ.get("SYNCBOT_FEDERATION_ENABLED", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Startup configuration validation
#
# Validates that all required environment variables are set before the app
# handles any requests.  Fails fast in production; warns in local dev.
# ---------------------------------------------------------------------------

def get_database_backend() -> str:
    """Return ``postgresql``, ``mysql``, or ``sqlite``.

    Defaults to ``mysql`` when unset.
    """
    return os.environ.get(DATABASE_BACKEND, "mysql").lower().strip() or "mysql"


def _env_bool(name: str, default: bool) -> bool:
    """Parse common boolean env values with a safe default."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def database_tls_enabled() -> bool:
    """Return True when MySQL/PostgreSQL TLS should be used.

    Defaults:
    - local dev: disabled
    - non-local: enabled
    Can be overridden with DATABASE_TLS_ENABLED=true/false.
    """
    default = not LOCAL_DEVELOPMENT
    return _env_bool(DATABASE_TLS_ENABLED, default)


def database_ssl_ca_path() -> str:
    """Return optional CA bundle path for DB TLS verification."""
    return os.environ.get(DATABASE_SSL_CA_PATH, "/etc/pki/tls/certs/ca-bundle.crt")


def get_required_db_vars() -> list:
    """Return list of required env var names for the current database backend."""
    backend = get_database_backend()
    if backend == "sqlite":
        return [DATABASE_URL]
    # mysql / postgresql: require URL or host/user/password/schema
    if os.environ.get(DATABASE_URL):
        return []  # URL is enough
    return [
        DATABASE_HOST,
        DATABASE_USER,
        DATABASE_PASSWORD,
        DATABASE_SCHEMA,
    ]


# Required in all environments (non-DB vars; DB vars are backend-dependent)
_REQUIRED_ALWAYS_NON_DB: list = []

# Required only in production (non-local deployments).
_REQUIRED_PRODUCTION = [
    SLACK_SIGNING_SECRET,
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    SLACK_BOT_SCOPES,
    TOKEN_ENCRYPTION_KEY,
]


# Minimum length for TOKEN_ENCRYPTION_KEY in production (reject weak/placeholder values).
_TOKEN_ENCRYPTION_KEY_MIN_LEN = 16
_TOKEN_ENCRYPTION_KEY_PLACEHOLDERS = frozenset({"123", "changeme", "secret", "password"})


def _encryption_active() -> bool:
    """Return True if bot-token encryption is configured with a strong key.

    In non-local environments the key must be set, at least _TOKEN_ENCRYPTION_KEY_MIN_LEN
    characters, and not a known placeholder. Local dev can use any value or leave unset.
    """
    key = (os.environ.get(TOKEN_ENCRYPTION_KEY) or "").strip()
    if not key or len(key) < _TOKEN_ENCRYPTION_KEY_MIN_LEN:
        return False
    if key.lower() in _TOKEN_ENCRYPTION_KEY_PLACEHOLDERS:
        return False
    return True


def validate_config() -> None:
    """Check that required environment variables are present.

    In production this raises immediately so the Lambda fails on cold-start
    rather than silently misbehaving.  In local development it only warns.
    DB requirements depend on DATABASE_BACKEND (postgresql, mysql, or sqlite).
    """
    required = list(_REQUIRED_ALWAYS_NON_DB) + list(get_required_db_vars())
    if not LOCAL_DEVELOPMENT:
        required.extend(_REQUIRED_PRODUCTION)

    missing = [var for var in required if not os.environ.get(var)]

    if missing:
        msg = "Missing required environment variable(s): " + ", ".join(missing)
        if LOCAL_DEVELOPMENT:
            _logger.warning(msg + " (continuing in local-dev mode)")
        else:
            _logger.critical(msg)
            raise OSError(msg)

    if not LOCAL_DEVELOPMENT and not _encryption_active():
        msg = (
            "TOKEN_ENCRYPTION_KEY is required in production and must be a secure, random value "
            f"(at least {_TOKEN_ENCRYPTION_KEY_MIN_LEN} characters). "
            "Use your provider's secret manager; the AWS template auto-generates it. "
            "Back up the key after first deploy. In local dev you may set it manually or leave unset."
        )
        _logger.critical(msg)
        raise OSError(msg)
