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
            element=orm.ConversationsSelectElement(placeholder="Select a Channel"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Select the Channel you want to sync. The Sync will be named after the Channel. "
                "If a Sync has already been set up in another Workspace, use 'Join existing Sync' instead.",
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
            element=orm.ConversationsSelectElement(placeholder="Select a Channel to use for this Sync"),
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
                initial_value="Enter the invite code shared by an Admin from another Workspace in the Group.",
            ),
        ),
    ]
)


PUBLISH_CHANNEL_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Channel to Publish",
            action=actions.CONFIG_PUBLISH_CHANNEL_SELECT,
            element=orm.ConversationsSelectElement(placeholder="Select a Channel to publish"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Select a Channel from your Workspace to make available for Syncing.",
            ),
        ),
    ]
)


SUBSCRIBE_CHANNEL_FORM = orm.BlockView(
    blocks=[
        orm.InputBlock(
            label="Channel for Sync",
            action=actions.CONFIG_SUBSCRIBE_CHANNEL_SELECT,
            element=orm.ConversationsSelectElement(placeholder="Select a Channel to sync into"),
            optional=False,
        ),
        orm.ContextBlock(
            element=orm.ContextElement(
                initial_value="Select a Channel in your Workspace to receive messages from the published Channel.",
            ),
        ),
    ]
)
