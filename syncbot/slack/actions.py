"""Slack Block Kit action ID constants.

These string constants are used as ``action_id`` / ``callback_id`` values
throughout the UI forms and handler routing tables.  Keeping them in one
place avoids typos and makes refactoring easier.
"""

CONFIG_JOIN_EXISTING_SYNC = "join_existing_sync"
"""Action: user clicked "Join existing Sync" button."""

CONFIG_CREATE_NEW_SYNC = "create_new_sync"
"""Action: user clicked "Create new Sync" button."""

CONFIG_REMOVE_SYNC = "remove_sync"
"""Action: user clicked "DeSync" button (prefix-matched)."""

CONFIG_NEW_SYNC_CHANNEL_SELECT = "config_new_sync_channel_select"
"""Input: channel picker in the new-sync form."""

CONFIG_NEW_SYNC_SUBMIT = "config_new_sync_submit"
"""Callback: new-sync modal submitted."""

CONFIG_JOIN_SYNC_SELECT = "config_join_sync_select"
"""Input: sync selector in the join-sync form."""

CONFIG_JOIN_SYNC_CHANNEL_SELECT = "config_join_sync_channel_select"
"""Input: channel selector in the join-sync form (dispatches an action on change)."""

CONFIG_JOIN_SYNC_SUMBIT = "config_join_sync_submit"
"""Callback: join-sync modal submitted."""

# ---------------------------------------------------------------------------
# User Matching actions
# ---------------------------------------------------------------------------

CONFIG_MANAGE_USER_MATCHING = "manage_user_matching"
"""Action: user clicked "User Mapping" button on the Home tab."""

CONFIG_USER_MAPPING_BACK = "user_mapping_back"
"""Action: user clicked "Back" on the user mapping screen to return to main Home tab."""

CONFIG_USER_MAPPING_EDIT = "user_mapping_edit"
"""Action: user clicked "Edit" on a user row in the mapping screen (prefix-matched with mapping ID)."""

CONFIG_USER_MAPPING_EDIT_SUBMIT = "user_mapping_edit_submit"
"""Callback: per-user edit mapping modal submitted."""

CONFIG_USER_MAPPING_EDIT_SELECT = "user_mapping_edit_select"
"""Input: user picker dropdown in the edit mapping modal."""

CONFIG_USER_MAPPING_REFRESH = "user_mapping_refresh"
"""Action: user clicked "Refresh" on the user mapping screen."""

# ---------------------------------------------------------------------------
# Workspace Group actions
# ---------------------------------------------------------------------------

CONFIG_CREATE_GROUP = "create_group"
"""Action: user clicked "Create Group" on the Home tab."""

CONFIG_CREATE_GROUP_SUBMIT = "create_group_submit"
"""Callback: create-group modal submitted."""

CONFIG_CREATE_GROUP_NAME = "create_group_name"
"""Input: text field for the group name."""

CONFIG_JOIN_GROUP = "join_group"
"""Action: user clicked "Join Group" on the Home tab."""

CONFIG_JOIN_GROUP_SUBMIT = "join_group_submit"
"""Callback: join-group modal submitted."""

CONFIG_JOIN_GROUP_CODE = "join_group_code"
"""Input: text field for the group invite code."""

CONFIG_LEAVE_GROUP = "leave_group"
"""Action: user clicked "Leave Group" (prefix-matched with group_id)."""

CONFIG_LEAVE_GROUP_CONFIRM = "leave_group_confirm"
"""Callback: leave-group confirmation modal submitted."""

CONFIG_ACCEPT_GROUP_REQUEST = "accept_group_request"
"""Action: user clicked "Accept" on an incoming group join request (prefix-matched with member_id)."""

CONFIG_CANCEL_GROUP_REQUEST = "cancel_group_request"
"""Action: user clicked "Cancel Request" on an outgoing group join request (prefix-matched with member_id)."""

CONFIG_INVITE_WORKSPACE = "invite_workspace"
"""Action: user clicked "Invite Workspace" button on a group (value carries group_id)."""

CONFIG_INVITE_WORKSPACE_SUBMIT = "invite_workspace_submit"
"""Callback: invite-workspace modal submitted (sends DM invite to selected workspace)."""

CONFIG_INVITE_WORKSPACE_SELECT = "invite_workspace_select"
"""Input: workspace picker dropdown in the invite workspace modal."""

CONFIG_DECLINE_GROUP_REQUEST = "decline_group_request"
"""Action: user clicked "Decline" on an incoming group invite DM (prefix-matched with member_id)."""

# ---------------------------------------------------------------------------
# Channel Sync actions
# ---------------------------------------------------------------------------

CONFIG_PUBLISH_CHANNEL = "publish_channel"
"""Action: user clicked "Publish Channel" button (value carries group_id)."""

CONFIG_PUBLISH_CHANNEL_SELECT = "publish_channel_select"
"""Input: channel picker in the publish channel modal."""

CONFIG_PUBLISH_CHANNEL_SUBMIT = "publish_channel_submit"
"""Callback: publish channel modal submitted."""

CONFIG_PUBLISH_MODE_SUBMIT = "publish_mode_submit"
"""Callback: step 1 of publish channel (sync mode selection) submitted."""

CONFIG_PUBLISH_SYNC_MODE = "publish_sync_mode"
"""Input: radio buttons for direct vs group-wide sync mode."""

CONFIG_PUBLISH_DIRECT_TARGET = "publish_direct_target"
"""Input: workspace picker for direct (1-to-1) sync target."""

CONFIG_UNPUBLISH_CHANNEL = "unpublish_channel"
"""Action: user clicked "Unpublish" on a published channel (prefix-matched with sync_channel_id)."""

CONFIG_PAUSE_SYNC = "pause_sync"
"""Action: user clicked "Pause Syncing" on an active channel sync (prefix-matched with sync_id)."""

CONFIG_RESUME_SYNC = "resume_sync"
"""Action: user clicked "Resume Syncing" on a paused channel sync (prefix-matched with sync_id)."""

CONFIG_STOP_SYNC = "stop_sync"
"""Action: user clicked "Stop Syncing" on a channel sync (prefix-matched with sync_id)."""

CONFIG_STOP_SYNC_CONFIRM = "stop_sync_confirm"
"""View submission: user confirmed stopping a channel sync."""

CONFIG_SUBSCRIBE_CHANNEL = "subscribe_channel"
"""Action: user clicked "Start Syncing" on an available channel (prefix-matched with sync_id)."""

CONFIG_SUBSCRIBE_CHANNEL_SELECT = "subscribe_channel_select"
"""Input: channel picker in the subscribe channel modal."""

CONFIG_SUBSCRIBE_CHANNEL_SUBMIT = "subscribe_channel_submit"
"""Callback: subscribe channel modal submitted."""

# ---------------------------------------------------------------------------
# Home Tab actions
# ---------------------------------------------------------------------------

CONFIG_REFRESH_HOME = "refresh_home"
"""Action: user clicked the "Refresh" button on the Home tab."""

CONFIG_BACKUP_RESTORE = "backup_restore"
"""Action: user clicked "Backup/Restore" on the Home tab (opens modal)."""

CONFIG_BACKUP_RESTORE_SUBMIT = "backup_restore_submit"
"""Callback: Backup/Restore modal submitted (restore from backup)."""

CONFIG_BACKUP_RESTORE_CONFIRM = "backup_restore_confirm"
"""Callback: Confirm restore when HMAC or encryption key mismatch."""

CONFIG_BACKUP_DOWNLOAD = "backup_download"
"""Action: user clicked Download backup in Backup/Restore modal."""

CONFIG_BACKUP_RESTORE_JSON_INPUT = "backup_restore_json_input"
"""Input: plain text area for restore JSON in Backup/Restore modal."""

CONFIG_DATA_MIGRATION = "data_migration"
"""Action: user clicked "Data Migration" in External Connections (opens modal)."""

CONFIG_DATA_MIGRATION_SUBMIT = "data_migration_submit"
"""Callback: Data Migration modal submitted (import migration file)."""

CONFIG_DATA_MIGRATION_CONFIRM = "data_migration_confirm"
"""Callback: Confirm import when signature check failed."""

CONFIG_DATA_MIGRATION_EXPORT = "data_migration_export"
"""Action: user clicked Export in Data Migration modal."""

CONFIG_DATA_MIGRATION_JSON_INPUT = "data_migration_json_input"
"""Input: plain text area for migration import JSON."""

# ---------------------------------------------------------------------------
# External Connections (federation) actions
# ---------------------------------------------------------------------------

CONFIG_GENERATE_FEDERATION_CODE = "generate_federation_code"
"""Action: user clicked "Generate Connection Code" on the Home tab."""

CONFIG_ENTER_FEDERATION_CODE = "enter_federation_code"
"""Action: user clicked "Enter Connection Code" on the Home tab."""

CONFIG_FEDERATION_CODE_SUBMIT = "federation_code_submit"
"""Callback: enter-connection-code modal submitted."""

CONFIG_FEDERATION_CODE_INPUT = "federation_code_input"
"""Input: text field for the connection code in the modal."""

CONFIG_FEDERATION_LABEL_SUBMIT = "federation_label_submit"
"""Callback: connection label modal submitted (before code generation)."""

CONFIG_FEDERATION_LABEL_INPUT = "federation_label_input"
"""Input: text field for the connection label in the modal."""

CONFIG_REMOVE_FEDERATION_CONNECTION = "remove_federation_connection"
"""Action: user clicked "Remove Connection" on an external connection (prefix-matched)."""

# ---------------------------------------------------------------------------
# Database Reset (dev/admin tool, gated by ENABLE_DB_RESET env var)
# ---------------------------------------------------------------------------

CONFIG_DB_RESET = "db_reset"
"""Action: user clicked "Reset Database" on the Home tab."""

CONFIG_DB_RESET_CONFIRM = "db_reset_confirm"
"""Callback: user confirmed database reset in the warning modal."""
