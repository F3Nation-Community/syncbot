"""Core utility functions used throughout SyncBot."""

import logging
import os
from typing import Any

from slack_sdk.errors import SlackApiError

import constants
from slack import actions

_logger = logging.getLogger(__name__)


def safe_get(data: Any, *keys: Any) -> Any:
    """Safely traverse nested dicts/lists. Returns None on missing keys."""
    if not data:
        return None
    try:
        result = data
        for k in keys:
            if isinstance(k, int) and isinstance(result, list) or result.get(k):
                result = result[k]
            else:
                return None
        return result
    except (KeyError, AttributeError, IndexError):
        return None


def get_user_id_from_body(body: dict) -> str | None:
    """Extract the acting user's ID from any Slack request payload."""
    return safe_get(body, "user_id") or safe_get(body, "user", "id")


def is_user_authorized(client, user_id: str) -> bool:
    """Return *True* if the user is allowed to configure SyncBot.

    When ``REQUIRE_ADMIN`` is ``"true"`` (the default), only workspace
    admins and owners are authorized.
    """
    from .slack_api import _users_info

    require_admin = os.environ.get(constants.REQUIRE_ADMIN, "true").lower()
    if require_admin != "true":
        return True

    try:
        res = _users_info(client, user_id)
    except SlackApiError:
        _logger.warning(f"Could not verify admin status for user {user_id} — denying access")
        return False

    user = safe_get(res, "user") or {}
    return bool(user.get("is_admin") or user.get("is_owner"))


def format_admin_label(client, user_id: str, workspace) -> tuple[str, str]:
    """Return ``(display_name, full_label)`` for an admin."""
    from .slack_api import get_user_info
    from .workspace import resolve_workspace_name

    display_name, _ = get_user_info(client, user_id)
    display_name = display_name or "An admin"
    ws_name = resolve_workspace_name(workspace) if workspace else None
    if ws_name:
        return display_name, f"{display_name} ({ws_name})"
    return display_name, display_name


_PREFIXED_ACTIONS = (
    actions.CONFIG_REMOVE_FEDERATION_CONNECTION,
    actions.CONFIG_LEAVE_GROUP,
    actions.CONFIG_ACCEPT_GROUP_REQUEST,
    actions.CONFIG_DECLINE_GROUP_REQUEST,
    actions.CONFIG_CANCEL_GROUP_REQUEST,
    actions.CONFIG_SUBSCRIBE_CHANNEL,
    actions.CONFIG_UNPUBLISH_CHANNEL,
    actions.CONFIG_USER_MAPPING_EDIT,
    actions.CONFIG_REMOVE_SYNC,
    actions.CONFIG_RESUME_SYNC,
    actions.CONFIG_PAUSE_SYNC,
    actions.CONFIG_STOP_SYNC,
)


def get_request_type(body: dict) -> tuple[str, str]:
    """Classify an incoming Slack request into a ``(category, identifier)`` pair."""
    request_type = safe_get(body, "type")
    if request_type == "event_callback":
        return ("event_callback", safe_get(body, "event", "type"))
    elif request_type == "block_actions":
        block_action = safe_get(body, "actions", 0, "action_id")
        for prefix in _PREFIXED_ACTIONS:
            if block_action == prefix or block_action.startswith(prefix + "_"):
                block_action = prefix
                break
        return ("block_actions", block_action)
    elif request_type == "view_submission":
        return ("view_submission", safe_get(body, "view", "callback_id"))
    elif not request_type and "command" in body:
        return ("command", safe_get(body, "command"))
    else:
        return ("unknown", "unknown")
