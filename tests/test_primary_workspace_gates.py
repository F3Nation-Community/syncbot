"""Tests for PRIMARY_WORKSPACE backup gate and ENABLE_DB_RESET boolean."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from helpers.core import (  # noqa: E402
    is_backup_visible_for_workspace,
    is_db_reset_visible_for_workspace,
)


class TestIsBackupVisibleForWorkspace:
    def test_unset_primary_allows_all(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": ""}):
            assert is_backup_visible_for_workspace("T111") is True
            assert is_backup_visible_for_workspace(None) is True

    def test_matching_team_allowed(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "TABC123"}):
            assert is_backup_visible_for_workspace("TABC123") is True

    def test_non_matching_team_denied(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "TABC123"}):
            assert is_backup_visible_for_workspace("TOTHER") is False


class TestIsDbResetVisibleForWorkspace:
    def test_unset_primary_denies(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "", "ENABLE_DB_RESET": "true"}):
            assert is_db_reset_visible_for_workspace("T111") is False

    def test_primary_match_and_true_enables(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "TABC123", "ENABLE_DB_RESET": "true"}):
            assert is_db_reset_visible_for_workspace("TABC123") is True

    @pytest.mark.parametrize("truthy", ("true", "1", "yes"))
    def test_truthy_strings(self, truthy: str):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "TABC123", "ENABLE_DB_RESET": truthy}):
            assert is_db_reset_visible_for_workspace("TABC123") is True

    def test_unset_enable_db_reset_denies(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "TABC123"}, clear=False):
            os.environ.pop("ENABLE_DB_RESET", None)
            assert is_db_reset_visible_for_workspace("TABC123") is False

    def test_team_mismatch_denies(self):
        with patch.dict(os.environ, {"PRIMARY_WORKSPACE": "TABC123", "ENABLE_DB_RESET": "true"}):
            assert is_db_reset_visible_for_workspace("TOTHER") is False
