"""Unit tests for OAuth flow construction."""

import os
from unittest.mock import patch

os.environ.setdefault("DATABASE_HOST", "localhost")
os.environ.setdefault("DATABASE_USER", "root")
os.environ.setdefault("DATABASE_PASSWORD", "test")
os.environ.setdefault("DATABASE_SCHEMA", "syncbot")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-0-0")

from helpers.oauth import get_oauth_flow


class TestGetOAuthFlow:
    @patch("helpers.oauth.constants.LOCAL_DEVELOPMENT", True)
    @patch.dict(os.environ, {}, clear=True)
    def test_local_dev_without_oauth_credentials_returns_none(self):
        assert get_oauth_flow() is None

    @patch("helpers.oauth.constants.LOCAL_DEVELOPMENT", True)
    @patch.dict(
        os.environ,
        {
            "ENV_SLACK_CLIENT_ID": "cid",
            "ENV_SLACK_CLIENT_SECRET": "csecret",
            "ENV_SLACK_SCOPES": "chat:write,channels:read",
        },
        clear=True,
    )
    @patch("db.get_engine")
    @patch("helpers.oauth.SQLAlchemyOAuthStateStore")
    @patch("helpers.oauth.SQLAlchemyInstallationStore")
    def test_local_dev_with_credentials_uses_sql_stores(
        self,
        mock_installation_store_cls,
        mock_state_store_cls,
        mock_get_engine,
    ):
        engine = object()
        mock_get_engine.return_value = engine

        flow = get_oauth_flow()

        assert flow is not None
        mock_get_engine.assert_called_once_with()
        mock_installation_store_cls.assert_called_once_with(client_id="cid", engine=engine)
        mock_state_store_cls.assert_called_once_with(expiration_seconds=600, engine=engine)

    @patch("helpers.oauth.constants.LOCAL_DEVELOPMENT", False)
    @patch.dict(
        os.environ,
        {
            "ENV_SLACK_CLIENT_ID": "prod-cid",
            "ENV_SLACK_CLIENT_SECRET": "prod-secret",
            "ENV_SLACK_SCOPES": "chat:write,groups:read",
        },
        clear=True,
    )
    @patch("db.get_engine")
    @patch("helpers.oauth.SQLAlchemyOAuthStateStore")
    @patch("helpers.oauth.SQLAlchemyInstallationStore")
    def test_production_uses_sql_stores_without_s3(
        self,
        mock_installation_store_cls,
        mock_state_store_cls,
        mock_get_engine,
    ):
        engine = object()
        mock_get_engine.return_value = engine

        flow = get_oauth_flow()

        assert flow is not None
        mock_get_engine.assert_called_once_with()
        mock_installation_store_cls.assert_called_once_with(client_id="prod-cid", engine=engine)
        mock_state_store_cls.assert_called_once_with(expiration_seconds=600, engine=engine)
