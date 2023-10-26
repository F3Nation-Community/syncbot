from utils import handlers, builders
from utils.slack import actions

COMMAND_MAPPER = {
    "/config-syncbot": builders.build_config_form,
}

ACTION_MAPPER = {
    actions.CONFIG_JOIN_EXISTING_SYNC: builders.build_join_sync_form,
    actions.CONFIG_CREATE_NEW_SYNC: builders.build_new_sync_form,
    actions.CONFIG_REMOVE_SYNC: handlers.handle_remove_sync,
    actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT: handlers.check_join_sync_channel,
}

EVENT_MAPPER = {
    "message": handlers.respond_to_message_event,
}

VIEW_MAPPER = {
    actions.CONFIG_FORM_SUBMIT: handlers.handle_config_submission,
    actions.CONFIG_JOIN_SYNC_SUMBIT: handlers.handle_join_sync_submission,
    actions.CONFIG_NEW_SYNC_SUBMIT: handlers.handle_new_sync_submission,
}

MAIN_MAPPER = {
    "command": COMMAND_MAPPER,
    "block_actions": ACTION_MAPPER,
    "event_callback": EVENT_MAPPER,
    "view_submission": VIEW_MAPPER,
}
