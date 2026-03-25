"""Channel sync handlers — publish, unpublish, subscribe, pause, resume, stop."""

import contextlib
import logging
from datetime import UTC, datetime
from logging import Logger

from slack_sdk.web import WebClient

import builders
import helpers
from builders._common import _format_channel_ref, _get_group_members
from db import DbManager, schemas
from handlers._common import (
    _extract_team_id,
    _get_authorized_workspace,
    _get_selected_conversation_or_option,
    _get_selected_option_value,
    _parse_private_metadata,
    _sanitize_text,
)
from slack import actions, orm
from slack.blocks import context as block_context
from slack.blocks import section

_logger = logging.getLogger(__name__)

_MAX_PUBLISH_CHANNEL_OPTIONS = 100


def _get_publishable_channel_options(client: WebClient, workspace_id: int) -> list[orm.SelectorOption]:
    """Return selector options for channels that are not already published/synced in this workspace."""
    synced = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.workspace_id == workspace_id,
            schemas.SyncChannel.deleted_at.is_(None),
        ],
    )
    synced_ids = {c.channel_id for c in synced}

    options: list[orm.SelectorOption] = []
    cursor = ""
    try:
        while len(options) < _MAX_PUBLISH_CHANNEL_OPTIONS:
            resp = client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor or None,
            )
            chs = helpers.safe_get(resp, "channels") or []
            for slack_channel in chs:
                cid = slack_channel.get("id")
                if not cid or cid in synced_ids:
                    continue
                name = slack_channel.get("name") or cid
                label = f"#{name}"
                if len(label) > 75:
                    label = label[:72] + "..."
                options.append(orm.SelectorOption(name=label, value=cid))
                if len(options) >= _MAX_PUBLISH_CHANNEL_OPTIONS:
                    break
            cursor = helpers.safe_get(resp, "response_metadata", "next_cursor") or ""
            if not cursor:
                break
    except Exception as e:
        _logger.warning(f"_get_publishable_channel_options: {e}")

    return options


def _build_publish_step2(
    client: WebClient,
    group_id: int,
    sync_mode: str,
    other_members: list,
    workspace_id: int,
) -> orm.BlockView:
    """Build the step-2 modal blocks: channel picker (only unpublished channels) + optional target workspace."""
    modal_blocks: list[orm.BaseBlock] = []

    channel_options = _get_publishable_channel_options(client, workspace_id)
    if not channel_options:
        channel_options = [
            orm.SelectorOption(
                name="— No Channels available (all are already published or synced) —", value="__none__"
            ),
        ]
    modal_blocks.append(
        orm.InputBlock(
            label="Channel to Publish",
            action=actions.CONFIG_PUBLISH_CHANNEL_SELECT,
            element=orm.StaticSelectElement(
                placeholder="Select a Channel to publish",
                options=channel_options,
            ),
            optional=False,
        )
    )
    modal_blocks.append(block_context("Select a Channel from your Workspace to make available for Syncing."))

    if sync_mode == "direct" and other_members:
        ws_options: list[orm.SelectorOption] = []
        for other_member in other_members:
            other_workspace = helpers.get_workspace_by_id(other_member.workspace_id)
            name = (
                helpers.resolve_workspace_name(other_workspace)
                if other_workspace
                else f"Workspace {other_member.workspace_id}"
            )
            ws_options.append(orm.SelectorOption(name=name, value=str(other_member.workspace_id)))

        if ws_options:
            modal_blocks.append(
                orm.InputBlock(
                    label="Target Workspace",
                    action=actions.CONFIG_PUBLISH_DIRECT_TARGET,
                    element=orm.StaticSelectElement(
                        placeholder="Select target Workspace",
                        options=ws_options,
                    ),
                    optional=False,
                )
            )

    return orm.BlockView(blocks=modal_blocks)


def handle_publish_channel(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Open the publish-channel flow — always starts with step 1 (sync mode selection)."""
    auth_result = _get_authorized_workspace(body, client, context, "publish_channel")
    if not auth_result:
        return
    _, workspace_record = auth_result

    trigger_id = helpers.safe_get(body, "trigger_id")
    raw_group_id = helpers.safe_get(body, "actions", 0, "value")
    try:
        group_id = int(raw_group_id)
    except (TypeError, ValueError):
        _logger.warning(f"publish_channel: invalid group_id: {raw_group_id!r}")
        return

    mode_options = [
        orm.SelectorOption(
            name="Available to All Workspaces\nAny current or future Workspace Group Member can Sync.",
            value="group",
        ),
        orm.SelectorOption(
            name="Only with Specific Workspace\nChoose a specific Workspace Group Member to allow Syncing.",
            value="direct",
        ),
    ]
    step1_blocks: list[orm.BaseBlock] = [
        orm.InputBlock(
            label="Channel Sync Mode",
            action=actions.CONFIG_PUBLISH_SYNC_MODE,
            element=orm.RadioButtonsElement(
                initial_value="group",
                options=orm.as_selector_options(
                    [o.name for o in mode_options],
                    [o.value for o in mode_options],
                ),
            ),
            optional=False,
        ),
    ]
    orm.BlockView(blocks=step1_blocks).post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_PUBLISH_MODE_SUBMIT,
        title_text="Sync Channel",
        submit_button_text="Next",
        parent_metadata={"group_id": group_id, "workspace_id": workspace_record.id},
        new_or_add="new",
    )


def handle_publish_mode_submit_ack(
    body: dict,
    client: WebClient,
    context: dict,
) -> dict | None:
    """Ack phase for step 1: read sync mode and return ``response_action=update`` for step 2."""
    auth_result = _get_authorized_workspace(body, client, context, "publish_mode_submit")
    if not auth_result:
        return None
    _, workspace_record = auth_result

    metadata = _parse_private_metadata(body)
    group_id = metadata.get("group_id")
    if not group_id:
        raw_pm = helpers.safe_get(body, "view", "private_metadata") or ""
        _logger.warning(
            "publish_mode_submit: missing group_id in metadata",
            extra={
                "team_id": _extract_team_id(body),
                "workspace_id": metadata.get("workspace_id"),
                "private_metadata_len": len(raw_pm) if isinstance(raw_pm, str) else None,
            },
        )
        return None

    sync_mode = _get_selected_option_value(body, actions.CONFIG_PUBLISH_SYNC_MODE) or "group"

    group_members = _get_group_members(group_id)
    other_members = [
        member for member in group_members if member.workspace_id != workspace_record.id and member.workspace_id
    ]
    step2 = _build_publish_step2(client, group_id, sync_mode, other_members, workspace_record.id)
    updated_view = step2.as_ack_update(
        callback_id=actions.CONFIG_PUBLISH_CHANNEL_SUBMIT,
        title_text="Sync Channel",
        submit_button_text="Publish",
        parent_metadata={"group_id": group_id, "sync_mode": sync_mode},
    )
    return {"response_action": "update", "view": updated_view}


def handle_publish_channel_submit_ack(
    body: dict,
    client: WebClient,
    context: dict,
) -> dict | None:
    """Ack phase for publish: validate and close modal (errors) or empty ack (success)."""
    auth_result = _get_authorized_workspace(body, client, context, "publish_channel_submit")
    if not auth_result:
        return None
    _, workspace_record = auth_result

    metadata = _parse_private_metadata(body)
    group_id = metadata.get("group_id")

    if not group_id:
        _logger.warning("publish_channel_submit: missing group_id in metadata")
        return None

    sync_mode = metadata.get("sync_mode", "group")
    target_workspace_id = None
    selected_target = _get_selected_option_value(body, actions.CONFIG_PUBLISH_DIRECT_TARGET)
    if selected_target:
        with contextlib.suppress(TypeError, ValueError):
            target_workspace_id = int(selected_target)

    if sync_mode == "direct" and not target_workspace_id:
        sync_mode = "group"

    channel_id = _get_selected_conversation_or_option(body, actions.CONFIG_PUBLISH_CHANNEL_SELECT)

    if not channel_id or channel_id == "__none__":
        return {
            "response_action": "errors",
            "errors": {actions.CONFIG_PUBLISH_CHANNEL_SELECT: "Select a Channel to publish."},
        }

    existing = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.channel_id == channel_id,
            schemas.SyncChannel.workspace_id == workspace_record.id,
            schemas.SyncChannel.deleted_at.is_(None),
        ],
    )
    if existing:
        return {
            "response_action": "errors",
            "errors": {actions.CONFIG_PUBLISH_CHANNEL_SELECT: "This Channel is already being synced."},
        }

    return None


def handle_publish_channel_submit_work(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Lazy work phase: create Sync + SyncChannel after modal closed."""
    auth_result = _get_authorized_workspace(body, client, context, "publish_channel_submit")
    if not auth_result:
        return
    _, workspace_record = auth_result

    metadata = _parse_private_metadata(body)
    group_id = metadata.get("group_id")

    if not group_id:
        return

    sync_mode = metadata.get("sync_mode", "group")
    target_workspace_id = None
    selected_target = _get_selected_option_value(body, actions.CONFIG_PUBLISH_DIRECT_TARGET)
    if selected_target:
        with contextlib.suppress(TypeError, ValueError):
            target_workspace_id = int(selected_target)

    if sync_mode == "direct" and not target_workspace_id:
        sync_mode = "group"

    channel_id = _get_selected_conversation_or_option(body, actions.CONFIG_PUBLISH_CHANNEL_SELECT)

    if not channel_id or channel_id == "__none__":
        return

    existing = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.channel_id == channel_id,
            schemas.SyncChannel.workspace_id == workspace_record.id,
            schemas.SyncChannel.deleted_at.is_(None),
        ],
    )
    if existing:
        return

    try:
        conv_info = client.conversations_info(channel=channel_id)
        channel_name = helpers.safe_get(conv_info, "channel", "name") or channel_id
    except Exception as exc:
        _logger.debug(f"handle_publish_channel_submit_work: conversations_info failed for {channel_id}: {exc}")
        channel_name = channel_id

    try:
        client.conversations_join(channel=channel_id)

        sync_record = schemas.Sync(
            title=_sanitize_text(channel_name),
            description=None,
            group_id=group_id,
            sync_mode=sync_mode,
            target_workspace_id=target_workspace_id if sync_mode == "direct" else None,
            publisher_workspace_id=workspace_record.id,
        )
        DbManager.create_record(sync_record)

        sync_channel_record = schemas.SyncChannel(
            sync_id=sync_record.id,
            channel_id=channel_id,
            workspace_id=workspace_record.id,
            created_at=datetime.now(UTC),
        )
        DbManager.create_record(sync_channel_record)

        _logger.info(
            "channel_published",
            extra={
                "workspace_id": workspace_record.id,
                "channel_id": channel_id,
                "group_id": group_id,
                "sync_id": sync_record.id,
                "sync_mode": sync_mode,
            },
        )
    except Exception as e:
        _logger.error(f"Failed to publish channel {channel_id}: {e}")

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
    _refresh_group_member_homes(group_id, workspace_record.id, logger, context=context)


def handle_unpublish_channel(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Unpublish a channel: hard-delete the Sync record.

    DB cascades remove all ``SyncChannel`` and ``PostMeta`` rows.
    Only the original publisher can unpublish.
    """
    auth_result = _get_authorized_workspace(body, client, context, "unpublish_channel")
    if not auth_result:
        return
    user_id, workspace_record = auth_result

    admin_name, admin_label = helpers.format_admin_label(client, user_id, workspace_record)

    raw_value = helpers.safe_get(body, "actions", 0, "value")
    try:
        sync_id = int(raw_value)
    except (TypeError, ValueError):
        _logger.warning(f"Invalid sync_id for unpublish: {raw_value!r}")
        return

    sync_record = DbManager.get_record(schemas.Sync, id=sync_id)
    if not sync_record:
        return

    if workspace_record and sync_record.publisher_workspace_id != workspace_record.id:
        _logger.warning("unpublish_denied: not the publisher")
        return

    group_id = sync_record.group_id

    all_channels = DbManager.find_records(
        schemas.SyncChannel,
        [schemas.SyncChannel.sync_id == sync_id, schemas.SyncChannel.deleted_at.is_(None)],
    )

    for sync_channel in all_channels:
        try:
            member_ws = helpers.get_workspace_by_id(sync_channel.workspace_id)
            if member_ws and member_ws.bot_token:
                name = (
                    admin_name if workspace_record and sync_channel.workspace_id == workspace_record.id else admin_label
                )
                member_client = WebClient(token=helpers.decrypt_bot_token(member_ws.bot_token))
                helpers.notify_synced_channels(
                    member_client,
                    [sync_channel.channel_id],
                    f":octagonal_sign: *{name}* unpublished this Channel. Syncing is no longer available.",
                )
                member_client.conversations_leave(channel=sync_channel.channel_id)
        except Exception as e:
            _logger.warning(f"Failed to notify/leave channel {sync_channel.channel_id}: {e}")

    DbManager.delete_records(schemas.Sync, [schemas.Sync.id == sync_id])

    _logger.info(
        "channel_unpublished",
        extra={"sync_id": sync_id, "group_id": group_id},
    )

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
    if group_id:
        _refresh_group_member_homes(group_id, workspace_record.id if workspace_record else 0, logger, context=context)


def _toggle_sync_status(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
    *,
    action_prefix: str,
    target_status: str,
    emoji: str,
    verb: str,
    log_event: str,
) -> None:
    """Shared logic for pausing or resuming a channel sync. Only the current workspace's channel is toggled."""
    action_id = helpers.safe_get(body, "actions", 0, "action_id") or ""
    sync_id_str = action_id.replace(action_prefix + "_", "")

    try:
        sync_id = int(sync_id_str)
    except (TypeError, ValueError):
        _logger.warning(f"{log_event}_invalid_id", extra={"action_id": action_id})
        return

    auth_result = _get_authorized_workspace(body, client, context, log_event)
    if not auth_result:
        return
    user_id, workspace_record = auth_result
    admin_name, admin_label = helpers.format_admin_label(client, user_id, workspace_record)

    all_channels = DbManager.find_records(
        schemas.SyncChannel,
        [schemas.SyncChannel.sync_id == sync_id, schemas.SyncChannel.deleted_at.is_(None)],
    )
    my_sync_channel = next(
        (c for c in all_channels if c.workspace_id == workspace_record.id),
        None,
    )
    if not my_sync_channel:
        _logger.warning(
            f"{log_event}_no_channel_for_workspace", extra={"sync_id": sync_id, "workspace_id": workspace_record.id}
        )
        return

    DbManager.update_records(
        schemas.SyncChannel,
        [schemas.SyncChannel.id == my_sync_channel.id],
        {schemas.SyncChannel.status: target_status},
    )
    helpers._cache_delete(f"sync_list:{my_sync_channel.channel_id}")

    ws_cache: dict[int, schemas.Workspace | None] = {}
    for sync_channel in [my_sync_channel]:
        try:
            channel_ws = ws_cache.get(sync_channel.workspace_id) or helpers.get_workspace_by_id(
                sync_channel.workspace_id
            )
            ws_cache[sync_channel.workspace_id] = channel_ws
            if channel_ws and channel_ws.bot_token:
                ws_client = WebClient(token=helpers.decrypt_bot_token(channel_ws.bot_token))
                if target_status == "active":
                    with contextlib.suppress(Exception):
                        ws_client.conversations_join(channel=sync_channel.channel_id)
                name = (
                    admin_name if workspace_record and sync_channel.workspace_id == workspace_record.id else admin_label
                )
                other_channels = [c for c in all_channels if c.workspace_id != sync_channel.workspace_id]
                if other_channels:
                    other_ws = ws_cache.get(other_channels[0].workspace_id) or helpers.get_workspace_by_id(
                        other_channels[0].workspace_id
                    )
                    ws_cache[other_channels[0].workspace_id] = other_ws
                    channel_ref = helpers.resolve_channel_name(other_channels[0].channel_id, other_ws)
                    msg = f":{emoji}: *{name}* {verb} syncing with *{channel_ref}*."
                else:
                    msg = f":{emoji}: *{name}* {verb} channel syncing."
                helpers.notify_synced_channels(ws_client, [sync_channel.channel_id], msg)
        except Exception as e:
            _logger.warning(f"Failed to notify channel {sync_channel.channel_id} about {verb}: {e}")

    _logger.info(log_event, extra={"sync_id": sync_id, "sync_channel_id": my_sync_channel.id})

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
    sync_record = DbManager.get_record(schemas.Sync, id=sync_id)
    if sync_record and sync_record.group_id:
        _refresh_group_member_homes(
            sync_record.group_id, workspace_record.id if workspace_record else 0, logger, context=context
        )


def handle_pause_sync(body: dict, client: WebClient, logger: Logger, context: dict) -> None:
    """Pause an active channel sync."""
    _toggle_sync_status(
        body,
        client,
        logger,
        context,
        action_prefix=actions.CONFIG_PAUSE_SYNC,
        target_status="paused",
        emoji="double_vertical_bar",
        verb="paused",
        log_event="sync_paused",
    )


def handle_resume_sync(body: dict, client: WebClient, logger: Logger, context: dict) -> None:
    """Resume a paused channel sync."""
    _toggle_sync_status(
        body,
        client,
        logger,
        context,
        action_prefix=actions.CONFIG_RESUME_SYNC,
        target_status="active",
        emoji="arrow_forward",
        verb="resumed",
        log_event="sync_resumed",
    )


def handle_stop_sync(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Show a confirmation modal before stopping a channel sync."""
    action_id = helpers.safe_get(body, "actions", 0, "action_id") or ""
    sync_id_str = action_id.replace(actions.CONFIG_STOP_SYNC + "_", "")

    try:
        sync_id = int(sync_id_str)
    except (TypeError, ValueError):
        _logger.warning("stop_sync_invalid_id", extra={"action_id": action_id})
        return

    trigger_id = helpers.safe_get(body, "trigger_id")
    if not trigger_id:
        return

    confirm_form = orm.BlockView(
        blocks=[
            section(
                ":warning: *Are you sure you want to stop syncing this Channel?*\n\n"
                "This will:\n"
                "\u2022 Remove your Workspace's Sync history for this Channel\n"
                "\u2022 Remove this Channel from the active Sync\n"
                "\u2022 Other Workspaces in the Sync will continue uninterrupted\n\n"
                "_No messages will be deleted from any Channel — only SyncBot's tracking history for your Workspace is removed._"
            ),
        ]
    )

    confirm_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_STOP_SYNC_CONFIRM,
        title_text="Stop Syncing",
        submit_button_text="Stop Syncing",
        close_button_text="Cancel",
        parent_metadata={"sync_id": sync_id},
    )


def handle_stop_sync_confirm(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Execute channel sync stop after confirmation.

    Removes only this workspace's ``SyncChannel`` and its ``PostMeta``.
    Other workspaces' data and the Sync record remain intact.
    """
    auth_result = _get_authorized_workspace(body, client, context, "stop_sync_confirm")
    if not auth_result:
        return
    user_id, workspace_record = auth_result

    meta = _parse_private_metadata(body)
    sync_id = meta.get("sync_id")
    if not sync_id:
        _logger.warning("stop_sync_confirm: missing sync_id in metadata")
        return

    admin_name, admin_label = helpers.format_admin_label(client, user_id, workspace_record)

    all_channels = DbManager.find_records(
        schemas.SyncChannel,
        [schemas.SyncChannel.sync_id == sync_id, schemas.SyncChannel.deleted_at.is_(None)],
    )

    my_channel = next((c for c in all_channels if c.workspace_id == workspace_record.id), None)
    other_channels = [c for c in all_channels if c.workspace_id != workspace_record.id]

    for sync_channel in all_channels:
        try:
            channel_ws = helpers.get_workspace_by_id(sync_channel.workspace_id)
            if channel_ws and channel_ws.bot_token:
                if sync_channel.workspace_id == workspace_record.id and other_channels:
                    other_ws = helpers.get_workspace_by_id(other_channels[0].workspace_id)
                    channel_ref = helpers.resolve_channel_name(other_channels[0].channel_id, other_ws)
                    msg = f":octagonal_sign: *{admin_name}* stopped syncing with *{channel_ref}*."
                elif sync_channel.workspace_id != workspace_record.id:
                    my_ref = (
                        helpers.resolve_channel_name(my_channel.channel_id, workspace_record)
                        if my_channel
                        else "the other Workspace"
                    )
                    msg = f":octagonal_sign: *{admin_label}* stopped syncing with *{my_ref}*."
                else:
                    msg = f":octagonal_sign: *{admin_name}* stopped Channel Syncing."
                ws_client = WebClient(token=helpers.decrypt_bot_token(channel_ws.bot_token))
                helpers.notify_synced_channels(ws_client, [sync_channel.channel_id], msg)
        except Exception as e:
            _logger.warning(f"Failed to notify channel {sync_channel.channel_id}: {e}")

    if my_channel:
        DbManager.delete_records(schemas.PostMeta, [schemas.PostMeta.sync_channel_id == my_channel.id])
        DbManager.delete_records(schemas.SyncChannel, [schemas.SyncChannel.id == my_channel.id])
        try:
            client.conversations_leave(channel=my_channel.channel_id)
        except Exception as e:
            _logger.warning(f"Failed to leave channel {my_channel.channel_id}: {e}")

    _logger.info(
        "sync_stopped",
        extra={
            "sync_id": sync_id,
            "workspace_id": workspace_record.id,
            "channel_id": my_channel.channel_id if my_channel else None,
        },
    )

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
    sync_record = DbManager.get_record(schemas.Sync, id=sync_id)
    if sync_record and sync_record.group_id:
        _refresh_group_member_homes(sync_record.group_id, workspace_record.id, logger, context=context)


def handle_subscribe_channel(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Push the channel picker modal for subscribing to an available channel.

    The channel list only shows channels that are not already in any sync
    (excluding already-synced and published-but-unsubscribed channels).
    """
    auth_result = _get_authorized_workspace(body, client, context, "subscribe_channel")
    if not auth_result:
        return
    _, workspace_record = auth_result

    trigger_id = helpers.safe_get(body, "trigger_id")
    sync_id = helpers.safe_get(body, "actions", 0, "value")

    blocks: list[orm.BaseBlock] = []

    if sync_id:
        publisher_channels = DbManager.find_records(
            schemas.SyncChannel,
            [schemas.SyncChannel.sync_id == int(sync_id), schemas.SyncChannel.deleted_at.is_(None)],
        )
        if publisher_channels:
            pub_ch = publisher_channels[0]
            pub_ws = helpers.get_workspace_by_id(pub_ch.workspace_id)
            ch_ref = _format_channel_ref(pub_ch.channel_id, pub_ws, is_local=False)
            blocks.append(section(f"Syncing with: {ch_ref}"))

    channel_options = _get_publishable_channel_options(client, workspace_record.id)
    if not channel_options:
        channel_options = [
            orm.SelectorOption(
                name="— No Channels available to Sync in this Workspace. —", value="__none__"
            ),
        ]
    blocks.append(
        orm.InputBlock(
            label="Channel for Sync",
            action=actions.CONFIG_SUBSCRIBE_CHANNEL_SELECT,
            element=orm.StaticSelectElement(
                placeholder="Select a Channel to Sync with.",
                options=channel_options,
            ),
            optional=False,
        )
    )
    blocks.append(block_context("Choose a Channel in your Workspace to start Syncing."))

    orm.BlockView(blocks=blocks).post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_SUBSCRIBE_CHANNEL_SUBMIT,
        title_text="Sync Channel",
        submit_button_text="Sync Channel",
        parent_metadata={"sync_id": int(sync_id)} if sync_id else None,
        new_or_add="new",
    )


def handle_subscribe_channel_submit(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Subscribe to an available channel sync: create SyncChannel for subscriber."""
    auth_result = _get_authorized_workspace(body, client, context, "subscribe_channel_submit")
    if not auth_result:
        return
    user_id, workspace_record = auth_result

    metadata = _parse_private_metadata(body)
    sync_id = metadata.get("sync_id")

    if not sync_id:
        _logger.warning("subscribe_channel_submit: missing sync_id")
        return

    channel_id = _get_selected_conversation_or_option(body, actions.CONFIG_SUBSCRIBE_CHANNEL_SELECT)

    if not channel_id or channel_id == "__none__":
        _logger.warning("subscribe_channel_submit: no channel selected")
        return

    sync_record = DbManager.get_record(schemas.Sync, id=sync_id)
    if not sync_record:
        return

    group_id = sync_record.group_id
    acting_user_id = helpers.safe_get(body, "user", "id") or user_id
    admin_name, admin_label = helpers.format_admin_label(client, acting_user_id, workspace_record)

    publisher_channels: list = []
    try:
        client.conversations_join(channel=channel_id)

        sync_channel_record = schemas.SyncChannel(
            sync_id=sync_id,
            channel_id=channel_id,
            workspace_id=workspace_record.id,
            created_at=datetime.now(UTC),
        )
        DbManager.create_record(sync_channel_record)

        publisher_channels = DbManager.find_records(
            schemas.SyncChannel,
            [
                schemas.SyncChannel.sync_id == sync_id,
                schemas.SyncChannel.deleted_at.is_(None),
                schemas.SyncChannel.workspace_id != workspace_record.id,
            ],
        )

        try:
            if publisher_channels:
                pub_ch = publisher_channels[0]
                pub_ws = helpers.get_workspace_by_id(pub_ch.workspace_id)
                channel_ref = helpers.resolve_channel_name(pub_ch.channel_id, pub_ws)
            else:
                channel_ref = sync_record.title or "the other Channel"
            client.chat_postMessage(
                channel=channel_id,
                text=f":arrows_counterclockwise: *{admin_name}* started syncing this Channel with *{channel_ref}*. Messages will be shared automatically.",
            )
        except Exception as exc:
            _logger.debug(f"subscribe_channel: failed to notify subscriber channel {channel_id}: {exc}")

        local_ref = helpers.resolve_channel_name(channel_id, workspace_record)
        for pub_ch in publisher_channels:
            try:
                pub_ws = helpers.get_workspace_by_id(pub_ch.workspace_id)
                if pub_ws:
                    pub_client = WebClient(token=helpers.decrypt_bot_token(pub_ws.bot_token))
                    pub_client.chat_postMessage(
                        channel=pub_ch.channel_id,
                        text=f":arrows_counterclockwise: *{admin_label}* started syncing *{local_ref}* with this Channel. Messages will be shared automatically.",
                    )
            except Exception as exc:
                _logger.debug(f"subscribe_channel: failed to notify publisher channel {pub_ch.channel_id}: {exc}")

        _logger.info(
            "channel_subscribed",
            extra={
                "workspace_id": workspace_record.id,
                "channel_id": channel_id,
                "sync_id": sync_id,
                "group_id": group_id,
            },
        )
    except Exception as e:
        _logger.error(f"Failed to subscribe to channel sync {sync_id}: {e}")

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
    if group_id:
        _refresh_group_member_homes(group_id, workspace_record.id, logger, context=context)


def _refresh_group_member_homes(
    group_id: int,
    exclude_workspace_id: int,
    logger: Logger,
    context: dict | None = None,
) -> None:
    """Refresh the Home tab for all group members except the acting workspace.

    Uses context=None when refreshing other members so admin lookups are always
    fresh for each workspace (avoids request-scoped cache from the acting ws).
    """
    members = _get_group_members(group_id)
    refreshed: set[int] = set()
    for member in members:
        if not member.workspace_id or member.workspace_id == exclude_workspace_id or member.workspace_id in refreshed:
            continue
        member_ws = helpers.get_workspace_by_id(member.workspace_id, context=context)
        if member_ws:
            builders.refresh_home_tab_for_workspace(member_ws, logger, context=None)
            refreshed.add(member.workspace_id)
