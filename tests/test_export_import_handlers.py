"""Focused unit tests for backup/restore and migration handler validation."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from handlers.export_import import handle_backup_restore_submit  # noqa: E402
from slack import actions  # noqa: E402


class TestBackupRestoreSubmitValidation:
    def test_returns_error_when_file_missing(self):
        client = MagicMock()
        logger = MagicMock()
        body = {"user": {"id": "U1"}, "view": {"state": {"values": {}}}}

        with patch("handlers.export_import._is_admin", return_value=True):
            resp = handle_backup_restore_submit(body, client, logger, context={})

        assert resp["response_action"] == "errors"
        assert actions.CONFIG_BACKUP_RESTORE_JSON_INPUT in resp["errors"]

    def test_returns_error_when_uploaded_file_has_no_url(self):
        client = MagicMock()
        logger = MagicMock()
        body = {
            "user": {"id": "U1"},
            "view": {
                "state": {
                    "values": {
                        actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: {
                            actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: {
                                "files": [{"id": "F123"}],
                            }
                        }
                    }
                }
            },
        }

        with patch("handlers.export_import._is_admin", return_value=True):
            resp = handle_backup_restore_submit(body, client, logger, context={})

        assert resp["response_action"] == "errors"
        assert "Could not retrieve the uploaded file." in resp["errors"][actions.CONFIG_BACKUP_RESTORE_JSON_INPUT]
