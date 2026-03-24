# SyncBot User Guide

This guide is for **workspace admins and end users** configuring SyncBot in Slack. For **installing or hosting** the app (AWS, GCP, Docker, GitHub Actions), see **[DEPLOYMENT.md](DEPLOYMENT.md)** and the root **[README](../README.md)**.

## Getting Started

1. Click the install link from a desktop browser (make sure you've selected the correct workspace in the upper right)
2. Open the **SyncBot** app from the sidebar and click the **Home** tab (requires workspace admin or owner)
3. The Home tab shows everything in one view:
   - **SyncBot Configuration (top row)** — **Refresh** and **Backup/Restore** (full-instance backup download and restore from JSON)
   - **Workspace Groups** — create or join groups of workspaces that can sync channels together
   - **Per-group sections** — for each group you can publish channels, manage user mapping (dedicated Home tab screen), and see/manage channel syncs inline
   - **Synced Channels** — each row shows the local channel and workspace list in brackets (e.g. _[Any: Your Workspace, Other Workspace]_), with pause/resume and stop controls, synced-since date, and tracked message count
   - **External Connections** *(when federation is enabled)* — Generate/Enter Connection Code and **Data Migration** (export workspace data for migration to another instance, or import a migration file)

## Things to Know

- Only workspace **admins and owners** can configure syncs (set `REQUIRE_ADMIN=false` to allow all users)
- Messages, threads, edits, deletes, reactions, images, videos, and GIFs are all synced
- Messages from other bots are synced; only SyncBot's own messages are filtered to prevent loops
- Existing messages are not back-filled; syncing starts from the moment a channel is linked
- Do not add SyncBot manually to channels. SyncBot adds itself when you configure a Sync. If it detects it was added to an unconfigured channel it will post a message and leave automatically
- Both public and private channels are supported

## Workspace Groups

Workspaces must belong to the same **group** before they can sync channels or map users. Admins can create a new group (which generates an invite code) or join an existing group by entering a code. A workspace can be in multiple groups with different combinations of other workspaces.

## Sync Modes

When publishing a channel inside a group, admins choose either **1-to-1** (only a specific workspace can subscribe) or **group-wide** (any group member can subscribe independently).

## Pause / Resume / Stop

- **Pause/Resume** — Individual channel syncs can be paused and resumed without losing configuration. Paused channels do not sync any messages, threads, or reactions.
- **Selective Stop** — When a workspace stops syncing a channel, only that workspace's history is removed. Other workspaces continue syncing uninterrupted. The published channel remains available until the original publisher unpublishes it.

## Uninstall / Reinstall

If a workspace uninstalls SyncBot, group memberships and syncs are paused (not deleted). Reinstalling within the retention period (default 30 days, configurable via `SOFT_DELETE_RETENTION_DAYS`) automatically restores everything. Group members are notified via DMs and channel messages.

## User Mapping

Users are automatically mapped across workspaces by email or display name. Admins can manually edit mappings via the User Mapping screen (scoped per group). Remote users are displayed as "Display Name (Workspace Name)" and sorted by normalized name.

## Refresh Behavior

The Home tab and User Mapping screens have Refresh buttons. To keep API usage low, repeated clicks with no data changes are handled lightly: a 60-second cooldown applies, and when nothing has changed the app reuses cached content and shows "No new data. Wait __ seconds before refreshing again."

## Media Sync

Images and videos are downloaded from the source and uploaded directly to each target channel. GIFs from the Slack GIF picker or GIPHY are synced as image blocks.

## External Connections

*(Opt-in — set `SYNCBOT_FEDERATION_ENABLED=true` and `SYNCBOT_PUBLIC_URL` to enable)*

Workspaces running their own SyncBot deployment can be connected via the "External Connections" section on the Home tab. One admin generates a connection code and shares it out-of-band; the other admin enters it. Messages, edits, deletes, reactions, and user matching work across instances.

**Data Migration** in the same section lets you export your workspace data (syncs, channels, post meta, user directory, user mappings) for moving to another instance, or import a migration file after connecting. See [Backup and Migration](BACKUP_AND_MIGRATION.md) for details.

## Backup / Restore

Use **Backup/Restore** on the Home tab to download a full-instance backup (all tables as JSON) or restore from a backup file. Intended for disaster recovery (e.g. before rebuilding AWS). See [Backup and Migration](BACKUP_AND_MIGRATION.md) for details.
