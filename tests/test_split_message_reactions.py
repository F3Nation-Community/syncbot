"""Tests for PostMeta rows on split text+file sync (reaction resolution)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from slack_sdk.web import WebClient

from handlers.messages import _handle_new_post, _handle_thread_reply


class TestSplitMessagePostMeta:
    def test_new_post_text_plus_file_stores_file_ts_same_post_id(self):
        logger = MagicMock()
        client = MagicMock(spec=WebClient)

        sc_source = SimpleNamespace(id=1, channel_id="C_SRC", sync_id=7)
        ws_source = SimpleNamespace(id=10, team_id="T1", bot_token="enc", workspace_name="A")
        sc_target = SimpleNamespace(id=2, channel_id="C_TGT", sync_id=7)
        ws_target = SimpleNamespace(id=20, team_id="T2", bot_token="enc", workspace_name="B")

        body = {
            "event": {
                "channel": "C_SRC",
                "ts": "100.000000",
                "team": "T1",
            }
        }
        ctx = {
            "team_id": "T1",
            "channel_id": "C_SRC",
            "msg_text": "hello",
            "mentioned_users": [],
            "user_id": "U1",
        }
        direct_files = [{"path": "/tmp/f.jpg", "name": "f.jpg"}]

        created: list = []

        def capture_post_meta(rows):
            created.extend(rows)

        with (
            patch("handlers.messages.helpers.get_sync_list", return_value=[(sc_source, ws_source), (sc_target, ws_target)]),
            patch("handlers.messages.helpers.get_user_info", return_value=("N", "http://i")),
            patch("handlers.messages.helpers.get_mapped_target_user_id", return_value=None),
            patch("handlers.messages.helpers.get_federated_workspace_for_sync", return_value=None),
            patch("handlers.messages.helpers.decrypt_bot_token", return_value="xoxb-test"),
            patch("handlers.messages.helpers.apply_mentioned_users", side_effect=lambda t, *a, **k: t),
            patch("handlers.messages.helpers.resolve_channel_references", side_effect=lambda t, *a, **k: t),
            patch("handlers.messages.helpers.get_workspace_by_id", return_value=None),
            patch(
                "handlers.messages.helpers.get_display_name_and_icon_for_synced_message",
                return_value=("N", None),
            ),
            patch("handlers.messages.helpers.post_message", return_value={"ts": "200.000000"}),
            patch("handlers.messages.helpers.upload_files_to_slack", return_value=(None, "300.000000")),
            patch("handlers.messages.helpers.cleanup_temp_files"),
            patch("handlers.messages.DbManager.create_records", side_effect=capture_post_meta),
        ):
            _handle_new_post(body, client, logger, ctx, [], [], direct_files)

        assert len(created) == 3
        assert {m.sync_channel_id for m in created} == {1, 2}
        target_rows = [m for m in created if m.sync_channel_id == 2]
        assert len(target_rows) == 2
        assert target_rows[0].post_id == target_rows[1].post_id
        assert {target_rows[0].ts, target_rows[1].ts} == {200.0, 300.0}

    def test_thread_reply_text_plus_file_stores_file_ts_same_post_id(self):
        logger = MagicMock()
        client = MagicMock(spec=WebClient)

        pm_src = SimpleNamespace(id=1, post_id="parent", ts=10.0)
        pm_tgt = SimpleNamespace(id=2, post_id="parent", ts=20.0)
        sc_source = SimpleNamespace(id=11, channel_id="C_SRC", sync_id=7)
        ws_source = SimpleNamespace(id=10, workspace_name="A", bot_token="enc")
        sc_target = SimpleNamespace(id=22, channel_id="C_TGT", sync_id=7)
        ws_target = SimpleNamespace(id=20, workspace_name="B", bot_token="enc")

        post_records = [(pm_src, sc_source, ws_source), (pm_tgt, sc_target, ws_target)]

        body = {"event": {"channel": "C_SRC", "ts": "150.000000"}}
        ctx = {
            "channel_id": "C_SRC",
            "msg_text": "reply",
            "mentioned_users": [],
            "user_id": "U1",
            "thread_ts": "10.000000",
        }
        direct_files = [{"path": "/tmp/f.jpg", "name": "f.jpg"}]

        created: list = []

        with (
            patch("handlers.messages.helpers.get_post_records", return_value=post_records),
            patch("handlers.messages.helpers.get_user_info", return_value=("N", "http://i")),
            patch("handlers.messages.helpers.get_mapped_target_user_id", return_value=None),
            patch("handlers.messages.helpers.get_federated_workspace_for_sync", return_value=None),
            patch("handlers.messages.helpers.decrypt_bot_token", return_value="xoxb-test"),
            patch("handlers.messages.helpers.apply_mentioned_users", side_effect=lambda t, *a, **k: t),
            patch("handlers.messages.helpers.resolve_channel_references", side_effect=lambda t, *a, **k: t),
            patch("handlers.messages.helpers.get_workspace_by_id", return_value=None),
            patch(
                "handlers.messages.helpers.get_display_name_and_icon_for_synced_message",
                return_value=("N", None),
            ),
            patch("handlers.messages.helpers.post_message", return_value={"ts": "250.000000"}),
            patch("handlers.messages.helpers.upload_files_to_slack", return_value=(None, "350.000000")),
            patch("handlers.messages.helpers.cleanup_temp_files"),
            patch("handlers.messages.DbManager.create_records", side_effect=lambda rows: created.extend(rows)),
        ):
            _handle_thread_reply(body, client, logger, ctx, [], direct_files)

        assert len(created) == 3
        target_rows = [m for m in created if m.sync_channel_id == 22]
        assert len(target_rows) == 2
        assert target_rows[0].post_id == target_rows[1].post_id
        assert {target_rows[0].ts, target_rows[1].ts} == {250.0, 350.0}
