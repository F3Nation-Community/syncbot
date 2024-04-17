import json
import os
import re
from logging import Logger
from typing import Dict, List, Tuple

import boto3
import requests
import slack_sdk
from PIL import Image
from pillow_heif import register_heif_opener
from slack_bolt.adapter.aws_lambda.lambda_s3_oauth_flow import LambdaS3OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk import WebClient
from utils import constants
from utils.db import DbManager, schemas
from utils.slack import actions

register_heif_opener()


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
    blocks: List[dict] = None,
) -> Dict:
    slack_client = WebClient(bot_token)
    posted_from = f"({region_name})" if region_name else "(via SyncBot)"
    if blocks:
        # msg_block = orm.SectionBlock(label=msg_text).as_form_field()
        msg_block = {"type": "section", "text": {"type": "mrkdwn", "text": msg_text}}
        blocks.insert(0, msg_block)
    if update_ts:
        res = slack_client.chat_update(
            channel=channel_id,
            text=msg_text,
            ts=update_ts,
            blocks=blocks,
        )
    else:
        res = slack_client.chat_postMessage(
            channel=channel_id,
            text=msg_text,
            username=f"{user_name} {posted_from}",
            icon_url=user_profile_url,
            thread_ts=thread_ts,
            blocks=blocks,
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


def upload_photos(files: List[dict], client: WebClient, logger: Logger) -> List[dict]:
    uploaded_photos = []
    photos = [file for file in files if file["mimetype"][:5] == "image"]
    for photo in photos:
        try:
            # Download photo
            # Try to get a medium size photo first, then fallback to smaller sizes
            r = requests.get(
                photo.get("thumb_480") or photo.get("thumb_360") or photo.get("thumb_80") or photo.get("url_private"),
                headers={"Authorization": f"Bearer {client.token}"},
            )
            r.raise_for_status()

            file_name = f"{photo['id']}.{photo['filetype']}"
            file_path = f"/tmp/{file_name}"
            file_mimetype = photo["mimetype"]

            # Save photo to disk
            with open(file_path, "wb") as f:
                f.write(r.content)

            # Convert HEIC to PNG
            if photo["filetype"] == "heic":
                heic_img = Image.open(file_path)
                x, y = heic_img.size
                coeff = min(constants.MAX_HEIF_SIZE / max(x, y), 1)
                heic_img = heic_img.resize((int(x * coeff), int(y * coeff)))
                heic_img.save(file_path.replace(".heic", ".png"), quality=95, optimize=True, format="PNG")
                os.remove(file_path)

                file_path = file_path.replace(".heic", ".png")
                file_name = file_name.replace(".heic", ".png")
                file_mimetype = "image/png"

            # Upload photo to S3
            if constants.LOCAL_DEVELOPMENT:
                s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=os.environ[constants.AWS_ACCESS_KEY_ID],
                    aws_secret_access_key=os.environ[constants.AWS_SECRET_ACCESS_KEY],
                )
            else:
                s3_client = boto3.client("s3")

            with open(file_path, "rb") as f:
                s3_client.upload_fileobj(
                    f, constants.S3_IMAGE_BUCKET, file_name, ExtraArgs={"ContentType": file_mimetype}
                )
                uploaded_photos.append(
                    {
                        "url": f"{constants.S3_IMAGE_URL}{file_name}",
                        "name": file_name,
                        "path": file_path,
                    }
                )
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
    return uploaded_photos


def parse_mentioned_users(msg_text: str, client: WebClient) -> List[Dict]:

    user_ids = re.findall(r"<@(\w+)>", msg_text or "")

    if user_ids != []:
        try:
            members = client.users_list()["members"]
        except slack_sdk.errors.SlackApiError:
            # TODO: rate limited, use client.user_info() to get individual user info
            members = []
        member_dict = {}
        for member in members:
            user_name = (
                member["profile"]["real_name"]
                if member["profile"]["display_name"] != ""
                else member["profile"]["display_name"]
            )
            member_dict.update({member["id"]: {"user_name": user_name, "email": safe_get(member, "profile", "email")}})

    return [member_dict[user_id] for user_id in user_ids]


def apply_mentioned_users(msg_text: str, client: WebClient, mentioned_user_info: List[Dict]) -> List[Dict]:

    email_list = [user["email"] for user in mentioned_user_info]
    msg_text = msg_text or ""

    if email_list == []:
        return msg_text
    else:
        try:
            members = client.users_list()["members"]
        except slack_sdk.errors.SlackApiError:
            # TODO: rate limited, use client.user_info() to get individual user info
            members = []
        member_dict = {
            member["profile"].get("email"): member["id"] for member in members if member["profile"].get("email")
        }

        replace_list = []
        for index, email in enumerate(email_list):
            user_id = member_dict.get(email)
            if user_id:
                replace_list.append(f"<@{user_id}>")
            else:
                replace_list.append(f"@{mentioned_user_info[index]['user_name']}")

        pattern = r"<@\w+>"
        return re.sub(pattern, "{}", msg_text).format(*replace_list)
