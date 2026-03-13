"""Slack OAuth flow construction."""

import logging
import os

from slack_bolt.oauth import OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk.oauth.installation_store.sqlalchemy import SQLAlchemyInstallationStore
from slack_sdk.oauth.state_store.sqlalchemy import SQLAlchemyOAuthStateStore

import constants

_logger = logging.getLogger(__name__)

_OAUTH_STATE_EXPIRATION_SECONDS = 600


def get_oauth_flow():
    """Build the Slack OAuth flow using MySQL-backed stores.

    Uses the same RDS/MySQL connection as the rest of the app. Works for both
    local development and production (Lambda). If OAuth credentials are not
    set and LOCAL_DEVELOPMENT is true, returns None (single-workspace mode).
    """
    client_id = os.environ.get(constants.SLACK_CLIENT_ID, "").strip()
    client_secret = os.environ.get(constants.SLACK_CLIENT_SECRET, "").strip()
    scopes_raw = os.environ.get(constants.SLACK_SCOPES, "").strip()

    if constants.LOCAL_DEVELOPMENT and not (client_id and client_secret and scopes_raw):
        _logger.info("OAuth credentials not set — running in single-workspace mode")
        return None

    from db import get_engine

    engine = get_engine()
    installation_store = SQLAlchemyInstallationStore(
        client_id=client_id,
        engine=engine,
    )
    state_store = SQLAlchemyOAuthStateStore(
        expiration_seconds=_OAUTH_STATE_EXPIRATION_SECONDS,
        engine=engine,
    )

    return OAuthFlow(
        settings=OAuthSettings(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes_raw.split(","),
            installation_store=installation_store,
            state_store=state_store,
        ),
    )
