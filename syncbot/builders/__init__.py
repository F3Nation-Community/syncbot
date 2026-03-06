"""Builders package – Slack modal and home-tab UI constructors.

Re-exports every public symbol so that ``import builders`` /
``from builders import X`` continues to work after the split.
"""

from builders._common import (
    _format_channel_ref,
    _get_group_members,
    _get_groups_for_workspace,
    _get_workspace_info,
)
from builders.channel_sync import (
    _build_inline_channel_sync,
)
from builders.home import (
    _REFRESH_BUTTON_BLOCK_INDEX,
    _home_tab_content_hash,
    build_home_tab,
    refresh_home_tab_for_workspace,
)
from builders.sync import build_join_sync_form, build_new_sync_form
from builders.user_mapping import (
    _USER_MAPPING_REFRESH_BUTTON_INDEX,
    _user_mapping_content_hash,
    build_user_mapping_edit_modal,
    build_user_mapping_screen,
    build_user_matching_entry,
)

__all__ = [
    "_build_inline_channel_sync",
    "_format_channel_ref",
    "_get_group_members",
    "_get_groups_for_workspace",
    "_get_workspace_info",
    "_REFRESH_BUTTON_BLOCK_INDEX",
    "_home_tab_content_hash",
    "build_home_tab",
    "build_join_sync_form",
    "build_new_sync_form",
    "_USER_MAPPING_REFRESH_BUTTON_INDEX",
    "_user_mapping_content_hash",
    "build_user_mapping_edit_modal",
    "build_user_mapping_screen",
    "build_user_matching_entry",
    "refresh_home_tab_for_workspace",
]
