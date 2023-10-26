from typing import List
from utils.db import schemas
from utils.slack import orm, actions

CONFIG_FORM = orm.BlockView(
    blocks=[
        orm.ActionsBlock(
            elements=[
                orm.ButtonElement(
                    label="Join existing Sync",
                    action=actions.CONFIG_JOIN_EXISTING_SYNC,
                ),
                orm.ButtonElement(
                    label="Create new Sync",
                    action=actions.CONFIG_CREATE_NEW_SYNC,
                ),
            ]
        ),
        orm.DividerBlock(),
    ]
)

NEW_SYNC_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Sync Title",
            action=actions.CONFIG_NEW_SYNC_TITLE,
            element=orm.PlainTextInputElement(placeholder="Enter a title for this Sync"),
            optional=False,
        ),
        orm.InputBlock(
            label="Sync Description",
            action=actions.CONFIG_NEW_SYNC_DESCRIPTION,
            element=orm.PlainTextInputElement(placeholder="Enter a description for this Sync"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Reminder: this form is for creating NEW Syncs. If the Sync has already been set up "
                "in another region, please use the 'Join existing Sync' button to join it.",
            ),
        ),
    ]
)

JOIN_SYNC_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Sync Select",
            action=actions.CONFIG_JOIN_SYNC_SELECT,
            element=orm.StaticSelectElement(placeholder="Select a Sync to join"),
            optional=False,
        ),
        orm.InputBlock(
            label="Sync Channel Select",
            action=actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT,
            element=orm.ChannelsSelectElement(placeholder="Select a channel to use for this Sync"),
            optional=False,
            dispatch_action=True,
        ),
    ]
)


def build_config_form_sync_block(sync_channel: schemas.SyncChannel, sync: schemas.Sync) -> List[orm.BaseBlock]:
    """Function to build a block for a sync channel.

    Args:
        sync_channel (orm.SyncChannel): SyncChannel database record.
        sync (orm.Sync): Sync database record.

    Returns:
        List[orm.BaseBlock]: List of blocks to be appended to the config form.
    """
    return [
        orm.SectionBlock(
            label=f"*{sync.title}*\n{sync.description}\nChannel: <#{sync_channel.channel_id}>",
            action=f"{actions.CONFIG_REMOVE_SYNC}_{sync_channel.id}",
            element=orm.ButtonElement(
                label="DeSync",
                style="danger",
                value=f"{sync_channel.id}",  # TODO: add confirmation block
            ),
        ),
        orm.DividerBlock(),
    ]
