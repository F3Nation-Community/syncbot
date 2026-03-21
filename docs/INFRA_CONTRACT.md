# Infrastructure Contract (Provider-Agnostic)

This document defines what any infrastructure provider (AWS, GCP, Azure, etc.) must supply so SyncBot runs correctly. Forks can swap provider-specific IaC in `infra/<provider>/` as long as they satisfy this contract.

**Pre-release:** This repo is pre-release. Database rollout assumes **fresh installs only** (no legacy schema migration or stamping). New databases are initialized via Alembic `upgrade head` at startup.

## Runtime Environment Variables

The application reads configuration from environment variables. Providers must inject these at runtime (e.g. Lambda env, Cloud Run env, or a compatible secret/config layer).

## Toolchain Baseline

- Runtime baseline: **Python 3.12**.
- Keep runtime/tooling aligned across:
  - Lambda/Cloud Run runtime configuration
  - CI Python version
  - `pyproject.toml` Python constraint
  - `syncbot/requirements.txt` deployment pins
- When dependency constraints change in `pyproject.toml`, refresh both lock and deployment requirements:

```bash
poetry lock
poetry export --only main --format requirements.txt --without-hashes --output syncbot/requirements.txt
```

### Database (backend-agnostic)

| Variable | Description |
|----------|-------------|
| `DATABASE_BACKEND` | `postgresql` (default), `mysql`, or `sqlite`. |
| `DATABASE_URL` | Full SQLAlchemy URL. When set, overrides host/user/password/schema. **Required for SQLite** (e.g. `sqlite:///path/to/syncbot.db`). For `mysql` / `postgresql`, optional if unset (legacy vars below are used). |
| `DATABASE_HOST` | Database hostname (IP or FQDN). Required when backend is `mysql` or `postgresql` and `DATABASE_URL` is unset. |
| `DATABASE_PORT` | Optional. Defaults to **5432** for `postgresql`, **3306** for `mysql`. |
| `DATABASE_USER` | Username. Required when backend is `mysql` or `postgresql` and `DATABASE_URL` is unset. |
| `DATABASE_PASSWORD` | Password. Required when backend is `mysql` or `postgresql` and `DATABASE_URL` is unset. |
| `DATABASE_SCHEMA` | Database name (MySQL) or PostgreSQL database name (same convention as MySQL). Use alphanumeric and underscore only for PostgreSQL when the app must `CREATE DATABASE` at bootstrap. |
| `DATABASE_TLS_ENABLED` | Optional TLS toggle (`true`/`false`). Defaults to enabled outside local dev. |
| `DATABASE_SSL_CA_PATH` | Optional CA bundle path when TLS is enabled (default `/etc/pki/tls/certs/ca-bundle.crt`). |

**SQLite (forks / local):** Set `DATABASE_BACKEND=sqlite` and `DATABASE_URL=sqlite:///path/to/file.db`. Single-writer; suitable for small teams and dev.

**PostgreSQL / Aurora DSQL (default):** Set `DATABASE_BACKEND=postgresql` (or rely on the default) and either `DATABASE_URL` (`postgresql+psycopg2://...`) or `DATABASE_HOST`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_SCHEMA`. The AWS SAM template parameter `DatabaseEngine=postgresql` matches this backend.

**MySQL (legacy):** Set `DATABASE_BACKEND=mysql` and either `DATABASE_URL` (`mysql+pymysql://...`) or the four host/user/password/schema vars. Deploy-time bootstrap credentials (e.g. `ExistingDatabaseAdmin*` in AWS) are used only for one-time setup; the app reads `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_SCHEMA` at runtime.

### Required in production (non–local)

| Variable | Description |
|----------|-------------|
| `SLACK_SIGNING_SECRET` | Slack request verification (Basic Information → App Credentials). |
| `ENV_SLACK_CLIENT_ID` | Slack OAuth client ID. |
| `ENV_SLACK_CLIENT_SECRET` | Slack OAuth client secret. |
| `ENV_SLACK_SCOPES` | Comma-separated OAuth scopes (see `.env.example`). |
| `TOKEN_ENCRYPTION_KEY` | **Required** in production; must be a strong, random value (e.g. 16+ characters). Providers may auto-generate it (e.g. AWS Secrets Manager). Back up the key after first deploy. In local dev you may set it manually or leave unset. |

### Optional

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Set by OAuth flow; placeholder until first install. |
| `REQUIRE_ADMIN` | `true` (default) or `false`; restricts config to admins/owners. |
| `ENABLE_DB_RESET` | When set to a Slack Team ID, enables the Reset Database button for that workspace. |
| `LOCAL_DEVELOPMENT` | `true` only for local dev; disables token verification and enables dev shortcuts. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default `INFO`). |
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
   Any path under `/api/federation` is used for federation when enabled.

2. **Secret injection**  
   Slack and DB credentials must be available as environment variables (or equivalent) at process start. No assumption of a specific secret store; provider chooses (e.g. Lambda env, Secret Manager, Parameter Store).

3. **Database**  
   **PostgreSQL / MySQL:** In non–local environments the app uses TLS by default; allow outbound TCP to the DB host (typically **5432** for PostgreSQL, **3306** for MySQL). **SQLite:** No network; the app uses a local file. Single-writer; ensure backups and file durability for production use.

4. **Keep-warm / scheduled ping (optional but recommended)**  
   To avoid cold-start latency, the app supports a periodic HTTP GET to a configurable path. The provider should support a scheduled job (e.g. CloudWatch Events, Cloud Scheduler) that hits the service on an interval (e.g. 5 minutes).

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

Provider-specific implementations may use different names (e.g. `GitHubDeployRoleArn`, `DeploymentBucketName`) but should document the mapping to this contract.

## Swapping Providers

To use a different cloud or IaC stack:

1. Keep `syncbot/` and app behavior unchanged.
2. Add or replace contents of `infra/<provider>/` with templates/scripts that satisfy the contract above.
3. Point CI (e.g. `.github/workflows/deploy-<provider>.yml`) at the new infra paths and provider-specific auth (OIDC, WIF, etc.).
4. Update [DEPLOYMENT.md](DEPLOYMENT.md) (or provider-specific README under `infra/<provider>/`) with bootstrap and deploy steps that emit the bootstrap output contract.

No application code changes are required when swapping infra as long as the runtime environment variables and platform capabilities are met.

## Fork Compatibility Policy

To keep forks easy to rebase and upstream contributions easy to merge:

1. Keep provider-specific changes under `infra/<provider>/` and `.github/workflows/deploy-<provider>.yml`.
2. Do not couple `syncbot/` application code to a cloud provider (AWS/GCP/Azure-specific SDK calls, metadata assumptions, or IAM wiring).
3. Treat this file as the source of truth for runtime env contract; if a fork adds infra behavior, map it back to this contract.
4. Upstream PRs should include only provider-neutral app changes unless a provider-specific file is explicitly being updated.
