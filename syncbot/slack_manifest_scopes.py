"""Canonical Slack OAuth scopes — keep in sync with repo root ``slack-manifest.json``.

``oauth_config.scopes.bot`` must match :envvar:`SLACK_BOT_SCOPES` (comma-separated).
``oauth_config.scopes.user`` must match :envvar:`SLACK_USER_SCOPES` (comma-separated).
This app always uses both **bot** and **user** scopes; ``USER_SCOPES`` is non-empty and must
match the manifest ``user`` array (order included). When changing scopes, edit this module and
``slack-manifest.json`` / ``slack-manifest_test.json`` together, then AWS SAM defaults,
GCP ``slack_user_scopes``, and env examples.
"""

from __future__ import annotations

# --- Must match slack-manifest.json oauth_config.scopes.bot (order as in manifest) ---

BOT_SCOPES: tuple[str, ...] = (
    "app_mentions:read",
    "channels:history",
    "channels:join",
    "channels:read",
    "channels:manage",
    "chat:write",
    "chat:write.customize",
    "files:read",
    "files:write",
    "groups:history",
    "groups:read",
    "groups:write",
    "im:write",
    "reactions:read",
    "reactions:write",
    "team:read",
    "users:read",
    "users:read.email",
)

# --- Must match slack-manifest.json oauth_config.scopes.user (order as in manifest) ---

USER_SCOPES: tuple[str, ...] = (
    "chat:write",
    "channels:history",
    "channels:read",
    "files:read",
    "files:write",
    "groups:history",
    "groups:read",
    "groups:write",
    "im:write",
    "reactions:read",
    "reactions:write",
    "team:read",
    "users:read",
    "users:read.email",
)


def bot_scopes_comma_separated() -> str:
    """Return the bot scope string for SLACK_BOT_SCOPES / CloudFormation."""
    return ",".join(BOT_SCOPES)


def user_scopes_comma_separated() -> str:
    """Return the user scope string for SLACK_USER_SCOPES / CloudFormation / Terraform."""
    return ",".join(USER_SCOPES)
