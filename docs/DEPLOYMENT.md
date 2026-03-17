# Deployment Guide

This guide covers deploying SyncBot on **AWS** (default) or **GCP**, with two paths per provider:

- **Fork and deploy** — One-time bootstrap, then all deploys via GitHub Actions (OIDC on AWS, Workload Identity Federation on GCP; no long-lived keys).
- **Download and deploy** — One-time bootstrap, then updates via local CLI (`sam` on AWS, `gcloud`/Terraform on GCP) using limited credentials.

The app code and [infrastructure contract](INFRA_CONTRACT.md) are provider-agnostic; only the infrastructure in `infra/<provider>/` and the CI workflow differ.

---

## Fork-First Model (Recommended)

If your goal is "fork and deploy on a different cloud, while still PR'ing app improvements back to SyncBot", use this model:

1. Keep `syncbot/` provider-neutral and depend only on env vars from [INFRA_CONTRACT.md](INFRA_CONTRACT.md).
2. Put provider implementation in `infra/<provider>/` and `.github/workflows/deploy-<provider>.yml`.
3. Keep AWS path as the reference implementation; treat other providers as swappable scaffolds.
4. Send upstream PRs for provider-neutral changes (DB abstraction, docs contract, tests) and keep fork-only deploy glue isolated.

This is the intended maintenance path for long-lived forks.

---

## Provider selection

| Provider | Infra folder | CI workflow | Default |
|----------|--------------|-------------|---------|
| **AWS**  | `infra/aws/` | `.github/workflows/deploy-aws.yml` | Yes |
| **GCP**  | `infra/gcp/` | `.github/workflows/deploy-gcp.yml` | No (opt-in) |

- **Use AWS:** Do nothing; the AWS workflow runs on push to `test`/`prod` unless you set `DEPLOY_TARGET=gcp`.
- **Use GCP:** Run `infra/gcp/` Terraform, configure Workload Identity Federation, set repository variable **`DEPLOY_TARGET`** = **`gcp`**, and disable or remove the AWS workflow so only `deploy-gcp.yml` runs.

See [Swapping providers](#swapping-providers) for changing providers in a fork.

---

## Database backend

The app supports **MySQL** (default) or **SQLite**. See [INFRA_CONTRACT.md](INFRA_CONTRACT.md) for required variables per backend. **Pre-release:** DB flow assumes **fresh installs only**; schema is created at startup via Alembic.

- **MySQL:** Use for production and when using AWS/GCP templates (RDS, Cloud SQL). Set `DATABASE_BACKEND=mysql` (or leave unset) and either `DATABASE_URL` or `DATABASE_HOST` + `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_SCHEMA`.
- **SQLite:** Use for forks or local runs where you prefer no DB server. Set `DATABASE_BACKEND=sqlite` and `DATABASE_URL=sqlite:///path/to/syncbot.db`. Single-writer; ensure backups and file durability. AWS/GCP reference templates assume MySQL; for SQLite you deploy the app (e.g. container or Lambda with a writable volume) and set the env vars only.

---

## AWS

### One-Time Bootstrap (AWS, both paths)

Deploy the bootstrap stack **once** from your machine with credentials that can create IAM roles, OIDC providers, and S3 buckets.

**Prerequisites:** AWS CLI, [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html). For fork-and-deploy: a GitHub repo `owner/repo`.

From the project root:

```bash
aws cloudformation deploy \
  --template-file infra/aws/template.bootstrap.yaml \
  --stack-name syncbot-bootstrap \
  --parameter-overrides \
    GitHubRepository=YOUR_GITHUB_OWNER/YOUR_REPO \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-2
```

Replace `YOUR_GITHUB_OWNER/YOUR_REPO` with your repo. Optionally set `CreateOIDCProvider=false` if the account already has the GitHub OIDC provider. The bootstrap template only accepts `GitHubRepository`, `CreateOIDCProvider`, and `DeploymentBucketPrefix` (database options go in the main app deploy, not bootstrap).

**Capture outputs:**

```bash
./infra/aws/scripts/print-bootstrap-outputs.sh
```

You need: **GitHubDeployRoleArn** → `AWS_ROLE_TO_ASSUME`, **DeploymentBucketName** → `AWS_S3_BUCKET`, **BootstrapRegion** → `AWS_REGION`, and suggested stack names for test/prod.

---

### Fork and Deploy (AWS, GitHub CI)

1. Complete [One-Time Bootstrap (AWS)](#one-time-bootstrap-aws-both-paths).
2. **First app deploy** (with credentials that can create RDS/VPC/Lambda/API Gateway):

   ```bash
   sam build -t infra/aws/template.yaml --use-container
   sam deploy --guided \
     --template-file infra/aws/template.yaml \
     --stack-name syncbot-test \
     --s3-bucket YOUR_DEPLOYMENT_BUCKET_NAME \
     --capabilities CAPABILITY_IAM \
     --region us-east-2
   ```

   Use the bootstrap **DeploymentBucketName**. Set parameters (Stage, DB, Slack, etc.) when prompted.

3. **GitHub:** Create environments `test` and `prod`. In **Settings → Secrets and variables → Actions**, set **Variables** (per env): `AWS_ROLE_TO_ASSUME`, `AWS_REGION`, `AWS_S3_BUCKET`, `AWS_STACK_NAME`, `STAGE_NAME`, `SLACK_CLIENT_ID` (Slack app Client ID from Basic Information → App Credentials), `EXISTING_DATABASE_HOST`, `EXISTING_DATABASE_ADMIN_USER` (when using existing RDS host), `DATABASE_USER` (when creating new RDS), `DATABASE_SCHEMA`. Set **Secrets**: `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_SECRET`, `EXISTING_DATABASE_ADMIN_PASSWORD` (when using existing host), `DATABASE_PASSWORD` (when creating new RDS). No access keys — the workflow uses OIDC.
4. Push to `test` or `prod` to build and deploy. The workflow file is `.github/workflows/deploy-aws.yml` (runs when `DEPLOY_TARGET` is not `gcp`).

**Important (token encryption key):** Non-local deploys require a secure `TOKEN_ENCRYPTION_KEY`. The AWS app stack **auto-generates** it in Secrets Manager by default. You must **back up the generated key** after first deploy; if it is lost, existing workspaces must reinstall to re-authorize bot tokens. For local development you may set the key manually in `.env` or leave it unset.

#### Using an existing RDS host (AWS)

To **reuse only the DB host** and have the deploy create the schema and a dedicated app user (and generated password) for you:

1. **What the stack does:**  
   When you set **ExistingDatabaseHost**, the template skips creating VPC, subnets, and RDS. A custom resource runs during deploy: it connects to your existing MySQL with a **bootstrap** (master) user you provide, creates the schema, creates an app user `syncbot_<stage>` with a **generated** password (stored in Secrets Manager), and grants that user full access to the schema. The app Lambda then uses that app user and generated password. You never manage the app DB password.

2. **What you provide:**
   - **Host:** The RDS endpoint (e.g. `mydb.xxxx.us-east-2.rds.amazonaws.com`). No `:3306` or path.
   - **Admin user & password:** A MySQL user that can create databases and users (e.g. RDS master). Used only by the deploy step; the app uses a separate `syncbot_<stage>` user.
   - **Schema name:** A dedicated schema per app or environment (e.g. `syncbot_test`, `syncbot_prod`). The deploy creates this schema and the app user with full access to it; the app runs Alembic migrations on startup.

3. **Connectivity:**  
   When using an existing host, Lambda is **not** put in a VPC. It can only reach **publicly accessible** endpoints. Your RDS must be:
   - Set to **publicly accessible** (in RDS settings), and
   - Protected by a security group that allows **inbound TCP 3306** from the internet (or restrict to known IPs).  
   For production, consider a VPC-enabled Lambda and private RDS; that would require template changes.

4. **First deploy (local `sam deploy`):**  
   Pass the **existing-host** parameters (admin user/password). When using **guided** mode, SAM will still prompt for **DatabaseUser** and **DatabasePassword**; the stack ignores these when using an existing host (app user/password are auto-generated). If the **DatabasePassword** prompt repeats in a loop (SAM often rejects empty for password fields), type any placeholder (e.g. `ignored`) and continue — it is never used. To avoid interactive prompts, use **parameter-overrides** and set `DatabaseUser=` and `DatabasePassword=ignored` (or any value) for existing-host deploys:
   ```bash
   sam deploy --guided ... \
     --parameter-overrides \
       ExistingDatabaseHost=your-db.xxxx.us-east-2.rds.amazonaws.com \
       ExistingDatabaseAdminUser=admin \
       ExistingDatabaseAdminPassword=your_master_password \
       DatabaseUser= \
       DatabasePassword=ignored \
       DatabaseSchema=syncbot_test \
       SlackClientID=your_slack_app_client_id \
       SlackSigningSecret=... \
       SlackClientSecret=...
   ```
   Omit **ExistingDatabaseHost** (or leave it empty) to have the stack create a new RDS instance; then you must pass **DatabaseUser** and **DatabasePassword** for the new RDS master.

5. **GitHub Actions:**  
   For **existing host** (deploy creates schema and app user), set **Variables**:
   - **EXISTING_DATABASE_HOST** — Full RDS hostname. Leave **empty** to create a new RDS instead.
   - **EXISTING_DATABASE_ADMIN_USER** — MySQL user that can create DBs/users (e.g. master).
   - **DATABASE_SCHEMA** — Schema name (e.g. `syncbot_test` or `syncbot_prod`).  
   Set **Secrets**:
   - **EXISTING_DATABASE_ADMIN_PASSWORD** — Password for the admin user.  
   For **new RDS** (stack creates the instance), set **DATABASE_USER**, **DATABASE_SCHEMA**, and secret **DATABASE_PASSWORD** instead, and leave **EXISTING_DATABASE_HOST** empty. The workflow passes all of these; the template uses the right set based on whether **EXISTING_DATABASE_HOST** is set.

**Disaster recovery:** if you must rebuild and keep existing encrypted tokens working, deploy with the old key:

```bash
sam deploy ... --parameter-overrides "... TokenEncryptionKeyOverride=<old_key>"
```

If using GitHub Actions, set optional secret `TOKEN_ENCRYPTION_KEY_OVERRIDE`; the AWS workflow will pass it automatically.

---

### Download and Deploy (AWS, local)

1. Run [One-Time Bootstrap (AWS)](#one-time-bootstrap-aws-both-paths) and the [first app deploy](#fork-and-deploy-aws-github-ci) once with admin (or equivalent) credentials.
2. **Future deploys** with limited credentials: assume the bootstrap deploy role (recommended):

   ```bash
   export AWS_PROFILE=syncbot-deploy   # profile with role_arn = GitHubDeployRoleArn
   sam build -t infra/aws/template.yaml --use-container
   sam deploy \
     -t .aws-sam/build/template.yaml \
     --stack-name syncbot-test \
     --s3-bucket YOUR_DEPLOYMENT_BUCKET_NAME \
     --capabilities CAPABILITY_IAM \
     --region us-east-2
   ```

   Or use a dedicated IAM user with the same policy. See [Deployment Guide (legacy detail)](#sharing-infrastructure-across-apps-aws) for shared RDS and parameter overrides.

---

## GCP

### One-Time Bootstrap (GCP, both paths)

From the project root (or `infra/gcp`):

```bash
cd infra/gcp
terraform init
terraform plan -var="project_id=YOUR_PROJECT_ID" -var="stage=test"
terraform apply -var="project_id=YOUR_PROJECT_ID" -var="stage=test"
```

Set Secret Manager secret values for Slack and DB (see [infra/gcp/README.md](../infra/gcp/README.md)). `TOKEN_ENCRYPTION_KEY` is auto-generated once and stored in Secret Manager during apply. Set **cloud_run_image** after building and pushing your container image. Capture outputs for CI: **service_url**, **region**, **project_id**, **artifact_registry_repository**, **deploy_service_account_email**.

**Disaster recovery:** if rebuilding and you need to preserve existing token decryption, re-apply with:

```bash
terraform apply -var="project_id=YOUR_PROJECT_ID" -var="stage=test" -var='token_encryption_key_override=<old_key>'
```

Helper script for GitHub vars:

```bash
./infra/gcp/scripts/print-bootstrap-outputs.sh
```

---

### Fork and Deploy (GCP, GitHub CI)

1. Complete [One-Time Bootstrap (GCP)](#one-time-bootstrap-gcp-both-paths).
2. Configure [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation) for GitHub so the repo can impersonate the deploy service account without a key file.
3. In GitHub: set **Variables** (e.g. `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`). Set **DEPLOY_TARGET** = **gcp** so `.github/workflows/deploy-gcp.yml` runs and `deploy-aws.yml` is skipped.
4. Replace the placeholder steps in `deploy-gcp.yml` with real build (e.g. Cloud Build or Docker push to Artifact Registry) and `gcloud run deploy` (or Terraform apply). See `deploy-gcp.yml` comments and [infra/gcp/README.md](../infra/gcp/README.md).
5. Keep those changes inside your fork's infra/workflow files so future upstream rebases remain straightforward.

---

### Download and Deploy (GCP, local)

1. Run [One-Time Bootstrap (GCP)](#one-time-bootstrap-gcp-both-paths).
2. Build and push the container image to the Terraform output **artifact_registry_repository**, then update the Cloud Run service:

   ```bash
   gcloud run deploy syncbot-test --image=REGION-docker.pkg.dev/PROJECT/REPO/syncbot:latest --region=REGION
   ```

   Or run `terraform apply` with an updated `cloud_run_image` variable.

---

## Swapping providers

To switch from AWS to GCP (or the other way) in a fork:

1. **Keep app code and [INFRA_CONTRACT.md](INFRA_CONTRACT.md) unchanged.** Only infra and CI are provider-specific.
2. **Disable the old provider:** Remove or disable the workflow for the provider you are leaving (e.g. delete or disable `deploy-aws.yml` when moving to GCP). If using the same repo, set `DEPLOY_TARGET` accordingly.
3. **Use the new provider folder:** Run bootstrap for the new provider (`infra/aws/` or `infra/gcp/`) and configure GitHub vars/secrets (and WIF for GCP) as in the sections above.
4. **Point Slack** at the new **service_url** (and run DB migrations or attach an existing DB as required by the contract).

No changes are needed under `syncbot/` or to the deployment contract; only `infra/<provider>/` and the chosen workflow change.

---

## Helper scripts

| Provider | Script | Use |
|----------|--------|-----|
| AWS | `./infra/aws/scripts/print-bootstrap-outputs.sh` | Print bootstrap stack outputs and suggested GitHub variables (run from repo root). |
| GCP | `./infra/gcp/scripts/print-bootstrap-outputs.sh` | Print Terraform outputs and suggested GitHub variables (run from repo root). |

---

## Security summary

- **Bootstrap** runs once with elevated credentials; it creates a deploy identity (IAM role or GCP service account) and artifact storage (S3 bucket or Artifact Registry).
- **GitHub** uses short-lived federation only: **AWS** OIDC with `AWS_ROLE_TO_ASSUME`; **GCP** Workload Identity Federation with a deploy service account. No long-lived API keys in secrets for deploy.
- **Local** future deploys use assume-role (AWS) or the same deploy service account (GCP) with limited scope.
- **Prod** can be protected with GitHub environment **Required reviewers**.

---

## Database schema (Alembic, fresh install only)

Schema is managed by **Alembic** (see `db/alembic/`). On startup the app runs **`alembic upgrade head`** only (pre-release: fresh installs only; no stamping of existing DBs).

- **Fresh installs:** A new database (MySQL or SQLite) gets all tables from the baseline migration at first run.
- **Rollback:** If bootstrap fails, fix the migration issue, reset the DB file/schema, and rerun; no manual downgrade is required for the baseline.

---

## Sharing infrastructure across apps (AWS)

To use an existing RDS instance instead of creating one per stack, see **[Using an existing RDS host (AWS)](#using-an-existing-rds-host-aws)**. Set **ExistingDatabaseHost** and use a **different DatabaseSchema** per app or environment (e.g. `syncbot_test`, `syncbot_prod`). API Gateway and Lambda are per stack; free-tier quotas are account-wide.
