"""View submission callback IDs whose handlers control the Slack interaction HTTP ack.

Kept separate from :mod:`app` so tests can import it without initializing the database.
"""

from slack.actions import (
    CONFIG_BACKUP_RESTORE_SUBMIT,
    CONFIG_DATA_MIGRATION_SUBMIT,
    CONFIG_PUBLISH_CHANNEL_SUBMIT,
    CONFIG_PUBLISH_MODE_SUBMIT,
)

DEFERRED_ACK_VIEW_CALLBACK_IDS = frozenset({
    CONFIG_PUBLISH_MODE_SUBMIT,
    CONFIG_PUBLISH_CHANNEL_SUBMIT,
    CONFIG_BACKUP_RESTORE_SUBMIT,
    CONFIG_DATA_MIGRATION_SUBMIT,
})
