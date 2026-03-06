# SyncBot Improvements Summary

This document outlines the improvements made to the SyncBot application and additional recommendations for future enhancements.

## ✅ Completed Improvements

### 1. Database Management Fixes
- **Added `@staticmethod` decorators** to all `DbManager` methods for proper static method usage
- **Fixed session management** - All database methods now properly close sessions in finally blocks
- **Improved error handling** in database operations

### 2. Code Quality Improvements
- **Removed duplicate constant definitions** in `constants.py` (SLACK_STATE_S3_BUCKET_NAME, SLACK_INSTALLATION_S3_BUCKET_NAME, etc. were defined twice)
- **Fixed type hints**:
  - `get_request_type()` now correctly returns `tuple[str, str]` instead of `tuple[str]`
  - `apply_mentioned_users()` now correctly returns `str` instead of `List[Dict]`

### 3. Error Handling Enhancements
- **Replaced bare `except Exception:` clauses** with proper error logging:
  - `handle_remove_sync()` now logs warnings when failing to leave channels
  - `handle_join_sync_submission()` now logs errors with context
  - Added null check for `sync_channel_record` before use
- **Improved exception handling** in `announcements.py`:
  - Replaced print statements with proper logging
  - Better handling of rate limiting errors
  - More descriptive error messages

### 4. Logging Improvements
- **Replaced all `print()` statements** with proper logging:
  - `orm.py`: Added logger and replaced print statements with `logger.error()` and `logger.debug()`
  - `announcements.py`: Replaced print statements with appropriate log levels (info, warning, error)
  - `handlers.py`: Removed debug print statement
- **Added logging module** where needed

### 5. Database Connection Pooling
- **Replaced `pool.NullPool` with `pool.QueuePool`** (`pool_size=3`, `max_overflow=2`, `pool_recycle=3600`) for connection reuse across warm Lambda invocations
- **Added `pool_pre_ping=True`** to detect and replace stale connections transparently
- **Added `_with_retry` decorator** on all `DbManager` methods to automatically retry on transient `OperationalError` (up to 2 retries with engine disposal between attempts)
- **Simplified `close_session()`** to return connections to the pool instead of disposing the entire engine

### 6. Rate Limiting Handling
- **Created `slack_retry` decorator** with exponential backoff for all Slack API calls:
  - Honors `Retry-After` headers on HTTP 429 responses
  - Retries on transient 5xx server errors
  - Configurable max retries (default 3) with exponential backoff (capped at 30s)
- **Refactored `parse_mentioned_users()`** to use individual `users.info()` calls instead of the heavy `users.list()` endpoint that is easily rate-limited
- **Refactored `apply_mentioned_users()`** to use `users.lookupByEmail()` for individual lookups instead of `users.list()`
- **Added user profile caching** (`_get_user_profile()`) with a 5-minute TTL to avoid redundant API calls for the same user
- **Applied `@slack_retry`** to `post_message()`, `delete_message()`, `_users_info()`, and `_lookup_user_by_email()`

### 7. Error Recovery
- **Added error isolation in sync loops** - a failure syncing to one channel no longer prevents syncing to the remaining channels:
  - `_handle_new_post()`: Individual channel failures are caught and logged; remaining channels continue
  - `_handle_thread_reply()`: Same per-channel error isolation
  - `_handle_message_edit()`: Same per-channel error isolation
  - `_handle_message_delete()`: Same per-channel error isolation
- **Guard against empty post lists** - `DbManager.create_records()` is only called when there are records to persist

### 8. Type Safety
- **Added `EventContext` TypedDict** for the parsed message event context, replacing untyped `dict`
- **Updated all sub-handler signatures** (`_handle_new_post`, `_handle_thread_reply`, `_handle_message_edit`, `_handle_message_delete`) to use `EventContext`
- **Added comprehensive type hints** across the codebase:
  - `helpers.py`: `safe_get()`, `get_user_info()`, `post_message()`, `delete_message()`, `update_modal()`, `parse_mentioned_users()`, `apply_mentioned_users()` and all new functions
  - `handlers.py`: `_build_photo_context()`, `_get_team_name()` return types
  - `schemas.py`: `GetDBClass` mixin methods (`get_id`, `get`, `to_json`, `__repr__`)
- **Improved exception handling in `safe_get()`** to also catch `AttributeError` and `IndexError`

### 9. Testing
- **Created unit test suite** with 40 tests across 3 modules:
  - `tests/test_helpers.py`: `safe_get()` (9 tests), encryption roundtrip/failure/wrong-key (5 tests), TTL cache (4 tests), `get_request_type()` (4 tests), `slack_retry` decorator (3 tests)
  - `tests/test_db.py`: `_with_retry` decorator (4 tests), engine QueuePool verification (1 test)
  - `tests/test_handlers.py`: `_parse_event_fields()` (4 tests), `EventContext` TypedDict (1 test), `_sanitize_text()` (5 tests)
- **Added pytest configuration** in `pyproject.toml` with `testpaths` and `pythonpath`

### 10. Code Organization (Medium Priority - Previously Completed)
- **Refactored `respond_to_message_event()`** (170+ lines) into a thin dispatcher and focused sub-handlers: `_parse_event_fields`, `_build_photo_context`, `_get_team__name`, `_handle_new_post`, `_handle_thread_reply`, `_handle_message_edit`, `_handle_message_delete`

### 11. Configuration Management (Medium Priority - Previously Completed)
- **Added `validate_config()`** startup validation for required environment variables
- **Fails fast in production** (raises `EnvironmentError`); warns in local development
- **Separate required lists** for always-required and production-only variables

### 12. Database Schema (Medium Priority - Previously Completed)
- **Added soft deletes** for `sync_channels` via `deleted_at` column with index
- **Created SQL migration scripts**: `migrate_001_security.sql`, `migrate_003_soft_deletes.sql`
- **Created Python migration script**: `migrate_002_encrypt_tokens.py` for encrypting existing tokens
- **Updated all queries** to filter out soft-deleted records

### 13. Security (Medium Priority - Previously Completed)
- **Bot token encryption** at rest using Fernet (AES-128-CBC + HMAC-SHA256)
- **Fail-closed decryption** - refuses to use tokens that fail decryption
- **Input sanitization** via `_sanitize_text()` on user-submitted form data
- **RDS SSL/TLS enforcement** (server-side parameter group + client-side connect_args)
- **API Gateway throttling** (20 burst / 10 sustained requests per second)

### 14. Performance (Medium Priority - Previously Completed)
- **In-process TTL cache** for `get_sync_list()` (60s TTL) and user info lookups (300s TTL)
- **Hoisted `get_user_info()` calls** outside loops where possible
- **Connection pooling** reuses DB connections across invocations in warm Lambda containers

### 15. Infrastructure as Code
- **AWS SAM template** (`template.yaml`) defining complete VPC, RDS, S3, Lambda, API Gateway stack
- **Free-tier optimized** (128 MB Lambda, db.t3.micro RDS, gp2 storage, no NAT Gateway)
- **CI/CD pipeline** (`.github/workflows/sam-pipeline.yml`) for automated build/deploy
- **SAM config** (`samconfig.toml`) for staging and production environments

### 16. Documentation (Low Priority - Completed)
- **Added module-level docstrings** to all Python modules across all packages
- **Added function-level docstrings** to all public functions across the codebase (encryption helpers, cache functions, Slack API wrappers, DB helpers, OAuth flow, photo upload, mention parsing, modal updates, request dispatch)
- **Added inline docstrings** to routing table dicts and action ID constants
- **Documented API endpoints** in the README (HTTP routes, subscribed events)
- **Documented deployment process** in the README (first-time deploy, subsequent deploys, CI/CD, migrations, shared infrastructure)

### 17. Monitoring & Observability (Low Priority - Completed)
- **Added structured JSON logging** via `StructuredFormatter` — every log entry is a single JSON object with `timestamp`, `level`, `correlation_id`, `module`, `function`, `message`, and optional extra fields
- **Added correlation IDs** — a unique 12-character ID is assigned at the start of each incoming Slack request (`set_correlation_id()`) and automatically included in every log line during that request
- **Added metrics emission** via `emit_metric()` — structured log entries for key operational metrics:
  - `request_handled` (with `duration_ms`, `request_type`, `request_id`)
  - `request_error` (with `request_type`, `request_id`)
  - `messages_synced` (with `sync_type`: `new_post`, `thread_reply`, `message_edit`, `message_delete`)
  - `sync_failures` (with `sync_type`)
- **Added CloudWatch Alarms** in `template.yaml` (within free-tier's 10-alarm limit):
  - `LambdaErrorAlarm` — fires on 3+ errors in 5 minutes
  - `LambdaThrottleAlarm` — fires on any throttling
  - `LambdaDurationAlarm` — fires when average duration exceeds 10 seconds
  - `ApiGateway5xxAlarm` — fires on 5+ server errors in 5 minutes
- **X-Ray distributed tracing** was already enabled (`Tracing: Active` in SAM template)

### 18. Code Style (Low Priority - Completed)
- **Configured `ruff`** as the project linter and formatter (added `[tool.ruff]` section to `pyproject.toml` with rules for pycodestyle, pyflakes, isort, pyupgrade, flake8-bugbear, flake8-simplify, flake8-logging)
- **Ran `ruff format`** across the entire codebase (all Python files in `syncbot/` and `tests/`)
- **Ran `ruff check --fix`** to auto-fix 123 issues (import sorting, deprecated typing imports, style modernization)
- **Manually fixed remaining issues**: mutable default argument (`orm.py`), `return` inside `finally` (`db/__init__.py`), `raise ... from None` for exception chaining (`helpers.py`), ternary ordering (`handlers.py`)
- **Created `.pre-commit-config.yaml`** with hooks for:
  - `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`, `check-merge-conflict`, `detect-private-key`
  - `ruff` lint (with `--fix`)
  - `ruff-format`

### 19. Architecture Diagrams (Low Priority - Completed)
- **Added message sync flow sequence diagram** (Mermaid) to README showing the full request path from user message through API Gateway, Lambda, DB lookup, image upload, mention re-mapping, cross-workspace posting, and metric emission
- **Added AWS infrastructure diagram** (Mermaid) to README showing the relationships between API Gateway, Lambda, S3 buckets, RDS, EventBridge keep-warm, and CloudWatch monitoring

### 20. Admin Authorization and Security Hardening (Completed)
- **Added admin/owner authorization** — only workspace admins and owners can run `/config-syncbot` and all related configuration actions (create sync, join sync, remove sync)
  - `is_user_authorized(client, user_id)` checks `is_admin` / `is_owner` from the Slack `users.info` API, with caching
  - `get_user_id_from_body(body)` extracts the user ID from any Slack request type (commands, actions, views)
  - Unauthorized users receive an ephemeral message: ":lock: Only workspace admins and owners can configure SyncBot."
- **Defense-in-depth** — authorization checks are enforced at both the entry points (`build_config_form`, `build_join_sync_form`, `build_new_sync_form`) and the mutation handlers (`handle_remove_sync`, `handle_join_sync_submission`, `handle_new_sync_submission`)
- **Configurable via `REQUIRE_ADMIN` env var** (default `"true"`) — set to `"false"` to allow all users (for small teams)
- **Removed `/send-syncbot-announcement` command** — the broadcast command could be triggered by any admin in any connected workspace, affecting all workspaces; removed entirely as a security risk
- **Fixed input validation in `handle_remove_sync`** — `int()` conversion now wrapped in try/except to prevent crashes on malformed payloads
- **Fixed join-sync ordering in `handle_join_sync_submission`** — `conversations_join` now runs before `DbManager.create_record` so the DB record isn't created if the bot can't actually join the channel

### 21. Cross-Workspace User Matching (Completed)
- **Persistent user matching pipeline** — @mentions in synced messages are resolved to the correct user in the target workspace using a multi-step algorithm: email lookup → name-based directory matching → bracketed fallback
- **New database tables**:
  - `user_directory` — cached copy of each workspace's user profiles (slack_user_id, email, real_name, display_name, normalized_name), refreshed every 24h
  - `user_mappings` — cross-workspace match results with TTL-based freshness (email: 30d, name: 14d, manual: never expires, none: 90d)
- **Name normalization** (`_normalize_name`) — trims trailing title/qualifier from display names (e.g., "Johnny B (Title)" → "Johnny B") while preserving original casing and spacing
- **Reactive matching via `team_join` event** — when a new user joins a connected workspace, their profile is added to the directory and all unmatched mappings targeting that workspace are re-checked automatically
- **Admin UI in `/config-syncbot`** — "User Matching" button opens a child modal showing:
  - Stats: "X matched, Y unmatched"
  - Refresh button to re-run auto-matching across all linked workspaces
  - Unmatched users with native Slack user-picker dropdowns for manual matching (saved as `match_method='manual'`)
  - Matched users with "Unlink" buttons to remove mappings
- **Fallback display** — unmatched mentions render as `[Display Name]` in square brackets instead of broken `@mentions`
- **Migration script** — `db/migrate_004_user_matching.sql` for existing deployments

### 22. Bot Message Syncing (Completed)
- **Selective bot filtering** — only messages from SyncBot itself are ignored (to prevent infinite loops); messages from all other bots are synced normally
- **Bot identity detection** (`get_own_bot_id`) — resolves SyncBot's `bot_id` using `context` or `auth.test`, with caching
- **Bot attribution** (`get_bot_info_from_event`) — extracts `username` and `icons` from bot message events so synced bot messages preserve the original bot's name and avatar
- **Unit tests** for `_is_own_bot_message` (own bot, other bots, user messages, message_changed events, auth.test fallback) and `get_bot_info_from_event`

### 23. Simplified Sync Creation (Completed)
- **One-step sync creation** — replaced the two-step flow (create sync title → join channel) with a single channel picker modal
- **`ConversationsSelectElement`** — new Block Kit element that shows both public and private channels (with `exclude_bot_users: true`)
- **Auto-naming** — the sync is named after the selected channel (resolved via `conversations.info`)
- **Combined operation** — on submit, the handler joins the channel, creates the `Sync` record, creates the `SyncChannel` link, and posts a welcome message in one step
- **Private channel support** — the "Join existing Sync" channel picker also upgraded to `ConversationsSelectElement` so private channels are now selectable

### 24. Workspace Pairing with Directed Trust Codes (Completed)
- **Directed workspace pairing** — the Workspace Pairing screen lists every workspace that has SyncBot installed, with its pairing status (Paired, Pending, or Not paired)
- **Pairing flow**: Admin A sees Workspace B listed as "Not paired" → clicks "Generate Code" → a code locked to Workspace B is created → Admin A shares the code out-of-band → Admin B enters the code → pairing is activated bidirectionally
- **Locked codes** — pairing codes are generated for a specific target workspace; if a different workspace tries to redeem the code, it is rejected
- **New database table** — `workspace_pairings` with `initiator_workspace_id`, `partner_workspace_id`, `invite_code`, `status` (`pending`/`active`), `created_at`, `paired_at`
- **Code validation** — codes are 7-character alphanumeric with format `XXX-XXXX`; pending codes expire after 24 hours; self-pairing, wrong-workspace, and duplicate pairing are all rejected
- **Pairing UI in `/config-syncbot`** — "Workspace Pairing" button opens a modal showing:
  - All installed workspaces with status: Paired (with Remove button), Pending (with code displayed and Cancel button), or Not paired (with Generate Code button)
  - "Enter Pairing Code" button at the top for the receiving side
- **Cascading unpair** — removing a pairing soft-deletes all `SyncChannel` records shared between the two workspaces and has the bot leave those channels
- **Migration script** — `db/migrate_005_workspace_pairings.sql` for existing deployments

### 25. Config Screen Redesign — Channel Sync & User Matching Overhaul (Completed)
- **Three-button config screen** — replaced the four-button layout (Join existing Sync, Create new Sync, User Matching, Workspace Pairing) with three focused buttons: **Workspace Pairing**, **User Matching**, **Channel Sync**
- **1-to-1 Channel Sync (publish/subscribe model)**:
  - A workspace "publishes" one of its channels to a specific paired workspace, making it available for syncing
  - The paired workspace "subscribes" by selecting one of their own channels to receive messages
  - Each publish is scoped to exactly one pairing — publishing to workspace B and workspace C are separate operations
  - Channel Sync modal shows: published channels (with Unpublish buttons), available channels from partner (with Subscribe buttons), and a Publish Channel button
  - Welcome messages are posted in both channels when a subscription is established
  - Unpublishing cleans up both sides (soft-deletes SyncChannels, bot leaves channels)
- **Database changes** — added `pairing_id` column to `syncs` table (FK to `workspace_pairings`, `ON DELETE CASCADE`), removed UNIQUE constraint on `syncs.title` (same channel can be published to multiple pairings)
- **Workspace picker pattern** — both Channel Sync and User Matching now show a workspace picker modal when multiple pairings exist; auto-selects when only one pairing is active
- **User Matching improvements**:
  - **Auto-sync on pairing activation** — when a pairing code is accepted, both workspaces' user directories are refreshed and auto-matching runs immediately in both directions
  - **Scoped to pairing** — user matching is now filtered to the selected paired workspace instead of showing all linked workspaces at once
  - **Filtered unmatchable users** — users with no possible candidate in the target workspace (by normalized display name or email) are hidden from the unmatched list
  - **Override dropdowns for matched users** — matched users now show a `UsersSelectElement` pre-populated with the current match, allowing direct reassignment without unlinking first
- **New action constants** — ~12 new Block Kit action/callback IDs for channel sync flows, workspace pickers, publish/subscribe, and user matching workspace selection
- **New form templates** — `WORKSPACE_PICKER_FORM`, `PUBLISH_CHANNEL_FORM`, `SUBSCRIBE_CHANNEL_FORM`
- **Prefix-match routing** — added entries for `CONFIG_UNPUBLISH_CHANNEL` and `CONFIG_SUBSCRIBE_CHANNEL` (suffix contains sync/channel IDs)
- **ORM fix** — `update_modal` now supports `submit_button_text="None"` to render modals without a submit button (consistent with `post_modal`)

### 26. Docker Local Development (Completed)
- **Dev Container support** — added `.devcontainer/devcontainer.json` and `.devcontainer/docker-compose.dev.yml` for full in-editor development inside a Docker container (Cursor / VS Code)
  - Python, Pylance, and Ruff extensions pre-configured with format-on-save
  - `PYTHONPATH` and database env vars set automatically
  - Ports 3000 (app) and 3306 (MySQL) forwarded to host
  - AWS CLI feature included for SAM operations
  - `pytest` and `boto3` installed on container creation
- **Docker Compose** — added `Dockerfile` and `docker-compose.yml` for standalone container-based development without the Dev Container extension
  - MySQL 8 with automatic schema initialization via `init.sql` mount
  - App code mounted as a volume for live editing without rebuilds
  - Named volume for database persistence across restarts
- **README updated** with three local development options: Dev Container (recommended), Docker Compose, and native Python

### 27. App Home Tab Migration (Completed)
- **Replaced `/config-syncbot` slash command** with a persistent **App Home tab** — all configuration is now managed through the Home tab instead of slash commands and nested modals
- **Inline content** — workspace pairings and channel syncs are rendered directly on the Home tab instead of requiring modal navigation
- **Per-pairing sections** — each paired workspace shows its own section with a "Manage User Matching" button and channel sync controls (publish/unpublish/subscribe)
- **Simplified modal flow** — sub-screens (enter pairing code, publish channel, subscribe channel, user matching) now open as standalone modals (`views.open`) instead of stacked modals (`views.push`)
- **Auto-refresh** — all mutations (generate code, cancel, remove pairing, publish/unpublish/subscribe channel) automatically re-publish the Home tab
- **Manifest updated** — added `app_home_opened` to bot events, removed `slash_commands` section and `commands` OAuth scope
- **Non-admin users** see a locked message on the Home tab instead of an error

### 28. Uninstall Soft-Delete & Reinstall Recovery (Completed)
- **Soft-delete on uninstall** — when a workspace uninstalls SyncBot, its record, pairings, and sync channels are soft-deleted (`deleted_at` timestamp) rather than hard-deleted
- **Automatic reinstall recovery** — if the workspace reinstalls within the retention period, all pairings and sync channels are automatically restored
- **Lifecycle notifications** — consistent notification model using channel messages and admin DMs:
  - **Started** — new pairing activated: admin DMs in both workspaces
  - **Paused** — workspace uninstalls: admin DMs + channel messages in partner workspace
  - **Resumed** — workspace reinstalls: admin DMs + channel messages in partner workspace
  - **Stopped** — manual removal: admin DMs + channel messages in partner workspace
  - **Purged** — auto-cleanup after retention period: admin DMs to partner workspace
- **Paused indicator** — Home tab and pairing form show `:double_vertical_bar: Paused (uninstalled)` for soft-deleted partner workspaces with no action buttons
- **Configurable retention** — `SOFT_DELETE_RETENTION_DAYS` env var (default 30 days) controls how long soft-deleted data is kept before permanent purge
- **Lazy daily purge** — stale soft-deleted workspaces are hard-deleted via `ON DELETE CASCADE` during the first `app_home_opened` event each day
- **Manifest updated** — added `tokens_revoked` to bot events, `im:write` to OAuth scopes
- **Migration** — `db/migrate_007_uninstall_soft_delete.sql` adds `deleted_at` to `workspaces` and `workspace_pairings`

### 29. External Connections — Cross-Instance Federation (Completed)
- **Cross-instance sync** — independent SyncBot deployments (e.g., on separate AWS accounts, GCP, or Cloudflare) can now connect and sync messages, edits, deletes, reactions, and user matching across instances
- **Connection pairing flow** — admin generates a connection code on one instance, shares it out-of-band, and the other admin enters it to establish a secure connection
  - Codes encode the instance's public URL and a unique instance ID in a base64 payload
  - On acceptance, both sides exchange a shared secret and store a `federated_workspaces` record
- **HMAC-SHA256 request authentication** — all inter-instance webhook calls (except the initial pairing handshake and health checks) are signed using the shared secret, with replay protection via 5-minute timestamp validation
- **Federation API endpoints** — seven new HTTP endpoints for cross-instance communication:
  - `POST /api/federation/pair` — accept an incoming connection request
  - `POST /api/federation/message` — receive forwarded messages (new posts and thread replies)
  - `POST /api/federation/message/edit` — receive message edits
  - `POST /api/federation/message/delete` — receive message deletions
  - `POST /api/federation/message/react` — receive reaction add/remove
  - `POST /api/federation/users` — exchange user directory for mention matching
  - `GET /api/federation/ping` — health check / connectivity test
- **Transparent message forwarding** — the core message handlers (`_handle_new_post`, `_handle_thread_reply`, `_handle_message_edit`, `_handle_message_delete`) detect whether a sync target is local or remote and dispatch accordingly — local channels are posted to directly, remote channels are forwarded via the federation webhook
- **User directory exchange** — when a connection is established, both instances exchange their user directories so @mention resolution works across instances
- **Image handling** — images use existing S3 URLs which are publicly accessible; the receiving instance uses them directly in Slack blocks
- **Retry with exponential backoff** — all outgoing federation HTTP calls retry up to 3 times with 1s/2s/4s backoff on transient failures (5xx, timeouts, connection errors)
- **Home tab UI** — "External Connections" section on the Home tab with "Generate Connection Code" and "Enter Connection Code" buttons, active connection display with status and remove button, and pending code display with cancel button
- **Connection label prompt** — generating a connection code prompts for a friendly name (e.g. "East Coast SyncBot") which is displayed on the Home tab and used as the remote workspace's display name
- **Code delivery via DM** — both internal pairing codes and external connection codes are sent as a DM to the admin for easy copy/paste (Slack Block Kit does not support clipboard buttons)
- **Opt-in feature flag** — external connections are disabled by default; set `SYNCBOT_FEDERATION_ENABLED=true` to enable. All UI, handlers, and API endpoints are gated behind this flag
- **New database table** — `federated_workspaces` (instance_id, webhook_url, public_key, status, name)
- **Schema change** — `federated_workspace_id` added to group members (NULL = local workspace, non-NULL = remote)
- **Environment variables** — `SYNCBOT_FEDERATION_ENABLED` (opt-in flag, default `false`), `SYNCBOT_INSTANCE_ID` (auto-generated UUID), `SYNCBOT_PUBLIC_URL` (required when enabled)
- **Federation package** — `syncbot/federation/core.py` (signing, HTTP client, payload builders), `syncbot/federation/api.py` (API endpoint handlers)
- **Migration** — `db/migrate_009_federated_workspaces.sql`

### 30. Reaction Syncing (Completed)
- **Threaded reaction messages** — emoji reactions (`reaction_added` / `reaction_removed`) are synced to all linked channels as threaded replies on the corresponding message
- **Bidirectional** — reactions work in both directions across workspaces
- **User attribution** — reaction messages display the reacting user's display name and workspace
- **Permalink reference** — each reaction message includes a link to the original message
- **PostMeta lookup** — uses the existing `PostMeta` table to resolve source timestamps to target message timestamps for accurate threading
- **File message timestamp extraction** — `_extract_file_message_ts` uses a retry loop on `files.info` (up to 4 attempts) to reliably capture the message timestamp for files uploaded via `files_upload_v2`, ensuring reactions work on image and video messages

### 31. GIF Syncing (Completed)
- **Slack GIF picker support** — GIFs sent via Slack's built-in `/giphy` picker or GIPHY integration are detected and synced
- **Nested block parsing** — `_build_file_context` extracts `image_url` from nested `image` blocks within `attachments`, which is how Slack structures GIF picker messages
- **Direct ImageBlock posting** — GIFs are always posted as `ImageBlock` elements via `chat.postMessage` using their public URLs, ensuring a proper message `ts` is captured for `PostMeta` (enabling reactions on GIFs)
- **No S3 required** — GIF URLs are already publicly accessible; no download or S3 upload needed

### 32. Video & Image Direct Upload (Completed)
- **S3 is now optional** — images and videos can be synced without S3 by using Slack's `files_upload_v2` directly
- **`S3_IMAGE_BUCKET` defaults to empty** — when not set, all media is uploaded directly to target channels
- **`S3_VIDEO_ENABLED` env var** — when `true` and S3 is configured, videos are also stored in S3; when `false` (default), videos always use direct upload regardless of S3 configuration
- **User attribution** — direct uploads include "Shared by User (Workspace)" in the `initial_comment`
- **Fallback text** — `post_message` supports a `fallback_text` argument for messages that contain only blocks (no text), satisfying Slack's accessibility requirements

### 33. Pause/Resume/Stop Sync (Completed)
- **Sync lifecycle controls** — individual channel syncs can be paused, resumed, or stopped from the Home tab
- **`status` column** on `sync_channels` — supports `active` and `paused` states
- **Paused syncs** — messages, threads, edits, deletes, and reactions are not processed for paused channels; the handler checks `status` before dispatching
- **Stop with confirmation** — stopping a sync shows a confirmation modal before soft-deleting; the bot leaves the channel and notifies the partner workspace
- **Admin attribution** — pause/resume/stop actions are attributed to the admin who performed them in notification messages
- **Home tab indicators** — paused syncs show a `:double_vertical_bar: Paused` status on the Home tab with a Resume button

### 34. User Profile Auto-Refresh (Completed)
- **`user_profile_changed` event** — subscribed in manifest and handled by `handle_user_profile_changed`
- **Directory update** — when a user changes their display name, real name, or email, the `user_directory` record is updated automatically
- **Mapping re-check** — after updating the directory, all user mappings involving the changed user are re-evaluated to detect new matches or update stale data

### 35. Member Joined Channel Handler (Completed)
- **`member_joined_channel` event** — subscribed in manifest and handled by `handle_member_joined_channel`
- **Untracked channel detection** — when SyncBot is added to a channel that is not part of any active sync, it posts a friendly message and leaves automatically
- **Self-check** — the handler verifies the joined user is SyncBot itself (via `get_own_bot_user_id`) before acting

### 36. Direct Pairing Requests (Completed)
- **Request-based pairing** — admins can send a direct pairing request to another workspace instead of manually sharing codes
- **DM notifications** — the partner workspace's admins receive a DM with Accept/Decline buttons and context about the requesting workspace
- **Home tab notification** — pending inbound pairing requests are shown on the partner's Home tab with Accept/Decline buttons
- **Bidirectional activation** — accepting a request activates the pairing on both sides, refreshes user directories, runs auto-matching, and updates both Home tabs
- **DM cleanup** — pairing request DMs are replaced with updated status messages when accepted, declined, or cancelled

### 37. Home Tab UI Enhancements (Completed)
- **Synced-since with year** — channel sync dates always display the full year (e.g., "February 18, 2026") using Python `datetime` formatting instead of Slack's `<!date>` token which omits the current year
- **Message count** — each sync displays the number of tracked messages from `PostMeta` (e.g., "Synced since: February 18, 2026 · 42 messages tracked")
- **Remote channel deep links** — target channel names in the Home tab and subscription modals are rendered as deep links using `slack://channel?team=T...&id=C...` URLs
- **Consolidated published channels** — all synced channels across pairings are shown in a single sorted list on the Home tab
- **Partner Home tab refresh** — all mutations (publish, unpublish, subscribe, pause, resume, stop, pairing changes) automatically re-publish the partner workspace's Home tab

### 38. User Mapping Screen Redesign (Completed)
- **Dedicated Home tab screen** — user mapping is now a full-screen Home tab view instead of a nested modal, providing more space and a better experience
- **Remote user avatars** — each mapped/unmapped user row displays the remote workspace user's profile photo as a right-aligned `ImageAccessoryElement`
- **Section headers with icons** — `:warning: *Unmapped Users*`, `:pencil2: *Soft / Manual Matches*`, `:lock: *Email Matches*` with `DividerBlock` separators
- **Edit modal avatars** — the user mapping edit modal also displays the remote user's avatar
- **Back navigation** — "Back to Home" button returns to the main Home tab view
- **Avatar caching** — `_avatar_lookup` fetches and caches profile photo URLs from the remote workspace

### 39. Code Refactoring — Module Split & Package Structure (Completed)
- **Flattened `utils/` directory** — all modules moved to top-level packages under `syncbot/` (no more `utils/` nesting)
- **Split monolithic files** into focused packages:
  - `helpers.py` → `helpers/` package (`core.py`, `slack_api.py`, `encryption.py`, `files.py`, `notifications.py`, `user_matching.py`, `workspace.py`, `oauth.py`, `_cache.py`)
  - `handlers.py` → `handlers/` package (`messages.py`, `groups.py`, `group_manage.py`, `channel_sync.py`, `users.py`, `tokens.py`, `federation_cmds.py`, `sync.py`, `_common.py`)
  - `builders.py` → `builders/` package (`home.py`, `channel_sync.py`, `user_mapping.py`, `sync.py`, `_common.py`)
  - `federation.py` + `federation_api.py` → `federation/` package (`core.py`, `api.py`)
- **Renamed `logging_config.py` to `logger.py`** — shorter, clearer module name
- **Added `__init__.py` re-exports** — `helpers/__init__.py` and `handlers/__init__.py` re-export public APIs for clean imports
- **Updated `pyproject.toml`** — `ruff` `known-first-party` updated, `per-file-ignores` for `app.py` E402

### 40. Security Audit — Dependency Updates & Hardening (Completed)
- **Dependency updates** — updated `cryptography`, `urllib3`, `certifi`, `requests`, and `pillow` to latest versions
- **Path traversal prevention** — file name sanitization via `_safe_file_parts` strips non-alphanumeric characters from file IDs and extensions
- **PyMySQL SSL hardening** — explicit SSL context with `certifi` CA bundle, `check_hostname=True`, `PROTOCOL_TLS_CLIENT`
- **URL-escaped credentials** — database username and password are `urllib.parse.quote_plus`-escaped in the connection string
- **Silent exception logging** — replaced bare `except: pass` blocks with `contextlib.suppress` or proper logging

### 41. Hardening & Performance Pass (Completed)
- **Critical bug fixes**:
  - Fixed broken import: `_users_list_page` was imported from `helpers.slack_api` instead of `helpers.user_matching` where it's defined
  - Fixed `str.format()` crash: messages containing literal curly braces (`{` or `}`) caused `KeyError`/`IndexError` in `apply_mentioned_users`; replaced with iterative `re.sub` using a lambda
- **Performance — Fernet caching**: Added `@functools.lru_cache(maxsize=2)` to `_get_fernet()` to cache the derived Fernet instance, eliminating 600,000 PBKDF2 iterations on every encrypt/decrypt call
- **Performance — `auth.test` consolidation**: Merged `get_own_bot_id` and `get_own_bot_user_id` into a single cached `_get_auth_info` call, halving Slack API round-trips for bot identity
- **Performance — `DbManager.count_records()`**: Added `SELECT COUNT(*)` method and replaced `len(find_records(...))` calls that were fetching all rows just to count them
- **Performance — module-level constants**: Moved `_PREFIXED_ACTIONS` tuple to module scope (avoids rebuilding on every request); cached `GetDBClass` column keys in a class-level `frozenset`
- **DoS — file download streaming**: All `requests.get` calls for files now use `stream=True` with 30s timeout, 8 KB chunks, and a 100 MB size cap
- **DoS — S3 client reuse**: `_get_s3_client()` creates the boto3 client once instead of per-file inside upload loops
- **DoS — input caps**: File attachments capped at 20 per event, mentions at 50 per message, federation user ingestion at 5,000 per request, federation images at 10 per message
- **DoS — federation body limit**: Local dev federation HTTP server enforces 1 MB max request body
- **DoS — connection pool safety**: `GLOBAL_ENGINE.dispose()` now only fires after all retries are exhausted, not on every transient failure (prevents disrupting other in-flight queries)
- **DoS — `decrypt_bot_token` reuse**: Eliminated duplicate `decrypt_bot_token` calls in the message edit handler
- **DRY — `_parse_private_metadata`**: Replaced 6 inline `import json; json.loads(private_metadata)` blocks across 4 handler files with a shared helper in `_common.py`
- **DRY — `_toggle_sync_status`**: Merged `handle_pause_sync` and `handle_resume_sync` (near-identical 60-line functions) into a single parameterized helper
- **DRY — `_activate_pairing_users`**: Extracted duplicated 30-line user directory refresh + seed + auto-match blocks from two pairing handlers
- **DRY — `_find_post_records`**: Extracted duplicated PostMeta query pattern (3 call sites) in `federation/api.py`
- **DRY — `_find_source_workspace_id`**: Extracted duplicated source-workspace lookup loop (5 call sites) in `messages.py`
- **DRY — user directory upsert**: Refactored `_refresh_user_directory` to call `_upsert_single_user_to_directory` instead of duplicating the upsert logic
- **DRY — `notify_admins_dm`**: Added optional `blocks` parameter for Block Kit support, consolidating the text-only and block DM paths
- **Lint clean**: All `ruff` checks pass with zero warnings

### 42. Workspace Groups Refactor — Many-to-Many Collaboration (Completed)
- **Replaced 1-to-1 Workspace Pairings with many-to-many Workspace Groups** — workspaces can now create or join groups, and a single workspace can belong to multiple groups with different combinations of members
- **New database tables**:
  - `workspace_groups` — group record with `name`, `invite_code`, `created_by_workspace_id`, `created_at`
  - `workspace_group_members` — junction table with `group_id`, `workspace_id`, `joined_at`, `deleted_at` (soft-delete)
- **Removed `workspace_pairings` table** — all pairing logic replaced by group membership
- **Schema changes to `syncs`** — replaced `pairing_id` with `group_id` (FK to `workspace_groups`), added `sync_mode` (`direct` or `group`), `target_workspace_id` (for direct syncs), and `publisher_workspace_id` (controls unpublish rights)
- **Schema changes to `user_mappings`** — replaced `pairing_id` with `group_id` (FK to `workspace_groups`)
- **Two sync modes**:
  - **Direct** — publish a channel 1-to-1 to a specific workspace in the group (behaves like legacy pairings)
  - **Group-wide** — publish a channel for any group member to subscribe independently
- **Selective stop sync** — when a workspace stops syncing, only that workspace's `PostMeta` and `SyncChannel` records are deleted; other group members continue uninterrupted
- **Publisher-only unpublish** — only the workspace that originally published a channel can unpublish it; the `Sync` record persists until the publisher explicitly removes it
- **Invite code flow** — creating a group generates a `XXX-XXXX` invite code; any workspace can join by entering the code; any existing group member can accept join requests
- **User mapping scoped per group** — user matching operates per workspace pair within a group; remote users displayed as "Display Name (Workspace Name)" and sorted by normalized name
- **Home tab redesign** — groups displayed as sections with member lists, inline channel syncs, "Publish Channel" button per group (no separate group selection step), and "Leave Group" button
- **Federation integration** — federated connections now create `WorkspaceGroup` and `WorkspaceGroupMember` records (with `federated_workspace_id`) instead of `WorkspacePairing` records
- **Leave group with cleanup** — soft-deletes the membership, removes associated `PostMeta`/`SyncChannel` records, leaves channels, removes user mappings, notifies remaining members, and deletes the group if empty
- **New handler modules** — `handlers/groups.py` (create/join) and `handlers/group_manage.py` (leave) replace `handlers/pairing.py` and `handlers/pairing_manage.py`
- **Removed modules** — `handlers/pairing.py`, `handlers/pairing_manage.py`, `builders/pairing.py`
- **Updated tests** — renamed test classes and methods to group terminology; updated action ID constants

### 43. Block Kit Shorthand & UI Polish (Completed)
- **Block Kit shorthand** — builders and handlers use `slack.blocks` helpers (`header`, `divider`, `context`, `section`, `button`, `actions`) instead of verbose `orm.*Block` constructors where applicable; `section` alias for section-style blocks in `slack/blocks.py`
- **Parameter shadowing** — in modules that take a `context` (request/Bolt) parameter, the blocks context helper is imported as `block_context` to avoid shadowing (e.g. `builders/home.py`, `builders/user_mapping.py`)
- **Synced Channels display** — Home tab Synced Channels rows no longer show the remote channel link; each row shows the local channel plus bracketed workspace list including the local workspace (e.g. _[Any: Sprocket Dev, Sprocket Dev Beta]_)
- **Deactivated/deleted users** — `UserDirectory` has `deleted_at`; deactivated users are soft-deleted and mappings purged; users no longer in `users.list` are hard-deleted; mapping UI, edit modal, and federation export filter out deleted users
- **Mapped display names** — synced messages in the target workspace use the mapped local user's name and icon when available; otherwise source name/icon with workspace indicator
- **Display name normalization** — `normalize_display_name()` used in user mapping UI and synced message display; user mapping screen shows "Display Name (Workspace)" with normalized names

### 44. Home and User Mapping Refresh — Performance & Cost (Completed)
- **Content hash** — Home tab and User Mapping Refresh handlers compute a stable hash from minimal DB queries (groups, members, syncs, pending invites; for User Mapping, mapping ids/methods). When the hash matches the last full refresh, the app skips the expensive path (no N× `team_info`, no directory refresh, no full rebuild).
- **Cached built blocks** — After a full refresh, the built Block Kit payload is cached (in-process, keyed by team/user and optionally group for User Mapping). When the hash matches, the app re-publishes that cached view with one `views.publish` instead of re-running all DB and Slack calls.
- **60-second cooldown** — If the user clicks Refresh again within 60 seconds and the hash is unchanged, the app re-publishes the cached view with a context message: "No new data. Wait __ seconds before refreshing again." The displayed seconds are the current remaining time from the last refresh (recomputed on each click). Cooldown constant: `REFRESH_COOLDOWN_SECONDS` (default 60) in `constants.py`.
- **Request-scoped caching** — `get_workspace_by_id(workspace_id, context=None)` and `get_admin_ids(client, team_id=None, context=None)` use the request `context` dict when provided: one DB read per distinct workspace, one `users.list` per distinct team per request. Reduces duplicate lookups when building the Home tab or when multiple workspaces' Home tabs are refreshed in one invocation.
- **Context through push-refresh paths** — When a change in one workspace triggers Home tab refreshes in others (e.g. publish channel, join group, user mapping refresh), the handler's `context` is passed into `_refresh_group_member_homes` and `refresh_home_tab_for_workspace`, so all `build_home_tab` calls in that request share the same request-scoped cache. Call sites updated in `channel_sync.py`, `group_manage.py`, `users.py`, `groups.py`, and `sync.py`.
- **User Mapping Refresh** — Same pattern applied to the User Mapping screen: content hash, cached blocks, 60s cooldown with message, and `build_user_mapping_screen(..., context=..., return_blocks=True)` for caching. Request-scoped `get_workspace_by_id` used when building the screen.

### 45. Backup, Restore, and Data Migration (Completed)
- **Slack UI** — Home tab has **Backup/Restore** (next to Refresh) and **Data Migration** (in External Connections when federation is enabled). Modals for download backup, restore from JSON, export workspace data, and import migration file; confirmation modals when HMAC or encryption-key/signature checks fail with option to proceed anyway.
- **Full-instance backup** — All tables exported as JSON with `version`, `exported_at`, `encryption_key_hash` (SHA-256 of `PASSWORD_ENCRYPT_KEY`), and HMAC over canonical JSON. Restore inserts in FK order; intended for empty/fresh DB (e.g. after AWS rebuild). On HMAC or encryption-key mismatch, payload stored in cache and confirmation modal pushed; after restore, Home tab caches invalidated for all workspaces.
- **Workspace migration export/import** — Export produces workspace-scoped JSON (syncs, sync channels, post meta, user directory, user mappings) with optional `source_instance` (webhook_url, instance_id, public_key, one-time connection code). Ed25519 signature for tampering detection. Import verifies signature, resolves or creates federated group (using `source_instance` when present), replace mode (remove then create SyncChannels/PostMeta/user_directory/user_mappings), optional tampering confirmation; Home tab and sync-list caches invalidated after import.
- **Instance A detection** — Federated pair request accepts optional `team_id` and `workspace_name`; stored as `primary_team_id` and `primary_workspace_name` on `federated_workspaces`. If a local workspace with that `team_id` exists, it is soft-deleted so the federated connection is the only representation of that workspace on the instance.

## Remaining Recommendations

### Low Priority

1. **Dependencies**
   - Update SQLAlchemy to 2.0+ (currently pinned to <2.0)
   - Review and update other dependencies

2. **Database Migrations**
   - Consider adopting Alembic for formal migration management

3. **Advanced Testing**
   - Add integration tests for database operations
   - Add tests for Slack API interactions (using mocks for full handler flows)
   - Add end-to-end sync workflow tests

## Notes

- The codebase is organized into focused packages (`handlers/`, `builders/`, `helpers/`, `federation/`, `db/`, `slack/`) with clear separation of concerns
- The routing system using mappers is clean and maintainable
- Database layer benefits from connection pooling, automatic retry with safe disposal, and `SELECT COUNT(*)` for counting
- All Slack API calls have rate-limit handling with exponential backoff
- Error isolation in sync loops ensures partial failures don't cascade
- 60 unit tests cover core helper functions, encryption, caching, event parsing, bot filtering, invite codes, and sync creation
- Structured JSON logging with correlation IDs enables fast CloudWatch Logs Insights queries
- Pre-commit hooks enforce consistent code style on every commit
- Admin/owner authorization enforced on all configuration actions with defense-in-depth
- Cross-workspace user matching resolves @mentions persistently with email, name, and manual matching (scoped per group)
- Bot messages from third-party bots are synced with proper attribution; only SyncBot's own messages are filtered
- Workspace Groups support many-to-many collaboration with invite codes, ensuring syncs are only established between explicitly trusted workspaces
- Channel sync supports both direct (1-to-1) and group-wide publish modes
- User matching auto-runs on group join; unmatchable users are filtered; matched users have inline override dropdowns
- Dev Container and Docker Compose configs provide zero-install local development with live editing
- Reactions, images, videos, and GIFs are all synced bidirectionally with proper user attribution
- Individual syncs can be paused, resumed, and stopped with selective history cleanup and publisher-only unpublish
- User profile changes (display name, email) are detected automatically and trigger mapping re-evaluation
- SyncBot self-removes from unconfigured channels with a friendly message
- All foreign key relationships use `ON DELETE CASCADE` for clean data removal
- File downloads are streamed with timeouts and size caps to prevent DoS
- Fernet key derivation is cached for performance; bot identity is resolved in a single API call
- Duplicated code has been consolidated into shared helpers throughout handlers and federation modules
- Home and User Mapping Refresh buttons use content hash, cached blocks, and a 60s cooldown to minimize RDS and Slack API usage when nothing has changed; request-scoped caching and context passing through push-refresh paths keep multi-workspace updates lightweight