"""Slack OAuth flow construction."""

import logging
import os

from slack_bolt.adapter.aws_lambda.lambda_s3_oauth_flow import LambdaS3OAuthFlow
from slack_bolt.oauth import OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk.oauth.installation_store import FileInstallationStore
from slack_sdk.oauth.state_store import FileOAuthStateStore

import constants

_logger = logging.getLogger(__name__)


def get_oauth_flow():
    """Build the Slack OAuth flow, choosing the right backend.

    - **Production (Lambda)**: Uses S3-backed stores.
    - **Local development with OAuth credentials**: Uses file-based stores.
    - **Local development without OAuth credentials**: Returns *None*.
    """
    client_id = os.environ.get(constants.SLACK_CLIENT_ID, "").strip()
    client_secret = os.environ.get(constants.SLACK_CLIENT_SECRET, "").strip()
    scopes_raw = os.environ.get(constants.SLACK_SCOPES, "").strip()

    if constants.LOCAL_DEVELOPMENT:
        if not (client_id and client_secret and scopes_raw):
            _logger.info("OAuth credentials not set — running in single-workspace mode")
            return None

        _logger.info("OAuth credentials found — enabling local OAuth flow (file-based stores)")
        base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".oauth-data")
        os.makedirs(base_dir, exist_ok=True)

        return OAuthFlow(
            settings=OAuthSettings(
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes_raw.split(","),
                installation_store=FileInstallationStore(
                    base_dir=os.path.join(base_dir, "installations"),
                    client_id=client_id,
                ),
                state_store=FileOAuthStateStore(
                    expiration_seconds=600,
                    base_dir=os.path.join(base_dir, "states"),
                    client_id=client_id,
                ),
            ),
        )
    else:
        return LambdaS3OAuthFlow(
            oauth_state_bucket_name=os.environ[constants.SLACK_STATE_S3_BUCKET_NAME],
            installation_bucket_name=os.environ[constants.SLACK_INSTALLATION_S3_BUCKET_NAME],
            settings=OAuthSettings(
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes_raw.split(","),
            ),
        )
