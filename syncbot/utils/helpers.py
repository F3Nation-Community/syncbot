import json
import os
from typing import Dict, List, Tuple
from slack_bolt.adapter.aws_lambda.lambda_s3_oauth_flow import LambdaS3OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk import WebClient
import slack_sdk
from utils.slack import actions
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
    sync_channel_record = DbManager.find_records(schemas.SyncChannel, [schemas.SyncChannel.channel_id == channel_id])
    if sync_channel_record:
        sync_channels = DbManager.find_join_records2(
            left_cls=schemas.SyncChannel,
            right_cls=schemas.Region,
            filters=[schemas.SyncChannel.sync_id == sync_channel_record[0].sync_id],
        )
    else:
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
    files: Dict[str, str] = None,
) -> Dict:
    slack_client = WebClient(bot_token)
    posted_from = f"({region_name})" if region_name else "(via SyncBot)"
    if update_ts:
        res = slack_client.chat_update(
            channel=channel_id,
            text=msg_text,
            ts=update_ts,
        )
    elif files:
        file_uploads = []
        for file in files:
            file_uploads.append(
                {
                    "file": file["path"],
                    "filename": file["name"],
                    "title": file["title"],
                }
            )
        res = slack_client.files_upload_v2(
            file_uploads=file_uploads,
            channels=channel_id,
            initial_comment=msg_text,
            username=f"{user_name} {posted_from}",
            icon_url=user_profile_url,
            thread_ts=thread_ts,
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


def get_request_type(body: dict) -> tuple[str]:
    request_type = safe_get(body, "type")
    if request_type == "event_callback":
        return ("event_callback", safe_get(body, "event", "type"))
    elif request_type == "block_actions":
        block_action = safe_get(body, "actions", 0, "action_id")
        if block_action[: len(actions.CONFIG_REMOVE_SYNC)] == actions.CONFIG_REMOVE_SYNC:
            block_action = actions.CONFIG_REMOVE_SYNC
        return ("block_actions", block_action)
    elif request_type == "view_submission":
        return ("view_submission", safe_get(body, "view", "callback_id"))
    elif not request_type and "command" in body:
        return ("command", safe_get(body, "command"))
    else:
        return ("unknown", "unknown")


def get_region_record(team_id: str, body: dict, context: dict, client: WebClient) -> schemas.Region:
    region_record: schemas.Region = DbManager.get_record(schemas.Region, id=team_id)
    team_domain = safe_get(body, "team", "domain")

    if not region_record:
        try:
            team_info = client.team_info()
            team_name = team_info["team"]["name"]
        except Exception:
            team_name = team_domain
        region_record: schemas.Region = DbManager.create_record(
            schemas.Region(
                team_id=team_id,
                workspace_name=team_name,
                bot_token=context["bot_token"],
            )
        )

    return region_record


def update_modal(
    blocks: List[dict],
    client: WebClient,
    view_id: str,
    title_text: str,
    callback_id: str,
    submit_button_text: str = "Submit",
    parent_metadata: dict = None,
    close_button_text: str = "Close",
    notify_on_close: bool = False,
):
    view = {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title_text},
        "submit": {"type": "plain_text", "text": submit_button_text},
        "close": {"type": "plain_text", "text": close_button_text},
        "notify_on_close": notify_on_close,
        "blocks": blocks,
    }
    if parent_metadata:
        view["private_metadata"] = json.dumps(parent_metadata)

    client.views_update(view_id=view_id, view=view)
