"""Slack API wrappers with automatic retry and rate-limit handling."""

import json
import logging
import time as _time
from functools import wraps

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from db import DbManager, schemas
from helpers._cache import _USER_INFO_CACHE_TTL, _cache_get, _cache_set
from helpers.core import safe_get

_logger = logging.getLogger(__name__)

_SLACK_MAX_RETRIES = 3
_SLACK_INITIAL_BACKOFF = 1.0  # seconds


def slack_retry(fn):
    """Decorator that retries Slack API calls on rate-limit and server errors."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        last_exc: Exception | None = None
        backoff = _SLACK_INITIAL_BACKOFF

        for attempt in range(_SLACK_MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except SlackApiError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response else 0

                if status == 429:
                    retry_after = float(exc.response.headers.get("Retry-After", backoff))
                    _logger.warning(f"{fn.__name__} rate-limited (attempt {attempt + 1}), sleeping {retry_after:.1f}s")
                    _time.sleep(retry_after)
                    backoff = min(backoff * 2, 30)
                elif 500 <= status < 600:
                    _logger.warning(
                        f"{fn.__name__} server error {status} (attempt {attempt + 1}), retrying in {backoff:.1f}s"
                    )
                    _time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                else:
                    raise
        raise last_exc

    return wrapper


@slack_retry
def _users_info(client: WebClient, user_id: str) -> dict:
    """Low-level wrapper so the retry decorator can catch SlackApiError."""
    return client.users_info(user=user_id)


def _get_auth_info(client: WebClient) -> dict | None:
    """Call ``auth.test`` once and cache both bot_id and user_id."""
    cache_key = "own_auth_info"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        res = client.auth_test()
        info = {"bot_id": safe_get(res, "bot_id"), "user_id": safe_get(res, "user_id")}
        _cache_set(cache_key, info, ttl=3600)
        return info
    except Exception:
        _logger.warning("Could not determine own identity via auth.test")
        return None


def get_own_bot_id(client: WebClient, context: dict) -> str | None:
    """Return SyncBot's own ``bot_id`` for the current workspace."""
    bot_id = context.get("bot_id")
    if bot_id:
        return bot_id
    info = _get_auth_info(client)
    return info["bot_id"] if info else None


def get_own_bot_user_id(client: WebClient) -> str | None:
    """Return SyncBot's own *user* ID (``U…``) for the current workspace."""
    info = _get_auth_info(client)
    return info["user_id"] if info else None


def get_bot_info_from_event(body: dict) -> tuple[str | None, str | None]:
    """Extract display name and icon URL from a bot_message event."""
    event = body.get("event", {})
    bot_name = event.get("username") or "Bot"
    icons = event.get("icons") or {}
    icon_url = icons.get("image_48") or icons.get("image_36") or icons.get("image_72")
    return bot_name, icon_url


def get_user_info(client: WebClient, user_id: str) -> tuple[str | None, str | None]:
    """Return (display_name, profile_image_url) for a Slack user."""
    cache_key = f"user_info:{user_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        res = _users_info(client, user_id)
    except SlackApiError as exc:
        _logger.debug(f"get_user_info: failed to look up user {user_id}: {exc}")
        return None, None

    user_name = (
        safe_get(res, "user", "profile", "display_name") or safe_get(res, "user", "profile", "real_name") or None
    )
    user_profile_url = safe_get(res, "user", "profile", "image_192")

    result = (user_name, user_profile_url)
    _cache_set(cache_key, result, ttl=_USER_INFO_CACHE_TTL)
    return result


@slack_retry
def post_message(
    bot_token: str,
    channel_id: str,
    msg_text: str,
    user_name: str | None = None,
    user_profile_url: str | None = None,
    thread_ts: str | None = None,
    update_ts: str | None = None,
    workspace_name: str | None = None,
    blocks: list[dict] | None = None,
) -> dict:
    """Post or update a message in a Slack channel."""
    slack_client = WebClient(bot_token)
    posted_from = f"({workspace_name})" if workspace_name else "(via SyncBot)"
    if blocks:
        if msg_text.strip():
            msg_block = {"type": "section", "text": {"type": "mrkdwn", "text": msg_text}}
            all_blocks = [msg_block] + blocks
        else:
            all_blocks = blocks
    else:
        all_blocks = []
    fallback_text = msg_text if msg_text.strip() else "Shared an image"
    if update_ts:
        res = slack_client.chat_update(
            channel=channel_id,
            text=fallback_text,
            ts=update_ts,
            blocks=all_blocks,
        )
    else:
        res = slack_client.chat_postMessage(
            channel=channel_id,
            text=fallback_text,
            username=f"{user_name} {posted_from}",
            icon_url=user_profile_url,
            thread_ts=thread_ts,
            blocks=all_blocks,
        )
    return res


def get_post_records(thread_ts: str) -> list[tuple[schemas.PostMeta, schemas.SyncChannel, schemas.Workspace]]:
    """Look up all PostMeta records that share the same ``post_id``."""
    post = DbManager.find_records(schemas.PostMeta, [schemas.PostMeta.ts == float(thread_ts)])
    if post:
        post_records = DbManager.find_join_records3(
            left_cls=schemas.PostMeta,
            right_cls1=schemas.SyncChannel,
            right_cls2=schemas.Workspace,
            filters=[
                schemas.PostMeta.post_id == post[0].post_id,
                schemas.SyncChannel.status == "active",
                schemas.SyncChannel.deleted_at.is_(None),
            ],
        )
    else:
        post_records = []

    post_records.sort(key=lambda row: row[0].id)

    seen: set[tuple[int, str]] = set()
    deduped: list[tuple[schemas.PostMeta, schemas.SyncChannel, schemas.Workspace]] = []
    for pm, sc, ws in post_records:
        key = (ws.id, sc.channel_id)
        if key not in seen:
            seen.add(key)
            deduped.append((pm, sc, ws))
    return deduped


@slack_retry
def delete_message(bot_token: str, channel_id: str, ts: str) -> dict:
    """Delete a message from a Slack channel."""
    slack_client = WebClient(bot_token)
    res = slack_client.chat_delete(
        channel=channel_id,
        ts=ts,
    )
    return res


def update_modal(
    blocks: list[dict],
    client: WebClient,
    view_id: str,
    title_text: str,
    callback_id: str,
    submit_button_text: str = "Submit",
    parent_metadata: dict | None = None,
    close_button_text: str = "Close",
    notify_on_close: bool = False,
) -> None:
    """Replace the contents of an existing Slack modal."""
    view = {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title_text},
        "submit": {"type": "plain_text", "text": submit_button_text},
        "close": {"type": "plain_text", "text": close_button_text},
        "notify_on_close": notify_on_close,
        "blocks": blocks,
    }
    if parent_metadata:
        view["private_metadata"] = json.dumps(parent_metadata)

    client.views_update(view_id=view_id, view=view)
