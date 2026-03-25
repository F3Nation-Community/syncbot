"""Unit tests for syncbot.app.main_response ack semantics (deferred vs immediate)."""

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


class TestMainResponseDeferredAck:
    """Deferred ack views must receive ack(**result) or context['ack'](...) in the same dispatch."""

    def test_returns_dict_uses_ack_kwargs(self):
        ack = MagicMock()
        context: dict = {}

        def handler(b, c, log, ctx):
            return {
                "response_action": "errors",
                "errors": {actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: "bad"},
            }

        custom = {actions.CONFIG_BACKUP_RESTORE_SUBMIT: handler}
        with (
            patch.object(app_module, "MAIN_MAPPER", {"view_submission": custom}),
            patch.object(app_module, "emit_metric"),
        ):
            app_module.main_response(
                _body_view_submit(actions.CONFIG_BACKUP_RESTORE_SUBMIT),
                MagicMock(),
                MagicMock(),
                ack,
                context,
            )

        ack.assert_called_once()
        assert ack.call_args.kwargs["response_action"] == "errors"
        assert "errors" in ack.call_args.kwargs

    def test_context_ack_called_skips_fallback(self):
        ack = MagicMock()
        context: dict = {}

        def handler(b, c, log, ctx):
            ctx["ack"](response_action="update", view={"type": "modal", "callback_id": "x"})

        custom = {actions.CONFIG_PUBLISH_MODE_SUBMIT: handler}
        with (
            patch.object(app_module, "MAIN_MAPPER", {"view_submission": custom}),
            patch.object(app_module, "emit_metric"),
        ):
            app_module.main_response(
                _body_view_submit(actions.CONFIG_PUBLISH_MODE_SUBMIT),
                MagicMock(),
                MagicMock(),
                ack,
                context,
            )

        ack.assert_called_once_with(response_action="update", view={"type": "modal", "callback_id": "x"})

    def test_no_ack_no_dict_logs_warning_and_calls_empty_ack(self):
        ack = MagicMock()
        context: dict = {}

        def handler(b, c, log, ctx):
            return None

        custom = {actions.CONFIG_PUBLISH_MODE_SUBMIT: handler}
        with (
            patch.object(app_module, "MAIN_MAPPER", {"view_submission": custom}),
            patch.object(app_module, "emit_metric"),
            patch.object(app_module, "_logger") as mock_log,
        ):
            app_module.main_response(
                _body_view_submit(actions.CONFIG_PUBLISH_MODE_SUBMIT),
                MagicMock(),
                MagicMock(),
                ack,
                context,
            )

        warn_text = " ".join(
            str(c.args[0]) if c.args else "" for c in mock_log.warning.call_args_list
        )
        assert "deferred_view_ack_fallback" in warn_text
        ack.assert_called_once_with()


class TestMainResponseImmediateAck:
    """Non-deferred view_submission: ack() runs before handler."""

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
