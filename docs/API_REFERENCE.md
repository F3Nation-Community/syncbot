# API Reference

## HTTP Endpoints (API Gateway)

All endpoints are served by a single Lambda function. Slack sends requests to the `/slack/*` URLs after you configure the app. The `/api/federation/*` endpoints handle cross-instance communication for external connections.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/slack/events` | Receives all Slack events (messages, actions, view submissions) and slash commands |
| `GET` | `/slack/install` | OAuth install page — redirects the user to Slack's authorization screen |
| `GET` | `/slack/oauth_redirect` | OAuth callback — Slack redirects here after the user approves the app |
| `POST` | `/api/federation/pair` | Accept an incoming external connection request |
| `POST` | `/api/federation/message` | Receive a forwarded message from a connected instance; resolves `@` mentions and `#` channel references locally before posting |
| `POST` | `/api/federation/message/edit` | Receive a message edit from a connected instance; applies the same local mention and channel resolution before updating |
| `POST` | `/api/federation/message/delete` | Receive a message deletion from a connected instance |
| `POST` | `/api/federation/message/react` | Receive a reaction from a connected instance |
| `POST` | `/api/federation/users` | Exchange user directory with a connected instance |
| `GET` | `/api/federation/ping` | Health check for connected instances |

## Subscribed Slack Events

| Event | Handler | Description |
|-------|---------|-------------|
| `app_home_opened` | `handle_app_home_opened` | Publishes the Home tab with workspace groups, channel syncs, and user matching. |
| `member_joined_channel` | `handle_member_joined_channel` | Detects when SyncBot is added to an unconfigured channel; posts a message and leaves. |
| `message.channels` / `message.groups` | `respond_to_message_event` | Fires on new messages, edits, deletes, and file shares in public/private channels. Dispatches to sub-handlers for new posts, thread replies, edits, deletes, and reactions. |
| `reaction_added` / `reaction_removed` | `_handle_reaction` | Syncs emoji reactions to the corresponding message in all target channels. |
| `team_join` | `handle_team_join` | Fires when a new user joins a connected workspace. Adds the user to the directory and re-checks unmatched user mappings. |
| `tokens_revoked` | `handle_tokens_revoked` | Handles workspace uninstall — soft-deletes workspace data and notifies group members. |
| `user_profile_changed` | `handle_user_profile_changed` | Detects display name or email changes and updates the user directory and mappings. |
