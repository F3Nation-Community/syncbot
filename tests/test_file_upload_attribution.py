"""Tests for threaded file upload ``initial_comment`` (mentions + permalink)."""

from unittest.mock import MagicMock, patch

from slack_sdk.web import WebClient

from handlers.messages import _shared_by_file_initial_comment


class TestSharedByFileInitialComment:
    def test_file_only_uses_mention_when_mapped(self):
        client = MagicMock(spec=WebClient)
        with patch("handlers.messages.helpers.get_mapped_target_user_id", return_value="UMAPPED"):
            text = _shared_by_file_initial_comment(
                user_id="U_SRC",
                source_workspace_id=1,
                target_workspace_id=2,
                name_for_target="Nacho",
                target_client=client,
                channel_id="C1",
                text_message_ts=None,
            )
        assert text == "Shared by <@UMAPPED>"
        client.chat_getPermalink.assert_not_called()

    def test_file_only_falls_back_to_display_name(self):
        client = MagicMock(spec=WebClient)
        with patch("handlers.messages.helpers.get_mapped_target_user_id", return_value=None):
            text = _shared_by_file_initial_comment(
                user_id="U_SRC",
                source_workspace_id=1,
                target_workspace_id=2,
                name_for_target="Nacho",
                target_client=client,
                channel_id="C1",
                text_message_ts=None,
            )
        assert text == "Shared by Nacho"

    def test_with_text_message_includes_permalink_link(self):
        client = MagicMock(spec=WebClient)
        client.chat_getPermalink.return_value = {"permalink": "https://example.slack.com/archives/C1/p123"}
        with patch("handlers.messages.helpers.get_mapped_target_user_id", return_value="U99"):
            text = _shared_by_file_initial_comment(
                user_id="U_SRC",
                source_workspace_id=1,
                target_workspace_id=2,
                name_for_target="Nacho",
                target_client=client,
                channel_id="C1",
                text_message_ts="1234.567890",
            )
        assert text == "Shared by <@U99> in <https://example.slack.com/archives/C1/p123|this message>"
        client.chat_getPermalink.assert_called_once_with(channel="C1", message_ts="1234.567890")

    def test_permalink_failure_falls_back_to_shared_by_only(self):
        client = MagicMock(spec=WebClient)
        client.chat_getPermalink.side_effect = RuntimeError("api error")
        with patch("handlers.messages.helpers.get_mapped_target_user_id", return_value=None):
            text = _shared_by_file_initial_comment(
                user_id="U_SRC",
                source_workspace_id=1,
                target_workspace_id=2,
                name_for_target="Pat",
                target_client=client,
                channel_id="C1",
                text_message_ts="1.0",
            )
        assert text == "Shared by Pat"
