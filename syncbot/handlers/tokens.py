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
    Notifies partner workspaces in shared groups via admin DMs and channel messages.
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
    for ch in my_channels:
        DbManager.update_records(
            schemas.SyncChannel,
            [schemas.SyncChannel.id == ch.id],
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
        for m in group_members:
            if not m.workspace_id or m.workspace_id in notified_ws:
                continue
            partner = helpers.get_workspace_by_id(m.workspace_id)
            if not partner or not partner.bot_token or partner.deleted_at:
                continue
            notified_ws.add(m.workspace_id)

            try:
                partner_client = WebClient(token=helpers.decrypt_bot_token(partner.bot_token))

                helpers.notify_admins_dm(
                    partner_client,
                    f":double_vertical_bar: *{ws_name}* has uninstalled SyncBot. "
                    f"Syncing has been paused. If they reinstall within {retention_days} days, "
                    "syncing will resume automatically.",
                )

                partner_channel_ids = []
                for ch in my_channels:
                    sibling_channels = DbManager.find_records(
                        schemas.SyncChannel,
                        [
                            schemas.SyncChannel.sync_id == ch.sync_id,
                            schemas.SyncChannel.workspace_id == m.workspace_id,
                            schemas.SyncChannel.deleted_at.is_(None),
                        ],
                    )
                    for sc in sibling_channels:
                        partner_channel_ids.append(sc.channel_id)

                if partner_channel_ids:
                    helpers.notify_synced_channels(
                        partner_client,
                        partner_channel_ids,
                        f":double_vertical_bar: Syncing with *{ws_name}* has been paused because they uninstalled the app.",
                    )
            except Exception as e:
                _logger.warning(f"handle_tokens_revoked: failed to notify partner {m.workspace_id}: {e}")

    _logger.info(
        "workspace_soft_deleted",
        extra={
            "workspace_id": workspace_record.id,
            "team_id": team_id,
            "memberships_paused": len(active_memberships),
            "channels_paused": len(my_channels),
        },
    )
