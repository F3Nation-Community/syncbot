"""Shared handler utilities and types."""

import logging
from typing import Any

import helpers
from db import schemas

_logger = logging.getLogger(__name__)

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict


class EventContext(TypedDict):
    """Strongly-typed dict returned by ``_parse_event_fields``."""

    team_id: str | None
    channel_id: str | None
    user_id: str | None
    msg_text: str
    mentioned_users: list[dict[str, Any]]
    thread_ts: str | None
    ts: str | None
    event_subtype: str | None


def _parse_private_metadata(body: dict) -> dict:
    """Extract and parse JSON ``private_metadata`` from a view submission."""
    import json as _json

    raw = helpers.safe_get(body, "view", "private_metadata") or "{}"
    try:
        return _json.loads(raw)
    except Exception as exc:
        _logger.debug(f"_parse_private_metadata: bad JSON: {exc}")
        return {}


def _get_authorized_workspace(
    body: dict, client, context: dict, action_name: str
) -> tuple[str, schemas.Workspace] | None:
    """Validate authorization and return ``(user_id, workspace_record)``.

    Returns *None* and logs a warning if the user is not authorized or
    the workspace cannot be resolved.
    """
    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        _logger.warning("authorization_denied", extra={"user_id": user_id, "action": action_name})
        return None

    team_id = (
        helpers.safe_get(body, "view", "team_id")
        or helpers.safe_get(body, "team", "id")
        or helpers.safe_get(body, "team_id")
    )
    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return None

    return user_id, workspace_record


def _sanitize_text(value: str, max_length: int = 100) -> str:
    """Strip and truncate user-supplied text to prevent oversized DB writes."""
    if not value:
        return value
    return value.strip()[:max_length]
