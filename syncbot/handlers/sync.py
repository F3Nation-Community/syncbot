"""Sync management handlers — create, join, remove syncs and Home tab."""

import logging
import time
from datetime import UTC, datetime
from logging import Logger

from slack_sdk.web import WebClient

import builders
import constants
import helpers
from db import DbManager, schemas
from handlers._common import _sanitize_text
from slack import actions, forms, orm

_logger = logging.getLogger(__name__)


def handle_remove_sync(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
):
    """Handles the "DeSync" button action by removing the SyncChannel record from the database.

    Requires admin/owner authorization (defense-in-depth).
    """
    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        _logger.warning("authorization_denied", extra={"user_id": user_id, "action": "remove_sync"})
        return

    raw_value = helpers.safe_get(body, "actions", 0, "value")
    try:
        sync_channel_id = int(raw_value)
    except (TypeError, ValueError):
        _logger.warning(f"Invalid sync_channel_id value: {raw_value!r}")
        return

    sync_channel_record = DbManager.get_record(schemas.SyncChannel, id=sync_channel_id)
    if not sync_channel_record:
        return

    team_id = helpers.safe_get(body, "team_id")
    workspace_record = DbManager.get_record(schemas.Workspace, team_id=team_id) if team_id else None
    if not workspace_record or sync_channel_record.workspace_id != workspace_record.id:
        _logger.warning(
            "ownership_denied",
            extra={"sync_channel_id": sync_channel_id, "team_id": team_id, "action": "remove_sync"},
        )
        return

    DbManager.update_records(
        schemas.SyncChannel,
        [schemas.SyncChannel.id == sync_channel_id],
        {schemas.SyncChannel.deleted_at: datetime.now(UTC)},
    )
    try:
        client.conversations_leave(channel=sync_channel_record.channel_id)
    except Exception as e:
        logger.warning(f"Failed to leave channel {sync_channel_record.channel_id}: {e}")
    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)

    other_sync_channels = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.sync_id == sync_channel_record.sync_id,
            schemas.SyncChannel.deleted_at.is_(None),
            schemas.SyncChannel.workspace_id != workspace_record.id,
        ],
    )
    for sync_channel in other_sync_channels:
        member_ws = helpers.get_workspace_by_id(sync_channel.workspace_id, context=context)
        if member_ws:
            builders.refresh_home_tab_for_workspace(member_ws, logger, context=None)


def handle_app_home_opened(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handle the ``app_home_opened`` event by publishing the Home tab."""
    helpers.purge_stale_soft_deletes()
    builders.build_home_tab(body, client, logger, context)


def handle_refresh_home(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handle the Refresh button on the Home tab.

    Uses content hash and cached blocks: full refresh only when data changed.
    When hash matches and within 60s cooldown, re-publishes with cooldown message.
    """
    team_id = helpers.safe_get(body, "view", "team_id") or helpers.safe_get(body, "team", "id")
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not team_id or not user_id:
        return

    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return

    current_hash = builders._home_tab_content_hash(workspace_record)
    hash_key = f"home_tab_hash:{team_id}"
    blocks_key = f"home_tab_blocks:{team_id}:{user_id}"
    refresh_at_key = f"refresh_at:home:{team_id}:{user_id}"

    action, cached_blocks, remaining = helpers.refresh_cooldown_check(
        current_hash, hash_key, blocks_key, refresh_at_key
    )
    cooldown_sec = getattr(constants, "REFRESH_COOLDOWN_SECONDS", 60)

    if action == "cooldown" and cached_blocks is not None and remaining is not None:
        refresh_idx = helpers.index_of_block_with_action(
            cached_blocks, actions.CONFIG_REFRESH_HOME
        )
        blocks_with_message = helpers.inject_cooldown_message(
            cached_blocks, refresh_idx, remaining
        )
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": blocks_with_message})
        return
    if action == "cached" and cached_blocks is not None:
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": cached_blocks})
        helpers._cache_set(refresh_at_key, time.monotonic(), ttl=cooldown_sec * 2)
        return

    # Full refresh: clear workspace name caches and refresh all workspace names
    stale_keys = [k for k in helpers._CACHE if k.startswith("ws_name_refresh:")]
    for k in stale_keys:
        helpers._CACHE.pop(k, None)

    all_workspaces = DbManager.find_records(
        schemas.Workspace,
        [schemas.Workspace.deleted_at.is_(None)],
    )
    for ws in all_workspaces:
        try:
            if ws.id == workspace_record.id:
                ws_client = client
            elif ws.bot_token:
                ws_client = WebClient(token=helpers.decrypt_bot_token(ws.bot_token))
            else:
                continue

            info = ws_client.team_info()
            current_name = info["team"]["name"]
            if current_name and current_name != ws.workspace_name:
                DbManager.update_records(
                    schemas.Workspace,
                    [schemas.Workspace.id == ws.id],
                    {schemas.Workspace.workspace_name: current_name},
                )
                _logger.info(
                    "workspace_name_refreshed",
                    extra={"workspace_id": ws.id, "new_name": current_name},
                )
        except Exception as e:
            ws_label = f"{ws.workspace_name} ({ws.team_id})"
            _logger.warning(f"Failed to refresh name for {ws_label}: {e}")

    block_dicts = builders.build_home_tab(body, client, logger, context, user_id=user_id, return_blocks=True)
    if block_dicts is None:
        return
    client.views_publish(user_id=user_id, view={"type": "home", "blocks": block_dicts})
    helpers.refresh_after_full(hash_key, blocks_key, refresh_at_key, current_hash, block_dicts)


def handle_join_sync_submission(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handles the join sync form submission by appending to the SyncChannel table.

    Requires admin/owner authorization (defense-in-depth).
    The bot joins the channel *before* the DB record is created so that
    a failed join doesn't leave an orphaned record.
    """
    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        _logger.warning("authorization_denied", extra={"user_id": user_id, "action": "join_sync"})
        return

    form_data = forms.JOIN_SYNC_FORM.get_selected_values(body)
    sync_id = helpers.safe_get(form_data, actions.CONFIG_JOIN_SYNC_SELECT)
    channel_id = helpers.safe_get(form_data, actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT)
    team_id = helpers.safe_get(body, "view", "team_id")

    if not sync_id or not channel_id or not team_id:
        logger.warning(f"Rejected join-sync: missing required field (sync_id={sync_id}, channel_id={channel_id})")
        return

    workspace_record: schemas.Workspace = DbManager.get_record(schemas.Workspace, id=team_id)
    sync_record: schemas.Sync = DbManager.get_record(schemas.Sync, id=sync_id)

    if not workspace_record or not sync_record:
        logger.warning("Rejected join-sync: workspace or sync record not found")
        return

    acting_user_id = helpers.safe_get(body, "user", "id") or user_id
    admin_name, admin_label = helpers.format_admin_label(client, acting_user_id, workspace_record)

    other_sync_channels: list = []
    try:
        client.conversations_join(channel=channel_id)
        channel_sync_record = schemas.SyncChannel(
            sync_id=sync_id,
            channel_id=channel_id,
            workspace_id=workspace_record.id,
            created_at=datetime.now(UTC),
        )
        DbManager.create_record(channel_sync_record)
        other_sync_channels = DbManager.find_records(
            schemas.SyncChannel,
            [
                schemas.SyncChannel.sync_id == sync_id,
                schemas.SyncChannel.deleted_at.is_(None),
                schemas.SyncChannel.workspace_id != workspace_record.id,
            ],
        )
        if other_sync_channels:
            first_channel = other_sync_channels[0]
            first_ws = helpers.get_workspace_by_id(first_channel.workspace_id)
            channel_ref = helpers.resolve_channel_name(first_channel.channel_id, first_ws)
        else:
            channel_ref = sync_record.title or "the other Channel"
        client.chat_postMessage(
            channel=channel_id,
            text=f":arrows_counterclockwise: *{admin_name}* started syncing this Channel with *{channel_ref}*. Messages will be shared automatically.",
        )

        local_ref = helpers.resolve_channel_name(channel_id, workspace_record)
        for sync_channel in other_sync_channels:
            try:
                member_ws = helpers.get_workspace_by_id(sync_channel.workspace_id)
                if member_ws and member_ws.bot_token:
                    member_client = WebClient(token=helpers.decrypt_bot_token(member_ws.bot_token))
                    member_client.chat_postMessage(
                        channel=sync_channel.channel_id,
                        text=f":arrows_counterclockwise: *{admin_label}* started syncing *{local_ref}* with this Channel. Messages will be shared automatically.",
                    )
            except Exception as exc:
                _logger.debug(f"join_sync: failed to notify channel {sync_channel.channel_id}: {exc}")
    except Exception as e:
        logger.error(f"Failed to join sync channel {channel_id}: {e}")

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)

    for sync_channel in other_sync_channels:
        member_ws = helpers.get_workspace_by_id(sync_channel.workspace_id, context=context)
        if member_ws:
            builders.refresh_home_tab_for_workspace(member_ws, logger, context=None)


def handle_new_sync_submission(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handles the new sync form submission.

    Creates a Sync named after the selected channel, links the channel
    to the sync, joins the channel, and posts a welcome message.
    Requires admin/owner authorization (defense-in-depth).
    """
    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        _logger.warning("authorization_denied", extra={"user_id": user_id, "action": "new_sync"})
        return

    form_data = forms.NEW_SYNC_FORM.get_selected_values(body)
    channel_id = helpers.safe_get(form_data, actions.CONFIG_NEW_SYNC_CHANNEL_SELECT)
    team_id = helpers.safe_get(body, "view", "team_id")

    if not channel_id or not team_id:
        logger.warning(f"Rejected sync creation: missing field (channel_id={channel_id})")
        return

    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        logger.warning("Rejected sync creation: workspace record not found")
        return

    try:
        conv_info = client.conversations_info(channel=channel_id)
        channel_name = helpers.safe_get(conv_info, "channel", "name") or channel_id
    except Exception as exc:
        _logger.debug(f"handle_create_sync: conversations_info failed for {channel_id}: {exc}")
        channel_name = channel_id

    sync_title = _sanitize_text(channel_name)
    if not sync_title:
        logger.warning("Rejected sync creation: could not determine channel name")
        return

    acting_user_id = helpers.safe_get(body, "user", "id") or user_id
    admin_name, _ = helpers.format_admin_label(client, acting_user_id, workspace_record)

    try:
        client.conversations_join(channel=channel_id)
        sync_record = schemas.Sync(title=sync_title, description=None)
        DbManager.create_record(sync_record)
        channel_sync_record = schemas.SyncChannel(
            sync_id=sync_record.id,
            channel_id=channel_id,
            workspace_id=workspace_record.id,
            created_at=datetime.now(UTC),
        )
        DbManager.create_record(channel_sync_record)
        client.chat_postMessage(
            channel=channel_id,
            text=f":outbox_tray: *{admin_name}* published this Channel for Syncing. Other Workspaces can now subscribe.",
        )
    except Exception as e:
        logger.error(f"Failed to create sync for channel {channel_id}: {e}")

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)


def handle_member_joined_channel(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handle member_joined_channel: check if SyncBot was added to an untracked channel."""
    event = body.get("event", {})
    user_id = event.get("user")
    channel_id = event.get("channel")
    team_id = helpers.safe_get(body, "team_id") or event.get("team")

    if not user_id or not channel_id or not team_id:
        return

    own_user_id = helpers.get_own_bot_user_id(client)
    if user_id != own_user_id:
        return

    any_sync_channel = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.channel_id == channel_id,
            schemas.SyncChannel.deleted_at.is_(None),
        ],
    )
    if any_sync_channel:
        return

    try:
        client.chat_postMessage(
            channel=channel_id,
            text=":wave: Hello! I'm SyncBot. I was added to this Channel, but this Channel "
            "doesn't seem to be part of a Sync. I'm leaving now. Please open the SyncBot Home "
            "tab to configure me.",
        )
        client.conversations_leave(channel=channel_id)
    except Exception as e:
        _logger.warning(f"Failed to notify and leave untracked channel {channel_id}: {e}")


def check_join_sync_channel(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Checks to see if the chosen channel id is already part of a sync."""
    view_id = helpers.safe_get(body, "view", "id")
    form_data = forms.JOIN_SYNC_FORM.get_selected_values(body)
    channel_id = helpers.safe_get(form_data, actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT)
    blocks = helpers.safe_get(body, "view", "blocks")
    already_warning = constants.WARNING_BLOCK in [block["block_id"] for block in blocks]
    sync_channel_records = DbManager.find_records(
        schemas.SyncChannel,
        [schemas.SyncChannel.channel_id == channel_id, schemas.SyncChannel.deleted_at.is_(None)],
    )

    if len(sync_channel_records) > 0 and not already_warning:
        blocks.append(
            orm.SectionBlock(
                action=constants.WARNING_BLOCK,
                label=":warning: :warning: This Channel is already part of a Sync! Please choose another Channel.",
            ).as_form_field()
        )
        helpers.update_modal(
            blocks=blocks,
            client=client,
            view_id=view_id,
            title_text="Join Sync",
            callback_id=actions.CONFIG_JOIN_SYNC_SUBMIT,
        )
    elif len(sync_channel_records) == 0 and already_warning:
        blocks = [block for block in blocks if block["block_id"] != constants.WARNING_BLOCK]
        helpers.update_modal(
            blocks=blocks,
            client=client,
            view_id=view_id,
            title_text="Join Sync",
            callback_id=actions.CONFIG_JOIN_SYNC_SUBMIT,
        )


# ---------------------------------------------------------------------------
# Database Reset (gated by ENABLE_DB_RESET)
# ---------------------------------------------------------------------------


def handle_db_reset(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Open a confirmation modal warning the user before a full DB reset. Only for the workspace whose team_id matches ENABLE_DB_RESET."""
    team_id = helpers.safe_get(body, "team", "id") or helpers.safe_get(body, "view", "team_id")
    if not helpers.is_db_reset_visible_for_workspace(team_id):
        return

    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        return

    trigger_id = helpers.safe_get(body, "trigger_id")
    if not trigger_id:
        return

    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "title": {"type": "plain_text", "text": "Yikes! Reset Database?"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            ":rotating_light: *This Will Permanently Delete ALL Data!* :rotating_light:\n\n"
                            "Every Slack Install, Workspace Group, Channel Sync, and User Mapping, "
                            "in this database will be erased and the schema will be reinitialized.\n\n"
                            "*NOTE:* _All Slack Workspaces will need to reinstall the SyncBot app to get started again._\n\n"
                            "*This action cannot be undone! MAKE A BACKUP FIRST!*"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Confirm, Erase Everything!"},
                            "style": "danger",
                            "action_id": actions.CONFIG_DB_RESET_PROCEED,
                        },
                    ],
                },
            ],
        },
    )


def handle_db_reset_proceed(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Execute the database reset after user confirmed via modal. Only for the workspace whose team_id matches ENABLE_DB_RESET."""
    team_id = helpers.safe_get(body, "team", "id") or helpers.safe_get(body, "view", "team_id")
    if not helpers.is_db_reset_visible_for_workspace(team_id):
        return

    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        return

    # Update the modal to a "done" state so the user can close it (Slack only allows
    # closing modals via view_submission, not block_actions, so we replace the view).
    view_id = helpers.safe_get(body, "view", "id")
    if view_id:
        try:
            client.views_update(
                view_id=view_id,
                view={
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "Reset Complete"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":skull_and_crossbones: You can close this now.",
                            },
                        },
                    ],
                },
            )
        except Exception as e:
            _logger.warning("Failed to update modal after DB reset: %s", e)

    _logger.critical(
        "DB_RESET triggered by user %s — dropping database and reinitializing from init.sql",
        user_id,
    )

    from db import drop_and_init_db

    drop_and_init_db()

    helpers.clear_all_caches()

    if team_id and user_id:
        try:
            client.views_publish(
                user_id=user_id,
                view={
                    "type": "home",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "*Database Has Been Reset!*\nPlease reinstall SyncBot in your Workspace.",
                            },
                        }
                    ],
                },
            )
        except Exception as e:
            _logger.warning("Failed to publish post-reset Home tab: %s", e)
