# SyncBot
<img src="assets/icon.png" alt="SyncBot Icon" width="128">

SyncBot is a Slack app originally developed for the [F3 Community](https://github.com/F3Nation-Community/syncbot) and has been forked here for general use by other Slack Workspace admins. It is intended to provide a replication ("Sync") service for messages and replies across Slack Workspaces on the free tier. Once configured, messages, threads, edits, deletes, reactions, images, videos, and GIFs are automatically mirrored to every channel in a Sync group.

## End-User Quick Start

1. Click the install link from a desktop browser (make sure you've selected the correct workspace in the upper right)
2. Open the **SyncBot** app from the sidebar and click the **Home** tab (requires workspace admin or owner)
3. The Home tab shows everything in one view:
   - **SyncBot Configuration (top row)** — **Refresh** and **Backup/Restore** (full-instance backup download and restore from JSON)
   - **Workspace Groups** — create or join groups of workspaces that can sync channels together
   - **Per-group sections** — for each group you can publish channels, manage user mapping (dedicated Home tab screen), and see/manage channel syncs inline
   - **Synced Channels** — each row shows the local channel and workspace list in brackets (e.g. _[Any: Your Workspace, Partner Workspace]_), with pause/resume and stop controls, synced-since date, and tracked message count
   - **External Connections** *(when federation is enabled)* — Generate/Enter Connection Code and **Data Migration** (export workspace data for migration to another instance, or import a migration file)

Things to know:

- Only workspace **admins and owners** can configure syncs (set `REQUIRE_ADMIN=false` to allow all users)
- Messages, threads, edits, deletes, reactions, images, videos, and GIFs are all synced
- Messages from other bots are synced; only SyncBot's own messages are filtered to prevent loops
- Existing messages are not back-filled; syncing starts from the moment a channel is linked
- Do not add SyncBot manually to channels. SyncBot adds itself when you configure a Sync. If it detects it was added to an unconfigured channel it will post a message and leave automatically
- Both public and private channels are supported
- **Workspace Groups**: Workspaces must belong to the same **group** before they can sync channels or map users. Admins can create a new group (which generates an invite code) or join an existing group by entering a code. A workspace can be in multiple groups with different combinations of other workspaces
- **Sync Modes**: When publishing a channel inside a group, admins choose either **1-to-1** (only a specific workspace can subscribe) or **group-wide** (any group member can subscribe independently)
- **Pause/Resume**: Individual channel syncs can be paused and resumed without losing configuration. Paused channels do not sync any messages, threads, or reactions
- **Selective Stop**: When a workspace stops syncing a channel, only that workspace's history is removed. Other workspaces continue syncing uninterrupted. The published channel remains available until the original publisher unpublishes it
- **Uninstall/Reinstall**: If a workspace uninstalls SyncBot, group memberships and syncs are paused (not deleted). Reinstalling within the retention period (default 30 days, configurable via `SOFT_DELETE_RETENTION_DAYS`) automatically restores everything. Group members are notified via DMs and channel messages
- **User Mapping**: Users are automatically mapped across workspaces by email or display name. Admins can manually edit mappings via the User Mapping screen (scoped per group). Remote users are displayed as "Display Name (Workspace Name)" and sorted by normalized name
- **Refresh buttons**: The Home tab and User Mapping screens have Refresh buttons. To keep RDS and Slack API usage low, repeated clicks with no data changes are handled lightly: a 60-second cooldown applies, and when nothing has changed the app reuses cached content and shows "No new data. Wait __ seconds before refreshing again." when you click again too soon
- **Media Sync**: Images and videos are uploaded directly to target channels (or via S3 if configured). GIFs from the Slack GIF picker or GIPHY are synced as image blocks
- **External Connections** *(opt-in)*: Workspaces running their own SyncBot deployment can be connected via the "External Connections" section on the Home tab. One admin generates a connection code and shares it out-of-band; the other admin enters it. Messages, edits, deletes, reactions, and user matching work across instances. **Data Migration** in the same section lets you export your workspace data (syncs, channels, post meta, user directory, user mappings) for moving to another instance, or import a migration file after connecting. Disabled by default — set `SYNCBOT_FEDERATION_ENABLED=true` and `SYNCBOT_PUBLIC_URL` to enable
- **Backup/Restore**: Use **Backup/Restore** on the Home tab to download a full-instance backup (all tables as JSON) or restore from a backup file. Intended for disaster recovery (e.g. before rebuilding AWS). Backup includes an integrity check (HMAC); restore checks the encryption key hash — if it differs, bot tokens will not decrypt until workspaces re-authorize. Restore targets an empty or fresh database
- **Data Migration**: When federation is enabled, **Data Migration** opens a modal to export your workspace data (for moving that workspace to its own instance) or import a migration file. The export can include a one-time connection code so the new instance can connect to the old one in one step. Import uses replace mode (existing sync channels in the federated group are replaced). User mappings are carried over (same Slack workspace, so user IDs match). Exports are signed (Ed25519) for tampering detection; import still proceeds on mismatch but shows a warning

---

## Deploying to AWS

SyncBot ships with a full AWS SAM template (`template.yaml`) that provisions everything on the **free tier**:

| Resource | Service | Free-Tier Detail |
|----------|---------|-----------------|
| Compute | Lambda (128 MB) | 1M requests/month free |
| API | API Gateway v1 | 1M calls/month free |
| Database | RDS MySQL (db.t3.micro) | 750 hrs/month free (12 months) |
| Storage | S3 (3 buckets) | 5 GB free |

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for message sync flow, AWS infrastructure, backup/restore and data migration flows, and performance/cost optimizations (including Refresh button behavior and request-scoped caching).

---

## Backup, Restore, and Data Migration

### Full-instance backup and restore

Use **Backup/Restore** (Home tab, next to Refresh) to:

- **Download backup** — Generates a JSON file containing all tables (workspaces, groups, syncs, channels, post meta, user directory, user mappings, federation, instance keys). The file is sent to your DM. Backup includes an HMAC for integrity and a hash of the encryption key. **Use the same `PASSWORD_ENCRYPT_KEY` on the target instance** so restored bot tokens decrypt; otherwise workspaces must reinstall the app to re-authorize.
- **Restore from backup** — Paste the backup JSON in the modal and submit. Restore is intended for an **empty or fresh database** (e.g. after an AWS rebuild). If the encryption key hash or HMAC does not match, you will see a warning and can still proceed (e.g. if you edited the file on purpose).

After restore, Home tab caches are cleared so the next Refresh shows current data.

### Workspace data migration (federation)

When **External Connections** is enabled, **Data Migration** (in that section) lets you:

- **Export** — Download a workspace-scoped JSON file (syncs, sync channels, post meta, user directory, user mappings) plus an optional one-time connection code so the new instance can connect to the source in one step. The file is signed (Ed25519) for tampering detection.
- **Import** — Paste a migration file, then submit. If the file includes a connection payload and you are not yet connected, the app establishes the federation connection and creates the group, then imports. Existing sync channels for that workspace in the federated group are **replaced** (replace mode). User mappings are imported where both workspaces exist on the new instance. If the signature check fails, a warning is shown but you can still proceed.

After import, Home tab and sync-list caches for that workspace are cleared.

**Instance A behavior:** When a workspace that used to be on Instance A connects to A from a new instance (B) via federation and sends its `team_id`, A soft-deletes the matching local workspace row so only the federated connection represents that workspace. See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

---

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| **AWS SAM CLI** | latest | Build & deploy Lambda + infra |
| **Docker** | latest | SAM uses a container to build the Lambda package |
| **MySQL client** *(optional)* | any | Run schema scripts against the DB |

### Create a Slack app

Before deploying (or developing locally) you need a Slack app:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** → **From an app manifest**
2. Select your workspace, then paste the contents of [`slack-manifest.yaml`](slack-manifest.yaml)
3. After creating the app, upload the icon from [`assets/icon.png`](assets/icon.png) on the **Basic Information** page under **Display Information**
4. Note these values — you'll need them for deploy and/or local development:

| Where to find it | Value | Used for |
|-------------------|-------|----------|
| Basic Information → **App Credentials** | Signing Secret | Production deploy |
| Basic Information → **App Credentials** | Client ID, Client Secret | Production deploy (OAuth) |
| **OAuth & Permissions** → **Install to Workspace** → Install, then copy | Bot User OAuth Token (`xoxb-...`) | **Local development** |

5. After your first deploy, come back and replace the placeholder URLs in the app settings with your actual API Gateway endpoint (shown in the CloudFormation stack outputs)

> **Why do I need to install the app manually for local dev?** In production, SyncBot uses OAuth so each workspace gets its own token automatically. In local development mode, there's no OAuth flow — you connect to a single workspace using a bot token you copy from the Slack app settings.

### First-time deploy

1. **Build** the Lambda package:

```bash
sam build --use-container
```

2. **Deploy** with guided prompts:

```bash
sam deploy --guided
```

You'll be prompted for parameters like `DatabaseUser`, `DatabasePassword`, `SlackSigningSecret`, `SlackClientId`, `SlackClientSecret`, `EncryptionKey`, and `AllowedDBCidr`. These are stored as CloudFormation parameters (secrets use `NoEcho`).

3. **Initialize the database** — after the stack creates the RDS instance, grab the endpoint from the CloudFormation outputs and run:

```bash
mysql -h <RDS_ENDPOINT> -u <DB_USER> -p<DB_PASSWORD> syncbot < db/init.sql
```

4. **Update your Slack app URLs** to point at the API Gateway endpoint shown in the stack outputs (e.g., `https://xxxxx.execute-api.us-east-2.amazonaws.com/Prod/slack/events`).

### Sharing infrastructure across apps

If you run multiple apps in the same AWS account, you can point SyncBot at existing resources instead of creating new ones. Every `Existing*` parameter defaults to empty (create new); set it to an existing resource name to reuse it.

| Parameter | What it skips |
|-----------|---------------|
| `ExistingDatabaseHost` | VPC, subnets, security groups, RDS instance |
| `ExistingSlackStateBucket` | Slack OAuth state S3 bucket |
| `ExistingInstallationBucket` | Slack installation data S3 bucket |
| `ExistingImagesBucket` | Synced-images S3 bucket |

Example — deploy with an existing RDS and images bucket:

```bash
sam deploy --guided \
  --parameter-overrides \
    ExistingDatabaseHost=mydb.xxxx.us-east-2.rds.amazonaws.com \
    ExistingImagesBucket=my-shared-images-bucket
```

Each app sharing the same RDS should use a **different `DatabaseSchema`** (the default is `syncbot`). Create the schema and initialize the tables on the existing instance:

```bash
mysql -h <EXISTING_RDS_ENDPOINT> -u <DB_USER> -p -e "CREATE DATABASE IF NOT EXISTS syncbot;"
mysql -h <EXISTING_RDS_ENDPOINT> -u <DB_USER> -p syncbot < db/init.sql
```

**What about API Gateway and Lambda?** Each stack always creates its own API Gateway and Lambda function. These are lightweight resources that don't affect free-tier billing — the free tier quotas (1M API calls, 1M Lambda requests) are shared across your entire account regardless of how many gateways or functions you have. If you want a unified domain across apps, put a CloudFront distribution or API Gateway custom domain in front.

### Subsequent deploys

```bash
sam build --use-container
sam deploy                        # staging (default profile)
sam deploy --config-env prod      # production profile
```

The `samconfig.toml` file stores per-environment settings so you don't have to re-enter parameters.

### CI/CD via GitHub Actions

Pushes to `main` automatically build and deploy via `.github/workflows/sam-pipeline.yml`:

1. **Build** — `sam build --use-container`
2. **Deploy to test** — automatic
3. **Deploy to prod** — requires manual approval (configure in GitHub environment settings)

#### One-time setup

1. **Create an IAM user** for deployments with permissions for CloudFormation, Lambda, API Gateway, S3, IAM, and RDS. Generate an access key pair.

2. **Create a SAM deployment bucket** — SAM needs an S3 bucket to upload build artifacts during deploy:

```bash
aws s3 mb s3://my-sam-deploy-bucket --region us-east-2
```

3. **Create GitHub Environments** — Go to your repo → **Settings** → **Environments** and create two environments: `test` and `prod`. For `prod`, enable **Required reviewers** so production deploys need manual approval.

4. **Add GitHub Secrets** — Under **Settings** → **Secrets and variables** → **Actions**, add these as **environment secrets** for both `test` and `prod`:

| Secret | Where to find it |
|--------|-----------------|
| `AWS_ACCESS_KEY_ID` | IAM user access key (step 1) |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key (step 1) |
| `SLACK_SIGNING_SECRET` | Slack app → Basic Information → App Credentials |
| `SLACK_CLIENT_SECRET` | Slack app → Basic Information → App Credentials |
| `DATABASE_PASSWORD` | The RDS master password you chose |
| `PASSWORD_ENCRYPT_KEY` | Any passphrase for bot-token encryption at rest |

5. **Add GitHub Variables** — Under the same settings page, add these as **environment variables** for each environment:

| Variable | `test` value | `prod` value |
|----------|-------------|-------------|
| `AWS_STACK_NAME` | `syncbot-test` | `syncbot-prod` |
| `AWS_S3_BUCKET` | `my-sam-deploy-bucket` | `my-sam-deploy-bucket` |
| `STAGE_NAME` | `staging` | `prod` |

#### Deploy flow

Once configured, merge or push to `main` and the pipeline runs:

```
push to main → sam build → deploy to test → (manual approval) → deploy to prod
```

Monitor progress in your repo's **Actions** tab. The first deploy creates the full CloudFormation stack (VPC, RDS, Lambda, API Gateway, S3 buckets). Subsequent deploys update only what changed.

> **Tip:** If you prefer to do the very first deploy manually (to see the interactive prompts), run `sam deploy --guided` locally first, then let the pipeline handle all future deploys.

---

## Local Development

### Option A: Dev Container (recommended)

Opens the project inside a Docker container with full editor integration — IntelliSense, debugging, terminal, and linting all run in the container. No local Python or MySQL install needed.

**Prerequisites:** Docker Desktop + the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension

#### 1. Clone the repo and create a `.env` file

```bash
git clone https://github.com/GITHUB_ORG_NAME/syncbot.git
cd syncbot
```

Copy the example env file and fill in your bot token (from the [Create a Slack app](#create-a-slack-app) step above):

```bash
cp .env.example .env
```

At minimum, set `SLACK_BOT_TOKEN` to the `xoxb-...` token you copied from **OAuth & Permissions** after installing the app to your workspace.

#### 2. Open in Dev Container

Open the project folder in your VSCodium-based editor, then:

- Press `Cmd+Shift+P` → **Dev Containers: Reopen in Container**
- Or click the green remote indicator in the bottom-left corner → **Reopen in Container**

The first build takes a minute or two. After that, your editor is running inside the container with Python, MySQL, and all dependencies ready.

#### 3. Run the app

Open the integrated terminal (it's already inside the container) and run:

```bash
cd syncbot && python app.py
```

The app starts on **port 3000** (auto-forwarded to your host).

#### 4. Expose to Slack

In a **local** terminal (outside the container), start a tunnel using your favorite platform, for instance [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) or [ngrok](https://ngrok.com/docs/what-is-ngrok):

```bash
cloudflared tunnel --url http://localhost:3000/
```
or
```bash
ngrok http 3000
```

Then update your Slack app's **Event Subscriptions** and **Interactivity** URLs to point at the public URL.

#### 5. Run tests

```bash
python -m pytest tests -v
```

#### 6. Connect to the database

```bash
mysql -h db -u root -prootpass syncbot
```

The database schema is initialized automatically on first run. To reset it, rebuild the container with **Dev Containers: Rebuild Container**.

---

### Option B: Docker Compose (without Dev Container)

Runs everything in containers but you edit files on your host. Good if you don't want to use the Dev Container extension.

**Prerequisites:** Docker Desktop

#### 1. Clone and configure

```bash
git clone https://github.com/GITHUB_ORG_NAME/syncbot.git
cd syncbot
```

Create a `.env` file (same as Option A above — `cp .env.example .env` and set your `SLACK_BOT_TOKEN`).

#### 2. Start the app

```bash
docker compose up --build
```

This starts both MySQL and the app. The database schema is initialized automatically on first run. The app listens on **port 3000**.

To run in the background:

```bash
docker compose up --build -d
docker compose logs -f app      # follow app logs
```

Code changes require a restart (no rebuild — the code is mounted as a volume):

```bash
docker compose restart app
```

Only rebuild when `requirements.txt` changes:

```bash
docker compose up --build
```

#### 3. Run tests and other commands

```bash
docker compose exec app python -m pytest /app/tests -v
docker compose exec db mysql -u root -prootpass syncbot
```

#### Resetting

```bash
docker compose down                  # stop everything
docker compose down -v               # stop and delete the database volume
```

---

### Option C: Native Python

Run the app directly on your machine with a local or containerized MySQL instance.

**Prerequisites:**

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | 3.11+ | Runtime |
| **Poetry** | 1.6+ | Dependency management |
| **Docker** *(optional)* | latest | Easiest way to run MySQL locally |

#### 1. Clone and install dependencies

```bash
git clone https://github.com/GITHUB_ORG_NAME/syncbot.git
cd syncbot
poetry install --with dev
```

#### 2. Set up a local MySQL database

Run a MySQL 8 instance (Docker is easiest):

```bash
docker run -d --name syncbot-db \
  -e MYSQL_ROOT_PASSWORD=rootpass \
  -e MYSQL_DATABASE=syncbot \
  -p 3306:3306 \
  mysql:8
```

Initialize the schema:

```bash
mysql -h 127.0.0.1 -u root -prootpass syncbot < db/init.sql
```

#### 3. Configure environment variables

Copy the example env file and fill in your bot token (from the [Create a Slack app](#create-a-slack-app) step):

```bash
cp .env.example .env
source .env
```

At minimum, set `SLACK_BOT_TOKEN` to the `xoxb-...` token from **OAuth & Permissions**. For native Python, also verify the database values match your local MySQL (`DATABASE_HOST=127.0.0.1` by default). See `.env.example` for all available options.

#### 4. Run the app

```bash
poetry run python syncbot/app.py
```

The app starts a local Bolt server on **port 3000**. Use your favorite tunnel platform to expose it to Slack:

```bash
cloudflared tunnel --url http://localhost:3000/
```
or
```bash
ngrok http 3000
```

Then update your Slack app's **Event Subscriptions** and **Interactivity** URLs to the public URL.

#### 5. Run tests

```bash
poetry run pytest -v
```

All tests run against mocked dependencies — no database or Slack credentials needed.

---

## Environment Variables Reference

### Always required

| Variable | Description |
|----------|-------------|
| `DATABASE_HOST` | MySQL hostname |
| `ADMIN_DATABASE_USER` | MySQL username |
| `ADMIN_DATABASE_PASSWORD` | MySQL password |
| `ADMIN_DATABASE_SCHEMA` | MySQL database name |

### Required in production (Lambda)

| Variable | Description |
|----------|-------------|
| `SLACK_SIGNING_SECRET` | Verifies incoming Slack requests |
| `ENV_SLACK_CLIENT_ID` | OAuth client ID |
| `ENV_SLACK_CLIENT_SECRET` | OAuth client secret |
| `ENV_SLACK_SCOPES` | Comma-separated OAuth scopes |
| `ENV_SLACK_STATE_S3_BUCKET_NAME` | S3 bucket for OAuth state |
| `ENV_SLACK_INSTALLATION_S3_BUCKET_NAME` | S3 bucket for installations |
| `PASSWORD_ENCRYPT_KEY` | Passphrase for Fernet bot-token encryption |

### Local development only

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Bot token (presence triggers local-dev mode) |
| `AWS_ACCESS_KEY_ID` | For S3 uploads during local dev |
| `AWS_SECRET_ACCESS_KEY` | For S3 uploads during local dev |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCAL_DEVELOPMENT` | `false` | Set to `true` to skip Slack token verification at startup and use human-readable log output instead of JSON. |
| `REQUIRE_ADMIN` | `true` | When `true`, only workspace admins/owners can configure syncs. Set to `false` to allow all users. |
| `S3_IMAGE_BUCKET` | *(empty)* | S3 bucket name for synced images. When empty, images are uploaded directly to Slack via `files_upload_v2`. |
| `S3_IMAGE_URL` | *(auto from bucket)* | Public URL prefix for S3 images (e.g., `https://mybucket.s3.amazonaws.com/`). Auto-generated from `S3_IMAGE_BUCKET` if not set. |
| `S3_VIDEO_ENABLED` | `false` | When `true` and `S3_IMAGE_BUCKET` is set, videos are also stored in S3. When `false`, videos are uploaded directly to Slack regardless of S3 configuration. |
| `SOFT_DELETE_RETENTION_DAYS` | `30` | Days to keep soft-deleted workspace data before permanent purge. When a workspace uninstalls, its group memberships and syncs are paused; reinstalling within this window restores everything. |
| `SYNCBOT_FEDERATION_ENABLED` | `false` | Set to `true` to enable the External Connections feature (cross-instance sync with other SyncBot deployments). |
| `SYNCBOT_INSTANCE_ID` | *(auto-generated)* | Unique UUID for this SyncBot instance. Auto-generated on first run if not set. Used by external connections. |
| `SYNCBOT_PUBLIC_URL` | *(none)* | Publicly reachable base URL of this instance (e.g., `https://syncbot.example.com`). Required when external connections are enabled. |

---

## API Endpoints and Slack Commands

### HTTP Endpoints (API Gateway)

All endpoints are served by a single Lambda function. Slack sends requests to the `/slack/*` URLs after you configure the app. The `/api/federation/*` endpoints handle cross-instance communication for external connections.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/slack/events` | Receives all Slack events (messages, actions, view submissions) and slash commands |
| `GET` | `/slack/install` | OAuth install page — redirects the user to Slack's authorization screen |
| `GET` | `/slack/oauth_redirect` | OAuth callback — Slack redirects here after the user approves the app |
| `POST` | `/api/federation/pair` | Accept an incoming external connection request |
| `POST` | `/api/federation/message` | Receive a forwarded message from a connected instance |
| `POST` | `/api/federation/message/edit` | Receive a message edit from a connected instance |
| `POST` | `/api/federation/message/delete` | Receive a message deletion from a connected instance |
| `POST` | `/api/federation/message/react` | Receive a reaction from a connected instance |
| `POST` | `/api/federation/users` | Exchange user directory with a connected instance |
| `GET` | `/api/federation/ping` | Health check for connected instances |

### Subscribed Slack Events

| Event | Handler | Description |
|-------|---------|-------------|
| `app_home_opened` | `handle_app_home_opened` | Publishes the Home tab with workspace groups, channel syncs, and user matching. |
| `member_joined_channel` | `handle_member_joined_channel` | Detects when SyncBot is added to an unconfigured channel; posts a message and leaves. |
| `message.channels` / `message.groups` | `respond_to_message_event` | Fires on new messages, edits, deletes, and file shares in public/private channels. Dispatches to sub-handlers for new posts, thread replies, edits, deletes, and reactions. |
| `reaction_added` / `reaction_removed` | `_handle_reaction` | Syncs emoji reactions to the corresponding message in all target channels. |
| `team_join` | `handle_team_join` | Fires when a new user joins a connected workspace. Adds the user to the directory and re-checks unmatched user mappings. |
| `tokens_revoked` | `handle_tokens_revoked` | Handles workspace uninstall — soft-deletes workspace data and notifies group members. |
| `user_profile_changed` | `handle_user_profile_changed` | Detects display name or email changes and updates the user directory and mappings. |

---

## Project Structure

```
syncbot/
├── syncbot/                   # Application code (Lambda function)
│   ├── app.py                 # Entry point — Slack Bolt app + Lambda handler
│   ├── constants.py           # Env-var names, startup validation
│   ├── routing.py             # Event/action → handler dispatcher
│   ├── logger.py              # Structured JSON logging, correlation IDs, metrics
│   ├── requirements.txt       # Pinned runtime dependencies (used by SAM build)
│   ├── builders/              # Slack UI construction (Home tab, modals, forms)
│   │   ├── home.py            # App Home tab builder
│   │   ├── channel_sync.py    # Publish/subscribe channel sync UI
│   │   ├── user_mapping.py    # User mapping Home tab screen & edit modal
│   │   └── sync.py            # Sync detail views
│   ├── handlers/              # Slack event & action handlers
│   │   ├── messages.py        # Message sync — posts, threads, edits, deletes, reactions
│   │   ├── groups.py          # Group lifecycle — create, join, accept, cancel
│   │   ├── group_manage.py    # Leave group with confirmation
│   │   ├── channel_sync.py    # Publish, unpublish, subscribe, pause, resume, stop
│   │   ├── users.py           # team_join, profile changes, user mapping edits
│   │   ├── tokens.py          # Uninstall / tokens_revoked handler
│   │   ├── federation_cmds.py # Federation UI actions (generate/enter/remove codes)
│   │   ├── sync.py            # Sync join/remove handlers
│   │   └── _common.py         # Shared handler utilities (EventContext, sanitize, metadata)
│   ├── helpers/               # Business logic, Slack API wrappers, utilities
│   │   ├── core.py            # safe_get, request classification, admin checks
│   │   ├── slack_api.py       # Slack API helpers (retry, bot identity, user info)
│   │   ├── encryption.py      # Fernet bot-token encryption (cached PBKDF2)
│   │   ├── files.py           # File download/upload (streaming, S3, size caps)
│   │   ├── notifications.py   # Admin DMs, channel notifications
│   │   ├── user_matching.py   # Cross-workspace user matching & mention resolution
│   │   ├── workspace.py       # Workspace record helpers, group lookups
│   │   ├── oauth.py           # OAuth install/redirect helpers
│   │   └── _cache.py          # Simple in-process TTL cache
│   ├── federation/            # Cross-instance sync (opt-in)
│   │   ├── core.py            # HMAC signing, HTTP client, payload builders
│   │   └── api.py             # Federation API endpoint handlers
│   ├── db/
│   │   ├── __init__.py        # Engine, session, DbManager (pooling + retry)
│   │   └── schemas.py         # SQLAlchemy ORM models
│   └── slack/
│       ├── actions.py         # Action/callback ID constants
│       ├── forms.py           # Form definitions
│       ├── blocks.py          # Block Kit shorthand helpers
│       └── orm.py             # Block Kit ORM (BlockView, SectionBlock, etc.)
├── db/
│   └── init.sql               # Complete database schema (pre-release: single source)
├── tests/                     # pytest unit tests (60 tests)
├── .devcontainer/             # Dev Container config (Cursor/VS Code)
├── Dockerfile                 # App container for local development
├── docker-compose.yml         # Full local stack (app + MySQL)
├── template.yaml              # AWS SAM infrastructure-as-code
├── samconfig.toml             # SAM CLI deploy profiles (staging / prod)
├── slack-manifest.yaml        # Slack app manifest (paste into api.slack.com)
├── pyproject.toml             # Poetry project config + ruff linter settings
└── .github/workflows/
    └── sam-pipeline.yml       # CI/CD: build → deploy staging → deploy prod
```

## Improvements and Roadmap

See [IMPROVEMENTS.md](IMPROVEMENTS.md) for a detailed list of completed and planned improvements.

## License

This project is licensed under **AGPL-3.0**, which means you can use and modify it, just keep it open and shareable. See [LICENSE](LICENSE) for details.
