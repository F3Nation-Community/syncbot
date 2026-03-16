#!/usr/bin/env bash
# Print SyncBot GCP Terraform outputs for GitHub variables (WIF, deploy).
# Run from repo root:  infra/gcp/scripts/print-bootstrap-outputs.sh
# Requires: terraform in PATH; run from repo root so infra/gcp is available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GCP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -d "$GCP_DIR" ]] || [[ ! -f "$GCP_DIR/main.tf" ]]; then
  echo "Error: infra/gcp not found (expected at $GCP_DIR). Run from repo root." >&2
  exit 1
fi

echo "GCP Terraform outputs (infra/gcp)"
echo ""

cd "$GCP_DIR"
if ! terraform output -json >/dev/null 2>&1; then
  echo "Error: Terraform state not initialized or no outputs. Run 'terraform init' and 'terraform apply' in infra/gcp first." >&2
  exit 1
fi

terraform output

echo ""
echo "--- GitHub Actions variables (suggested) ---"
echo "GCP_PROJECT_ID      = $(terraform output -raw project_id 2>/dev/null || echo '<set from output project_id>')"
echo "GCP_REGION          = $(terraform output -raw region 2>/dev/null || echo '<set from output region>')"
echo "GCP_SERVICE_ACCOUNT = $(terraform output -raw deploy_service_account_email 2>/dev/null || echo '<set from output deploy_service_account_email>')"
echo "Artifact Registry   = $(terraform output -raw artifact_registry_repository 2>/dev/null || echo '<set from output artifact_registry_repository>')"
echo "Service URL         = $(terraform output -raw service_url 2>/dev/null || echo '<set from output service_url>')"
echo ""
echo "For deploy-gcp.yml also set: GCP_WORKLOAD_IDENTITY_PROVIDER (after configuring WIF for GitHub)."
echo ""
echo "WARNING: TOKEN_ENCRYPTION_KEY is generated once and stored in Secret Manager."
echo "Back up the secret value (or ensure durable secret backup/replication)."
echo "If this key is lost, existing workspaces must reinstall the app to re-authorize tokens."
echo "Secret name: $(terraform output -raw token_encryption_secret_name 2>/dev/null || echo '<set from output token_encryption_secret_name>')"
echo "Disaster recovery: re-apply with -var='token_encryption_key_override=<old_key>' to preserve decryption."
