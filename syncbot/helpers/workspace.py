"""Workspace record management and name resolution."""

import logging

from slack_sdk import WebClient

from db import DbManager, schemas
from helpers._cache import _cache_get, _cache_set
from helpers.core import safe_get
from helpers.encryption import decrypt_bot_token, encrypt_bot_token

_logger = logging.getLogger(__name__)


def get_sync_list(team_id: str, channel_id: str) -> list[tuple[schemas.SyncChannel, schemas.Workspace]]:
    """Return every (SyncChannel, Workspace) pair that shares a sync with *channel_id*."""
    cache_key = f"sync_list:{channel_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    sync_channel_record = DbManager.find_records(
        schemas.SyncChannel,
        [
            schemas.SyncChannel.channel_id == channel_id,
            schemas.SyncChannel.deleted_at.is_(None),
            schemas.SyncChannel.status == "active",
        ],
    )
    if sync_channel_record:
        sync_channels = DbManager.find_join_records2(
            left_cls=schemas.SyncChannel,
            right_cls=schemas.Workspace,
            filters=[
                schemas.SyncChannel.sync_id == sync_channel_record[0].sync_id,
                schemas.SyncChannel.deleted_at.is_(None),
                schemas.SyncChannel.status == "active",
            ],
        )
    else:
        sync_channels = []

    _cache_set(cache_key, sync_channels)
    return sync_channels


def get_federated_workspace(group_id: int, workspace_id: int) -> schemas.FederatedWorkspace | None:
    """Return the federated workspace for a group membership, if one exists."""
    members = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.group_id == group_id,
            schemas.WorkspaceGroupMember.workspace_id == workspace_id,
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )
    if not members or not members[0].federated_workspace_id:
        return None

    fed_ws = DbManager.get_record(schemas.FederatedWorkspace, id=members[0].federated_workspace_id)
    if not fed_ws or fed_ws.status != "active":
        return None

    return fed_ws


def get_federated_workspace_for_sync(sync_id: int) -> schemas.FederatedWorkspace | None:
    """Return the federated workspace for a sync, checking group membership."""
    sync = DbManager.get_record(schemas.Sync, id=sync_id)
    if not sync or not sync.group_id:
        return None

    fed_members = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.group_id == sync.group_id,
            schemas.WorkspaceGroupMember.federated_workspace_id.isnot(None),
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
            schemas.WorkspaceGroupMember.status == "active",
        ],
    )
    if not fed_members:
        return None

    fed_ws = DbManager.get_record(schemas.FederatedWorkspace, id=fed_members[0].federated_workspace_id)
    if not fed_ws or fed_ws.status != "active":
        return None

    return fed_ws


def get_workspace_record(team_id: str, body: dict, context: dict, client: WebClient) -> schemas.Workspace:
    """Fetch or create the Workspace record for a Slack workspace."""
    workspace_record: schemas.Workspace = DbManager.get_record(schemas.Workspace, id=team_id)
    team_domain = safe_get(body, "team", "domain")

    if not workspace_record:
        try:
            team_info = client.team_info()
            ws_name = team_info["team"]["name"]
        except Exception as exc:
            _logger.debug(f"get_workspace: team_info failed, falling back to domain: {exc}")
            ws_name = team_domain
        workspace_record: schemas.Workspace = DbManager.create_record(
            schemas.Workspace(
                team_id=team_id,
                workspace_name=ws_name,
                bot_token=encrypt_bot_token(context["bot_token"]),
            )
        )
    elif workspace_record.deleted_at is not None:
        workspace_record = _restore_workspace(workspace_record, context, client)
    else:
        _maybe_refresh_bot_token(workspace_record, context)
        _maybe_refresh_workspace_name(workspace_record, client)

    return workspace_record


def _maybe_refresh_bot_token(workspace_record: schemas.Workspace, context: dict) -> None:
    """Update the stored bot token if the OAuth flow provided a newer one."""
    new_token = safe_get(context, "bot_token")
    if not new_token:
        return

    encrypted_new = encrypt_bot_token(new_token)
    if encrypted_new != workspace_record.bot_token:
        DbManager.update_records(
            schemas.Workspace,
            [schemas.Workspace.id == workspace_record.id],
            {schemas.Workspace.bot_token: encrypted_new},
        )
        workspace_record.bot_token = encrypted_new
        _logger.info(
            "bot_token_refreshed",
            extra={"workspace_id": workspace_record.id, "team_id": workspace_record.team_id},
        )


def _maybe_refresh_workspace_name(workspace_record: schemas.Workspace, client: WebClient) -> None:
    """Refresh the stored workspace name from the Slack API (at most once per day)."""
    cache_key = f"ws_name_refresh:{workspace_record.id}"
    if _cache_get(cache_key):
        return

    _cache_set(cache_key, True, ttl=86400)

    try:
        team_info = client.team_info()
        current_name = team_info["team"]["name"]
    except Exception as exc:
        _logger.debug(f"_maybe_refresh_workspace_name: team_info call failed: {exc}")
        return

    if current_name and current_name != workspace_record.workspace_name:
        DbManager.update_records(
            schemas.Workspace,
            [schemas.Workspace.id == workspace_record.id],
            {schemas.Workspace.workspace_name: current_name},
        )
        workspace_record.workspace_name = current_name
        _logger.info(
            "workspace_name_refreshed",
            extra={"workspace_id": workspace_record.id, "new_name": current_name},
        )


def _restore_workspace(
    workspace_record: schemas.Workspace,
    context: dict,
    client: WebClient,
) -> schemas.Workspace:
    """Restore a soft-deleted workspace and notify group members."""
    from helpers.notifications import notify_admins_dm, notify_synced_channels

    ws_name = resolve_workspace_name(workspace_record)

    new_token = safe_get(context, "bot_token")
    update_fields = {schemas.Workspace.deleted_at: None}
    if new_token:
        update_fields[schemas.Workspace.bot_token] = encrypt_bot_token(new_token)
    DbManager.update_records(
        schemas.Workspace,
        [schemas.Workspace.id == workspace_record.id],
        update_fields,
    )

    workspace_record = DbManager.get_record(schemas.Workspace, id=workspace_record.team_id)

    soft_deleted_memberships = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.workspace_id == workspace_record.id,
            schemas.WorkspaceGroupMember.deleted_at.isnot(None),
            schemas.WorkspaceGroupMember.status == "active",
        ],
    )

    restored_group_ids: set[int] = set()
    for membership in soft_deleted_memberships:
        group = DbManager.get_record(schemas.WorkspaceGroup, id=membership.group_id)
        if not group or group.status != "active":
            continue

        DbManager.update_records(
            schemas.WorkspaceGroupMember,
            [schemas.WorkspaceGroupMember.id == membership.id],
            {schemas.WorkspaceGroupMember.deleted_at: None},
        )
        restored_group_ids.add(membership.group_id)

    if restored_group_ids:
        my_soft_channels = DbManager.find_records(
            schemas.SyncChannel,
            [
                schemas.SyncChannel.workspace_id == workspace_record.id,
                schemas.SyncChannel.deleted_at.isnot(None),
            ],
        )
        for sync_channel in my_soft_channels:
            sync = DbManager.get_record(schemas.Sync, id=sync_channel.sync_id)
            if sync and sync.group_id in restored_group_ids:
                DbManager.update_records(
                    schemas.SyncChannel,
                    [schemas.SyncChannel.id == sync_channel.id],
                    {schemas.SyncChannel.deleted_at: None, schemas.SyncChannel.status: "active"},
                )

    notified_ws: set[int] = set()
    for group_id in restored_group_ids:
        members = DbManager.find_records(
            schemas.WorkspaceGroupMember,
            [
                schemas.WorkspaceGroupMember.group_id == group_id,
                schemas.WorkspaceGroupMember.status == "active",
                schemas.WorkspaceGroupMember.deleted_at.is_(None),
                schemas.WorkspaceGroupMember.workspace_id != workspace_record.id,
            ],
        )
        for m in members:
            if not m.workspace_id or m.workspace_id in notified_ws:
                continue
            member_ws = get_workspace_by_id(m.workspace_id)
            if not member_ws or not member_ws.bot_token or member_ws.deleted_at is not None:
                continue
            notified_ws.add(m.workspace_id)
            try:
                member_client = WebClient(token=decrypt_bot_token(member_ws.bot_token))
                notify_admins_dm(
                    member_client,
                    f":arrow_forward: *{ws_name}* has been restored. Group syncing will resume.",
                )

                syncs_in_group = DbManager.find_records(
                    schemas.Sync, [schemas.Sync.group_id == group_id],
                )
                other_channel_ids = []
                for sync in syncs_in_group:
                    other_sync_channels = DbManager.find_records(
                        schemas.SyncChannel,
                        [
                            schemas.SyncChannel.sync_id == sync.id,
                            schemas.SyncChannel.workspace_id == m.workspace_id,
                            schemas.SyncChannel.deleted_at.is_(None),
                        ],
                    )
                    for sync_channel in other_sync_channels:
                        other_channel_ids.append(sync_channel.channel_id)
                if other_channel_ids:
                    notify_synced_channels(
                        member_client,
                        other_channel_ids,
                        f":arrow_forward: Syncing with *{ws_name}* has been resumed.",
                    )
            except Exception as e:
                _logger.warning(f"_restore_workspace: failed to notify member {m.workspace_id}: {e}")

    _logger.info(
        "workspace_restored",
        extra={
            "workspace_id": workspace_record.id,
            "groups_restored": len(restored_group_ids),
        },
    )

    return workspace_record


def get_workspace_by_id(workspace_id: int, context: dict | None = None) -> schemas.Workspace | None:
    """Look up a workspace by its integer primary-key ``id`` column.

    If *context* is provided, uses request-scoped cache to avoid repeated DB
    lookups for the same workspace_id within one request.
    """
    if context is not None:
        cache = context.setdefault("_workspace_by_id", {})
        if workspace_id in cache:
            return cache[workspace_id]
    rows = DbManager.find_records(schemas.Workspace, [schemas.Workspace.id == workspace_id])
    result = rows[0] if rows else None
    if context is not None:
        context.setdefault("_workspace_by_id", {})[workspace_id] = result
    return result


def get_groups_for_workspace(workspace_id: int) -> list[schemas.WorkspaceGroup]:
    """Return all active groups the workspace belongs to."""
    members = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.workspace_id == workspace_id,
            schemas.WorkspaceGroupMember.status == "active",
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )
    groups: list[schemas.WorkspaceGroup] = []
    for m in members:
        g = DbManager.get_record(schemas.WorkspaceGroup, id=m.group_id)
        if g and g.status == "active":
            groups.append(g)
    return groups


def get_group_members(group_id: int) -> list[schemas.WorkspaceGroupMember]:
    """Return all active members of a group."""
    return DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.group_id == group_id,
            schemas.WorkspaceGroupMember.status == "active",
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )


def resolve_workspace_name(workspace: schemas.Workspace) -> str:
    """Return a human-readable name for a workspace."""
    if workspace.workspace_name:
        return workspace.workspace_name

    if workspace.bot_token:
        try:
            ws_client = WebClient(token=decrypt_bot_token(workspace.bot_token))
            team_info = ws_client.team_info()
            name = safe_get(team_info, "team", "name")
            if name:
                DbManager.update_records(
                    schemas.Workspace,
                    [schemas.Workspace.id == workspace.id],
                    {schemas.Workspace.workspace_name: name},
                )
                workspace.workspace_name = name
                return name
        except Exception as exc:
            # Name lookup is best-effort; falling back to team_id keeps UI usable
            # even when Slack API calls fail intermittently.
            _logger.debug(
                "resolve_workspace_name_failed",
                extra={"workspace_id": workspace.id, "team_id": workspace.team_id, "error": str(exc)},
            )

    return workspace.team_id or f"Workspace {workspace.id}"


def resolve_channel_name(channel_id: str, workspace=None) -> str:
    """Resolve a channel ID to a human-readable name."""
    if not channel_id:
        return channel_id

    cache_key = f"chan_name:{channel_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    ch_name = channel_id
    ws_name = None

    if workspace and hasattr(workspace, "bot_token") and workspace.bot_token:
        ws_name = getattr(workspace, "workspace_name", None)
        try:
            ws_client = WebClient(token=decrypt_bot_token(workspace.bot_token))
            info = ws_client.conversations_info(channel=channel_id)
            ch_name = safe_get(info, "channel", "name") or channel_id
        except Exception as exc:
            _logger.debug(f"resolve_channel_name: conversations_info failed for {channel_id}: {exc}")

    if ws_name:
        result = f"#{ch_name} ({ws_name})"
    else:
        result = f"#{ch_name}"

    if ch_name != channel_id:
        _cache_set(cache_key, result, ttl=3600)
    return result
