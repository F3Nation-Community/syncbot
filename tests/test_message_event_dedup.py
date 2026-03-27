"""Tests for message event deduplication (file_share vs plain message, Slack retries)."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from handlers.messages import (  # noqa: E402
    _should_skip_slack_event_retry,
    respond_to_message_event,
)


class TestShouldSkipSlackEventRetry:
    def test_skips_when_context_slack_retry_num_ge_1(self):
        assert _should_skip_slack_event_retry({}, {"slack_retry_num": 1}) is True

    def test_no_skip_when_slack_retry_num_zero(self):
        assert _should_skip_slack_event_retry({}, {"slack_retry_num": 0}) is False

    def test_skips_when_body_retry_attempt_ge_1(self):
        assert _should_skip_slack_event_retry({"retry_attempt": 1}, {}) is True

    def test_no_skip_first_delivery(self):
        assert _should_skip_slack_event_retry({}, {}) is False


class TestRespondToMessageEventDedup:
    def _base_body(self):
        return {
            "team_id": "T001",
            "event": {
                "type": "message",
                "channel": "C001",
                "user": "U001",
                "text": "Hello",
                "ts": "1234567890.000001",
            },
        }

    def test_text_only_no_subtype_still_calls_new_post(self):
        client = MagicMock()
        logger = MagicMock()
        context = {}

        with (
            patch("handlers.messages._is_own_bot_message", return_value=False),
            patch("handlers.messages._handle_new_post") as mock_new,
            patch("handlers.messages._build_file_context", return_value=([], [], [])),
        ):
            respond_to_message_event(self._base_body(), client, logger, context)

        mock_new.assert_called_once()

    def test_no_subtype_with_files_skips_without_building_file_context(self):
        body = self._base_body()
        body["event"]["files"] = [{"id": "F1", "mimetype": "image/jpeg"}]

        client = MagicMock()
        logger = MagicMock()
        context = {}

        with (
            patch("handlers.messages._is_own_bot_message", return_value=False),
            patch("handlers.messages._handle_new_post") as mock_new,
            patch("handlers.messages._build_file_context") as build_fc,
        ):
            respond_to_message_event(body, client, logger, context)

        mock_new.assert_not_called()
        build_fc.assert_not_called()

    def test_file_share_subtype_still_calls_new_post(self):
        body = self._base_body()
        body["event"]["subtype"] = "file_share"
        body["event"]["files"] = [{"id": "F1", "mimetype": "image/jpeg"}]

        client = MagicMock()
        logger = MagicMock()
        context = {}

        with (
            patch("handlers.messages._is_own_bot_message", return_value=False),
            patch("handlers.messages._handle_new_post") as mock_new,
            patch("handlers.messages._build_file_context", return_value=([], [], [{"path": "/tmp/x", "name": "x.jpg", "mimetype": "image/jpeg"}])),
        ):
            respond_to_message_event(body, client, logger, context)

        mock_new.assert_called_once()
        assert mock_new.call_args is not None

    def test_retry_skips_handler(self):
        client = MagicMock()
        logger = MagicMock()
        context = {"slack_retry_num": 1}

        with (
            patch("handlers.messages._is_own_bot_message", return_value=False),
            patch("handlers.messages._handle_new_post") as mock_new,
            patch("handlers.messages._build_file_context") as build_fc,
        ):
            respond_to_message_event(self._base_body(), client, logger, context)

        mock_new.assert_not_called()
        build_fc.assert_not_called()
