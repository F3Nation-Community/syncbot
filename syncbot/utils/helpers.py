import os
from typing import Dict, List, Tuple
from slack_bolt.adapter.aws_lambda.lambda_s3_oauth_flow import LambdaS3OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk import WebClient
import slack_sdk
from utils import constants
from utils.db import schemas, DbManager


def get_oauth_flow():
    if constants.LOCAL_DEVELOPMENT:
        return None
    else:
        return LambdaS3OAuthFlow(
            oauth_state_bucket_name=os.environ[constants.SLACK_STATE_S3_BUCKET_NAME],
            installation_bucket_name=os.environ[constants.SLACK_INSTALLATION_S3_BUCKET_NAME],
            settings=OAuthSettings(
                client_id=os.environ[constants.SLACK_CLIENT_ID],
                client_secret=os.environ[constants.SLACK_CLIENT_SECRET],
                scopes=os.environ[constants.SLACK_SCOPES].split(","),
            ),
        )


def safe_get(data, *keys):
    if not data:
        return None
    try:
        result = data
        for k in keys:
            if isinstance(k, int) and isinstance(result, list):
                result = result[k]
            elif result.get(k):
                result = result[k]
            else:
                return None
        return result
    except KeyError:
        return None


def get_sync_list(team_id: str, channel_id: str) -> List[Tuple[schemas.SyncChannel, schemas.Region]]:
    # TODO: this query is not exactly right
    sync = DbManager.find_records(
        schemas.SyncChannel, [schemas.SyncChannel.channel_id == channel_id, schemas.Region.team_id == team_id]
    )
    if sync:
        sync_channels = DbManager.find_join_records2(
            left_cls=schemas.SyncChannel,
            right_cls=schemas.Region,
            filters=[schemas.SyncChannel.sync_id == sync[0].sync_id],
        )
    else:
        # TODO: remove self from channel?
        sync_channels = []
    return sync_channels


def get_user_info(client: WebClient, user_id: str) -> Tuple[str, str]:
    try:
        res = client.users_info(user=user_id)
    except slack_sdk.errors.SlackApiError:
        return None, None

    user_name = (
        safe_get(res, "user", "profile", "display_name") or safe_get(res, "user", "profile", "real_name") or None
    )
    user_profile_url = safe_get(res, "user", "profile", "image_192")
    return user_name, user_profile_url


def post_message(
    bot_token: str,
    channel_id: str,
    msg_text: str,
    user_name: str = None,
    user_profile_url: str = None,
    thread_ts: str = None,
    update_ts: str = None,
    region_name: str = None,
) -> Dict:
    slack_client = WebClient(bot_token)
    posted_from = f"({region_name})" if region_name else "(via SyncBot)"
    if update_ts:
        res = slack_client.chat_update(
            channel=channel_id,
            text=msg_text,
            ts=update_ts,
        )
    else:
        res = slack_client.chat_postMessage(
            channel=channel_id,
            text=msg_text,
            username=f"{user_name} {posted_from}",
            icon_url=user_profile_url,
            thread_ts=thread_ts,
        )
    return res


def get_post_records(thread_ts: str) -> List[Tuple[schemas.PostMeta, schemas.SyncChannel, schemas.Region]]:
    post = DbManager.find_records(schemas.PostMeta, [schemas.PostMeta.ts == float(thread_ts)])
    if post:
        post_records = DbManager.find_join_records3(
            left_cls=schemas.PostMeta,
            right_cls1=schemas.SyncChannel,
            right_cls2=schemas.Region,
            filters=[schemas.PostMeta.post_id == post[0].post_id],
        )
    else:
        post_records = []
    return post_records


def delete_message(bot_token: str, channel_id: str, ts: str) -> Dict:
    slack_client = WebClient(bot_token)
    res = slack_client.chat_delete(
        channel=channel_id,
        ts=ts,
    )
    return res
