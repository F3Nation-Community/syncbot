"""Unit tests for helper utilities under ``syncbot/helpers``."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure minimal env vars are set before importing app code
os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
# Placeholder only; never a real token (avoids secret scanners)
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

import helpers

# -----------------------------------------------------------------------
# safe_get
# -----------------------------------------------------------------------


class TestSafeGet:
    def test_simple_dict(self):
        assert helpers.safe_get({"a": 1}, "a") == 1

    def test_nested_dict(self):
        data = {"a": {"b": {"c": 42}}}
        assert helpers.safe_get(data, "a", "b", "c") == 42

    def test_missing_key_returns_none(self):
        assert helpers.safe_get({"a": 1}, "b") is None

    def test_nested_missing_key_returns_none(self):
        assert helpers.safe_get({"a": {"b": 1}}, "a", "c") is None

    def test_none_data_returns_none(self):
        assert helpers.safe_get(None) is None

    def test_empty_dict_returns_none(self):
        assert helpers.safe_get({}, "a") is None

    def test_list_index_access(self):
        data = {"items": [{"name": "first"}, {"name": "second"}]}
        assert helpers.safe_get(data, "items", 0, "name") == "first"
        assert helpers.safe_get(data, "items", 1, "name") == "second"

    def test_list_index_out_of_bounds(self):
        data = {"items": [1]}
        assert helpers.safe_get(data, "items", 5) is None

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        assert helpers.safe_get(data, "a", "b", "c", "d", "e") == "deep"


# -----------------------------------------------------------------------
# Encryption helpers
# -----------------------------------------------------------------------


class TestEncryption:
    @patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": "my-secret-key"})
    def test_encrypt_decrypt_roundtrip(self):
        # Use a non-secret placeholder; encryption accepts any string
        token = "xoxb-0-0"
        encrypted = helpers.encrypt_bot_token(token)
        assert encrypted != token
        decrypted = helpers.decrypt_bot_token(encrypted)
        assert decrypted == token

    @patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": "my-secret-key"})
    def test_decrypt_invalid_token_raises(self):
        with pytest.raises(ValueError, match="decryption failed"):
            helpers.decrypt_bot_token("not-a-valid-encrypted-token")

    @patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": "123"})
    def test_encryption_disabled_with_default_key(self):
        token = "xoxb-0-0"
        assert helpers.encrypt_bot_token(token) == token
        assert helpers.decrypt_bot_token(token) == token

    @patch.dict(os.environ, {}, clear=False)
    def test_encryption_disabled_when_key_missing(self):
        os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
        token = "xoxb-0-0"
        assert helpers.encrypt_bot_token(token) == token
        assert helpers.decrypt_bot_token(token) == token

    @patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": "key-A"})
    def test_wrong_key_raises(self):
        token = "xoxb-0-0"
        encrypted = helpers.encrypt_bot_token(token)

        with (
            patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": "key-B"}),
            pytest.raises(ValueError, match="decryption failed"),
        ):
            helpers.decrypt_bot_token(encrypted)


# -----------------------------------------------------------------------
# In-process cache
# -----------------------------------------------------------------------


class TestCache:
    def setup_method(self):
        helpers._CACHE.clear()

    def test_cache_set_and_get(self):
        helpers._cache_set("k1", "value1")
        assert helpers._cache_get("k1") == "value1"

    def test_cache_miss(self):
        assert helpers._cache_get("nonexistent") is None

    def test_cache_expiry(self):
        helpers._cache_set("k2", "value2", ttl=0)
        time.sleep(0.01)
        assert helpers._cache_get("k2") is None

    def test_cache_within_ttl(self):
        helpers._cache_set("k3", "value3", ttl=60)
        assert helpers._cache_get("k3") == "value3"


# -----------------------------------------------------------------------
# get_request_type
# -----------------------------------------------------------------------


class TestGetRequestType:
    def test_event_callback(self):
        body = {"type": "event_callback", "event": {"type": "message"}}
        assert helpers.get_request_type(body) == ("event_callback", "message")

    def test_view_submission(self):
        body = {"type": "view_submission", "view": {"callback_id": "my_callback"}}
        assert helpers.get_request_type(body) == ("view_submission", "my_callback")

    def test_command(self):
        body = {"command": "/config-syncbot"}
        assert helpers.get_request_type(body) == ("command", "/config-syncbot")

    def test_unknown(self):
        body = {"type": "something_else"}
        assert helpers.get_request_type(body) == ("unknown", "unknown")


# -----------------------------------------------------------------------
# slack_retry decorator
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# get_bot_info_from_event
# -----------------------------------------------------------------------


class TestGetBotInfoFromEvent:
    def test_extracts_username_and_icon(self):
        body = {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "bot_id": "B123",
                "username": "WeatherBot",
                "icons": {"image_48": "https://example.com/icon48.png"},
                "text": "hello",
            }
        }
        name, icon = helpers.get_bot_info_from_event(body)
        assert name == "WeatherBot"
        assert icon == "https://example.com/icon48.png"

    def test_fallback_name_when_no_username(self):
        body = {"event": {"type": "message", "subtype": "bot_message", "bot_id": "B123", "text": "hello"}}
        name, icon = helpers.get_bot_info_from_event(body)
        assert name == "Bot"
        assert icon is None

    def test_icon_fallback_order(self):
        body = {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "bot_id": "B123",
                "username": "MyBot",
                "icons": {"image_36": "https://example.com/icon36.png", "image_72": "https://example.com/icon72.png"},
                "text": "hello",
            }
        }
        name, icon = helpers.get_bot_info_from_event(body)
        assert icon == "https://example.com/icon36.png"


# -----------------------------------------------------------------------
# slack_retry decorator
# -----------------------------------------------------------------------


class TestSlackRetry:
    def test_success_on_first_try(self):
        @helpers.slack_retry
        def fn():
            return "ok"

        assert fn() == "ok"

    def test_retries_on_429(self):
        from slack_sdk.errors import SlackApiError

        call_count = 0

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "0"}

        @helpers.slack_retry
        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SlackApiError("rate_limited", response=mock_response)
            return "ok"

        assert fn() == "ok"
        assert call_count == 3

    def test_non_retryable_error_raises_immediately(self):
        from slack_sdk.errors import SlackApiError

        mock_response = MagicMock()
        mock_response.status_code = 404

        @helpers.slack_retry
        def fn():
            raise SlackApiError("not_found", response=mock_response)

        with pytest.raises(SlackApiError):
            fn()


# -----------------------------------------------------------------------
# resolve_channel_references
# -----------------------------------------------------------------------


class TestResolveChannelReferences:
    """Tests for helpers.resolve_channel_references (archive URL generation)."""

    def setup_method(self):
        helpers._CACHE.clear()

    def _make_workspace(self, team_id="T123", name="Acme"):
        ws = MagicMock()
        ws.team_id = team_id
        ws.workspace_name = name
        return ws

    def _make_client(self, channel_name="general", domain="acme"):
        client = MagicMock()
        client.conversations_info.return_value = {"channel": {"name": channel_name}}
        client.team_info.return_value = {"team": {"domain": domain}}
        return client

    def test_no_channel_refs_unchanged(self):
        result = helpers.resolve_channel_references("hello world", MagicMock())
        assert result == "hello world"

    def test_empty_text(self):
        result = helpers.resolve_channel_references("", MagicMock())
        assert result == ""

    def test_none_text(self):
        result = helpers.resolve_channel_references(None, MagicMock())
        assert result is None

    def test_archive_url_with_workspace(self):
        client = self._make_client(channel_name="general", domain="acme")
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references("see <#CABC123>", client, ws)
        assert "https://acme.slack.com/archives/CABC123" in result
        assert "#general (Acme)" in result

    def test_archive_url_without_workspace(self):
        client = self._make_client(channel_name="general", domain="acme")
        result = helpers.resolve_channel_references("see <#CABC123>", client, None)
        assert "#general" in result

    def test_fallback_when_domain_unavailable(self):
        client = MagicMock()
        client.conversations_info.return_value = {"channel": {"name": "general"}}
        client.team_info.side_effect = Exception("api error")
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references("see <#CABC123>", client, ws)
        assert result == "see `[#general (Acme)]`"
        assert "slack.com" not in result

    def test_fallback_when_channel_unresolvable(self):
        client = MagicMock()
        client.conversations_info.side_effect = Exception("channel_not_found")
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references("see <#CABC123>", client, ws)
        assert result == "see #CABC123"

    def test_channel_ref_with_label(self):
        client = self._make_client(channel_name="general", domain="acme")
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references("see <#CABC123|general>", client, ws)
        assert "https://acme.slack.com/archives/CABC123" in result

    def test_multiple_channel_refs(self):
        client = MagicMock()
        call_count = 0

        def conv_info(channel):
            nonlocal call_count
            call_count += 1
            names = {"CABC111": "alpha", "CABC222": "beta"}
            return {"channel": {"name": names.get(channel, channel)}}

        client.conversations_info.side_effect = conv_info
        client.team_info.return_value = {"team": {"domain": "acme"}}
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references(
            "see <#CABC111> and <#CABC222>", client, ws
        )
        assert "archives/CABC111" in result
        assert "archives/CABC222" in result
        assert "#alpha" in result
        assert "#beta" in result

    def test_no_app_redirect_in_output(self):
        client = self._make_client(channel_name="general", domain="acme")
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references("see <#CABC123>", client, ws)
        assert "app_redirect" not in result

    @patch("helpers.user_matching.find_synced_channel_in_target")
    def test_native_channel_when_synced_to_target(self, mock_find):
        mock_find.return_value = "C_LOCAL_TARGET"
        client = self._make_client(channel_name="general", domain="acme")
        ws = self._make_workspace(team_id="T123", name="Acme")
        result = helpers.resolve_channel_references(
            "see <#CSOURCE123>", client, ws, target_workspace_id=42
        )
        assert result == "see <#C_LOCAL_TARGET>"
        mock_find.assert_called_with("CSOURCE123", 42)
        assert "slack.com" not in result

    @patch("helpers.user_matching.find_synced_channel_in_target")
    def test_archive_mrkdwn_rewritten_to_native_when_synced(self, mock_find):
        mock_find.return_value = "C_LOCAL"
        client = MagicMock()
        text = "see <https://acme.slack.com/archives/CSRC|#general (Remote)>"
        result = helpers.resolve_channel_references(text, client, None, target_workspace_id=1)
        assert result == "see <#C_LOCAL>"

    @patch("helpers.user_matching.find_synced_channel_in_target")
    def test_archive_mrkdwn_unchanged_when_not_synced(self, mock_find):
        mock_find.return_value = None
        client = MagicMock()
        text = "see <https://acme.slack.com/archives/CSRC|#general (Remote)>"
        result = helpers.resolve_channel_references(text, client, None, target_workspace_id=1)
        assert result == text
