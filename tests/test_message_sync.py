"""Tests for sync list / post record deduplication and join-sync duplicate guard."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from handlers.sync import handle_join_sync_submission
from helpers.slack_api import get_post_records
from helpers.workspace import get_sync_list
from slack import actions


class TestGetSyncListDeduplication:
    def test_deduplicates_same_workspace_and_channel(self):
        ws = SimpleNamespace(id=42, team_id="T1", workspace_name="WS")
        sc_source = SimpleNamespace(id=1, sync_id=7, channel_id="Csource")
        sc_dup_a = SimpleNamespace(id=2, sync_id=7, channel_id="C999")
        sc_dup_b = SimpleNamespace(id=3, sync_id=7, channel_id="C999")

        with (
            patch("helpers.workspace._cache_get", return_value=None),
            patch("helpers.workspace._cache_set") as cache_set,
            patch("helpers.workspace.DbManager.find_records", return_value=[sc_source]),
            patch(
                "helpers.workspace.DbManager.find_join_records2",
                return_value=[(sc_dup_a, ws), (sc_dup_b, ws)],
            ),
        ):
            result = get_sync_list("T1", "Csource")

        assert len(result) == 1
        assert result[0][0] is sc_dup_a
        assert result[0][1] is ws  # first wins among duplicates
        cache_set.assert_called_once()


class TestGetPostRecordsDeduplication:
    def test_deduplicates_same_workspace_and_channel(self):
        pm = SimpleNamespace(id=1, post_id="p1", ts=123.456789)
        ws = SimpleNamespace(id=42)
        sc_a = SimpleNamespace(id=10, channel_id="C777")
        sc_b = SimpleNamespace(id=11, channel_id="C777")

        with (
            patch("helpers.slack_api.DbManager.find_records", return_value=[pm]),
            patch(
                "helpers.slack_api.DbManager.find_join_records3",
                return_value=[(pm, sc_a, ws), (pm, sc_b, ws)],
            ),
        ):
            result = get_post_records("123.456789")

        assert len(result) == 1
        assert result[0][1] is sc_a

    def test_dedup_prefers_lower_post_meta_id_for_split_file_alias(self):
        """Reactions on file thread replies share post_id; primary text row must win."""
        pm_file = SimpleNamespace(id=99, post_id="p1", ts=888.888)
        pm_text = SimpleNamespace(id=10, post_id="p1", ts=111.111)
        ws = SimpleNamespace(id=42)
        sc = SimpleNamespace(id=10, channel_id="C777")

        with (
            patch("helpers.slack_api.DbManager.find_records", return_value=[pm_file]),
            patch(
                "helpers.slack_api.DbManager.find_join_records3",
                return_value=[(pm_file, sc, ws), (pm_text, sc, ws)],
            ),
        ):
            result = get_post_records("888.888")

        assert len(result) == 1
        assert result[0][0].id == 10
        assert result[0][0].ts == 111.111


class TestJoinSyncDuplicateSkip:
    def test_duplicate_channel_skips_join_and_create(self):
        client = MagicMock()
        logger = MagicMock()
        context = {}
        workspace = SimpleNamespace(id=10, team_id="T1")
        sync_record = SimpleNamespace(id=5, title="Other")

        body = {
            "user": {"id": "Uadmin"},
            "view": {"team_id": "T1", "state": {"values": {}}},
        }
        form_values = {
            actions.CONFIG_JOIN_SYNC_SELECT: 5,
            actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT: "Cdup",
        }

        with (
            patch("handlers.sync.helpers.get_user_id_from_body", return_value="Uadmin"),
            patch("handlers.sync.helpers.is_user_authorized", return_value=True),
            patch("handlers.sync.forms.JOIN_SYNC_FORM.get_selected_values", return_value=form_values),
            patch("handlers.sync.DbManager.get_record", side_effect=[workspace, sync_record]),
            patch("handlers.sync.DbManager.find_records", return_value=[object()]),
            patch("handlers.sync.DbManager.create_record") as create_record,
            patch("handlers.sync.helpers.format_admin_label", return_value=("Admin", "Admin")),
            patch("handlers.sync.builders.refresh_home_tab_for_workspace") as refresh_home,
        ):
            handle_join_sync_submission(body, client, logger, context)

        create_record.assert_not_called()
        client.conversations_join.assert_not_called()
        refresh_home.assert_called_once()
