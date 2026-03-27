"""Token revocation handler."""

import logging
from datetime import UTC, datetime
from logging import Logger

from slack_sdk.web import WebClient

import constants
import helpers
from db import DbManager, schemas

_logger = logging.getLogger(__name__)


def handle_tokens_revoked(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handle ``tokens_revoked`` event: a workspace uninstalled the app.

    Soft-deletes the workspace, its group memberships, and its sync channels.
    Notifies other group member workspaces via admin DMs and channel messages.
    """
    team_id = helpers.safe_get(body, "team_id")
    if not team_id:
        _logger.warning("handle_tokens_revoked: missing team_id")
        return

    workspace_record = DbManager.get_record(schemas.Workspace, team_id=team_id)
    if not workspace_record:
        _logger.warning("handle_tokens_revoked: unknown workspace", extra={"team_id": team_id})
        return

    now = datetime.now(UTC)
    ws_name = helpers.resolve_workspace_name(workspace_record)
    retention_days = constants.SOFT_DELETE_RETENTION_DAYS

    DbManager.update_records(
        schemas.Workspace,
        [schemas.Workspace.id == workspace_record.id],
        {schemas.Workspace.deleted_at: now},
    )

    active_memberships = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.workspace_id == workspace_record.id,
            schemas.WorkspaceGroupMember.status == "active",
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )

    for membership in active_memberships:
        DbManager.update_records(
            schemas.WorkspaceGroupMember,
            [schemas.WorkspaceGroupMember.id == membership.id],
            {schemas.WorkspaceGroupMember.deleted_at: now},
        )

    my_channels = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.workspace_id == workspace_record.id,
            schemas.SyncChannel.deleted_at.is_(None),
        ],
    )
    for sync_channel in my_channels:
        DbManager.update_records(
            schemas.SyncChannel,
            [schemas.SyncChannel.id == sync_channel.id],
            {schemas.SyncChannel.deleted_at: now, schemas.SyncChannel.status: "paused"},
        )

    notified_ws: set[int] = set()
    for membership in active_memberships:
        group_members = DbManager.find_records(
            schemas.WorkspaceGroupMember,
            [
                schemas.WorkspaceGroupMember.group_id == membership.group_id,
                schemas.WorkspaceGroupMember.workspace_id != workspace_record.id,
                schemas.WorkspaceGroupMember.status == "active",
                schemas.WorkspaceGroupMember.deleted_at.is_(None),
            ],
        )
        for member in group_members:
            if not member.workspace_id or member.workspace_id in notified_ws:
                continue
            member_ws = helpers.get_workspace_by_id(member.workspace_id)
            if not member_ws or not member_ws.bot_token or member_ws.deleted_at:
                continue
            notified_ws.add(member.workspace_id)

            try:
                member_client = WebClient(token=helpers.decrypt_bot_token(member_ws.bot_token))

                helpers.notify_admins_dm(
                    member_client,
                    f":double_vertical_bar: *{ws_name}* has uninstalled SyncBot. "
                    f"Syncing has been paused. If they reinstall within {retention_days} days, "
                    "Syncing will resume automatically.",
                )

                member_channel_ids = []
                for sync_channel in my_channels:
                    sibling_channels = DbManager.find_records(
                        schemas.SyncChannel,
                        [
                            schemas.SyncChannel.sync_id == sync_channel.sync_id,
                            schemas.SyncChannel.workspace_id == member.workspace_id,
                            schemas.SyncChannel.deleted_at.is_(None),
                        ],
                    )
                    for sibling in sibling_channels:
                        member_channel_ids.append(sibling.channel_id)

                if member_channel_ids:
                    helpers.notify_synced_channels(
                        member_client,
                        member_channel_ids,
                        f":double_vertical_bar: Syncing with *{ws_name}* has been paused because they uninstalled the app.",
                    )
            except Exception as e:
                _logger.warning(f"handle_tokens_revoked: failed to notify member {member.workspace_id}: {e}")

    _logger.info(
        "workspace_soft_deleted",
        extra={
            "workspace_id": workspace_record.id,
            "team_id": team_id,
            "memberships_paused": len(active_memberships),
            "channels_paused": len(my_channels),
        },
    )
