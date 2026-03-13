"""Message sync handlers — new posts, replies, edits, deletes, reactions."""

import logging
import uuid
from logging import Logger

from slack_sdk.web import WebClient

import federation
import helpers
from db import DbManager, schemas
from handlers._common import EventContext
from logger import emit_metric
from slack import orm


def _find_source_workspace_id(records: list[tuple], channel_id: str, ws_index: int = 1) -> int | None:
    """Return the workspace ID from the record whose channel matches *channel_id*."""
    for rec in records:
        sc = rec[ws_index - 1] if ws_index > 1 else rec[0]
        ws = rec[ws_index]
        if sc.channel_id == channel_id:
            return ws.id
    return None

_logger = logging.getLogger(__name__)


def _parse_event_fields(body: dict, client: WebClient) -> EventContext:
    """Extract the common fields every message handler needs."""
    event: dict = body.get("event", {})
    msg_text: str = helpers.safe_get(event, "text") or helpers.safe_get(event, "message", "text")
    msg_text = msg_text if msg_text else " "

    return EventContext(
        team_id=helpers.safe_get(body, "team_id"),
        channel_id=helpers.safe_get(event, "channel"),
        user_id=(helpers.safe_get(event, "user") or helpers.safe_get(event, "message", "user")),
        msg_text=msg_text,
        mentioned_users=helpers.parse_mentioned_users(msg_text, client),
        thread_ts=helpers.safe_get(event, "thread_ts"),
        ts=(
            helpers.safe_get(event, "message", "ts")
            or helpers.safe_get(event, "previous_message", "ts")
            or helpers.safe_get(event, "ts")
        ),
        event_subtype=helpers.safe_get(event, "subtype"),
    )


def _build_file_context(body: dict, client: WebClient, logger: Logger) -> tuple[list[dict], list[dict], list[dict]]:
    """Process files attached to a message event.

    Returns ``(photo_list, photo_blocks, direct_files)`` where:

    * *photo_list* — always [] (kept for cleanup API; no S3).
    * *photo_blocks* — Slack Block Kit ``image`` blocks for inline images
      (e.g. GIF picker URLs), ready for ``chat.postMessage``.
    * *direct_files* — files downloaded to ``/tmp`` for direct upload to
      each target channel via ``files_upload_v2``.
    """
    event = body.get("event", {})
    files = (helpers.safe_get(event, "files") or helpers.safe_get(event, "message", "files") or [])[:20]
    event_subtype = helpers.safe_get(event, "subtype")

    images = [f for f in files if f.get("mimetype", "").startswith("image")]
    videos = [f for f in files if f.get("mimetype", "").startswith("video")]

    photo_blocks: list[dict] = []
    direct_files: list[dict] = []

    is_edit = event_subtype in ("message_changed", "message_deleted")

    if not is_edit:
        direct_files = helpers.download_slack_files(images + videos, client, logger)

    # Handle GIFs/images from attachments (e.g. GIPHY bot, Slack GIF picker,
    # unfurled URLs) when no file attachments are present.  We always use
    # image blocks for these since the URLs are publicly accessible — this
    # avoids a download/re-upload round-trip and gives us a proper message
    # ts for PostMeta so reactions work correctly.
    if not files and not is_edit:
        attachments = event.get("attachments") or helpers.safe_get(event, "message", "attachments") or []
        for att in attachments:
            img_url = att.get("image_url") or att.get("thumb_url")

            # Slack's built-in GIF picker nests the image inside blocks
            if not img_url:
                for blk in att.get("blocks") or []:
                    if blk.get("type") == "image" and blk.get("image_url"):
                        img_url = blk["image_url"]
                        break

            # Also check top-level event blocks for image blocks
            if not img_url:
                for blk in event.get("blocks") or []:
                    if blk.get("type") == "image" and blk.get("image_url"):
                        img_url = blk["image_url"]
                        break

            if not img_url:
                _logger.info(
                    "attachment_no_image_url", extra={"att_keys": list(att.keys()), "fallback": att.get("fallback")}
                )
                continue

            name = att.get("fallback") or "attachment.gif"
            photo_blocks.append(orm.ImageBlock(image_url=img_url, alt_text=name).as_form_field())

    return [], photo_blocks, direct_files


def _get_workspace_name(records: list, channel_id: str, workspace_index: int) -> str | None:
    """Pull the workspace name for the originating channel from a record list."""
    return helpers.safe_get(
        [r[workspace_index].workspace_name for r in records if r[workspace_index - 1].channel_id == channel_id],
        0,
    )


def _handle_new_post(
    body: dict,
    client: WebClient,
    logger: Logger,
    ctx: EventContext,
    photo_list: list[dict],
    photo_blocks: list[dict],
    direct_files: list[dict] | None = None,
) -> None:
    """Sync a brand-new top-level message to all linked channels."""
    team_id = ctx["team_id"]
    channel_id = ctx["channel_id"]
    msg_text = ctx["msg_text"]
    mentioned_users = ctx["mentioned_users"]
    user_id = ctx["user_id"]

    sync_records = helpers.get_sync_list(team_id, channel_id)
    if not sync_records:
        any_sync_channel = DbManager.find_records(
            schemas.SyncChannel,
            [
                schemas.SyncChannel.channel_id == channel_id,
                schemas.SyncChannel.deleted_at.is_(None),
            ],
        )
        if any_sync_channel:
            return
        if user_id:
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    text=":wave: Hello! I'm SyncBot. I was added to this Channel, but this Channel "
                    "doesn't seem to be part of a Sync. I'm leaving now. Please open the SyncBot Home "
                    "tab to configure me.",
                )
                client.conversations_leave(channel=channel_id)
            except Exception as e:
                logger.error(f"Failed to notify and leave unconfigured channel {channel_id}: {e}")
        return

    if user_id:
        user_name, user_profile_url = helpers.get_user_info(client, user_id)
    else:
        user_name, user_profile_url = helpers.get_bot_info_from_event(body)

    workspace_name = _get_workspace_name(sync_records, channel_id, workspace_index=1)
    posted_from = f"({workspace_name})" if workspace_name else "(via SyncBot)"

    post_uuid = uuid.uuid4().hex
    post_list: list[schemas.PostMeta] = []

    source_workspace_id = _find_source_workspace_id(sync_records, channel_id)

    fed_ws = None
    if sync_records:
        fed_ws = helpers.get_federated_workspace_for_sync(sync_records[0][0].sync_id)

    for sync_channel, workspace in sync_records:
        try:
            if sync_channel.channel_id == channel_id:
                ts = helpers.safe_get(body, "event", "ts")
            elif fed_ws and workspace.id != source_workspace_id:
                image_payloads = []
                for block in photo_blocks or []:
                    if block.get("type") == "image":
                        image_payloads.append(
                            {
                                "url": block.get("image_url", ""),
                                "alt_text": block.get("alt_text", "Shared image"),
                            }
                        )
                payload = federation.build_message_payload(
                    sync_id=sync_channel.sync_id,
                    post_id=post_uuid,
                    channel_id=sync_channel.channel_id,
                    user_name=user_name,
                    user_avatar_url=user_profile_url,
                    workspace_name=workspace_name,
                    text=msg_text,
                    images=image_payloads,
                    timestamp=helpers.safe_get(body, "event", "ts"),
                )
                result = federation.push_message(fed_ws, payload)
                ts = helpers.safe_get(result, "ts") if result else helpers.safe_get(body, "event", "ts")
                if not ts:
                    ts = helpers.safe_get(body, "event", "ts")
            else:
                bot_token = helpers.decrypt_bot_token(workspace.bot_token)
                target_client = WebClient(token=bot_token)
                adapted_text = helpers.apply_mentioned_users(
                    msg_text,
                    client,
                    target_client,
                    mentioned_users,
                    source_workspace_id=source_workspace_id or 0,
                    target_workspace_id=workspace.id,
                )
                source_ws = helpers.get_workspace_by_id(source_workspace_id) if source_workspace_id else None
                adapted_text = helpers.resolve_channel_references(adapted_text, client, source_ws)

                target_display_name, target_icon_url = helpers.get_display_name_and_icon_for_synced_message(
                    user_id or "",
                    source_workspace_id or 0,
                    user_name,
                    user_profile_url,
                    target_client,
                    workspace.id,
                )
                name_for_target = target_display_name or user_name or "Someone"

                if direct_files and not msg_text.strip():
                    _, file_ts = helpers.upload_files_to_slack(
                        bot_token=bot_token,
                        channel_id=sync_channel.channel_id,
                        files=direct_files,
                        initial_comment=f"Shared by {name_for_target} {posted_from}",
                    )
                    ts = file_ts or helpers.safe_get(body, "event", "ts")
                else:
                    res = helpers.post_message(
                        bot_token=bot_token,
                        channel_id=sync_channel.channel_id,
                        msg_text=adapted_text,
                        user_name=name_for_target,
                        user_profile_url=target_icon_url or user_profile_url,
                        workspace_name=workspace_name,
                        blocks=photo_blocks,
                    )
                    ts = helpers.safe_get(res, "ts") or helpers.safe_get(body, "event", "ts")

                    if direct_files:
                        helpers.upload_files_to_slack(
                            bot_token=bot_token,
                            channel_id=sync_channel.channel_id,
                            files=direct_files,
                            thread_ts=ts,
                        )

            if ts:
                post_list.append(schemas.PostMeta(post_id=post_uuid, sync_channel_id=sync_channel.id, ts=float(ts)))
        except Exception as exc:
            _logger.error(f"Failed to sync new post to channel {sync_channel.channel_id}: {exc}")

    synced = len(post_list)
    failed = len(sync_records) - synced
    emit_metric("messages_synced", value=synced, sync_type="new_post")
    if failed:
        emit_metric("sync_failures", value=failed, sync_type="new_post")

    helpers.cleanup_temp_files(photo_list, direct_files)

    if post_list:
        DbManager.create_records(post_list)


def _handle_thread_reply(
    body: dict,
    client: WebClient,
    logger: Logger,
    ctx: EventContext,
    photo_blocks: list[dict],
    direct_files: list[dict] | None = None,
) -> None:
    """Sync a threaded reply to all linked channels."""
    channel_id = ctx["channel_id"]
    msg_text = ctx["msg_text"]
    mentioned_users = ctx["mentioned_users"]
    user_id = ctx["user_id"]
    thread_ts = ctx["thread_ts"]

    post_records = helpers.get_post_records(thread_ts)
    if not post_records:
        return

    workspace_name = _get_workspace_name(post_records, channel_id, workspace_index=2)
    posted_from = f"({workspace_name})" if workspace_name else "(via SyncBot)"

    if user_id:
        user_name, user_profile_url = helpers.get_user_info(client, user_id)
    else:
        user_name, user_profile_url = helpers.get_bot_info_from_event(body)

    post_uuid = uuid.uuid4().hex
    post_list: list[schemas.PostMeta] = []

    source_workspace_id = _find_source_workspace_id(post_records, channel_id, ws_index=2)

    fed_ws = None
    if post_records:
        fed_ws = helpers.get_federated_workspace_for_sync(post_records[0][1].sync_id)

    thread_post_id = post_records[0][0].post_id if post_records else None

    for post_meta, sync_channel, workspace in post_records:
        try:
            if sync_channel.channel_id == channel_id:
                ts = helpers.safe_get(body, "event", "ts")
            elif fed_ws and workspace.id != source_workspace_id:
                payload = federation.build_message_payload(
                    sync_id=sync_channel.sync_id,
                    post_id=post_uuid,
                    channel_id=sync_channel.channel_id,
                    user_name=user_name,
                    user_avatar_url=user_profile_url,
                    workspace_name=workspace_name,
                    text=msg_text,
                    thread_post_id=str(thread_post_id) if thread_post_id else None,
                    timestamp=helpers.safe_get(body, "event", "ts"),
                )
                result = federation.push_message(fed_ws, payload)
                ts = helpers.safe_get(result, "ts") if result else helpers.safe_get(body, "event", "ts")
                if not ts:
                    ts = helpers.safe_get(body, "event", "ts")
            else:
                bot_token = helpers.decrypt_bot_token(workspace.bot_token)
                target_client = WebClient(token=bot_token)
                adapted_text = helpers.apply_mentioned_users(
                    msg_text,
                    client,
                    target_client,
                    mentioned_users,
                    source_workspace_id=source_workspace_id or 0,
                    target_workspace_id=workspace.id,
                )
                source_ws = helpers.get_workspace_by_id(source_workspace_id) if source_workspace_id else None
                adapted_text = helpers.resolve_channel_references(adapted_text, client, source_ws)
                parent_ts = f"{post_meta.ts:.6f}"

                target_display_name, target_icon_url = helpers.get_display_name_and_icon_for_synced_message(
                    user_id or "",
                    source_workspace_id or 0,
                    user_name,
                    user_profile_url,
                    target_client,
                    workspace.id,
                )
                name_for_target = target_display_name or user_name or "Someone"

                if direct_files and not msg_text.strip():
                    _, file_ts = helpers.upload_files_to_slack(
                        bot_token=bot_token,
                        channel_id=sync_channel.channel_id,
                        files=direct_files,
                        initial_comment=f"Shared by {name_for_target} {posted_from}",
                        thread_ts=parent_ts,
                    )
                    ts = file_ts or helpers.safe_get(body, "event", "ts")
                else:
                    res = helpers.post_message(
                        bot_token=bot_token,
                        channel_id=sync_channel.channel_id,
                        msg_text=adapted_text,
                        user_name=name_for_target,
                        user_profile_url=target_icon_url or user_profile_url,
                        thread_ts=parent_ts,
                        workspace_name=workspace_name,
                        blocks=photo_blocks,
                    )
                    ts = helpers.safe_get(res, "ts")

                    if direct_files:
                        helpers.upload_files_to_slack(
                            bot_token=bot_token,
                            channel_id=sync_channel.channel_id,
                            files=direct_files,
                            thread_ts=parent_ts,
                        )

            if ts:
                post_list.append(schemas.PostMeta(post_id=post_uuid, sync_channel_id=sync_channel.id, ts=float(ts)))
        except Exception as exc:
            _logger.error(f"Failed to sync thread reply to channel {sync_channel.channel_id}: {exc}")

    synced = len(post_list)
    failed = len(post_records) - synced
    emit_metric("messages_synced", value=synced, sync_type="thread_reply")
    if failed:
        emit_metric("sync_failures", value=failed, sync_type="thread_reply")

    helpers.cleanup_temp_files(None, direct_files)

    if post_list:
        DbManager.create_records(post_list)


def _handle_message_edit(
    client: WebClient,
    logger: Logger,
    ctx: EventContext,
    photo_blocks: list[dict],
) -> None:
    """Propagate an edited message to all linked channels."""
    channel_id = ctx["channel_id"]
    msg_text = ctx["msg_text"]
    mentioned_users = ctx["mentioned_users"]
    ts = ctx["ts"]

    post_records = helpers.get_post_records(ts)
    if not post_records:
        return

    workspace_name = _get_workspace_name(post_records, channel_id, workspace_index=2)

    source_workspace_id = _find_source_workspace_id(post_records, channel_id, ws_index=2)

    fed_ws = None
    if post_records:
        fed_ws = helpers.get_federated_workspace_for_sync(post_records[0][1].sync_id)

    synced = 0
    failed = 0
    for post_meta, sync_channel, workspace in post_records:
        if sync_channel.channel_id == channel_id:
            continue
        try:
            if fed_ws and workspace.id != source_workspace_id:
                payload = federation.build_edit_payload(
                    post_id=post_meta.post_id.hex() if isinstance(post_meta.post_id, bytes) else str(post_meta.post_id),
                    channel_id=sync_channel.channel_id,
                    text=msg_text,
                    timestamp=f"{post_meta.ts:.6f}",
                )
                federation.push_edit(fed_ws, payload)
            else:
                bot_token = helpers.decrypt_bot_token(workspace.bot_token)
                target_client = WebClient(token=bot_token)
                adapted_text = helpers.apply_mentioned_users(
                    msg_text,
                    client,
                    target_client,
                    mentioned_users,
                    source_workspace_id=source_workspace_id or 0,
                    target_workspace_id=workspace.id,
                )
                source_ws = helpers.get_workspace_by_id(source_workspace_id) if source_workspace_id else None
                adapted_text = helpers.resolve_channel_references(adapted_text, client, source_ws)
                helpers.post_message(
                    bot_token=bot_token,
                    channel_id=sync_channel.channel_id,
                    msg_text=adapted_text,
                    update_ts=f"{post_meta.ts:.6f}",
                    workspace_name=workspace_name,
                    blocks=photo_blocks,
                )
            synced += 1
        except Exception as exc:
            failed += 1
            _logger.error(f"Failed to sync message edit to channel {sync_channel.channel_id}: {exc}")

    emit_metric("messages_synced", value=synced, sync_type="message_edit")
    if failed:
        emit_metric("sync_failures", value=failed, sync_type="message_edit")


def _handle_message_delete(
    ctx: EventContext,
    logger: Logger,
) -> None:
    """Propagate a deleted message to all linked channels."""
    channel_id = ctx["channel_id"]
    ts = ctx["ts"]

    post_records = helpers.get_post_records(ts)
    if not post_records:
        return

    fed_ws = None
    if post_records:
        fed_ws = helpers.get_federated_workspace_for_sync(post_records[0][1].sync_id)

    source_workspace_id = _find_source_workspace_id(post_records, channel_id, ws_index=2)

    synced = 0
    failed = 0
    for post_meta, sync_channel, workspace in post_records:
        if sync_channel.channel_id == channel_id:
            continue
        try:
            if fed_ws and workspace.id != source_workspace_id:
                payload = federation.build_delete_payload(
                    post_id=post_meta.post_id.hex() if isinstance(post_meta.post_id, bytes) else str(post_meta.post_id),
                    channel_id=sync_channel.channel_id,
                    timestamp=f"{post_meta.ts:.6f}",
                )
                federation.push_delete(fed_ws, payload)
            else:
                helpers.delete_message(
                    bot_token=helpers.decrypt_bot_token(workspace.bot_token),
                    channel_id=sync_channel.channel_id,
                    ts=f"{post_meta.ts:.6f}",
                )
            synced += 1
        except Exception as exc:
            failed += 1
            _logger.error(f"Failed to sync message delete to channel {sync_channel.channel_id}: {exc}")

    emit_metric("messages_synced", value=synced, sync_type="message_delete")
    if failed:
        emit_metric("sync_failures", value=failed, sync_type="message_delete")


def _handle_reaction(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Sync a reaction to all linked channels as a threaded message.

    Posts a short message (e.g. "reacted with :thumbsup: to <link>") using
    the same Display Name (Workspace Name) impersonation used for synced
    messages.  The message is always threaded under the top-level synced
    message, with a permalink to the exact message that was reacted to.
    Only ``reaction_added`` events are synced.
    """
    event = body.get("event", {})
    reaction = event.get("reaction")
    user_id = event.get("user")
    item = event.get("item", {})
    item_type = item.get("type")
    channel_id = item.get("channel")
    msg_ts = item.get("ts")
    event_type = event.get("type")

    if event_type != "reaction_added":
        return

    if not reaction or not channel_id or not msg_ts or item_type != "message":
        return

    own_user_id = helpers.get_own_bot_user_id(client)
    if own_user_id and user_id == own_user_id:
        return

    reacted_records = helpers.get_post_records(msg_ts)
    if not reacted_records:
        _logger.info(
            "reaction_no_post_meta",
            extra={"msg_ts": msg_ts, "channel_id": channel_id, "float_ts": float(msg_ts)},
        )
        return

    fed_ws = helpers.get_federated_workspace_for_sync(reacted_records[0][1].sync_id)

    source_workspace_id = _find_source_workspace_id(reacted_records, channel_id, ws_index=2)

    user_name, user_profile_url = helpers.get_user_info(client, user_id) if user_id else (None, None)
    source_ws = helpers.get_workspace_by_id(source_workspace_id) if source_workspace_id else None
    ws_name = helpers.resolve_workspace_name(source_ws) if source_ws else None
    posted_from = f"({ws_name})" if ws_name else "(via SyncBot)"

    post_uuid = uuid.uuid4().hex
    post_list: list[schemas.PostMeta] = []

    synced = 0
    failed = 0
    for post_meta, sync_channel, workspace in reacted_records:
        try:
            if fed_ws and workspace.id != source_workspace_id:
                payload = federation.build_reaction_payload(
                    post_id=str(post_meta.post_id),
                    channel_id=sync_channel.channel_id,
                    reaction=reaction,
                    action="add",
                    timestamp=f"{post_meta.ts:.6f}",
                )
                federation.push_reaction(fed_ws, payload)
            else:
                target_client = WebClient(token=helpers.decrypt_bot_token(workspace.bot_token))
                target_msg_ts = f"{post_meta.ts:.6f}"

                target_display_name, target_icon_url = helpers.get_display_name_and_icon_for_synced_message(
                    user_id or "",
                    source_workspace_id or 0,
                    user_name,
                    user_profile_url,
                    target_client,
                    workspace.id,
                )
                display_name = target_display_name or user_name or user_id or "Someone"

                permalink = None
                try:
                    plink_resp = target_client.chat_getPermalink(
                        channel=sync_channel.channel_id,
                        message_ts=target_msg_ts,
                    )
                    permalink = helpers.safe_get(plink_resp, "permalink")
                except Exception:
                    pass

                if permalink:
                    msg_text = f"reacted with :{reaction}: to <{permalink}|this message>"
                else:
                    msg_text = f"reacted with :{reaction}:"

                resp = target_client.chat_postMessage(
                    channel=sync_channel.channel_id,
                    text=msg_text,
                    username=f"{display_name} {posted_from}",
                    icon_url=target_icon_url or user_profile_url,
                    thread_ts=target_msg_ts,
                    unfurl_links=False,
                    unfurl_media=False,
                )
                ts = helpers.safe_get(resp, "ts")
                if ts:
                    post_list.append(schemas.PostMeta(post_id=post_uuid, sync_channel_id=sync_channel.id, ts=float(ts)))
            synced += 1
        except Exception as exc:
            failed += 1
            _logger.error(f"Failed to sync reaction to channel {sync_channel.channel_id}: {exc}")

    if post_list:
        DbManager.create_records(post_list)

    emit_metric("messages_synced", value=synced, sync_type="reaction_add")
    if failed:
        emit_metric("sync_failures", value=failed, sync_type="reaction_add")


def _is_own_bot_message(body: dict, client: WebClient, context: dict) -> bool:
    """Return *True* if the event was generated by SyncBot itself.

    Compares the ``bot_id`` in the event payload against SyncBot's own
    bot ID.  This replaces the old blanket ``bot_message`` filter so
    that messages from *other* bots are synced normally while SyncBot's
    own re-posts are still ignored (preventing infinite loops).
    """
    event = body.get("event", {})
    event_bot_id = (
        event.get("bot_id")
        or helpers.safe_get(event, "message", "bot_id")
        or helpers.safe_get(event, "previous_message", "bot_id")
    )
    if not event_bot_id:
        return False

    own_bot_id = helpers.get_own_bot_id(client, context)
    return event_bot_id == own_bot_id


def respond_to_message_event(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Dispatch incoming message events to the appropriate sub-handler."""
    ctx = _parse_event_fields(body, client)
    event_type = helpers.safe_get(body, "event", "type")
    event_subtype = ctx["event_subtype"]

    if event_type != "message":
        return

    # Skip messages from SyncBot itself to prevent infinite sync loops.
    # Messages from OTHER bots are synced normally.
    if _is_own_bot_message(body, client, context):
        return

    s3_photo_list, photo_blocks, direct_files = _build_file_context(body, client, logger)

    has_files = bool(photo_blocks or direct_files)
    if (
        (not event_subtype)
        or event_subtype == "bot_message"
        or (event_subtype == "file_share" and (ctx["msg_text"] != "" or has_files))
    ):
        if not ctx["thread_ts"]:
            _handle_new_post(body, client, logger, ctx, s3_photo_list, photo_blocks, direct_files)
        else:
            _handle_thread_reply(body, client, logger, ctx, photo_blocks, direct_files)
    elif event_subtype == "message_changed":
        _handle_message_edit(client, logger, ctx, photo_blocks)
    elif event_subtype == "message_deleted":
        _handle_message_delete(ctx, logger)
