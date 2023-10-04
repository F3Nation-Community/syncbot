import json
import uuid
from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
from utils import constants
from utils.db import DbManager, schemas
from utils import helpers
import logging

SlackRequestHandler.clear_all_log_handlers()
logger = logging.getLogger()
logger.setLevel(level=logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)

app = App(
    process_before_response=not constants.LOCAL_DEVELOPMENT,
    oauth_flow=helpers.get_oauth_flow(),
)


def handler(event, context):
    slack_request_handler = SlackRequestHandler(app=app)
    return slack_request_handler.handle(event, context)


@app.event("message")
def respond_to_event(body, logger, client, ack):
    ack()
    logging.info(f"Received an event: {json.dumps(body, indent=2)}")
    event_type = helpers.safe_get(body, "event", "type")
    event_subtype = helpers.safe_get(body, "event", "subtype")
    message_subtype = helpers.safe_get(body, "event", "message", "subtype") or helpers.safe_get(
        body, "event", "previous_message", "subtype"
    )
    team_id = helpers.safe_get(body, "team_id")
    channel_id = helpers.safe_get(body, "event", "channel")
    msg_text = helpers.safe_get(body, "event", "text") or helpers.safe_get(body, "event", "message", "text")
    user_id = helpers.safe_get(body, "event", "user") or helpers.safe_get(body, "event", "message", "user")
    thread_ts = helpers.safe_get(body, "event", "thread_ts")
    ts = helpers.safe_get(body, "event", "message", "ts") or helpers.safe_get(body, "event", "previous_message", "ts")
    helpers.safe_get(body, "event", "message", "parent_user_id")
    helpers.safe_get(body, "event_context")

    if (event_type == "message") and (message_subtype != "bot_message"):  # and (event_context not in EVENT_LIST):
        # EVENT_LIST.append(event_context)
        if (not event_subtype) or (event_subtype == "file_share" and msg_text != ""):
            post_list = []
            post_uuid = uuid.uuid4().bytes
            if not thread_ts:
                # handle new post
                sync_records = helpers.get_sync_list(team_id, channel_id)
                for record in sync_records:
                    sync_channel, region = record
                    user_name, user_profile_url = helpers.get_user_info(client, user_id)
                    if sync_channel.channel_id == channel_id:
                        ts = helpers.safe_get(body, "event", "ts")
                    else:
                        res = helpers.post_message(
                            bot_token=region.bot_token,
                            channel_id=sync_channel.channel_id,
                            msg_text=msg_text,
                            user_name=user_name,
                            user_profile_url=user_profile_url,
                            region_name=region.workspace_name,
                        )
                        ts = helpers.safe_get(res, "ts")
                    post_list.append(
                        schemas.PostMeta(
                            post_id=post_uuid,
                            sync_channel_id=sync_channel.id,
                            ts=float(ts),
                        )
                    )
                DbManager.create_records(post_list)
            else:
                # handle threaded reply
                post_list = []
                post_uuid = uuid.uuid4().bytes
                post_records = helpers.get_post_records(thread_ts)
                for record in post_records:
                    post_meta, sync_channel, region = record
                    user_name, user_profile_url = helpers.get_user_info(client, user_id)
                    if sync_channel.channel_id == channel_id:
                        ts = helpers.safe_get(body, "event", "ts")
                    else:
                        res = helpers.post_message(
                            bot_token=region.bot_token,
                            channel_id=sync_channel.channel_id,
                            msg_text=msg_text,
                            user_name=user_name,
                            user_profile_url=user_profile_url,
                            thread_ts="{:.6f}".format(post_meta.ts),
                            region_name=region.workspace_name,
                        )
                        ts = helpers.safe_get(res, "ts")
                    post_list.append(
                        schemas.PostMeta(
                            post_id=post_uuid,
                            sync_channel_id=sync_channel.id,
                            ts=float(ts),
                        )
                    )
                DbManager.create_records(post_list)

        elif event_subtype == "message_changed":
            # handle edited message
            post_records = helpers.get_post_records(ts)
            for record in post_records:
                post_meta, sync_channel, region = record
                if sync_channel.channel_id == channel_id:
                    continue
                else:
                    res = helpers.post_message(
                        bot_token=region.bot_token,
                        channel_id=sync_channel.channel_id,
                        msg_text=msg_text,
                        update_ts="{:.6f}".format(post_meta.ts),
                        region_name=region.workspace_name,
                    )
        elif event_subtype == "message_deleted":
            # handle deleted message
            post_records = helpers.get_post_records(ts)
            for record in post_records:
                post_meta, sync_channel, region = record
                if sync_channel.channel_id == channel_id:
                    continue
                else:
                    res = helpers.delete_message(
                        bot_token=region.bot_token,
                        channel_id=sync_channel.channel_id,
                        ts="{:.6f}".format(post_meta.ts),
                    )


if __name__ == "__main__":
    app.start(3000)
