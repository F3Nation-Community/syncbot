import time
from logging import Logger
from typing import List

from slack_sdk.web import WebClient
from utils.db import DbManager
from utils.db.schemas import Region, SyncChannel

# msg = ":rotating_light: Hey, {region}! This is Moneyball, coming at you with some new features for Syncbot! :rotating_light:\n\n"
# msg += ":camera_with_flash: *Photo Sync*: photos will now be synced when you post them to linked channels. Videos are not supported at this time. Also, animated GIFs will be synced, but they will show up as still images.\n\n"
# msg += ":speech_balloon: *@ mention tagging*: you can now @ mention users in your synced posts, and Syncbot will do its best to translate them to the appropriate user in the target workspace. Linked users must be in both workspaces for this to work, otherwise it will default to a non-tagged representation of a mention.\n\n"
# msg += "~ :moneybag: :baseball:"


def send(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
):
    if body.get("text")[:7] == "confirm":
        msg = body.get("text")[8:]
        region_records: List[Region] = DbManager.find_records(Region, filters=[True])
        for region in region_records:
            sync_channels: List[SyncChannel] = DbManager.find_records(
                SyncChannel, filters=[SyncChannel.region_id == region.id]
            )
            client = WebClient(token=region.bot_token)
            for channel in sync_channels:
                try:
                    client.chat_postMessage(channel=channel.channel_id, text=msg.format(region=region.workspace_name))
                    print("Message sent!")
                except Exception as e:
                    if e.response.get("error") == "ratelimited":
                        print("Rate limited, waiting 10 seconds")
                        time.sleep(10)
                        try:
                            client.chat_postMessage(
                                channel=channel.channel_id, text=msg.format(region=region.workspace_name)
                            )
                            print("Message sent!")
                        except Exception as e:
                            print(f"Error sending message to {region.workspace_name}: {e}")
                    print(f"Error sending message to {region.workspace_name}: {e}")
