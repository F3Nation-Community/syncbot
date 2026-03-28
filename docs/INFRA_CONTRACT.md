# Infrastructure Contract (Provider-Agnostic)

This document defines what any infrastructure provider (AWS, GCP, Azure, etc.) must supply so SyncBot runs correctly. Forks can swap provider-specific IaC in `infra/<provider>/` as long as they satisfy this contract.

**Deploy entrypoint:** From the repo root, `./deploy.sh` (macOS/Linux, or Git Bash/WSL bash) or `.\deploy.ps1` (Windows PowerShell — finds Git Bash or WSL, then bash) runs an interactive helper that delegates to `infra/<provider>/scripts/deploy.sh`. After identity/auth prompts, each provider script shows a **Deploy Tasks** menu (comma-separated numbers, default all): bootstrap (AWS only), build/deploy, CI/CD (GitHub Actions), Slack API configuration, and DR backup secret output—so operators can run subsets (e.g. CI/CD only against an existing stack) without mid-flow surprises. That flow sets Cloud/Terraform resources and runtime env vars consistent with this document. Step-by-step and manual alternatives: [DEPLOYMENT.md](DEPLOYMENT.md).

**Schema:** The database schema is managed by **Alembic** (`alembic upgrade head`). **AWS Lambda:** Migrations are **not** run on every cold start (that would exceed Slack’s interaction ack budget). The Lambda handler accepts a post-deploy invoke with payload `{"action":"migrate"}` to run migrations; the reference GitHub Actions deploy workflow invokes this after `sam deploy`. **Cloud Run / local / container:** Migrations still run at process startup before the HTTP server accepts traffic (no Slack ack on that path).

## Runtime Environment Variables

The application reads configuration from environment variables. Providers must inject these at runtime (e.g. Lambda env, Cloud Run env, or a compatible secret/config layer).

## Toolchain Baseline

- Runtime baseline: **Python 3.12**.
- Keep runtime/tooling aligned across:
  - Lambda/Cloud Run runtime configuration
  - CI Python version
  - `pyproject.toml` Python constraint
  - `syncbot/requirements.txt` deployment pins
- When dependency constraints change in `pyproject.toml`, refresh the lockfile and deployment requirements. The **pre-commit `sync-requirements` hook** regenerates **`syncbot/requirements.txt`** and **`infra/aws/db_setup/requirements.txt`** from `poetry.lock` when you commit lockfile changes. Manually: `poetry lock`, then `poetry export -f requirements.txt --without-hashes -o syncbot/requirements.txt` and rebuild `infra/aws/db_setup/requirements.txt` as in [.pre-commit-config.yaml](../.pre-commit-config.yaml).

### Database (backend-agnostic)

| Variable | Description |
|----------|-------------|
| `DATABASE_BACKEND` | `mysql` (default), `postgresql`, or `sqlite`. |
| `DATABASE_URL` | Full SQLAlchemy URL. When set, overrides host/user/password/schema. **Required for SQLite** (e.g. `sqlite:///path/to/syncbot.db`). For `mysql` / `postgresql`, optional if unset (legacy vars below are used). |
| `DATABASE_HOST` | Database hostname (IP or FQDN). Required when backend is `mysql` or `postgresql` and `DATABASE_URL` is unset. |
| `DATABASE_PORT` | Optional. Defaults to **5432** for `postgresql`, **3306** for `mysql`. Set explicitly for external providers that use a non-standard port (e.g. TiDB Cloud **4000**). |
| `DATABASE_USER` | Username. Required when backend is `mysql` or `postgresql` and `DATABASE_URL` is unset. Some providers (e.g. TiDB Cloud Serverless) require a cluster-specific prefix on every SQL user; AWS SAM exposes **`ExistingDatabaseUsernamePrefix`** so the bootstrap Lambda prepends it to **ExistingDatabaseAdminUser** and to the dedicated app user `{prefix}.syncbot_user_{stage}` (a dot separator is added automatically; use bare admin names like `root` when set). On GCP with **`existing_db_username_prefix`** set, Terraform sets `DATABASE_USER` the same way and ignores **`existing_db_user`**. |
| `DATABASE_PASSWORD` | Password. Required when backend is `mysql` or `postgresql` and `DATABASE_URL` is unset. |
| `DATABASE_SCHEMA` | Database name (MySQL) or PostgreSQL database name (same convention as MySQL). Use alphanumeric and underscore only for PostgreSQL when the app must `CREATE DATABASE` at bootstrap. |
| `DATABASE_TLS_ENABLED` | Optional TLS toggle (`true`/`false`). Defaults to enabled outside local dev. |
| `DATABASE_SSL_CA_PATH` | Optional CA bundle path when TLS is enabled. If unset, the app uses the first existing file among common OS locations (Amazon Linux, Debian, Alpine); PostgreSQL omits `sslrootcert` when none exist so libpq uses the system trust store. |

**SQLite (forks / local):** Set `DATABASE_BACKEND=sqlite` and `DATABASE_URL=sqlite:///path/to/file.db`. Single-writer; suitable for small teams and dev.

**MySQL (default):** Set `DATABASE_BACKEND=mysql` (or rely on the default) and either `DATABASE_URL` (`mysql+pymysql://...`) or the four host/user/password/schema vars. The AWS SAM template parameter `DatabaseEngine=mysql` (default) matches this backend.

**PostgreSQL:** Set `DATABASE_BACKEND=postgresql` and either `DATABASE_URL` (`postgresql+psycopg2://...`) or `DATABASE_HOST`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_SCHEMA`. Deploy-time bootstrap credentials (e.g. `ExistingDatabaseAdmin*` in AWS) are used only for one-time setup; the app reads `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_SCHEMA` at runtime.

### Required in production (non–local)

| Variable | Description |
|----------|-------------|
| `SLACK_SIGNING_SECRET` | Slack request verification (Basic Information → App Credentials). |
| `SLACK_CLIENT_ID` | Slack OAuth client ID. |
| `SLACK_CLIENT_SECRET` | Slack OAuth client secret. |
| `SLACK_BOT_SCOPES` | Comma-separated OAuth **bot** scopes. Must match `slack-manifest.json` `oauth_config.scopes.bot` and `syncbot/slack_manifest_scopes.py` `BOT_SCOPES`. |
| `SLACK_USER_SCOPES` | Comma-separated OAuth **user** scopes. Must match `oauth_config.scopes.user` and `syncbot/slack_manifest_scopes.py` `USER_SCOPES`. If this env requests scopes that are not declared on the Slack app, install fails with `invalid_scope`. |
| `TOKEN_ENCRYPTION_KEY` | **Required** in production; must be a strong, random value (e.g. 16+ characters). Providers may auto-generate it (e.g. AWS Secrets Manager). Back up the key after first deploy. In local dev you may set it manually or leave unset. |

**Reference wiring:** AWS SAM ([`infra/aws/template.yaml`](../infra/aws/template.yaml)) maps CloudFormation parameters to Lambda env: **`SlackOauthBotScopes`** / **`SlackOauthUserScopes`** → **`SLACK_BOT_SCOPES`** / **`SLACK_USER_SCOPES`** (defaults match `BOT_SCOPES` / `USER_SCOPES`); **`LogLevel`** → **`LOG_LEVEL`**; **`RequireAdmin`** → **`REQUIRE_ADMIN`**; **`SoftDeleteRetentionDays`** → **`SOFT_DELETE_RETENTION_DAYS`**; **`SyncbotFederationEnabled`**, **`SyncbotInstanceId`**, **`SyncbotPublicUrl`** (optional override) → federation env vars; **`PrimaryWorkspace`** → **`PRIMARY_WORKSPACE`**; **`EnableDbReset`** → **`ENABLE_DB_RESET`** (boolean `true` when enabled); optional **`DatabaseTlsEnabled`** / **`DatabaseSslCaPath`** → **`DATABASE_TLS_ENABLED`** / **`DATABASE_SSL_CA_PATH`** (omit when empty so app defaults apply). When using an **existing** DB host: optional **`ExistingDatabasePort`** → **`DATABASE_PORT`** (empty uses engine default); **`ExistingDatabaseCreateAppUser`** / **`ExistingDatabaseCreateSchema`** control the DB setup custom resource (dedicated app user and `CREATE DATABASE`), not direct Lambda env names—see [DEPLOYMENT.md](DEPLOYMENT.md). **`SYNCBOT_PUBLIC_URL`** defaults to the API Gateway stage base URL unless **`SyncbotPublicUrl`** is set; stack output **`SyncBotPublicBaseUrl`** documents that base. GCP Terraform uses **`secret_slack_bot_scopes`** (Secret Manager → `SLACK_BOT_SCOPES`) and variables **`slack_user_scopes`**, **`log_level`**, **`require_admin`**, **`database_backend`**, **`database_port`**, **`soft_delete_retention_days`**, **`syncbot_federation_enabled`**, **`syncbot_instance_id`**, **`syncbot_public_url_override`**, **`primary_workspace`**, **`enable_db_reset`**, **`database_tls_enabled`**, **`database_ssl_ca_path`** for the corresponding runtime env on Cloud Run (see [infra/gcp/README.md](../infra/gcp/README.md)); when **`use_existing_database`** is true, **`existing_db_create_app_user`** / **`existing_db_create_schema`** are recorded as Cloud Run service labels for operator documentation. **`syncbot_public_url_override`** is empty by default—set it to your service’s public HTTPS base (e.g. after first deploy) if you need **`SYNCBOT_PUBLIC_URL`** for federation.

### Optional

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Set by OAuth flow; placeholder until first install. |
| `REQUIRE_ADMIN` | `true` (default) or `false`; restricts config to admins/owners. |
| `PRIMARY_WORKSPACE` | Slack Team ID of the primary workspace. Required for backup/restore to be visible. DB reset (if enabled) is also scoped to this workspace. |
| `ENABLE_DB_RESET` | When `true` / `1` / `yes` and `PRIMARY_WORKSPACE` matches the current workspace, shows the Reset Database button. Not prompted during deploy; set manually via infra config or GitHub Actions variable. |
| `LOCAL_DEVELOPMENT` | `true` only for local dev; disables token verification and enables dev shortcuts. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default `INFO`). |
| `PORT` | HTTP listen port for container entrypoint (`python app.py` / Cloud Run). Cloud Run injects this (typically `8080`); default `3000` when unset. |
| `SOFT_DELETE_RETENTION_DAYS` | Days to retain soft-deleted workspace data (default `30`). |
| `SYNCBOT_FEDERATION_ENABLED` | `true` to enable external connections (federation). |
| `SYNCBOT_INSTANCE_ID` | UUID for this instance (optional; can be auto-generated). |
| `SYNCBOT_PUBLIC_URL` | Public base URL of the app (required when federation is enabled). |

## Platform Capabilities

The provider must deliver:

1. **Public HTTPS endpoint**  
   Slack sends events and interactivity to a single base URL. The app expects:
   - `POST /slack/events` — events and actions
   - `GET /slack/install` — OAuth start
   - `GET /slack/oauth_redirect` — OAuth callback
   - `GET /health` — liveness (JSON `{"status":"ok"}`) for keep-warm probes  
   Any path under `/api/federation` is used for federation when enabled.

2. **Secret injection**  
   Slack and DB credentials must be available as environment variables (or equivalent) at process start. No assumption of a specific secret store; provider chooses (e.g. Lambda env, Secret Manager, Parameter Store).

3. **Database**  
   **PostgreSQL / MySQL:** In non–local environments the app uses TLS by default; allow outbound TCP to the DB host (typically **5432** for PostgreSQL, **3306** for MySQL). **SQLite:** No network; the app uses a local file. Single-writer; ensure backups and file durability for production use.

4. **Keep-warm / scheduled ping (optional but recommended)**  
   To avoid cold-start latency, the app supports a periodic HTTP GET to a configurable path. The provider should support a scheduled job (e.g. CloudWatch Events, Cloud Scheduler) that hits the service on an interval (e.g. 5 minutes). **AWS (SAM):** EventBridge Scheduler invokes the Lambda directly on a schedule; the Lambda handler returns a small JSON success for `source` `aws.scheduler` / `aws.events` without treating the payload as a Slack request.

5. **Stateless execution**  
   The app is stateless; state lives in the configured database (PostgreSQL, MySQL, or SQLite). Horizontal scaling is supported with PostgreSQL/MySQL as long as all instances share the same DB and env; SQLite is single-writer.

## CI Auth Model

- **Preferred:** Short-lived federation (e.g. OIDC for AWS, Workload Identity Federation for GCP). No long-lived API keys in GitHub Secrets for deploy.
- **Bootstrap:** One-time creation of a deploy role (or service account) with least-privilege permissions for deploying the app and its resources.
- **Outputs:** Bootstrap should expose values needed for CI (see below) so users can plug them into GitHub variables.

## Bootstrap Output Contract

After running provider-specific bootstrap (e.g. AWS CloudFormation bootstrap stack, GCP Terraform), the following outputs should be available so users can configure GitHub Actions and/or local deploy:

| Output key | Description | Typical use |
|------------|-------------|-------------|
| `deploy_role` | ARN or identifier of the role/identity that CI (or local) uses to deploy | GitHub variable for OIDC/WIF role-to-assume |
| `artifact_bucket` (or equivalent) | Bucket or registry where deploy artifacts (packages, images) are stored | GitHub variable; deploy step uploads here |
| `region` | Primary region for the deployment | GitHub variable (e.g. `AWS_REGION`, `GCP_REGION`) |
| `service_url` | Public base URL of the deployed app (optional at bootstrap; may come from app stack) | For Slack app configuration and docs |

**AWS:** `artifact_bucket` is `DeploymentBucketName` in bootstrap outputs; this repo stores it as the GitHub variable `AWS_S3_BUCKET` (SAM/CI packaging for `sam deploy` only; not Slack or app media).

Provider-specific implementations may use different names (e.g. `GitHubDeployRoleArn`, `DeploymentBucketName`) but should document the mapping to this contract.

## Swapping Providers

To use a different cloud or IaC stack:

1. Keep `syncbot/` and app behavior unchanged.
2. Add or replace contents of `infra/<provider>/` with templates/scripts that satisfy the contract above.
   - To integrate with the repo-level launcher (`./deploy.sh` and `.\deploy.ps1`), provide `infra/<provider>/scripts/deploy.sh` only. On Windows, `deploy.ps1` invokes that bash script via Git Bash or WSL; do not add a separate `deploy.ps1` under `infra/`.
3. Point CI (e.g. `.github/workflows/deploy-<provider>.yml`) at the new infra paths and provider-specific auth (OIDC, WIF, etc.).
4. Update [DEPLOYMENT.md](DEPLOYMENT.md) (or provider-specific README under `infra/<provider>/`) with bootstrap and deploy steps that emit the bootstrap output contract.

No application code changes are required when swapping infra as long as the runtime environment variables and platform capabilities are met.

## Fork Compatibility Policy

To keep forks easy to rebase and upstream contributions easy to merge:

1. Keep provider-specific changes under `infra/<provider>/` and `.github/workflows/deploy-<provider>.yml`.
2. Do not couple `syncbot/` application code to a cloud provider (AWS/GCP/Azure-specific SDK calls, metadata assumptions, or IAM wiring).
3. Treat this file as the source of truth for runtime env contract; if a fork adds infra behavior, map it back to this contract.
4. Upstream PRs should include only provider-neutral app changes unless a provider-specific file is explicitly being updated.
