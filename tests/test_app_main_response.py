"""Unit tests for syncbot.app.view_ack and main_response (ack + lazy work)."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

import app as app_module  # noqa: E402
from slack import actions  # noqa: E402


def _body_view_submit(callback_id: str) -> dict:
    return {
        "type": "view_submission",
        "team_id": "T001",
        "view": {"callback_id": callback_id},
    }


class TestViewAck:
    """Production ``view_ack``: deferred views get custom ack kwargs."""

    def test_returns_dict_uses_ack_kwargs(self):
        ack = MagicMock()
        context: dict = {}

        def ack_handler(b, c, ctx):
            return {
                "response_action": "errors",
                "errors": {actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: "bad"},
            }

        custom = {actions.CONFIG_BACKUP_RESTORE_SUBMIT: ack_handler}
        with patch.object(app_module, "VIEW_ACK_MAPPER", custom):
            app_module.view_ack(
                _body_view_submit(actions.CONFIG_BACKUP_RESTORE_SUBMIT),
                MagicMock(),
                MagicMock(),
                ack,
                context,
            )

        ack.assert_called_once()
        assert ack.call_args.kwargs["response_action"] == "errors"
        assert "errors" in ack.call_args.kwargs

    def test_returns_none_calls_empty_ack(self):
        ack = MagicMock()
        context: dict = {}

        def ack_handler(b, c, ctx):
            return None

        custom = {actions.CONFIG_PUBLISH_MODE_SUBMIT: ack_handler}
        with patch.object(app_module, "VIEW_ACK_MAPPER", custom):
            app_module.view_ack(
                _body_view_submit(actions.CONFIG_PUBLISH_MODE_SUBMIT),
                MagicMock(),
                MagicMock(),
                ack,
                context,
            )

        ack.assert_called_once_with()

    def test_unknown_callback_calls_empty_ack(self):
        ack = MagicMock()
        context: dict = {}
        with patch.object(app_module, "VIEW_ACK_MAPPER", {}):
            app_module.view_ack(_body_view_submit("unknown_callback"), MagicMock(), MagicMock(), ack, context)
        ack.assert_called_once_with()


class TestMainResponseLocalDevViewSubmission:
    """With LOCAL_DEVELOPMENT, main_response runs ack + work in one call."""

    @patch.object(app_module, "LOCAL_DEVELOPMENT", True)
    def test_non_deferred_ack_before_handler(self):
        ack = MagicMock()
        context: dict = {}

        def handler(b, c, log, ctx):
            assert ack.call_count == 1
            return None

        cid = actions.CONFIG_NEW_SYNC_SUBMIT
        custom = {cid: handler}
        with (
            patch.object(app_module, "MAIN_MAPPER", {"view_submission": custom}),
            patch.object(app_module, "emit_metric"),
        ):
            app_module.main_response(_body_view_submit(cid), MagicMock(), MagicMock(), ack, context)

        ack.assert_called_once_with()


class TestMainResponseProdViewSubmission:
    """Production main_response (lazy): does not call ack for view_submission."""

    @patch.object(app_module, "LOCAL_DEVELOPMENT", False)
    def test_view_submission_skips_ack_in_main_response(self):
        ack = MagicMock()
        context: dict = {}

        def handler(b, c, log, ctx):
            return None

        cid = actions.CONFIG_NEW_SYNC_SUBMIT
        custom = {cid: handler}
        with (
            patch.object(app_module, "MAIN_MAPPER", {"view_submission": custom}),
            patch.object(app_module, "emit_metric"),
        ):
            app_module.main_response(_body_view_submit(cid), MagicMock(), MagicMock(), ack, context)

        ack.assert_not_called()
