# SyncBot

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

SyncBot is a Slack app that replicates ("syncs") posts and replies across Slack workspaces. Once configured, this will happen automatically in synced channels.

## Installation and Getting Started

Set up is simple: 

1. Click [this link](https://utazcizeo0.execute-api.us-east-2.amazonaws.com/Prod/slack/install) from a desktop computer. Make sure you have selected your desired workspace in the upper right!
2. Next, you can configure SyncBot by using the `/config-syncbot` slash command
3. If this is the first workspace you are configuring, use the "Create new Sync" button. Otherwise, use "Join existing Sync".

Some notes:
 - Bot messages will not be synced, only actual user messages
 - Existing messages are not synced, but going forward all posts and their thread replies will be
 - Do not add SyncBot manually to channels - SyncBot will add itself to channels you configure. If it detects that it has been added to a non-configured channel, it will leave the channel
 - Private channels are not supported

## Feature Request and Roadmap

I use GitHub Issues for tracking feature requests. Feel free to add some here: https://github.com/F3Nation-Community/syncbot/issues

Roadmap:
 - Picture sync
 - Reaction sync