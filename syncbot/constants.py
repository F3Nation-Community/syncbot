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
SLACK_CLIENT_ID = "ENV_SLACK_CLIENT_ID"
SLACK_CLIENT_SECRET = "ENV_SLACK_CLIENT_SECRET"
SLACK_SCOPES = "ENV_SLACK_SCOPES"
SLACK_SIGNING_SECRET = "SLACK_SIGNING_SECRET"
PASSWORD_ENCRYPT_KEY = "PASSWORD_ENCRYPT_KEY"
REQUIRE_ADMIN = "REQUIRE_ADMIN"

DATABASE_HOST = "DATABASE_HOST"
ADMIN_DATABASE_USER = "ADMIN_DATABASE_USER"
ADMIN_DATABASE_PASSWORD = "ADMIN_DATABASE_PASSWORD"
ADMIN_DATABASE_SCHEMA = "ADMIN_DATABASE_SCHEMA"

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

# Required in all environments
_REQUIRED_ALWAYS = [
    DATABASE_HOST,
    ADMIN_DATABASE_USER,
    ADMIN_DATABASE_PASSWORD,
    ADMIN_DATABASE_SCHEMA,
]

# Required only in production (Lambda). OAuth uses MySQL; no S3 buckets.
_REQUIRED_PRODUCTION = [
    SLACK_SIGNING_SECRET,
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    SLACK_SCOPES,
    PASSWORD_ENCRYPT_KEY,
]


def _encryption_active() -> bool:
    """Return True if bot-token encryption is configured with a real key."""
    key = os.environ.get(PASSWORD_ENCRYPT_KEY, "")
    return bool(key) and key != "123"


def validate_config() -> None:
    """Check that required environment variables are present.

    In production this raises immediately so the Lambda fails on cold-start
    rather than silently misbehaving.  In local development it only warns.
    """
    required = list(_REQUIRED_ALWAYS)
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
        _logger.critical(
            "Bot-token encryption is DISABLED in production. "
            "Set PASSWORD_ENCRYPT_KEY to a strong passphrase to encrypt tokens at rest."
        )
