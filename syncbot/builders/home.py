"""Home tab builder."""

import hashlib
import logging
from logging import Logger

from slack_sdk.web import WebClient

import constants
import helpers
from builders._common import (
    _get_group_members,
    _get_groups_for_workspace,
    _get_team_id,
    _get_user_id,
    _get_workspace_info,
)
from builders.channel_sync import _build_inline_channel_sync
from db import DbManager
from db.schemas import (
    FederatedWorkspace,
    Sync,
    SyncChannel,
    UserMapping,
    Workspace,
    WorkspaceGroup,
    WorkspaceGroupMember,
)
from slack import actions, orm
from slack.blocks import context as block_context
from slack.blocks import divider, header, section

_logger = logging.getLogger(__name__)


def _home_tab_content_hash(workspace_record: Workspace) -> str:
    """Compute a stable hash of the data that drives the Home tab.

    Includes groups, members, syncs, sync channels (id/workspace/status), mapped counts,
    pending invite ids, and reset-button visibility so the hash changes when anything
    visible on Home changes (including ENABLE_DB_RESET / team_id for the Reset button).
    """
    workspace_id = workspace_record.id
    workspace_name = (workspace_record.workspace_name or "") or ""
    reset_visible = helpers.is_db_reset_visible_for_workspace(workspace_record.team_id)
    my_groups = _get_groups_for_workspace(workspace_id)
    group_ids = sorted(g.id for g, _ in my_groups)
    pending_invites = DbManager.find_records(
        WorkspaceGroupMember,
        [
            WorkspaceGroupMember.workspace_id == workspace_id,
            WorkspaceGroupMember.status == "pending",
            WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )
    pending_ids = tuple(sorted(inv.id for inv in pending_invites))
    group_payload: list[tuple] = []
    for group, _ in my_groups:
        members = _get_group_members(group.id)
        syncs = DbManager.find_records(Sync, [Sync.group_id == group.id])
        sync_ids = [s.id for s in syncs]
        # Sync channels drive the "Synced Channels" section
        sync_channel_tuples: list[tuple] = []
        for sync in syncs:
            channels = DbManager.find_records(
                SyncChannel,
                [
                    SyncChannel.sync_id == sync.id,
                    SyncChannel.deleted_at.is_(None),
                ],
            )
            channel_sig = tuple(
                (sync_channel.workspace_id, sync_channel.channel_id, sync_channel.status or "active")
                for sync_channel in sorted(channels, key=lambda c: (c.workspace_id, c.channel_id))
            )
            sync_channel_tuples.append((sync.id, channel_sig))
        sync_channel_tuples.sort(key=lambda x: x[0])
        # Per-member channel_count and mapped_count (shown in group section)
        member_sigs: list[tuple] = []
        for member in members:
            ws_id = member.workspace_id or 0
            ch_count = 0
            if ws_id and sync_ids:
                ch_count = len(
                    DbManager.find_records(
                        SyncChannel,
                        [
                            SyncChannel.sync_id.in_(sync_ids),
                            SyncChannel.workspace_id == ws_id,
                            SyncChannel.deleted_at.is_(None),
                        ],
                    )
                )
            mapped_count = 0
            if ws_id:
                mapped_count = len(
                    DbManager.find_records(
                        UserMapping,
                        [
                            UserMapping.group_id == group.id,
                            UserMapping.target_workspace_id == ws_id,
                            UserMapping.match_method != "none",
                        ],
                    )
                )
            member_sigs.append((ws_id, ch_count, mapped_count))
        member_sigs.sort(key=lambda x: x[0])
        group_payload.append((group.id, len(members), len(syncs), tuple(sync_channel_tuples), tuple(member_sigs)))
    group_payload.sort(key=lambda x: x[0])
    payload = (
        workspace_id,
        workspace_name,
        tuple(group_ids),
        tuple(group_payload),
        pending_ids,
        reset_visible,
    )
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def refresh_home_tab_for_workspace(workspace: Workspace, logger: Logger, context: dict | None = None) -> None:
    """Publish an updated Home tab for every admin in *workspace*."""
    if not workspace or not workspace.bot_token or workspace.deleted_at:
        return
    ctx = context if context is not None else {}
    try:
        ws_client = WebClient(token=helpers.decrypt_bot_token(workspace.bot_token))
        admin_ids = helpers.get_admin_ids(ws_client, team_id=workspace.team_id, context=ctx)
    except Exception as e:
        _logger.warning(f"refresh_home_tab_for_workspace: failed to get admins: {e}")
        return

    synthetic_body = {"team": {"id": workspace.team_id}}
    for uid in admin_ids:
        try:
            build_home_tab(synthetic_body, ws_client, logger, ctx, user_id=uid)
        except Exception as e:
            _logger.warning(
                "refresh_home_tab_for_workspace: failed for user %s in workspace %s: %s",
                uid,
                getattr(workspace, "team_id", workspace.id if workspace else None),
                e,
            )


def build_home_tab(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
    *,
    user_id: str | None = None,
    return_blocks: bool = False,
) -> list[dict] | None:
    """Build and publish the App Home tab. If return_blocks is True, return block dicts and do not publish."""
    team_id = _get_team_id(body)
    user_id = user_id or _get_user_id(body)
    if not team_id or not user_id:
        _logger.warning("build_home_tab: missing team_id or user_id")
        return None

    workspace_record: Workspace = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return None

    is_admin = helpers.is_user_authorized(client, user_id)

    blocks: list[orm.BaseBlock] = []

    if not is_admin:
        blocks.append(block_context(":lock: Only Workspace Admins can configure SyncBot."))
        block_dicts = orm.BlockView(blocks=blocks).as_form_field()
        if return_blocks:
            return block_dicts
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": block_dicts})
        return None

    # Compute hash for admin view so we can update cache after publish (manual or automatic)
    current_hash = _home_tab_content_hash(workspace_record)

    # ── Workspace Groups ──────────────────────────────────────
    blocks.append(header("Workspace Groups"))
    blocks.append(block_context("_Groups of Workspaces that can Sync Channels._"))
    blocks.append(
        orm.ActionsBlock(
            elements=[
                orm.ButtonElement(
                    label="Create Group",
                    action=actions.CONFIG_CREATE_GROUP,
                ),
                orm.ButtonElement(
                    label="Join Group",
                    action=actions.CONFIG_JOIN_GROUP,
                ),
            ]
        )
    )

    my_groups = _get_groups_for_workspace(workspace_record.id)

    pending_invites = DbManager.find_records(
        WorkspaceGroupMember,
        [
            WorkspaceGroupMember.workspace_id == workspace_record.id,
            WorkspaceGroupMember.status == "pending",
            WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )

    if not my_groups and not pending_invites:
        blocks.append(
            block_context(
                "You are not in any Workspace Groups yet. Create or join a Group before you can Sync Channels with other Workspaces."
            )
        )
    else:
        for group, my_membership in my_groups:
            _build_group_section(blocks, group, my_membership, workspace_record, context)

    for invite in pending_invites:
        _build_pending_invite_section(blocks, invite, context)

    # ── External Connections (federation) ─────────────────────
    if constants.FEDERATION_ENABLED:
        _build_federation_section(blocks, workspace_record)

    # ── SyncBot Configuration ────────────────────
    blocks.append(block_context("\u200b"))
    blocks.append(divider())
    blocks.append(header("SyncBot Configuration"))
    config_buttons = [
        orm.ButtonElement(
            label="Refresh",
            action=actions.CONFIG_REFRESH_HOME,
        ),
        orm.ButtonElement(
            label="Backup/Restore",
            action=actions.CONFIG_BACKUP_RESTORE,
        ),
    ]
    if helpers.is_db_reset_visible_for_workspace(workspace_record.team_id):
        config_buttons.append(
            orm.ButtonElement(
                label=":bomb: Reset Database",
                action=actions.CONFIG_DB_RESET,
                style="danger",
            ),
        )
    blocks.append(orm.ActionsBlock(elements=config_buttons))

    block_dicts = orm.BlockView(blocks=blocks).as_form_field()
    if return_blocks:
        return block_dicts
    client.views_publish(user_id=user_id, view={"type": "home", "blocks": block_dicts})
    # Update cache so next manual Refresh skips full rebuild when data unchanged
    helpers.refresh_after_full(
        f"home_tab_hash:{team_id}",
        f"home_tab_blocks:{team_id}:{user_id}",
        f"refresh_at:home:{team_id}:{user_id}",
        current_hash,
        block_dicts,
    )
    return None


def _build_pending_invite_section(
    blocks: list,
    invite: WorkspaceGroupMember,
    context: dict | None = None,
) -> None:
    """Append blocks for an incoming group invite the workspace hasn't responded to yet."""
    group = DbManager.get_record(WorkspaceGroup, id=invite.group_id)
    if not group:
        return

    inviting_members = DbManager.find_records(
        WorkspaceGroupMember,
        [
            WorkspaceGroupMember.group_id == group.id,
            WorkspaceGroupMember.status == "active",
            WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )
    inviter_workspace_names = []
    for member in inviting_members:
        if member.workspace_id:
            ws = helpers.get_workspace_by_id(member.workspace_id, context=context)
            inviter_workspace_names.append(
                helpers.resolve_workspace_name(ws) if ws else f"Workspace {member.workspace_id}"
            )
    workspace_label = ", ".join(inviter_workspace_names) if inviter_workspace_names else "Another Workspace"

    inviter_label = workspace_label
    if getattr(invite, "invited_by_slack_user_id", None) and getattr(invite, "invited_by_workspace_id", None):
        inviter_ws = helpers.get_workspace_by_id(invite.invited_by_workspace_id, context=context)
        if inviter_ws and inviter_ws.bot_token:
            try:
                ws_client = WebClient(token=helpers.decrypt_bot_token(inviter_ws.bot_token))
                admin_name, _ = helpers.get_user_info(ws_client, invite.invited_by_slack_user_id)
                if admin_name:
                    inviter_label = f"{admin_name} from {workspace_label}"
            except Exception as exc:
                # Keep the workspace-level fallback label if we cannot resolve the
                # inviter's display name from Slack.
                _logger.debug(
                    "pending_invite_inviter_name_lookup_failed",
                    extra={"invite_id": invite.id, "workspace_id": invite.invited_by_workspace_id, "error": str(exc)},
                )

    blocks.append(divider())
    blocks.append(header(f"{group.name}"))
    blocks.append(section(f":punch: *{inviter_label}* has invited your Workspace to join this Group."))
    blocks.append(
        orm.ActionsBlock(
            elements=[
                orm.ButtonElement(
                    label="Accept",
                    action=f"{actions.CONFIG_ACCEPT_GROUP_REQUEST}_{invite.id}",
                    value=str(invite.id),
                    style="primary",
                ),
                orm.ButtonElement(
                    label="Decline",
                    action=f"{actions.CONFIG_DECLINE_GROUP_REQUEST}_{invite.id}",
                    value=str(invite.id),
                    style="danger",
                ),
            ]
        )
    )


def _build_group_section(
    blocks: list,
    group: WorkspaceGroup,
    my_membership: WorkspaceGroupMember,
    workspace_record: Workspace,
    context: dict | None = None,
) -> None:
    """Append blocks for a single workspace group."""
    blocks.append(divider())

    all_members = _get_group_members(group.id)
    other_members = [member for member in all_members if member.workspace_id != workspace_record.id]

    blocks.append(header(f"{group.name}"))

    # Action buttons for this group
    group_actions: list[orm.ButtonElement] = [
        orm.ButtonElement(
            label="Invite Workspace",
            action=actions.CONFIG_INVITE_WORKSPACE,
            value=str(group.id),
        ),
        orm.ButtonElement(
            label="Sync Channel",
            action=actions.CONFIG_PUBLISH_CHANNEL,
            value=str(group.id),
        ),
        orm.ButtonElement(
            label="User Mapping",
            action=actions.CONFIG_MANAGE_USER_MATCHING,
            value=str(group.id),
        ),
    ]
    group_actions.append(
        orm.ButtonElement(
            label="Leave Group",
            action=f"{actions.CONFIG_LEAVE_GROUP}_{group.id}",
            style="danger",
            value=str(group.id),
        ),
    )
    blocks.append(orm.ActionsBlock(elements=group_actions))

    syncs_for_group = DbManager.find_records(Sync, [Sync.group_id == group.id])
    sync_ids = [s.id for s in syncs_for_group]

    for member in all_members:
        if member.workspace_id:
            member_ws = helpers.get_workspace_by_id(member.workspace_id, context=context)
            name = helpers.resolve_workspace_name(member_ws) if member_ws else f"Workspace {member.workspace_id}"
            if member.role == "creator":
                name += " _(Group Creator)_"
        elif member.federated_workspace_id:
            fed_ws = DbManager.get_record(FederatedWorkspace, id=member.federated_workspace_id)
            name = f":globe_with_meridians: {fed_ws.name}" if fed_ws and fed_ws.name else "External"
        else:
            name = "Unknown"

        joined_str = f"{member.joined_at:%B %d, %Y}" if member.joined_at else "Unknown"

        ws_id = member.workspace_id
        channel_count = 0
        if ws_id and sync_ids:
            channels = DbManager.find_records(
                SyncChannel,
                [
                    SyncChannel.sync_id.in_(sync_ids),
                    SyncChannel.workspace_id == ws_id,
                    SyncChannel.deleted_at.is_(None),
                ],
            )
            channel_count = len(channels)

        mapped_count = 0
        if ws_id:
            mapped = DbManager.find_records(
                UserMapping,
                [
                    UserMapping.group_id == group.id,
                    UserMapping.target_workspace_id == ws_id,
                    UserMapping.match_method != "none",
                ],
            )
            mapped_count = len(mapped)

        stats = f"Member Since: `{joined_str}`\nSynced Channels: `{channel_count}`\nMapped Users: `{mapped_count}` "
        text = f"*{name}*\n{stats}"
        if member.workspace_id and member_ws:
            ws_info = _get_workspace_info(member_ws)
            icon_url = ws_info.get("icon_url")
            if icon_url:
                blocks.append(
                    orm.SectionBlock(
                        label=text,
                        element=orm.ImageAccessoryElement(
                            image_url=icon_url,
                            alt_text=name.split(" ")[0] if name else "Workspace",
                        ),
                    )
                )
            else:
                blocks.append(block_context(text))
        else:
            blocks.append(block_context(text))

    pending_members = DbManager.find_records(
        WorkspaceGroupMember,
        [
            WorkspaceGroupMember.group_id == group.id,
            WorkspaceGroupMember.status == "pending",
            WorkspaceGroupMember.deleted_at.is_(None),
        ],
    )
    for pending_member in pending_members:
        pending_ws = None
        if pending_member.workspace_id:
            pending_ws = helpers.get_workspace_by_id(pending_member.workspace_id, context=context)
            pname = (
                helpers.resolve_workspace_name(pending_ws) if pending_ws else f"Workspace {pending_member.workspace_id}"
            )
        else:
            pname = "Unknown"
        stats_pending = "Member Since: `Pending Invite`"
        text_pending = f"*{pname}*\n{stats_pending}"
        if pending_member.workspace_id and pending_ws:
            ws_info = _get_workspace_info(pending_ws)
            icon_url = ws_info.get("icon_url")
            if icon_url:
                blocks.append(
                    orm.SectionBlock(
                        label=text_pending,
                        element=orm.ImageAccessoryElement(
                            image_url=icon_url,
                            alt_text=pname.split(" ")[0] if pname else "Workspace",
                        ),
                    )
                )
            else:
                blocks.append(block_context(text_pending))
        else:
            blocks.append(block_context(text_pending))
        blocks.append(
            orm.ActionsBlock(
                elements=[
                    orm.ButtonElement(
                        label="Cancel Invite",
                        action=f"{actions.CONFIG_CANCEL_GROUP_REQUEST}_{pending_member.id}",
                        value=str(pending_member.id),
                        style="danger",
                    ),
                ]
            )
        )

    _build_inline_channel_sync(blocks, group, workspace_record, other_members, context)


def _build_federation_section(
    blocks: list,
    workspace_record: Workspace,
) -> None:
    """Append the federation section to the home tab."""
    blocks.append(divider())
    blocks.append(block_context("\u200b"))
    blocks.append(section("*External Connections*"))
    blocks.append(block_context("Connect with Workspaces on other SyncBot deployments."))
    blocks.append(
        orm.ActionsBlock(
            elements=[
                orm.ButtonElement(
                    label=":globe_with_meridians: Generate Connection Code",
                    action=actions.CONFIG_GENERATE_FEDERATION_CODE,
                ),
                orm.ButtonElement(
                    label=":link: Enter Connection Code",
                    action=actions.CONFIG_ENTER_FEDERATION_CODE,
                ),
                orm.ButtonElement(
                    label=":package: Data Migration",
                    action=actions.CONFIG_DATA_MIGRATION,
                ),
            ]
        )
    )

    fed_members = DbManager.find_records(
        WorkspaceGroupMember,
        [
            WorkspaceGroupMember.federated_workspace_id.isnot(None),
            WorkspaceGroupMember.deleted_at.is_(None),
            WorkspaceGroupMember.status == "active",
        ],
    )

    shown_fed: set[int] = set()
    for fed_member in fed_members:
        if not fed_member.federated_workspace_id or fed_member.federated_workspace_id in shown_fed:
            continue
        my_groups = _get_groups_for_workspace(workspace_record.id)
        my_group_ids = {g.id for g, _ in my_groups}
        if fed_member.group_id not in my_group_ids:
            continue

        shown_fed.add(fed_member.federated_workspace_id)
        fed_ws = DbManager.get_record(FederatedWorkspace, id=fed_member.federated_workspace_id)
        if not fed_ws:
            continue

        fed_ws_name = fed_ws.name or f"Connection {fed_ws.instance_id[:8]}"
        status_icon = ":white_check_mark:" if fed_ws.status == "active" else ":warning:"

        blocks.append(block_context("\u200b"))
        label_text = f"{status_icon} *{fed_ws_name}*"
        label_text += f"\n:globe_with_meridians: {fed_ws.webhook_url}"
        blocks.append(section(label_text))

        blocks.append(
            orm.ActionsBlock(
                elements=[
                    orm.ButtonElement(
                        label="Remove Connection",
                        action=f"{actions.CONFIG_REMOVE_FEDERATION_CONNECTION}_{fed_member.id}",
                        style="danger",
                        value=str(fed_member.id),
                    ),
                ]
            )
        )
