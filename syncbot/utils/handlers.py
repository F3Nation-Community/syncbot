import os

# import time
import uuid
from logging import Logger

from slack_sdk.web import WebClient
from utils import builders, constants, helpers
from utils.db import DbManager, schemas
from utils.slack import actions, forms, orm


def handle_remove_sync(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
):
    """Handles the "DeSync" button action by removing the SyncChannel record from the database.

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.
    """
    sync_channel_id = int(helpers.safe_get(body, "actions", 0, "value"))
    sync_channel_record = DbManager.get_record(schemas.SyncChannel, id=sync_channel_id)
    DbManager.delete_records(schemas.SyncChannel, [schemas.SyncChannel.id == sync_channel_id])
    try:
        client.conversations_leave(channel=sync_channel_record.channel_id)
    except Exception:
        pass
    builders.build_config_form(body, client, logger, context)


def respond_to_message_event(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Main function for handling message events.

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.
    """
    event_type = helpers.safe_get(body, "event", "type")
    event_subtype = helpers.safe_get(body, "event", "subtype")
    message_subtype = helpers.safe_get(body, "event", "message", "subtype") or helpers.safe_get(
        body, "event", "previous_message", "subtype"
    )
    team_id = helpers.safe_get(body, "team_id")
    channel_id = helpers.safe_get(body, "event", "channel")
    msg_text = helpers.safe_get(body, "event", "text") or helpers.safe_get(body, "event", "message", "text")
    msg_text = " " if (msg_text or "") == "" else msg_text
    mentioned_users = helpers.parse_mentioned_users(msg_text, client)
    user_id = helpers.safe_get(body, "event", "user") or helpers.safe_get(body, "event", "message", "user")
    thread_ts = helpers.safe_get(body, "event", "thread_ts")
    ts = (
        helpers.safe_get(body, "event", "message", "ts")
        or helpers.safe_get(body, "event", "previous_message", "ts")
        or helpers.safe_get(body, "event", "ts")
    )
    files = [
        file
        for file in helpers.safe_get(body, "event", "files")
        or helpers.safe_get(body, "event", "message", "files")
        or []
    ]
    photos = [photo for photo in files if helpers.safe_get(photo, "original_w")]
    if event_subtype in ["message_changed", "message_deleted"]:
        photo_names = [
            f"{photo['id']}.png" if photo['filetype'] == "heic" else f"{photo['id']}.{photo['filetype']}"
            for photo in photos
        ]
        photo_list = [{"url": f"{constants.S3_IMAGE_URL}{name}", "name": name} for name in photo_names]
    else:
        photo_list = helpers.upload_photos(files=photos, client=client, logger=logger)
    photo_blocks = [
        orm.ImageBlock(image_url=photo["url"], alt_text=photo["name"]).as_form_field() for photo in photo_list
    ]

    if (event_type == "message") and (message_subtype != "bot_message"):  # and (event_context not in EVENT_LIST):
        # EVENT_LIST.append(event_context)
        if (not event_subtype) or (event_subtype == "file_share" and msg_text != ""):
            post_list = []
            post_uuid = uuid.uuid4().bytes
            if not thread_ts:
                # handle new post
                sync_records = helpers.get_sync_list(team_id, channel_id)
                if not sync_records:
                    try:
                        client.chat_postMessage(
                            channel=channel_id,
                            text=":wave: Hello! I'm SyncBot. I was added to this channel, but this channel doesn't seem to be part of a Sync. Please use the `/config-syncbot` command to configure me.",
                        )
                        client.conversations_leave(channel=channel_id)
                    except Exception as e:
                        logger.error(e)
                    return
                user_name, user_profile_url = helpers.get_user_info(client, user_id)
                region_name = helpers.safe_get(
                    [record[1].workspace_name for record in sync_records if record[0].channel_id == channel_id], 0
                )
                for record in sync_records:
                    sync_channel, region = record
                    if sync_channel.channel_id == channel_id:
                        ts = helpers.safe_get(body, "event", "ts")
                    else:
                        msg_text = helpers.apply_mentioned_users(msg_text, client, mentioned_users)
                        res = helpers.post_message(
                            bot_token=region.bot_token,
                            channel_id=sync_channel.channel_id,
                            msg_text=msg_text,
                            user_name=user_name,
                            user_profile_url=user_profile_url,
                            region_name=region_name,
                            blocks=photo_blocks,
                        )
                        # if photos != []:
                        #     time.sleep(3)  # required so the next step catches the latest ts
                        #     posts = client.conversations_history(channel=sync_channel.channel_id, limit=1)
                        #     print(posts["messages"][0]["ts"])
                        #     # ts = posts["messages"][0]["ts"]
                        #     ts = helpers.safe_get(res, "ts") or helpers.safe_get(body, "event", "ts")
                        # else:
                        #     ts = helpers.safe_get(res, "ts") or helpers.safe_get(body, "event", "ts")
                        ts = helpers.safe_get(res, "ts") or helpers.safe_get(body, "event", "ts")
                    post_list.append(
                        schemas.PostMeta(
                            post_id=post_uuid,
                            sync_channel_id=sync_channel.id,
                            ts=float(ts),
                        )
                    )
                for photo in photo_list:
                    os.remove(photo["path"])
                DbManager.create_records(post_list)
            else:
                # handle threaded reply
                post_list = []
                post_uuid = uuid.uuid4().bytes
                post_records = helpers.get_post_records(thread_ts)
                region_name = helpers.safe_get(
                    [record[2].workspace_name for record in post_records if record[1].channel_id == channel_id], 0
                )
                for record in post_records:
                    post_meta, sync_channel, region = record
                    user_name, user_profile_url = helpers.get_user_info(client, user_id)
                    if sync_channel.channel_id == channel_id:
                        ts = helpers.safe_get(body, "event", "ts")
                    else:
                        msg_text = helpers.apply_mentioned_users(msg_text, client, mentioned_users)
                        res = helpers.post_message(
                            bot_token=region.bot_token,
                            channel_id=sync_channel.channel_id,
                            msg_text=msg_text,
                            user_name=user_name,
                            user_profile_url=user_profile_url,
                            thread_ts="{:.6f}".format(post_meta.ts),
                            region_name=region_name,
                            blocks=photo_blocks,
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
            region_name = helpers.safe_get(
                [record[2].workspace_name for record in post_records if record[1].channel_id == channel_id], 0
            )
            for record in post_records:
                post_meta, sync_channel, region = record
                if sync_channel.channel_id == channel_id:
                    continue
                else:
                    msg_text = helpers.apply_mentioned_users(msg_text, client, mentioned_users)
                    res = helpers.post_message(
                        bot_token=region.bot_token,
                        channel_id=sync_channel.channel_id,
                        msg_text=msg_text,
                        update_ts="{:.6f}".format(post_meta.ts),
                        region_name=region_name,
                        blocks=photo_blocks,
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


def handle_config_submission(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handles the config form submission (currently does nothing)

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.
    """
    pass


def handle_join_sync_submission(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handles the join sync form submission by appending to the SyncChannel table.

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.
    """
    form_data = forms.JOIN_SYNC_FORM.get_selected_values(body)
    sync_id = helpers.safe_get(form_data, actions.CONFIG_JOIN_SYNC_SELECT)
    channel_id = helpers.safe_get(form_data, actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT)
    team_id = helpers.safe_get(body, "view", "team_id")
    region_record: schemas.Region = DbManager.get_record(schemas.Region, id=team_id)
    sync_record: schemas.Sync = DbManager.get_record(schemas.Sync, id=sync_id)

    channel_sync_record = schemas.SyncChannel(
        sync_id=sync_id,
        channel_id=channel_id,
        region_id=region_record.id,
    )
    try:
        DbManager.create_record(channel_sync_record)
        client.conversations_join(channel=channel_id)
        client.chat_postMessage(
            channel=channel_id,
            text=f":wave: Hello! I'm SyncBot. I'll be keeping this channel in sync with *{sync_record.title}*.",
        )
    except Exception:
        body["error_message"] = "Your chosen channel is already part of a Sync. Please choose another channel."

    builders.build_config_form(body, client, logger, context)


def handle_new_sync_submission(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Handles the new sync form submission by appending to the Sync table.

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.
    """
    form_data = forms.NEW_SYNC_FORM.get_selected_values(body)
    sync_title = helpers.safe_get(form_data, actions.CONFIG_NEW_SYNC_TITLE)
    sync_description = helpers.safe_get(form_data, actions.CONFIG_NEW_SYNC_DESCRIPTION)

    sync_record = schemas.Sync(
        title=sync_title,
        description=sync_description,
    )
    DbManager.create_record(sync_record)


def check_join_sync_channel(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Checks to see if the chosen channel id is already part of a sync

    Args:
        body (dict): Event body from the invocation.
        client (WebClient): Slack WebClient object.
        logger (Logger): Logger object.
        context (dict): Context object.
    """
    view_id = helpers.safe_get(body, "view", "id")
    form_data = forms.JOIN_SYNC_FORM.get_selected_values(body)
    channel_id = helpers.safe_get(form_data, actions.CONFIG_JOIN_SYNC_CHANNEL_SELECT)
    blocks = helpers.safe_get(body, "view", "blocks")
    already_warning = constants.WARNING_BLOCK in [block["block_id"] for block in blocks]
    sync_channel_records = DbManager.find_records(schemas.SyncChannel, [schemas.SyncChannel.channel_id == channel_id])

    if len(sync_channel_records) > 0 and not already_warning:
        block = orm.SectionBlock(
            action=constants.WARNING_BLOCK,
            label=":warning: :warning: This channel is already part of a Sync! Please choose another channel.",
        ).as_form_field()
        print(block)
        blocks.append(
            orm.SectionBlock(
                action=constants.WARNING_BLOCK,
                label=":warning: :warning: This channel is already part of a Sync! Please choose another channel.",
            ).as_form_field()
        )
        helpers.update_modal(
            blocks=blocks,
            client=client,
            view_id=view_id,
            title_text="Join Sync",
            callback_id=actions.CONFIG_JOIN_SYNC_SUMBIT,
        )
    elif len(sync_channel_records) == 0 and already_warning:
        blocks = [block for block in blocks if block["block_id"] != constants.WARNING_BLOCK]
        helpers.update_modal(
            blocks=blocks,
            client=client,
            view_id=view_id,
            title_text="Join Sync",
            callback_id=actions.CONFIG_JOIN_SYNC_SUMBIT,
        )
