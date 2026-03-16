#!/usr/bin/env bash
# Print SyncBot AWS bootstrap stack outputs for GitHub variables or local config.
# Run from repo root:  infra/aws/scripts/print-bootstrap-outputs.sh
# Optional env: BOOTSTRAP_STACK_NAME (default syncbot-bootstrap), AWS_REGION (default us-east-2).

set -euo pipefail

STACK_NAME="${BOOTSTRAP_STACK_NAME:-syncbot-bootstrap}"
REGION="${AWS_REGION:-us-east-2}"

echo "Bootstrap stack: $STACK_NAME (region: $REGION)"
echo ""

outputs=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output text \
  --region "$REGION" 2>/dev/null) || {
  echo "Error: Could not describe stack '$STACK_NAME' in $REGION. Is the bootstrap stack deployed?" >&2
  exit 1
}

while read -r key value; do
  echo "$key = $value"
done <<< "$outputs"

echo ""
echo "--- GitHub Actions variables (set these per environment) ---"
echo "AWS_ROLE_TO_ASSUME  = $(echo "$outputs" | awk -F'\t' '$1=="GitHubDeployRoleArn"{print $2}')"
echo "AWS_S3_BUCKET       = $(echo "$outputs" | awk -F'\t' '$1=="DeploymentBucketName"{print $2}')"
echo "AWS_REGION          = $(echo "$outputs" | awk -F'\t' '$1=="BootstrapRegion"{print $2}')"
echo ""
echo "WARNING: TOKEN_ENCRYPTION_KEY is generated once in app-stack Secrets Manager."
echo "Back up this secret value after first app deploy."
echo "If the key is lost, existing workspaces must reinstall the app to re-authorize tokens."
echo "Expected secret name after app deploy: syncbot-<stage>-token-encryption-key"
echo "Disaster recovery: pass the old key as SAM parameter TokenEncryptionKeyOverride=<old_key>."
