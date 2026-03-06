"""Pre-built Slack Block Kit forms for SyncBot configuration modals.

Defines reusable form templates that are deep-copied and customised at
runtime before being sent to Slack:

* :data:`NEW_SYNC_FORM` — Modal for creating a new sync group (channel picker).
* :data:`JOIN_SYNC_FORM` — Modal for joining an existing sync group
  (sync selector + channel selector).
* :data:`ENTER_GROUP_CODE_FORM` — Modal for entering a group invite code.
* :data:`PUBLISH_CHANNEL_FORM` — Modal for publishing a channel.
* :data:`SUBSCRIBE_CHANNEL_FORM` — Modal for subscribing to a channel.
"""

from slack import actions, orm

NEW_SYNC_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Channel to Sync",
            action=actions.CONFIG_NEW_SYNC_CHANNEL_SELECT,
            element=orm.ConversationsSelectElement(placeholder="Select a channel"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Select the channel you want to sync. The sync will be named after the channel. "
                "If a sync has already been set up in another workspace, use 'Join existing Sync' instead.",
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
            element=orm.ConversationsSelectElement(placeholder="Select a channel to use for this Sync"),
            optional=False,
            dispatch_action=True,
        ),
    ]
)


ENTER_GROUP_CODE_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Group Invite Code",
            action=actions.CONFIG_JOIN_GROUP_CODE,
            element=orm.PlainTextInputElement(placeholder="Enter the code (e.g. A7X-K9M)"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Enter the invite code shared by an admin from another workspace in the group.",
            ),
        ),
    ]
)


PUBLISH_CHANNEL_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Channel to Publish",
            action=actions.CONFIG_PUBLISH_CHANNEL_SELECT,
            element=orm.ConversationsSelectElement(placeholder="Select a channel to publish"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Select a channel from your workspace to make available for syncing.",
            ),
        ),
    ]
)


SUBSCRIBE_CHANNEL_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Channel for Sync",
            action=actions.CONFIG_SUBSCRIBE_CHANNEL_SELECT,
            element=orm.ConversationsSelectElement(placeholder="Select a channel to sync into"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Select a channel in your workspace to receive messages from the published channel.",
            ),
        ),
    ]
)
