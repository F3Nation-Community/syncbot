"""Tests for federated reaction payload and fallback behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from slack_sdk.errors import SlackApiError

from federation import api as federation_api
from federation import core as federation_core


class TestFederationReactionPayload:
    def test_build_reaction_payload_includes_user_fields(self):
        payload = federation_core.build_reaction_payload(
            post_id="post-1",
            channel_id="C123",
            reaction="custom_emoji",
            action="add",
            user_name="Alice",
            user_avatar_url="https://avatar.example/alice.png",
            workspace_name="Workspace A",
            timestamp="100.000001",
        )

        assert payload["post_id"] == "post-1"
        assert payload["channel_id"] == "C123"
        assert payload["reaction"] == "custom_emoji"
        assert payload["action"] == "add"
        assert payload["user_name"] == "Alice"
        assert payload["user_avatar_url"] == "https://avatar.example/alice.png"
        assert payload["workspace_name"] == "Workspace A"
        assert payload["timestamp"] == "100.000001"


class TestFederationReactionFallback:
    def test_invalid_name_reaction_falls_back_to_thread_text(self):
        body = {
            "post_id": "post-1",
            "channel_id": "C123",
            "reaction": "missing_custom",
            "action": "add",
            "user_name": "Alice",
            "user_avatar_url": "https://avatar.example/alice.png",
            "workspace_name": "Workspace A",
        }
        fed_ws = SimpleNamespace(instance_id="remote-instance")
        sync_channel = SimpleNamespace(id=101, channel_id="C123")
        workspace = SimpleNamespace(bot_token="enc-token")
        post_meta = SimpleNamespace(ts=123.456)

        slack_response = MagicMock()
        slack_response.get.return_value = "invalid_name"
        slack_exc = SlackApiError(message="emoji not found", response=slack_response)

        ws_client = MagicMock()
        ws_client.reactions_add.side_effect = slack_exc

        with (
            patch.object(federation_api, "_resolve_channel_for_federated", return_value=(sync_channel, workspace)),
            patch.object(federation_api, "_find_post_records", return_value=[post_meta]),
            patch.object(federation_api.helpers, "decrypt_bot_token", return_value="xoxb-test"),
            patch.object(federation_api, "WebClient", return_value=ws_client),
            patch.object(federation_api.helpers, "post_message", return_value={"ts": "200.000001"}) as post_message_mock,
        ):
            status, resp = federation_api.handle_message_react(body, fed_ws)

        assert status == 200
        assert resp["ok"] is True
        assert resp["applied"] == 1
        ws_client.reactions_add.assert_called_once_with(channel="C123", timestamp="123.456", name="missing_custom")
        post_message_mock.assert_called_once_with(
            bot_token="xoxb-test",
            channel_id="C123",
            msg_text="reacted with :missing_custom:",
            user_name="Alice",
            user_profile_url="https://avatar.example/alice.png",
            workspace_name="Workspace A",
            thread_ts="123.456",
        )

    def test_non_invalid_name_error_does_not_fallback(self):
        """Other Slack errors (rate limit, network, etc.) should NOT trigger the text fallback."""
        body = {
            "post_id": "post-1",
            "channel_id": "C123",
            "reaction": "thumbsup",
            "action": "add",
            "user_name": "Alice",
        }
        fed_ws = SimpleNamespace(instance_id="remote-instance")
        sync_channel = SimpleNamespace(id=101, channel_id="C123")
        workspace = SimpleNamespace(bot_token="enc-token")
        post_meta = SimpleNamespace(ts=123.456)

        slack_response = MagicMock()
        slack_response.get.return_value = "too_many_reactions"
        slack_exc = SlackApiError(message="too many reactions", response=slack_response)

        ws_client = MagicMock()
        ws_client.reactions_add.side_effect = slack_exc

        with (
            patch.object(federation_api, "_resolve_channel_for_federated", return_value=(sync_channel, workspace)),
            patch.object(federation_api, "_find_post_records", return_value=[post_meta]),
            patch.object(federation_api.helpers, "decrypt_bot_token", return_value="xoxb-test"),
            patch.object(federation_api, "WebClient", return_value=ws_client),
            patch.object(federation_api.helpers, "post_message") as post_message_mock,
        ):
            status, resp = federation_api.handle_message_react(body, fed_ws)

        assert status == 200
        assert resp["applied"] == 0
        post_message_mock.assert_not_called()

    def test_successful_reaction_add_no_fallback(self):
        """When reactions_add succeeds, no text fallback should be posted."""
        body = {
            "post_id": "post-1",
            "channel_id": "C123",
            "reaction": "thumbsup",
            "action": "add",
            "user_name": "Alice",
        }
        fed_ws = SimpleNamespace(instance_id="remote-instance")
        sync_channel = SimpleNamespace(id=101, channel_id="C123")
        workspace = SimpleNamespace(bot_token="enc-token")
        post_meta = SimpleNamespace(ts=123.456)

        ws_client = MagicMock()

        with (
            patch.object(federation_api, "_resolve_channel_for_federated", return_value=(sync_channel, workspace)),
            patch.object(federation_api, "_find_post_records", return_value=[post_meta]),
            patch.object(federation_api.helpers, "decrypt_bot_token", return_value="xoxb-test"),
            patch.object(federation_api, "WebClient", return_value=ws_client),
            patch.object(federation_api.helpers, "post_message") as post_message_mock,
        ):
            status, resp = federation_api.handle_message_react(body, fed_ws)

        assert status == 200
        assert resp["applied"] == 1
        ws_client.reactions_add.assert_called_once()
        post_message_mock.assert_not_called()

    def test_reaction_remove_invalid_name_no_fallback(self):
        """Removing a non-existent emoji should not post a text fallback."""
        body = {
            "post_id": "post-1",
            "channel_id": "C123",
            "reaction": "missing_custom",
            "action": "remove",
            "user_name": "Alice",
        }
        fed_ws = SimpleNamespace(instance_id="remote-instance")
        sync_channel = SimpleNamespace(id=101, channel_id="C123")
        workspace = SimpleNamespace(bot_token="enc-token")
        post_meta = SimpleNamespace(ts=123.456)

        slack_response = MagicMock()
        slack_response.get.return_value = "invalid_name"
        slack_exc = SlackApiError(message="emoji not found", response=slack_response)

        ws_client = MagicMock()
        ws_client.reactions_remove.side_effect = slack_exc

        with (
            patch.object(federation_api, "_resolve_channel_for_federated", return_value=(sync_channel, workspace)),
            patch.object(federation_api, "_find_post_records", return_value=[post_meta]),
            patch.object(federation_api.helpers, "decrypt_bot_token", return_value="xoxb-test"),
            patch.object(federation_api, "WebClient", return_value=ws_client),
            patch.object(federation_api.helpers, "post_message") as post_message_mock,
        ):
            status, resp = federation_api.handle_message_react(body, fed_ws)

        assert status == 200
        assert resp["applied"] == 0
        post_message_mock.assert_not_called()

    def test_missing_user_fields_use_defaults(self):
        """When user_name/workspace_name are absent from payload, defaults are used."""
        body = {
            "post_id": "post-1",
            "channel_id": "C123",
            "reaction": "missing_custom",
            "action": "add",
        }
        fed_ws = SimpleNamespace(instance_id="remote-instance")
        sync_channel = SimpleNamespace(id=101, channel_id="C123")
        workspace = SimpleNamespace(bot_token="enc-token")
        post_meta = SimpleNamespace(ts=123.456)

        slack_response = MagicMock()
        slack_response.get.return_value = "invalid_name"
        slack_exc = SlackApiError(message="emoji not found", response=slack_response)

        ws_client = MagicMock()
        ws_client.reactions_add.side_effect = slack_exc

        with (
            patch.object(federation_api, "_resolve_channel_for_federated", return_value=(sync_channel, workspace)),
            patch.object(federation_api, "_find_post_records", return_value=[post_meta]),
            patch.object(federation_api.helpers, "decrypt_bot_token", return_value="xoxb-test"),
            patch.object(federation_api, "WebClient", return_value=ws_client),
            patch.object(federation_api.helpers, "post_message", return_value={"ts": "200.000001"}) as post_message_mock,
        ):
            status, resp = federation_api.handle_message_react(body, fed_ws)

        assert status == 200
        assert resp["applied"] == 1
        post_message_mock.assert_called_once_with(
            bot_token="xoxb-test",
            channel_id="C123",
            msg_text="reacted with :missing_custom:",
            user_name="Remote User",
            user_profile_url=None,
            workspace_name="Remote",
            thread_ts="123.456",
        )
