"""Request routing tables.

Maps incoming Slack request types to handler functions.  The
:data:`MAIN_MAPPER` is a two-level dict keyed first by request category
(``block_actions``, ``event_callback``, ``view_submission``) and then by
the specific identifier (action ID, event type, or callback ID).

:func:`~app.main_response` uses these tables to dispatch every request.
"""

import builders
import handlers
from slack import actions

ACTION_MAPPER = {
    actions.CONFIG_JOIN_EXISTING_SYNC: builders.build_join_sync_form,
    actions.CONFIG_CREATE_NEW_SYNC: builders.build_new_sync_form,
    actions.CONFIG_REMOVE_SYNC: handlers.handle_remove_sync,
    actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT: handlers.check_join_sync_channel,
    actions.CONFIG_MANAGE_USER_MATCHING: builders.build_user_matching_entry,
    actions.CONFIG_USER_MAPPING_BACK: handlers.handle_user_mapping_back,
    actions.CONFIG_USER_MAPPING_EDIT: builders.build_user_mapping_edit_modal,
    actions.CONFIG_USER_MAPPING_REFRESH: handlers.handle_user_mapping_refresh,
    actions.CONFIG_CREATE_GROUP: handlers.handle_create_group,
    actions.CONFIG_JOIN_GROUP: handlers.handle_join_group,
    actions.CONFIG_INVITE_WORKSPACE: handlers.handle_invite_workspace,
    actions.CONFIG_LEAVE_GROUP: handlers.handle_leave_group,
    actions.CONFIG_ACCEPT_GROUP_REQUEST: handlers.handle_accept_group_invite,
    actions.CONFIG_DECLINE_GROUP_REQUEST: handlers.handle_decline_group_invite,
    actions.CONFIG_CANCEL_GROUP_REQUEST: handlers.handle_decline_group_invite,
    actions.CONFIG_PUBLISH_CHANNEL: handlers.handle_publish_channel,
    actions.CONFIG_UNPUBLISH_CHANNEL: handlers.handle_unpublish_channel,
    actions.CONFIG_PAUSE_SYNC: handlers.handle_pause_sync,
    actions.CONFIG_RESUME_SYNC: handlers.handle_resume_sync,
    actions.CONFIG_STOP_SYNC: handlers.handle_stop_sync,
    actions.CONFIG_SUBSCRIBE_CHANNEL: handlers.handle_subscribe_channel,
    actions.CONFIG_REFRESH_HOME: handlers.handle_refresh_home,
    actions.CONFIG_BACKUP_RESTORE: handlers.handle_backup_restore,
    actions.CONFIG_BACKUP_DOWNLOAD: handlers.handle_backup_download,
    actions.CONFIG_BACKUP_RESTORE_PROCEED: handlers.handle_backup_restore_proceed,
    actions.CONFIG_DATA_MIGRATION: handlers.handle_data_migration,
    actions.CONFIG_DATA_MIGRATION_EXPORT: handlers.handle_data_migration_export,
    actions.CONFIG_DATA_MIGRATION_PROCEED: handlers.handle_data_migration_proceed,
    actions.CONFIG_DB_RESET: handlers.handle_db_reset,
    actions.CONFIG_DB_RESET_PROCEED: handlers.handle_db_reset_proceed,
    actions.CONFIG_GENERATE_FEDERATION_CODE: handlers.handle_generate_federation_code,
    actions.CONFIG_ENTER_FEDERATION_CODE: handlers.handle_enter_federation_code,
    actions.CONFIG_REMOVE_FEDERATION_CONNECTION: handlers.handle_remove_federation_connection,
}
"""Block-action ``action_id`` -> handler."""

EVENT_MAPPER = {
    "app_home_opened": handlers.handle_app_home_opened,
    "member_joined_channel": handlers.handle_member_joined_channel,
    "message": handlers.respond_to_message_event,
    "reaction_added": handlers._handle_reaction,
    "reaction_removed": handlers._handle_reaction,
    "team_join": handlers.handle_team_join,
    "tokens_revoked": handlers.handle_tokens_revoked,
    "user_profile_changed": handlers.handle_user_profile_changed,
}
"""Event ``type`` -> handler."""

VIEW_MAPPER = {
    actions.CONFIG_JOIN_SYNC_SUMBIT: handlers.handle_join_sync_submission,
    actions.CONFIG_NEW_SYNC_SUBMIT: handlers.handle_new_sync_submission,
    actions.CONFIG_USER_MAPPING_EDIT_SUBMIT: handlers.handle_user_mapping_edit_submit,
    actions.CONFIG_CREATE_GROUP_SUBMIT: handlers.handle_create_group_submit,
    actions.CONFIG_JOIN_GROUP_SUBMIT: handlers.handle_join_group_submit,
    actions.CONFIG_INVITE_WORKSPACE_SUBMIT: handlers.handle_invite_workspace_submit,
    actions.CONFIG_LEAVE_GROUP_CONFIRM: handlers.handle_leave_group_confirm,
    actions.CONFIG_PUBLISH_MODE_SUBMIT: handlers.handle_publish_mode_submit,
    actions.CONFIG_PUBLISH_CHANNEL_SUBMIT: handlers.handle_publish_channel_submit,
    actions.CONFIG_SUBSCRIBE_CHANNEL_SUBMIT: handlers.handle_subscribe_channel_submit,
    actions.CONFIG_STOP_SYNC_CONFIRM: handlers.handle_stop_sync_confirm,
    actions.CONFIG_FEDERATION_CODE_SUBMIT: handlers.handle_federation_code_submit,
    actions.CONFIG_FEDERATION_LABEL_SUBMIT: handlers.handle_federation_label_submit,
    actions.CONFIG_BACKUP_RESTORE_SUBMIT: handlers.handle_backup_restore_submit,
    actions.CONFIG_DATA_MIGRATION_SUBMIT: handlers.handle_data_migration_submit,
}
"""View submission ``callback_id`` -> handler."""

MAIN_MAPPER = {
    "block_actions": ACTION_MAPPER,
    "event_callback": EVENT_MAPPER,
    "view_submission": VIEW_MAPPER,
}
"""Top-level dispatcher: request category -> sub-mapper."""
