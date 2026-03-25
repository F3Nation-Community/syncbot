"""Focused unit tests for channel sync handler branches."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from handlers.channel_sync import (  # noqa: E402
    handle_publish_channel_submit,
    handle_publish_mode_submit,
    handle_subscribe_channel_submit,
)


class TestPublishModeSubmit:
    def test_missing_group_id_logs_warning(self):
        client = MagicMock()
        logger = MagicMock()
        context = {"ack": MagicMock()}
        workspace = SimpleNamespace(id=10)
        body = {"view": {"team_id": "T1", "private_metadata": "{}"}}

        with (
            patch("handlers.channel_sync._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.channel_sync._parse_private_metadata", return_value={}),
            patch("handlers.channel_sync._logger.warning") as warn_log,
        ):
            handle_publish_mode_submit(body, client, logger, context)

        assert warn_log.call_args is not None
        assert "publish_mode_submit: missing group_id in metadata" in warn_log.call_args.args[0]
        context["ack"].assert_not_called()


class TestPublishChannelSubmit:
    def test_missing_group_id_exits_early(self):
        client = MagicMock()
        logger = MagicMock()
        context = {"ack": MagicMock()}
        workspace = SimpleNamespace(id=10)

        with (
            patch("handlers.channel_sync._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.channel_sync._parse_private_metadata", return_value={}),
            patch("handlers.channel_sync.DbManager.create_record") as create_record,
        ):
            handle_publish_channel_submit({}, client, logger, context)

        context["ack"].assert_not_called()
        create_record.assert_not_called()

    def test_missing_channel_selection_returns_ack_error(self):
        client = MagicMock()
        logger = MagicMock()
        ack = MagicMock()
        context = {"ack": ack}
        workspace = SimpleNamespace(id=10)

        with (
            patch("handlers.channel_sync._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.channel_sync._parse_private_metadata", return_value={"group_id": 7}),
            patch("handlers.channel_sync._get_selected_conversation_or_option", return_value="__none__"),
            patch("handlers.channel_sync.DbManager.create_record") as create_record,
        ):
            handle_publish_channel_submit({}, client, logger, context)

        ack.assert_called_once()
        kwargs = ack.call_args.kwargs
        assert kwargs["response_action"] == "errors"
        assert "Select a Channel to publish." in kwargs["errors"].values()
        create_record.assert_not_called()

    def test_existing_sync_channel_returns_ack_error(self):
        client = MagicMock()
        logger = MagicMock()
        ack = MagicMock()
        context = {"ack": ack}
        workspace = SimpleNamespace(id=10)

        with (
            patch("handlers.channel_sync._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.channel_sync._parse_private_metadata", return_value={"group_id": 7}),
            patch("handlers.channel_sync._get_selected_conversation_or_option", return_value="C123"),
            patch("handlers.channel_sync.DbManager.find_records", return_value=[object()]),
            patch("handlers.channel_sync.DbManager.create_record") as create_record,
        ):
            handle_publish_channel_submit({}, client, logger, context)

        ack.assert_called_once()
        kwargs = ack.call_args.kwargs
        assert kwargs["response_action"] == "errors"
        assert "already being synced" in next(iter(kwargs["errors"].values()))
        create_record.assert_not_called()


class TestSubscribeChannelSubmit:
    def test_missing_sync_id_exits_early(self):
        client = MagicMock()
        logger = MagicMock()
        context = {}
        workspace = SimpleNamespace(id=10)

        with (
            patch("handlers.channel_sync._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.channel_sync._parse_private_metadata", return_value={}),
            patch("handlers.channel_sync.DbManager.create_record") as create_record,
        ):
            handle_subscribe_channel_submit({}, client, logger, context)

        create_record.assert_not_called()

    def test_missing_channel_selection_exits_early(self):
        client = MagicMock()
        logger = MagicMock()
        context = {}
        workspace = SimpleNamespace(id=10)

        with (
            patch("handlers.channel_sync._get_authorized_workspace", return_value=("U1", workspace)),
            patch("handlers.channel_sync._parse_private_metadata", return_value={"sync_id": 55}),
            patch("handlers.channel_sync._get_selected_conversation_or_option", return_value="__none__"),
            patch("handlers.channel_sync.DbManager.create_record") as create_record,
        ):
            handle_subscribe_channel_submit({}, client, logger, context)

        create_record.assert_not_called()
