"""User event handlers — team join, profile changes, user mapping management."""

import contextlib
import logging
import time
from datetime import UTC, datetime
from logging import Logger

from slack_sdk.web import WebClient

import builders
import constants
import helpers
from builders._common import _get_group_members, _get_groups_for_workspace
from db import DbManager, schemas
from handlers._common import _get_authorized_workspace

_logger = logging.getLogger(__name__)


def handle_team_join(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handle a team_join event: a new user joined a connected workspace.

    1. Upsert the new user into ``user_directory`` for this workspace.
    2. Re-check all ``match_method='none'`` mappings targeting this workspace.
    """
    event = body.get("event", {})
    user_data = event.get("user", {})
    team_id = helpers.safe_get(body, "team_id")

    if not user_data or not team_id:
        return

    if user_data.get("is_bot") or user_data.get("id") == "USLACKBOT":
        return

    workspace_record = DbManager.get_record(schemas.Workspace, id=team_id)
    if not workspace_record:
        _logger.warning(f"team_join: unknown team_id {team_id}")
        return

    _logger.info(
        "team_join_received",
        extra={"team_id": team_id, "user_id": user_data.get("id")},
    )

    helpers._upsert_single_user_to_directory(user_data, workspace_record.id)

    newly_matched, still_unmatched = helpers.run_auto_match_for_workspace(client, workspace_record.id)
    _logger.info(
        "team_join_matching_complete",
        extra={
            "workspace_id": workspace_record.id,
            "newly_matched": newly_matched,
            "still_unmatched": still_unmatched,
        },
    )


def handle_user_profile_changed(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handle a user_profile_changed event: update directory and notify group members."""
    event = body.get("event", {})
    user_data = event.get("user", {})
    team_id = helpers.safe_get(body, "team_id")

    if not user_data or not team_id:
        return

    if user_data.get("is_bot") or user_data.get("id") == "USLACKBOT":
        return

    workspace_record = DbManager.get_record(schemas.Workspace, id=team_id)
    if not workspace_record:
        return

    helpers._upsert_single_user_to_directory(user_data, workspace_record.id)

    my_groups = _get_groups_for_workspace(workspace_record.id)
    notified_ws: set[int] = set()
    for group, _ in my_groups:
        members = _get_group_members(group.id)
        for member in members:
            if (
                member.workspace_id
                and member.workspace_id != workspace_record.id
                and member.workspace_id not in notified_ws
            ):
                member_ws = helpers.get_workspace_by_id(member.workspace_id, context=context)
                if member_ws:
                    builders.refresh_home_tab_for_workspace(member_ws, logger, context=None)
                    notified_ws.add(member.workspace_id)

    _logger.info(
        "user_profile_updated",
        extra={"team_id": team_id, "user_id": user_data.get("id")},
    )


def handle_user_mapping_back(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Return from the user mapping screen to the main Home tab."""
    user_id = helpers.get_user_id_from_body(body)
    if not user_id:
        return
    builders.build_home_tab(body, client, logger, context, user_id=user_id)


def handle_user_mapping_refresh(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Refresh user mappings: re-seed, auto-match, then re-render the mapping screen.

    Uses content hash and cached blocks; when hash unchanged and within 60s cooldown,
    re-publishes with cooldown message.
    """
    auth_result = _get_authorized_workspace(body, client, context, "user_mapping_refresh")
    if not auth_result:
        return
    user_id, workspace_record = auth_result

    raw_group = helpers.safe_get(body, "actions", 0, "value") or "0"
    try:
        group_id = int(raw_group)
    except (TypeError, ValueError):
        group_id = 0

    gid_opt = group_id or None
    current_hash = builders._user_mapping_content_hash(workspace_record, gid_opt)
    hash_key = f"user_mapping_hash:{workspace_record.team_id}:{user_id}:{group_id}"
    blocks_key = f"user_mapping_blocks:{workspace_record.team_id}:{user_id}:{group_id}"
    refresh_at_key = f"refresh_at:user_mapping:{workspace_record.team_id}:{user_id}:{group_id}"

    action, cached_blocks, remaining = helpers.refresh_cooldown_check(
        current_hash, hash_key, blocks_key, refresh_at_key
    )
    cooldown_sec = getattr(constants, "REFRESH_COOLDOWN_SECONDS", 60)

    if action == "cooldown" and cached_blocks is not None and remaining is not None:
        blocks_with_message = helpers.inject_cooldown_message(
            cached_blocks, builders._USER_MAPPING_REFRESH_BUTTON_INDEX, remaining
        )
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": blocks_with_message})
        return
    if action == "cached" and cached_blocks is not None:
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": cached_blocks})
        helpers._cache_set(refresh_at_key, time.monotonic(), ttl=cooldown_sec * 2)
        return

    helpers._CACHE.pop(f"dir_refresh:{workspace_record.id}", None)

    if group_id:
        members = _get_group_members(group_id)
    else:
        members = []
        for group, _ in _get_groups_for_workspace(workspace_record.id):
            members.extend(_get_group_members(group.id))

    member_clients: list[tuple[WebClient, int]] = []

    for member in members:
        if not member.workspace_id or member.workspace_id == workspace_record.id:
            continue
        try:
            # Force a fresh directory pull before rematching. Cached directory
            # snapshots can keep stale display names/emails after profile edits.
            helpers._CACHE.pop(f"dir_refresh:{member.workspace_id}", None)
            member_ws = helpers.get_workspace_by_id(member.workspace_id, context=context)
            if member_ws and member_ws.bot_token:
                member_client = WebClient(token=helpers.decrypt_bot_token(member_ws.bot_token))
                helpers._refresh_user_directory(member_client, member.workspace_id)
                member_clients.append((member_client, member.workspace_id))
            helpers.seed_user_mappings(member.workspace_id, workspace_record.id, group_id=gid_opt)
            helpers.seed_user_mappings(workspace_record.id, member.workspace_id, group_id=gid_opt)
        except Exception as exc:
            _logger.warning(
                "user_mapping_refresh_member_sync_failed",
                extra={
                    "workspace_id": workspace_record.id,
                    "member_workspace_id": member.workspace_id,
                    "group_id": gid_opt,
                    "error": str(exc),
                },
            )

    helpers.run_auto_match_for_workspace(client, workspace_record.id)
    for member_client, member_ws_id in member_clients:
        with contextlib.suppress(Exception):
            helpers.run_auto_match_for_workspace(member_client, member_ws_id)

    block_dicts = builders.build_user_mapping_screen(
        client,
        workspace_record,
        user_id,
        group_id=gid_opt,
        context=context,
        return_blocks=True,
    )
    if block_dicts is None:
        return
    client.views_publish(user_id=user_id, view={"type": "home", "blocks": block_dicts})
    helpers.refresh_after_full(hash_key, blocks_key, refresh_at_key, current_hash, block_dicts)


def handle_user_mapping_edit_submit(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Save the per-user mapping edit and refresh the mapping screen."""
    from handlers._common import _parse_private_metadata

    auth_result = _get_authorized_workspace(body, client, context, "user_mapping_edit_submit")
    if not auth_result:
        return
    user_id, workspace_record = auth_result

    meta = _parse_private_metadata(body)
    mapping_id = meta.get("mapping_id")
    group_id = meta.get("group_id") or 0

    if not mapping_id:
        _logger.warning("user_mapping_edit_submit: missing mapping_id")
        return

    mapping = DbManager.get_record(schemas.UserMapping, id=mapping_id)
    if not mapping:
        return

    values = helpers.safe_get(body, "view", "state", "values") or {}
    selected = None
    for block_data in values.values():
        for action_data in block_data.values():
            sel = action_data.get("selected_option")
            if sel:
                selected = sel.get("value")

    now = datetime.now(UTC)
    if selected == "__remove__":
        DbManager.update_records(
            schemas.UserMapping,
            [schemas.UserMapping.id == mapping.id],
            {
                schemas.UserMapping.target_user_id: None,
                schemas.UserMapping.match_method: "none",
                schemas.UserMapping.matched_at: now,
            },
        )
        _logger.info("user_mapping_removed", extra={"mapping_id": mapping.id})
    elif selected:
        DbManager.update_records(
            schemas.UserMapping,
            [schemas.UserMapping.id == mapping.id],
            {
                schemas.UserMapping.target_user_id: selected,
                schemas.UserMapping.match_method: "manual",
                schemas.UserMapping.matched_at: now,
            },
        )
        _logger.info("user_mapping_updated", extra={"mapping_id": mapping.id, "target_user_id": selected})

    # Invalidate user-mapping caches so next Refresh on that screen does a full rebuild
    helpers._cache_delete_prefix(f"user_mapping_hash:{workspace_record.team_id}:")
    helpers._cache_delete_prefix(f"user_mapping_blocks:{workspace_record.team_id}:")
    helpers._cache_delete_prefix(f"refresh_at:user_mapping:{workspace_record.team_id}:")

    builders.build_user_mapping_screen(
        client,
        workspace_record,
        user_id,
        group_id=group_id or None,
        context=context,
    )
    builders.refresh_home_tab_for_workspace(workspace_record, logger, context=context)
