import copy
from slack_sdk.web import WebClient
from logging import Logger
from utils import helpers
from utils.slack import forms, orm, actions
from utils.db.schemas import Region, SyncChannel, Sync
from utils.db import DbManager
from utils.helpers import safe_get


def build_config_form(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> orm.BlockView:
    """Builds a BlockView config form for the given region.

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.

    """

    team_id: str = safe_get(body, "team_id") or safe_get(body, "view", "team_id")
    trigger_id: str = safe_get(body, "trigger_id")
    root_view_id: str = safe_get(body, "view", "root_view_id")
    error_message: str = safe_get(body, "error_message")
    region_record: Region = helpers.get_region_record(team_id, body, context, client)

    config_form = copy.deepcopy(forms.CONFIG_FORM)

    # pull all Syncs, SyncChannels for this region
    records = DbManager.find_join_records2(
        left_cls=SyncChannel,
        right_cls=Sync,
        filters=[SyncChannel.region_id == region_record.id],
    )

    for record in records:
        sync_channel: SyncChannel = record[0]
        sync: Sync = record[1]
        config_form.blocks.extend(
            forms.build_config_form_sync_block(
                sync_channel=sync_channel,
                sync=sync,
            )
        )

    if error_message:
        config_form.blocks.insert(
            0,
            orm.SectionBlock(
                text=orm.MrkdwnText(error_message),
            ),
        )

    if root_view_id:
        config_form.update_modal(
            client=client,
            view_id=root_view_id,
            callback_id=actions.CONFIG_FORM_SUBMIT,
            title_text="SyncBot Configuration",
        )
    else:
        config_form.post_modal(
            client=client,
            trigger_id=trigger_id,
            callback_id=actions.CONFIG_FORM_SUBMIT,
            title_text="SyncBot Configuration",
        )


def build_join_sync_form(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Pushes a new modal layer to join a new sync.

    Args:
        body (dict): Event body from the action invocation.
        client (WebClient): The Slack WebClient object.
        logger (Logger): A logger object.
        context (dict): A context object.
    """
    trigger_id: str = safe_get(body, "trigger_id")
    team_id = safe_get(body, "view", "team_id")
    join_sync_form: orm.BlockView = copy.deepcopy(forms.JOIN_SYNC_FORM)

    sync_records: list[Sync] = DbManager.find_records(Sync, [True])
    channel_sync_region_records: list[tuple[SyncChannel, Region]] = DbManager.find_join_records2(
        left_cls=SyncChannel,
        right_cls=Region,
        filters=[Region.team_id == team_id],
    )
    sync_records = [
        sync for sync in sync_records if sync.id not in [record[0].sync_id for record in channel_sync_region_records]
    ]

    options = orm.as_selector_options([sync.title for sync in sync_records], [str(sync.id) for sync in sync_records])
    join_sync_form.set_options({actions.CONFIG_JOIN_SYNC_SELECT: options})
    join_sync_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_JOIN_SYNC_SUMBIT,
        title_text="Join Sync",
        new_or_add="add",
    )


def build_new_sync_form(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Pushes a new modal layer to create a new sync.

    Args:
        body (dict): Event body from the action invocation.
        client (WebClient): The Slack WebClient object.
        logger (Logger): A logger object.
        context (dict): A context object.
    """
    trigger_id: str = safe_get(body, "trigger_id")
    new_sync_form: orm.BlockView = copy.deepcopy(forms.NEW_SYNC_FORM)
    new_sync_form.post_modal(
        client=client,
        trigger_id=trigger_id,
        callback_id=actions.CONFIG_NEW_SYNC_SUBMIT,
        title_text="New Sync",
        new_or_add="add",
    )
