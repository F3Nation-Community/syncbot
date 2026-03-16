# SyncBot on GCP (Terraform)

Minimal Terraform scaffold to run SyncBot on Google Cloud. Satisfies the [infrastructure contract](../../docs/INFRA_CONTRACT.md): Cloud Run (public HTTPS), Secret Manager, optional Cloud SQL, and optional Cloud Scheduler keep-warm.

## Prerequisites

- [Terraform](https://www.terraform.io/downloads) >= 1.0
- [gcloud](https://cloud.google.com/sdk/docs/install) CLI, authenticated
- A GCP project with billing enabled

## Quick start

1. **Enable APIs and create secrets (one-time)**  
   Terraform will enable required APIs. Create Secret Manager secrets and set their values (or let Terraform create placeholder secrets and add versions manually):

   ```bash
   cd infra/gcp
   terraform init
   terraform plan -var="project_id=YOUR_PROJECT_ID" -var="stage=test"
   terraform apply -var="project_id=YOUR_PROJECT_ID" -var="stage=test"
   ```

2. **Set secret values**  
   After the first apply, add secret versions for Slack and DB (if using existing DB). Use the secret IDs shown in Terraform (e.g. `syncbot-test-syncbot-slack-signing-secret`):

   ```bash
   echo -n "YOUR_SLACK_SIGNING_SECRET" | gcloud secrets versions add syncbot-test-syncbot-slack-signing-secret --data-file=-
   # Repeat for ENV_SLACK_CLIENT_ID, ENV_SLACK_CLIENT_SECRET, ENV_SLACK_SCOPES, syncbot-db-password (if existing DB)
   ```

   `TOKEN_ENCRYPTION_KEY` is generated once automatically by Terraform and stored in Secret Manager. Back it up. If lost, existing workspaces must reinstall to re-authorize bot tokens.
   For disaster recovery, restore with `-var='token_encryption_key_override=<old_key>'`.

3. **Set the Cloud Run image**  
   By default the service uses a placeholder image. Build and push your SyncBot image to Artifact Registry, then:

   ```bash
   terraform apply -var="project_id=YOUR_PROJECT_ID" -var="stage=test" \
     -var='cloud_run_image=REGION-docker.pkg.dev/PROJECT/syncbot-test-images/syncbot:latest'
   ```

## Variables (summary)

| Variable | Description |
|----------|-------------|
| `project_id` | GCP project ID (required) |
| `region` | Region for Cloud Run and optional Cloud SQL (default `us-central1`) |
| `stage` | Stage name, e.g. `test` or `prod` |
| `use_existing_database` | If `true`, use `existing_db_*` vars instead of creating Cloud SQL |
| `existing_db_host`, `existing_db_schema`, `existing_db_user` | Existing MySQL connection (when `use_existing_database = true`) |
| `cloud_run_image` | Container image URL for Cloud Run (set after first build) |
| `enable_keep_warm` | Create Cloud Scheduler job to ping the service (default `true`) |

See [variables.tf](variables.tf) for all options.

## Outputs (deploy contract)

After `terraform apply`, outputs align with [docs/INFRA_CONTRACT.md](../../docs/INFRA_CONTRACT.md):

- **service_url** — Public base URL (for Slack app configuration)
- **region** — Primary region
- **project_id** — GCP project ID
- **artifact_registry_repository** — Image registry URL (CI pushes here)
- **deploy_service_account_email** — Service account for CI (use with Workload Identity Federation)

Use the [GCP bootstrap output script](scripts/print-bootstrap-outputs.sh) to print these as GitHub variable suggestions.

## Keep-warm

If `enable_keep_warm` is `true`, a Cloud Scheduler job pings the service at `/health` on the configured interval. Ensure your app exposes a `/health` endpoint or change the job target in [main.tf](main.tf) to another path (e.g. `/`).

## Security

- The Cloud Run service is publicly invokable so Slack can reach it. For production, consider Cloud Armor or IAP.
- Deploy uses a dedicated service account; prefer [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation) for GitHub Actions instead of long-lived keys.
