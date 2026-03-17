"""Focused unit tests for group handler edge branches."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from handlers.groups import handle_join_group_submit  # noqa: E402


class TestJoinGroupSubmit:
    def test_invalid_group_code_log_is_sanitized(self):
        client = MagicMock()
        logger = MagicMock()
        workspace = SimpleNamespace(id=42)

        body = {
            "user": {"id": "U1"},
            "view": {"state": {"values": {}}},
        }

        with (
            patch("handlers.groups._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.groups.forms.ENTER_GROUP_CODE_FORM.get_selected_values", return_value={}),
            patch("handlers.groups.helpers._cache_get", return_value=0),
            patch("handlers.groups.helpers._cache_set"),
            patch("handlers.groups.DbManager.find_records", return_value=[]),
            patch("handlers.groups.builders.refresh_home_tab_for_workspace"),
            patch("handlers.groups._logger.warning") as warn_log,
        ):
            handle_join_group_submit(body, client, logger, context={})

        matched = [
            call
            for call in warn_log.call_args_list
            if call.args and call.args[0] == "group_code_invalid"
        ]
        assert matched, "Expected group_code_invalid warning"
        extra = matched[0].kwargs["extra"]
        assert "code" not in extra
        assert extra["workspace_id"] == workspace.id
        assert extra["attempt"] == 1
        assert "code_length" in extra
