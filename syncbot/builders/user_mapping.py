"""User mapping screen builders."""

import contextlib  # noqa: I001
import hashlib
import logging

from slack_sdk.web import WebClient

import helpers
from builders._common import (
    _deny_unauthorized,
    _get_group_members,
    _get_groups_for_workspace,
    _get_team_id,
    _get_user_id,
)
from db import DbManager
from db.schemas import UserDirectory, UserMapping, Workspace, WorkspaceGroup
from slack import actions, orm
from slack.blocks import actions as blocks_actions, button, context as block_context, divider, header, section

_logger = logging.getLogger(__name__)

# Index of the Actions block that contains the Refresh button (after header at 0)
_USER_MAPPING_REFRESH_BUTTON_INDEX = 1


def _user_mapping_content_hash(workspace_record: Workspace, group_id: int | None) -> str:
    """Compute a stable hash of the data that drives the user mapping screen (minimal DB)."""
    workspace_id = workspace_record.id
    gid = group_id or 0
    if gid:
        members = _get_group_members(gid)
        linked_workspace_ids = {
            m.workspace_id for m in members if m.workspace_id and m.workspace_id != workspace_id
        }
    else:
        my_groups = _get_groups_for_workspace(workspace_id)
        linked_workspace_ids = set()
        for g, _ in my_groups:
            for m in _get_group_members(g.id):
                if m.workspace_id and m.workspace_id != workspace_id:
                    linked_workspace_ids.add(m.workspace_id)

    all_mappings: list[UserMapping] = []
    for source_ws_id in linked_workspace_ids:
        mappings = DbManager.find_records(
            UserMapping,
            [
                UserMapping.source_workspace_id == source_ws_id,
                UserMapping.target_workspace_id == workspace_id,
            ],
        )
        all_mappings.extend(mappings)

    payload = (workspace_id, gid, tuple((m.id, m.match_method, m.target_user_id) for m in sorted(all_mappings, key=lambda x: x.id)))
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def build_user_matching_entry(
    body: dict,
    client: WebClient,
    logger,
    context: dict,
) -> None:
    """Entry point when user clicks "User Mapping" on the Home tab."""
    if _deny_unauthorized(body, client, logger):
        return

    raw_value = helpers.safe_get(body, "actions", 0, "value")
    group_id = None
    if raw_value:
        with contextlib.suppress(TypeError, ValueError):
            group_id = int(raw_value)

    user_id = _get_user_id(body)
    team_id = _get_team_id(body)
    if not user_id or not team_id:
        return

    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return

    build_user_mapping_screen(client, workspace_record, user_id, group_id=group_id)


def build_user_mapping_screen(
    client: WebClient,
    workspace_record: Workspace,
    user_id: str,
    *,
    group_id: int | None = None,
    context: dict | None = None,
    return_blocks: bool = False,
) -> list | None:
    """Publish the user mapping screen on the Home tab. If return_blocks is True, return block dicts and do not publish."""
    group_name = "Group"
    if group_id:
        groups = DbManager.find_records(WorkspaceGroup, [WorkspaceGroup.id == group_id])
        if groups:
            group_name = groups[0].name

    if group_id:
        members = _get_group_members(group_id)
        linked_workspace_ids = {
            m.workspace_id for m in members if m.workspace_id and m.workspace_id != workspace_record.id
        }
    else:
        my_groups = _get_groups_for_workspace(workspace_record.id)
        linked_workspace_ids: set[int] = set()
        for g, _ in my_groups:
            for m in _get_group_members(g.id):
                if m.workspace_id and m.workspace_id != workspace_record.id:
                    linked_workspace_ids.add(m.workspace_id)

    all_mappings: list[UserMapping] = []
    for source_ws_id in linked_workspace_ids:
        mappings = DbManager.find_records(
            UserMapping,
            [
                UserMapping.source_workspace_id == source_ws_id,
                UserMapping.target_workspace_id == workspace_record.id,
            ],
        )
        all_mappings.extend(mappings)

    unmapped = [m for m in all_mappings if m.target_user_id is None or m.match_method == "none"]
    soft_matched = [m for m in all_mappings if m.match_method in ("name", "manual") and m.target_user_id is not None]
    email_matched = [m for m in all_mappings if m.match_method == "email" and m.target_user_id is not None]

    _ws_name_lookup: dict[int, str] = {}
    for source_ws_id in linked_workspace_ids:
        ws = helpers.get_workspace_by_id(source_ws_id, context=context)
        if ws:
            _ws_name_lookup[source_ws_id] = helpers.resolve_workspace_name(ws) or ""

    def _display_for_mapping(m: UserMapping, ws_lookup: dict[int, str]) -> str:
        """Formatted display string: normalized name + workspace in parens if present."""
        display = helpers.normalize_display_name(m.source_display_name or m.source_user_id)
        ws_label = ws_lookup.get(m.source_workspace_id, "")
        return f"{display} ({ws_label})" if ws_label else display

    unmapped.sort(key=lambda m: _display_for_mapping(m, _ws_name_lookup).lower())
    soft_matched.sort(key=lambda m: _display_for_mapping(m, _ws_name_lookup).lower())
    email_matched.sort(key=lambda m: _display_for_mapping(m, _ws_name_lookup).lower())

    _email_lookup: dict[tuple[int, str], str] = {}
    _avatar_lookup: dict[tuple[int, str], str] = {}
    for source_ws_id in linked_workspace_ids:
        ws = helpers.get_workspace_by_id(source_ws_id, context=context)
        partner_client = None
        if ws and ws.bot_token:
            with contextlib.suppress(Exception):
                partner_client = WebClient(token=helpers.decrypt_bot_token(ws.bot_token))
        dir_entries = DbManager.find_records(
            UserDirectory,
            [UserDirectory.workspace_id == source_ws_id, UserDirectory.deleted_at.is_(None)],
        )
        for entry in dir_entries:
            if entry.email:
                _email_lookup[(source_ws_id, entry.slack_user_id)] = entry.email
            if partner_client:
                with contextlib.suppress(Exception):
                    _, avatar_url = helpers.get_user_info(partner_client, entry.slack_user_id)
                    if avatar_url:
                        _avatar_lookup[(source_ws_id, entry.slack_user_id)] = avatar_url

    def _user_context_block(mapping: UserMapping, label_text: str) -> orm.ContextBlock:
        avatar_url = _avatar_lookup.get((mapping.source_workspace_id, mapping.source_user_id))
        elements: list = []
        if avatar_url:
            elements.append(
                orm.ImageContextElement(
                    image_url=avatar_url,
                    alt_text=mapping.source_display_name or "user",
                )
            )
        elements.append(orm.ContextElement(initial_value=label_text))
        return orm.ContextBlock(elements=elements)

    group_val = str(group_id) if group_id else "0"
    blocks: list[orm.BaseBlock] = [
        header(f"User Mapping — {group_name}"),
        blocks_actions(
            button(":arrow_left: Back", actions.CONFIG_USER_MAPPING_BACK, value=group_val),
            button(":arrows_counterclockwise: Refresh", actions.CONFIG_USER_MAPPING_REFRESH, value=group_val),
        ),
        block_context(f":busts_in_silhouette: *{len(soft_matched) + len(email_matched)} mapped*  \u00b7  *{len(unmapped)} unmapped*"),
        divider(),
    ]

    if unmapped:
        blocks.append(section(":warning: *Unmapped Users*"))
        blocks.append(block_context("\u200b"))
        for m in unmapped:
            blocks.append(_user_context_block(m, f"*{_display_for_mapping(m, _ws_name_lookup)}*"))
            blocks.append(blocks_actions(button("Edit", f"{actions.CONFIG_USER_MAPPING_EDIT}_{m.id}", value=group_val)))
            blocks.append(divider())

    if soft_matched:
        blocks.append(section(":pencil2: *Soft / Manual Matches*"))
        blocks.append(block_context("\u200b"))
        for m in soft_matched:
            method_tag = "manual" if m.match_method == "manual" else "name"
            blocks.append(_user_context_block(m, f"*{_display_for_mapping(m, _ws_name_lookup)}*  \u2192  <@{m.target_user_id}> _[{method_tag}]_"))
            blocks.append(blocks_actions(button("Edit", f"{actions.CONFIG_USER_MAPPING_EDIT}_{m.id}", value=group_val)))
            blocks.append(divider())

    if email_matched:
        blocks.append(section(":lock: *Email Matches*"))
        blocks.append(block_context("\u200b"))
        for m in email_matched:
            email_addr = _email_lookup.get((m.source_workspace_id, m.source_user_id), "")
            email_tag = f"_{email_addr}_" if email_addr else "_[email]_"
            blocks.append(_user_context_block(m, f"*{_display_for_mapping(m, _ws_name_lookup)}*  \u2192  <@{m.target_user_id}> {email_tag}"))
            blocks.append(divider())

    if not unmapped and not soft_matched and not email_matched:
        blocks.append(block_context("_No user mappings yet. Mappings are created automatically when "
            "workspaces join a group and users share display names or emails._"))

    block_dicts = orm.BlockView(blocks=blocks).as_form_field()
    if return_blocks:
        return block_dicts
    client.views_publish(user_id=user_id, view={"type": "home", "blocks": block_dicts})
    return None


def build_user_mapping_edit_modal(
    body: dict,
    client: WebClient,
    logger,
    context: dict,
) -> None:
    """Open a modal to edit a single user mapping."""
    if _deny_unauthorized(body, client, logger):
        return

    trigger_id = helpers.safe_get(body, "trigger_id")
    if not trigger_id:
        return

    action_id = helpers.safe_get(body, "actions", 0, "action_id") or ""
    mapping_id_str = action_id.replace(actions.CONFIG_USER_MAPPING_EDIT + "_", "")
    try:
        mapping_id = int(mapping_id_str)
    except (TypeError, ValueError):
        _logger.warning(f"build_user_mapping_edit_modal: invalid mapping_id: {mapping_id_str}")
        return

    raw_group = helpers.safe_get(body, "actions", 0, "value") or "0"
    try:
        group_id = int(raw_group)
    except (TypeError, ValueError):
        group_id = 0

    mapping = DbManager.get_record(UserMapping, id=mapping_id)
    if not mapping:
        _logger.warning(f"build_user_mapping_edit_modal: mapping {mapping_id} not found")
        return

    team_id = _get_team_id(body)
    workspace_record = helpers.get_workspace_record(team_id, body, context, client) if team_id else None
    if not workspace_record:
        return

    source_ws = helpers.get_workspace_by_id(mapping.source_workspace_id)
    source_ws_name = helpers.resolve_workspace_name(source_ws) if source_ws else "Partner"
    display = helpers.normalize_display_name(mapping.source_display_name or mapping.source_user_id)

    existing_mappings = DbManager.find_records(
        UserMapping,
        [
            UserMapping.source_workspace_id == mapping.source_workspace_id,
            UserMapping.target_workspace_id == mapping.target_workspace_id,
            UserMapping.target_user_id.isnot(None),
            UserMapping.match_method != "none",
            UserMapping.id != mapping.id,
        ],
    )
    taken_target_ids = {m.target_user_id for m in existing_mappings}

    directory = DbManager.find_records(
        UserDirectory,
        [UserDirectory.workspace_id == workspace_record.id, UserDirectory.deleted_at.is_(None)],
    )
    directory.sort(key=lambda u: (u.display_name or u.real_name or u.slack_user_id).lower())

    has_mapping = mapping.target_user_id is not None and mapping.match_method != "none"
    options: list[orm.SelectorOption] = []
    if has_mapping:
        options.append(orm.SelectorOption(name="\u274c  Remove Mapping", value="__remove__"))
    for entry in directory:
        if entry.slack_user_id in taken_target_ids:
            continue
        label = entry.display_name or entry.real_name or entry.slack_user_id
        if entry.email:
            label = f"{label} ({entry.email})"
        if len(label) > 75:
            label = label[:72] + "..."
        options.append(orm.SelectorOption(name=label, value=entry.slack_user_id))

    initial_value = None
    if mapping.target_user_id and mapping.match_method != "none":
        initial_value = mapping.target_user_id

    avatar_accessory = None
    if source_ws and source_ws.bot_token:
        with contextlib.suppress(Exception):
            partner_client = WebClient(token=helpers.decrypt_bot_token(source_ws.bot_token))
            _, avatar_url = helpers.get_user_info(partner_client, mapping.source_user_id)
            if avatar_url:
                avatar_accessory = orm.ImageAccessoryElement(image_url=avatar_url, alt_text=display)

    blocks: list[orm.BaseBlock] = [
        orm.SectionBlock(label=f"*{display}*\n_{source_ws_name}_", element=avatar_accessory),
    ]
    if mapping.target_user_id and mapping.match_method != "none":
        blocks.append(block_context(f"Currently mapped to <@{mapping.target_user_id}> _[{mapping.match_method}]_"))
    blocks.append(divider())
    if options:
        blocks.append(
            orm.InputBlock(
                label="Map to user",
                action=actions.CONFIG_USER_MAPPING_EDIT_SELECT,
                element=orm.StaticSelectElement(
                    placeholder="Select a user...",
                    options=options,
                    initial_value=initial_value,
                ),
                optional=True,
            )
        )
    else:
        blocks.append(block_context("_No available users to map to. All users in your workspace "
            "are already mapped to other users._"))

    meta = {"mapping_id": mapping_id, "group_id": group_id or 0}
    modal_form = orm.BlockView(blocks=blocks)
    modal_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_USER_MAPPING_EDIT_SUBMIT,
        title_text="Edit Mapping",
        submit_button_text="Save",
        close_button_text="Cancel",
        parent_metadata=meta,
        new_or_add="new",
    )
