"""Join/New sync form builders."""

import copy
import logging

from slack_sdk.web import WebClient

import helpers
from builders._common import _deny_unauthorized, _get_group_members, _get_groups_for_workspace
from db import DbManager
from db.schemas import Sync, SyncChannel, Workspace
from helpers import safe_get
from slack import actions, forms, orm

_logger = logging.getLogger(__name__)


def build_join_sync_form(
    body: dict,
    client: WebClient,
    logger,
    context: dict,
) -> None:
    """Pushes a new modal layer to join an existing sync."""
    if _deny_unauthorized(body, client, logger):
        return

    trigger_id: str = safe_get(body, "trigger_id")
    team_id = safe_get(body, "view", "team_id")
    join_sync_form: orm.BlockView = copy.deepcopy(forms.JOIN_SYNC_FORM)

    workspace_record: Workspace = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return

    my_groups = _get_groups_for_workspace(workspace_record.id)
    group_ws_ids: set[int] = {workspace_record.id}
    for group, _ in my_groups:
        for m in _get_group_members(group.id):
            if m.workspace_id:
                group_ws_ids.add(m.workspace_id)

    channel_sync_workspace_records: list[tuple[SyncChannel, Workspace]] = DbManager.find_join_records2(
        left_cls=SyncChannel,
        right_cls=Workspace,
        filters=[Workspace.team_id == team_id, SyncChannel.deleted_at.is_(None)],
    )
    already_joined_sync_ids = {record[0].sync_id for record in channel_sync_workspace_records}

    all_syncs: list[Sync] = DbManager.find_records(Sync, [True])
    eligible_syncs: list[Sync] = []

    for sync in all_syncs:
        if sync.id in already_joined_sync_ids:
            continue
        sync_channels = DbManager.find_records(
            SyncChannel,
            [SyncChannel.sync_id == sync.id, SyncChannel.deleted_at.is_(None)],
        )
        if any(sc.workspace_id in group_ws_ids for sc in sync_channels):
            eligible_syncs.append(sync)

    options = orm.as_selector_options(
        [sync.title for sync in eligible_syncs],
        [str(sync.id) for sync in eligible_syncs],
    )
    join_sync_form.set_options({actions.CONFIG_JOIN_SYNC_SELECT: options})
    join_sync_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_JOIN_SYNC_SUMBIT,
        title_text="Join Sync",
        new_or_add="new",
    )


def build_new_sync_form(
    body: dict,
    client: WebClient,
    logger,
    context: dict,
) -> None:
    """Pushes a new modal layer to create a new sync."""
    if _deny_unauthorized(body, client, logger):
        return

    trigger_id: str = safe_get(body, "trigger_id")
    new_sync_form: orm.BlockView = copy.deepcopy(forms.NEW_SYNC_FORM)
    new_sync_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_NEW_SYNC_SUBMIT,
        title_text="New Sync",
        new_or_add="new",
    )
