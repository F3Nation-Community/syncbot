"""Tests for federation inbound text resolution (mentions and channels)."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from db import schemas
from federation import api as federation_api


class TestResolveMentionsForFederated:
    def test_maps_via_user_mapping_target(self):
        m = MagicMock()
        m.target_user_id = "ULOCAL"
        m.source_display_name = "Alice"

        def fake_find(model, _filters):
            if model == schemas.UserMapping:
                return [m]
            return []

        with patch.object(federation_api.DbManager, "find_records", side_effect=fake_find):
            out = federation_api._resolve_mentions_for_federated("hi <@UREMOTE>", 10, "Partner WS")
        assert out == "hi <@ULOCAL>"

    def test_fallback_stub_mapping_display_name(self):
        m = MagicMock()
        m.target_user_id = None
        m.source_display_name = "Bob"

        def fake_find(model, _filters):
            if model == schemas.UserMapping:
                return [m]
            return []

        with patch.object(federation_api.DbManager, "find_records", side_effect=fake_find):
            out = federation_api._resolve_mentions_for_federated("hi <@UREMOTE>", 10, "Partner WS")
        assert out == "hi `[@Bob (Partner WS)]`"

    def test_fallback_user_directory_display_name(self):
        entry = MagicMock()
        entry.display_name = "Carol"
        entry.real_name = None

        def fake_find(model, _filters):
            if model == schemas.UserMapping:
                return []
            if model == schemas.UserDirectory:
                return [entry]
            return []

        with patch.object(federation_api.DbManager, "find_records", side_effect=fake_find):
            out = federation_api._resolve_mentions_for_federated("hey <@UX>", 10, "Remote")
        assert out == "hey `[@Carol (Remote)]`"

    def test_prefers_mapping_with_target_user_id(self):
        good = MagicMock()
        good.target_user_id = "UBEST"
        good.source_display_name = "Best"
        stale = MagicMock()
        stale.target_user_id = None
        stale.source_display_name = "Stale"

        def fake_find(model, _filters):
            if model == schemas.UserMapping:
                return [stale, good]
            return []

        with patch.object(federation_api.DbManager, "find_records", side_effect=fake_find):
            out = federation_api._resolve_mentions_for_federated("<@U1>", 10, "R")
        assert out == "<@UBEST>"
