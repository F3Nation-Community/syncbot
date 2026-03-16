# Infrastructure Contract (Provider-Agnostic)

This document defines what any infrastructure provider (AWS, GCP, Azure, etc.) must supply so SyncBot runs correctly. Forks can swap provider-specific IaC in `infra/<provider>/` as long as they satisfy this contract.

**Pre-release:** This repo is pre-release. Database rollout assumes **fresh installs only** (no legacy schema migration or stamping). New databases are initialized via Alembic `upgrade head` at startup.

## Runtime Environment Variables

The application reads configuration from environment variables. Providers must inject these at runtime (e.g. Lambda env, Cloud Run env, or a compatible secret/config layer).

### Database (backend-agnostic)

| Variable | Description |
|----------|-------------|
| `DATABASE_BACKEND` | `mysql` (default) or `sqlite`. |
| `DATABASE_URL` | Full SQLAlchemy URL. When set, overrides legacy MySQL vars. **Required for SQLite** (e.g. `sqlite:///path/to/syncbot.db`). For MySQL, optional (if unset, legacy vars below are used). |
| `DATABASE_HOST` | MySQL hostname (IP or FQDN). Required when backend is `mysql` and `DATABASE_URL` is unset. |
| `ADMIN_DATABASE_USER` | MySQL username. Required when backend is `mysql` and `DATABASE_URL` is unset. |
| `ADMIN_DATABASE_PASSWORD` | MySQL password. Required when backend is `mysql` and `DATABASE_URL` is unset. |
| `ADMIN_DATABASE_SCHEMA` | MySQL database/schema name (e.g. `syncbot`, `syncbot_prod`). Required when backend is `mysql` and `DATABASE_URL` is unset. |
| `DATABASE_TLS_ENABLED` | Optional MySQL TLS toggle (`true`/`false`). Defaults to enabled outside local dev. |
| `DATABASE_SSL_CA_PATH` | Optional CA bundle path used when TLS is enabled (default `/etc/pki/tls/certs/ca-bundle.crt`). |

**SQLite (forks / local):** Set `DATABASE_BACKEND=sqlite` and `DATABASE_URL=sqlite:///path/to/file.db`. Single-writer; suitable for small teams and dev. Caveats: single-writer behavior, file durability, and backup expectations are your responsibility. For production at scale, prefer MySQL.

**MySQL (default):** Set `DATABASE_BACKEND=mysql` (or leave unset) and either `DATABASE_URL` or the four legacy vars above.

### Required in production (non–local)

| Variable | Description |
|----------|-------------|
| `SLACK_SIGNING_SECRET` | Slack request verification (Basic Information → App Credentials). |
| `ENV_SLACK_CLIENT_ID` | Slack OAuth client ID. |
| `ENV_SLACK_CLIENT_SECRET` | Slack OAuth client secret. |
| `ENV_SLACK_SCOPES` | Comma-separated OAuth scopes (see `.env.example`). |
| `TOKEN_ENCRYPTION_KEY` | Passphrase for bot-token encryption at rest (any value except `123` to enable). |

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
   **MySQL:** In non–local environments the app uses TLS; the provider must allow outbound TCP to the MySQL host (typically 3306). **SQLite:** No network; the app uses a local file. Single-writer; ensure backups and file durability for production use.

4. **Keep-warm / scheduled ping (optional but recommended)**  
   To avoid cold-start latency, the app supports a periodic HTTP GET to a configurable path. The provider should support a scheduled job (e.g. CloudWatch Events, Cloud Scheduler) that hits the service on an interval (e.g. 5 minutes).

5. **Stateless execution**  
   The app is stateless; state lives in the configured database (MySQL or SQLite). Horizontal scaling is supported with MySQL as long as all instances share the same DB and env; SQLite is single-writer.

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
