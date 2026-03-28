# Deployment Guide

This guide explains **what the guided deploy scripts do**, how to perform the **same steps manually** on **AWS** or **GCP**, and how **GitHub Actions** fits in. For the runtime environment variables the app expects in any cloud, see [INFRA_CONTRACT.md](INFRA_CONTRACT.md).

**Runtime baseline:** Python 3.12 — keep `pyproject.toml`, `syncbot/requirements.txt`, Lambda/Cloud Run runtimes, and CI aligned.

---

## Quick start: root launcher

From the **repository root**:

| OS | Command |
|----|---------|
| macOS / Linux | `./deploy.sh` |
| Windows (PowerShell) | `.\deploy.ps1` |

The launcher discovers `infra/<provider>/scripts/deploy.sh`, shows a numbered menu, and runs the script you pick.

**Non-interactive:** `./deploy.sh aws`, `./deploy.sh gcp`, `./deploy.sh 1` (same for `deploy.ps1`).

**Windows:** `deploy.ps1` requires **Git Bash** or **WSL** with bash, then runs the same `infra/.../deploy.sh` as macOS/Linux. Alternatively install [Git for Windows](https://git-scm.com/download/win) or [WSL](https://learn.microsoft.com/windows/wsl/install) and run `./deploy.sh` from Git Bash or a WSL shell.

**Prerequisites** (short list in the root [README](../README.md); full detail below):

- **AWS path:** AWS CLI v2, SAM CLI, Docker (`sam build --use-container`), Python 3 (`python3`), **`curl`** (Slack manifest API). **Optional:** `gh` (GitHub Actions setup). The script prints a CLI status line per tool (✓ / !) and Slack doc links; if `gh` is missing, it asks whether to continue.
- **GCP path:** Terraform, `gcloud`, Python 3, **`curl`**. **Optional:** `gh` — same behavior as AWS.

**Slack install error `invalid_scope` / “Invalid permissions requested”:** The OAuth authorize URL is built from **`SLACK_BOT_SCOPES`** and **`SLACK_USER_SCOPES`** in your deployed app (Lambda / Cloud Run). They must **exactly match** the scopes on your Slack app (`slack-manifest.json` → **OAuth & Permissions** after manifest update) and `BOT_SCOPES` / `USER_SCOPES` in `syncbot/slack_manifest_scopes.py`. SAM and GCP Terraform defaults include both bot and user scope strings; if your environment has **stale** overrides, redeploy with parameters matching the manifest or update the Slack app to match. On GCP, `slack_user_scopes` must stay aligned with `oauth_config.scopes.user`. **Renames (older stacks):** `SLACK_SCOPES` → `SLACK_BOT_SCOPES`; SAM `SlackOauthScopes` → `SlackOauthBotScopes`; SAM `SlackUserOauthScopes` → `SlackOauthUserScopes` (`SLACK_USER_SCOPES` unchanged).

---

## What the deploy scripts do

### Root: `deploy.sh` / `deploy.ps1`

- Scans `infra/*/scripts/deploy.sh` and lists providers (e.g. **aws**, **gcp**).
- Runs the selected provider script in Bash.
- **`./deploy.sh` (macOS / Linux):** Invokes `bash` with the chosen `infra/<provider>/scripts/deploy.sh`.
- **`.\deploy.ps1` (Windows):** Verifies **Git Bash** or **WSL** bash is available (shows which one will be used), then runs the same `deploy.sh` path. There are **no** `deploy.ps1` files under `infra/` — only the repo-root launcher uses PowerShell. Provider prerequisite checks (AWS/GCP tools, optional `gh`, Slack links) run **inside** the bash `deploy.sh` scripts.

### AWS: `infra/aws/scripts/deploy.sh`

Runs from repo root (or via `./deploy.sh` → **aws**). It:

1. **Prerequisites** — Verifies `aws`, `sam`, `docker`, `python3`, `curl` are on `PATH` (with install hints). Prints a status matrix; if optional `gh` is missing, shows install hints and asks whether to continue. Prints Slack app / API token / manifest API links.
2. **AWS auth** — Checks credentials; suggests `aws login`, SSO, or `aws configure` as appropriate.
3. **Bootstrap probe** — Reads bootstrap stack outputs if the stack exists (for suggested stack names and later CI/CD). Full **bootstrap** create/sync runs only if you select it in **Deploy Tasks** (see below).
4. **App stack identity** — Prompts for stage (`test`/`prod`) and stack name; detects an existing CloudFormation stack for update.
5. **Deploy Tasks** — Multi-select menu (comma-separated, default all): **Bootstrap** (create/sync bootstrap stack; respects `SYNCBOT_SKIP_BOOTSTRAP_SYNC=1` for sync), **Build/Deploy** (full config + SAM), **CI/CD** (`gh` / GitHub Actions), **Slack API**, **Backup Secrets** (DR plaintext echo). Omitting **Build/Deploy** requires an existing stack for tasks that need live outputs.
6. **Configuration** (if Build/Deploy selected) — **Database source** (stack-managed RDS vs existing RDS host) and **engine** (MySQL vs PostgreSQL). **Slack app credentials** (signing secret, client secret, client ID). **Existing database host** mode: RDS endpoint, admin user/password, optional **ExistingDatabasePort** (blank = engine default; use for non-standard ports e.g. TiDB **4000**), optional **ExistingDatabaseAppUsernamePrefix** (e.g. TiDB Cloud cluster prefix `abc123`; a dot separator is added automatically; app user becomes `{prefix}.syncbot_user_{stage}`), whether to **create a dedicated app DB user** and whether to run **`CREATE DATABASE IF NOT EXISTS`**, **public vs private** network mode, and for **private** mode: subnet IDs and Lambda security group (with optional auto-detect and **connectivity preflight** using the effective DB port). **New RDS in stack** mode: summarizes auto-generated DB users and prompts for **DatabaseSchema**. Optional **token encryption** recovery override, **log level** (numbered list `1`–`5` with `Choose level [N]:`, default from prior stack or **INFO**), **deploy summary**, then **SAM build** (`--use-container`) and **sam deploy**.
7. **Post-deploy** — According to selected tasks: stack outputs, `slack-manifest_<stage>.json`, Slack API, **`gh`** setup, deploy receipt under `deploy-receipts/` (gitignored), and DR backup lines.

### GCP: `infra/gcp/scripts/deploy.sh`

Runs from repo root (or `./deploy.sh` → **gcp**). It:

1. Verifies **Terraform**, **gcloud**, **python3**, **curl**; optional **gh** handling (same as AWS).
2. Guides **auth** (`gcloud auth login` plus `gcloud auth application-default login`; quota project as needed).
3. **Project / stage / existing service** — Prompts for project, region, stage; can detect existing Cloud Run for defaults.
4. **Deploy Tasks** — Multi-select menu (comma-separated, default all): **Build/Deploy** (full Terraform flow), **CI/CD**, **Slack API**, **Backup Secrets**. Skipping **Build/Deploy** requires existing Terraform state/outputs for tasks that need them.
5. **Terraform** (if Build/Deploy selected) — Prompts for DB mode, `cloud_run_image` (required), log level, etc.; `terraform init` / `plan` / `apply` in `infra/gcp` (no separate y/n gates on plan/apply).
6. **Post-deploy** — According to selected tasks: manifest, Slack API, deploy receipt, **`gh`**, `print-bootstrap-outputs.sh`, DR backup lines.

See [infra/gcp/README.md](../infra/gcp/README.md) for Terraform variables and outputs.

---

## Fork-First model (recommended for forks)

**Branch roles** (see [CONTRIBUTING.md](../CONTRIBUTING.md)): use **`main`** to track upstream and merge contributions; on your fork, use **`test`** and **`prod`** for automated deploys (CI runs on push to those branches).

1. Keep `syncbot/` provider-neutral; use only env vars from [INFRA_CONTRACT.md](INFRA_CONTRACT.md).
2. Put provider code in `infra/<provider>/` and `.github/workflows/deploy-<provider>.yml`.
3. Prefer the AWS layout as reference; treat other providers as swappable scaffolds.

---

## Provider selection (CI)

| Provider | Infra | CI workflow | Default |
|----------|-------|-------------|---------|
| **AWS** | `infra/aws/` | `.github/workflows/deploy-aws.yml` | Yes |
| **GCP** | `infra/gcp/` | `.github/workflows/deploy-gcp.yml` | Opt-in |

- **AWS only:** Do not set `DEPLOY_TARGET=gcp` (or set it to something other than `gcp`).
- **GCP only:** Set repository variable **`DEPLOY_TARGET`** = **`gcp`**, complete GCP bootstrap + WIF, and disable or skip the AWS workflow so only `deploy-gcp.yml` runs.

---

## Database backends

The app supports **MySQL** (default), **PostgreSQL**, and **SQLite**. Schema changes are applied at startup via Alembic (`alembic upgrade head`).

- **AWS:** Choose engine in the deploy script or pass `DatabaseEngine=mysql` / `postgresql` to `sam deploy`.
- **Contract:** [INFRA_CONTRACT.md](INFRA_CONTRACT.md) — `DATABASE_BACKEND`, `DATABASE_URL` or host/user/password/schema.

---

## AWS — manual steps (no helper script)

Use this when you already know SAM/CloudFormation or are debugging.

### 1. One-time bootstrap

**Prerequisites:** AWS CLI, SAM CLI (for later app deploy).

```bash
aws cloudformation deploy \
  --template-file infra/aws/template.bootstrap.yaml \
  --stack-name syncbot-bootstrap \
  --parameter-overrides \
    GitHubRepository=YOUR_GITHUB_OWNER/YOUR_REPO \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-2
```

Optional: `CreateOIDCProvider=false` if the GitHub OIDC provider already exists.

**Outputs:**

```bash
./infra/aws/scripts/print-bootstrap-outputs.sh
```

Map **GitHubDeployRoleArn** → `AWS_ROLE_TO_ASSUME`, **DeploymentBucketName** → `AWS_S3_BUCKET`, **BootstrapRegion** → `AWS_REGION`.

### 2. Build and deploy the app stack

```bash
sam build -t infra/aws/template.yaml --use-container
sam deploy \
  -t .aws-sam/build/template.yaml \
  --stack-name syncbot-test \
  --s3-bucket YOUR_DEPLOYMENT_BUCKET_NAME \
  --capabilities CAPABILITY_IAM \
  --region us-east-2 \
  --parameter-overrides \
    Stage=test \
    SlackSigningSecret=... \
    SlackClientID=... \
    SlackClientSecret=... \
    SlackOauthBotScopes=... \
    SlackOauthUserScopes=... \
    DatabaseEngine=mysql \
    ...
```

Use **`sam deploy --guided`** the first time if you prefer prompts. For **existing RDS**, set `ExistingDatabaseHost`, `ExistingDatabaseAdminUser`, `ExistingDatabaseAdminPassword`, and for **private** DBs also `ExistingDatabaseNetworkMode=private`, `ExistingDatabaseSubnetIdsCsv`, `ExistingDatabaseLambdaSecurityGroupId`. Optional: `ExistingDatabasePort` (empty = engine default), `ExistingDatabaseCreateAppUser` / `ExistingDatabaseCreateSchema` (`true`/`false`). Omit `ExistingDatabaseHost` to create a **new** RDS in the stack.

**samconfig:** Predefined profiles in `samconfig.toml` (`test-new-rds`, `test-existing-rds`, etc.) — adjust placeholders before use.

**Token key:** The stack can auto-generate `TOKEN_ENCRYPTION_KEY` in Secrets Manager. Back it up after first deploy. Optional: `TokenEncryptionKeyOverride`, `ExistingTokenEncryptionKeySecretArn` for recovery.

### 3. GitHub Actions (AWS)

Workflow: `.github/workflows/deploy-aws.yml` (runs on push to `test`/`prod` when not using GCP).

Configure **repository** variables: `AWS_ROLE_TO_ASSUME`, `AWS_S3_BUCKET`, `AWS_REGION`.

`AWS_S3_BUCKET` is the bootstrap **SAM deploy artifact** bucket (`DeploymentBucketName`): CI uses it for `sam deploy --s3-bucket` (Lambda package uploads) only. It is **not** for Slack file hosting or other app media. The guided deploy script resolves the target repo from **git remotes** (origin, upstream, then others): if your fork and upstream differ, it asks which `owner/repo` should receive variables, then passes `-R owner/repo` to `gh` so writes go there (not whatever `gh` infers from context alone).

Configure **per-environment** (`test` / `prod`) variables and secrets so they match your stack — especially if you use **existing RDS** or **private** networking:

| Type | Name | Notes |
|------|------|--------|
| Var | `AWS_STACK_NAME` | CloudFormation stack name |
| Var | `STAGE_NAME` | `test` or `prod` |
| Var | `DATABASE_SCHEMA` | e.g. `syncbot_test` |
| Var | `LOG_LEVEL` | Optional. `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Passed to SAM as `LogLevel`; defaults to `INFO` in the workflow when unset. |
| Var | `SLACK_CLIENT_ID` | From Slack app |
| Var | `DATABASE_ENGINE` | `mysql` or `postgresql` (workflow defaults to `mysql` if unset) |
| Var | `EXISTING_DATABASE_HOST` | Empty for **new** RDS in stack |
| Var | `EXISTING_DATABASE_ADMIN_USER` | When using existing host |
| Var | `EXISTING_DATABASE_NETWORK_MODE` | `public` or `private` |
| Var | `EXISTING_DATABASE_SUBNET_IDS_CSV` | **Private** mode: comma-separated subnet IDs (no spaces) |
| Var | `EXISTING_DATABASE_LAMBDA_SECURITY_GROUP_ID` | **Private** mode: Lambda ENI security group |
| Var | `EXISTING_DATABASE_PORT` | Optional; non-standard TCP port (e.g. `4000`). Empty = engine default in SAM. |
| Var | `EXISTING_DATABASE_CREATE_APP_USER` | `true` / `false` (default `true`). Set `false` when the DB cannot create a dedicated app user. |
| Var | `EXISTING_DATABASE_CREATE_SCHEMA` | `true` / `false` (default `true`). Set `false` when the database/schema already exists. |
| Var | `EXISTING_DATABASE_APP_USERNAME_PREFIX` | Optional. Provider-specific username prefix (e.g. TiDB Cloud `42bvZAUSurKwhxc.`). Empty for RDS/standard MySQL. |
| Secret | `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_SECRET` | |
| Secret | `EXISTING_DATABASE_ADMIN_PASSWORD` | When `EXISTING_DATABASE_HOST` is set |
| Secret | `TOKEN_ENCRYPTION_KEY_OVERRIDE` | Optional DR only |

The interactive deploy script can set these via `gh` when you opt in. Re-run that step after changing DB mode or engine so CI stays aligned.

**Dependency hygiene:** The AWS deploy workflow runs `pip-audit` on `syncbot/requirements.txt` and `infra/aws/db_setup/requirements.txt`. After changing `pyproject.toml`, run `poetry lock` and commit; the **pre-commit `sync-requirements` hook** (see [.pre-commit-config.yaml](../.pre-commit-config.yaml)) regenerates both requirements files when `poetry.lock` changes. If you do not use pre-commit, run the export commands documented in [DEVELOPMENT.md](DEVELOPMENT.md).

### 4. Ongoing local deploys (least privilege)

Assume the bootstrap **GitHubDeployRole** (or equivalent) and run `sam build` / `sam deploy` as in step 2.

---

## GCP — manual steps

### 1. Terraform bootstrap

From `infra/gcp` (or repo root with paths adjusted):

```bash
terraform init
terraform plan -var="project_id=YOUR_PROJECT_ID" -var="stage=test"
terraform apply -var="project_id=YOUR_PROJECT_ID" -var="stage=test"
```

Set Secret Manager values for Slack/DB as in [infra/gcp/README.md](../infra/gcp/README.md). Set **`cloud_run_image`** after building and pushing the container. Capture outputs: service URL, region, project, Artifact Registry, deploy service account.

```bash
./infra/gcp/scripts/print-bootstrap-outputs.sh
```

**DR:** Optional `token_encryption_key_override` if you must preserve existing encrypted tokens.

### 2. GitHub Actions (GCP)

1. Configure [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation) for GitHub → deploy service account.
2. Set **`DEPLOY_TARGET=gcp`** at repo level so `deploy-gcp.yml` runs and `deploy-aws.yml` is skipped.
3. Set variables: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, etc.

   The interactive `infra/gcp/scripts/deploy.sh` uses the same GitHub `owner/repo` selection as the AWS script (based on git remotes when fork and upstream differ).

**Note:** `.github/workflows/deploy-gcp.yml` is intentionally configured to fail until real CI steps are implemented (WIF auth, image build/push, deploy). Keep using `infra/gcp/scripts/deploy.sh` for interactive deploys until CI is fully wired.

### 3. Ongoing deploys

Build and push an image to Artifact Registry, then `gcloud run deploy` or `terraform apply` with updated `cloud_run_image`.

---

## Using an existing RDS host (AWS)

When **ExistingDatabaseHost** is set, the template **does not** create VPC/RDS; a custom resource can create the schema and optionally a dedicated app user (`syncbot_user_<stage>`) with a generated app password in Secrets Manager—or skip user/schema steps and copy the admin password into the app secret when **`ExistingDatabaseCreateAppUser=false`**.

- **Public:** Lambda is not in your VPC; the DB must be reachable on the Internet on the configured port (**`ExistingDatabasePort`**, or **3306** / **5432** by engine).
- **Private:** Lambda uses `ExistingDatabaseSubnetIdsCsv` and `ExistingDatabaseLambdaSecurityGroupId`; DB security group must allow the Lambda SG; subnets need **NAT** egress for Slack API calls.

See also [Sharing infrastructure across apps](#sharing-infrastructure-across-apps-aws) below.

---

## Swapping providers

1. Keep [INFRA_CONTRACT.md](INFRA_CONTRACT.md) satisfied.
2. Disable the old provider’s workflow; set `DEPLOY_TARGET` if using GCP.
3. Bootstrap the new provider; reconfigure GitHub and Slack URLs.

---

## Helper scripts

| Script | Purpose |
|--------|---------|
| `infra/aws/scripts/print-bootstrap-outputs.sh` | Bootstrap stack outputs → suggested GitHub vars |
| `infra/aws/scripts/deploy.sh` | Interactive AWS deploy (see [What the deploy scripts do](#what-the-deploy-scripts-do)) |
| `infra/gcp/scripts/print-bootstrap-outputs.sh` | Terraform outputs → suggested GitHub vars |
| `infra/gcp/scripts/deploy.sh` | Interactive GCP deploy |

---

## Security summary

- **Bootstrap** runs once with elevated credentials; creates deploy identity + artifact storage.
- **GitHub:** Short-lived **AWS OIDC** or **GCP WIF** — no long-lived cloud API keys in repos for deploy.
- **Prod:** Use GitHub environment protection rules as needed.

---

## Database schema (Alembic)

Schema lives under `syncbot/db/alembic/`. On startup the app runs **`alembic upgrade head`**.

---

## Post-deploy: Slack deferred modal flows (manual smoke test)

After deploying a build that changes Slack listener wiring, verify **in the deployed workspace** (not only local dev) that modals using custom interaction responses still work. These flows rely on `view_submission` acks (`response_action`: `update`, `errors`, or `push`) being returned in the **first** Lambda response:

1. **Sync Channel (publish)** — Open **Sync Channel**, choose sync mode, press **Next**; confirm step 2 (channel picker) appears. Submit with an invalid state to confirm field errors if applicable.
2. **Backup / Restore** — Open Backup/Restore; try restore validation (e.g. missing file) and, if possible, the integrity-warning confirmation path (`push`).
3. **Data migration** (if federation enabled) — Same style of checks for import validation and confirmation.
4. **Optional** — Trigger a Home tab action that opens a modal via **`views_open`** (uses `trigger_id`) after a cold start to spot-check latency.

---

## Sharing infrastructure across apps (AWS)

Reuse one RDS with **different `DatabaseSchema`** per app/environment; set **ExistingDatabaseHost** and distinct schemas. API Gateway and Lambda remain per stack.
