"""Group management handlers — leave group with confirmation."""

import logging
from logging import Logger

from slack_sdk.web import WebClient

import builders
import helpers
from db import DbManager, schemas
from slack import actions, orm

_logger = logging.getLogger(__name__)


def handle_leave_group(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Show a confirmation modal before leaving a workspace group."""
    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        _logger.warning("authorization_denied", extra={"user_id": user_id, "action": "leave_group"})
        return

    action_data = helpers.safe_get(body, "actions", 0) or {}
    action_id: str = action_data.get("action_id", "")
    group_id_str = action_id.replace(f"{actions.CONFIG_LEAVE_GROUP}_", "")

    try:
        group_id = int(group_id_str)
    except (TypeError, ValueError):
        _logger.warning("leave_group_invalid_id", extra={"action_id": action_id})
        return

    groups = DbManager.find_records(schemas.WorkspaceGroup, [schemas.WorkspaceGroup.id == group_id])
    if not groups:
        return
    group = groups[0]

    trigger_id = helpers.safe_get(body, "trigger_id")
    if not trigger_id:
        return

    confirm_form = orm.BlockView(
        blocks=[
            orm.SectionBlock(
                label=(
                    f":warning: *Are you sure you want to leave the group \"{group.name}\"?*\n\n"
                    "This will:\n"
                    "\u2022 Stop all channel syncs you have in this group\n"
                    "\u2022 Remove your synced message history from this group\n"
                    "\u2022 Remove your user mappings for this group\n\n"
                    "_Other members will continue syncing uninterrupted._"
                ),
            ),
        ]
    )

    confirm_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_LEAVE_GROUP_CONFIRM,
        title_text="Leave Group",
        submit_button_text="Leave",
        close_button_text="Cancel",
        parent_metadata={"group_id": group_id},
    )


def handle_leave_group_confirm(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Execute group departure after confirmation.

    - Soft-deletes the membership record
    - Removes this workspace's SyncChannels (and their PostMeta) for group syncs
    - Leaves all affected Slack channels
    - Cleans up syncs this workspace published (if all subscribers are gone)
    - Removes user mappings scoped to this group
    - Notifies remaining group members
    """
    from handlers._common import _parse_private_metadata

    user_id = helpers.get_user_id_from_body(body)
    if not user_id or not helpers.is_user_authorized(client, user_id):
        _logger.warning("authorization_denied", extra={"user_id": user_id, "action": "leave_group_confirm"})
        return

    meta = _parse_private_metadata(body)
    group_id = meta.get("group_id")
    if not group_id:
        _logger.warning("leave_group_confirm: missing group_id in metadata")
        return

    team_id = helpers.safe_get(body, "view", "team_id")
    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return

    groups = DbManager.find_records(schemas.WorkspaceGroup, [schemas.WorkspaceGroup.id == group_id])
    if not groups:
        return
    group = groups[0]

    members = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.group_id == group_id,
            schemas.WorkspaceGroupMember.workspace_id == workspace_record.id,
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )
    if not members:
        _logger.warning("leave_group_confirm: not a member", extra={"group_id": group_id})
        return

    acting_user_id = helpers.safe_get(body, "user", "id") or user_id
    _, admin_label = helpers.format_admin_label(client, acting_user_id, workspace_record)

    syncs_in_group = DbManager.find_records(schemas.Sync, [schemas.Sync.group_id == group_id])

    for sync in syncs_in_group:
        my_channels = DbManager.find_records(
            schemas.SyncChannel,
            [
                schemas.SyncChannel.sync_id == sync.id,
                schemas.SyncChannel.workspace_id == workspace_record.id,
                schemas.SyncChannel.deleted_at.is_(None),
            ],
        )
        for ch in my_channels:
            DbManager.delete_records(schemas.PostMeta, [schemas.PostMeta.sync_channel_id == ch.id])
            DbManager.delete_records(schemas.SyncChannel, [schemas.SyncChannel.id == ch.id])
            try:
                client.conversations_leave(channel=ch.channel_id)
            except Exception as e:
                _logger.warning(f"Failed to leave channel {ch.channel_id}: {e}")

        if sync.publisher_workspace_id == workspace_record.id:
            remaining = DbManager.find_records(
                schemas.SyncChannel,
                [schemas.SyncChannel.sync_id == sync.id, schemas.SyncChannel.deleted_at.is_(None)],
            )
            if not remaining:
                DbManager.delete_records(schemas.Sync, [schemas.Sync.id == sync.id])

    DbManager.delete_records(
        schemas.UserMapping,
        [
            schemas.UserMapping.group_id == group_id,
            (
                (schemas.UserMapping.source_workspace_id == workspace_record.id)
                | (schemas.UserMapping.target_workspace_id == workspace_record.id)
            ),
        ],
    )

    from datetime import UTC, datetime
    now = datetime.now(UTC)
    for m in members:
        DbManager.update_records(
            schemas.WorkspaceGroupMember,
            [schemas.WorkspaceGroupMember.id == m.id],
            {
                schemas.WorkspaceGroupMember.status: "inactive",
                schemas.WorkspaceGroupMember.deleted_at: now,
            },
        )

    _logger.info(
        "group_left",
        extra={"workspace_id": workspace_record.id, "group_id": group_id, "group_name": group.name},
    )

    remaining_members = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.group_id == group_id,
            schemas.WorkspaceGroupMember.status == "active",
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )

    if not remaining_members:
        DbManager.delete_records(schemas.WorkspaceGroup, [schemas.WorkspaceGroup.id == group_id])
        _logger.info("group_deleted_empty", extra={"group_id": group_id})
    else:
        for m in remaining_members:
            if not m.workspace_id:
                continue
            partner = helpers.get_workspace_by_id(m.workspace_id)
            if not partner or not partner.bot_token or partner.deleted_at:
                continue
            try:
                partner_client = WebClient(token=helpers.decrypt_bot_token(partner.bot_token))
                helpers.notify_admins_dm(
                    partner_client,
                    f":wave: *{admin_label}* left the group *{group.name}*.",
                )
                builders.refresh_home_tab_for_workspace(partner, logger, context=context)
            except Exception as e:
                _logger.warning(f"Failed to notify group member {m.workspace_id}: {e}")

    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
