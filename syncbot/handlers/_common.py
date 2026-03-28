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


def _extract_team_id(body: dict) -> str | None:
    """Return a workspace/team ID from common Slack payload locations."""
    return (
        helpers.safe_get(body, "view", "team_id")
        or helpers.safe_get(body, "team", "id")
        or helpers.safe_get(body, "team_id")
        or helpers.safe_get(body, "user", "team_id")
    )


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

    team_id = _extract_team_id(body)
    if not team_id:
        _logger.warning("workspace_resolution_failed", extra={"user_id": user_id, "action": action_name})
        return None
    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return None

    return user_id, workspace_record


def _iter_view_state_actions(body: dict):
    """Yield ``(action_id, action_data)`` pairs from ``view.state.values``."""
    state_values = helpers.safe_get(body, "view", "state", "values") or {}
    for block_data in state_values.values():
        yield from block_data.items()


def _get_selected_option_value(body: dict, action_id: str) -> str | None:
    """Return ``selected_option.value`` for a view state action."""
    for aid, action_data in _iter_view_state_actions(body):
        if aid == action_id:
            return helpers.safe_get(action_data, "selected_option", "value")
    return None


def _get_text_input_value(body: dict, action_id: str) -> str | None:
    """Return plain-text ``value`` for a view state action."""
    for aid, action_data in _iter_view_state_actions(body):
        if aid == action_id:
            return action_data.get("value")
    return None


def _get_selected_conversation_or_option(body: dict, action_id: str) -> str | None:
    """Return selected conversation ID, falling back to selected option value."""
    for aid, action_data in _iter_view_state_actions(body):
        if aid == action_id:
            return action_data.get("selected_conversation") or helpers.safe_get(action_data, "selected_option", "value")
    return None


def _sanitize_text(value: str, max_length: int = 100) -> str:
    """Strip and truncate user-supplied text to prevent oversized DB writes."""
    if not value:
        return value
    return value.strip()[:max_length]
