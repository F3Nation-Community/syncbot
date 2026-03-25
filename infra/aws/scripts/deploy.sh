#!/usr/bin/env bash
# Interactive AWS deploy helper for SyncBot.
# Handles: bootstrap (optional), sam build, sam deploy (new RDS or existing RDS).
#
# Run from repo root:
#   ./infra/aws/scripts/deploy.sh
#
# Phases (main path, after functions are defined below):
#   1) Prerequisites: CLI checks, template paths
#   2) Bootstrap: CloudFormation bootstrap stack and S3 artifact bucket (if missing)
#   3) App stack: region, stage, target stack name; detect existing stack for update
#   4) Database: source mode (stack RDS vs external host), engine, schema, existing-DB networking
#   5) Slack: signing secret, client secret, client ID
#   6) Confirm deploy summary, SAM build + deploy
#   7) After deploy: stage manifest, optional Slack API update, optional GitHub vars, deploy receipt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

BOOTSTRAP_TEMPLATE="$REPO_ROOT/infra/aws/template.bootstrap.yaml"
APP_TEMPLATE="$REPO_ROOT/infra/aws/template.yaml"
SLACK_MANIFEST_GENERATED_PATH=""
APP_DB_PASSWORD_OVERRIDE="${APP_DB_PASSWORD_OVERRIDE:-}"
APP_DB_PASSWORD_REUSED_FROM_SECRET=""
SLACK_SIGNING_SECRET_SOURCE=""
SLACK_CLIENT_SECRET_SOURCE=""
EXISTING_DB_ADMIN_PASSWORD_SOURCE=""
# Populated before write_deploy_receipt: backup summary + markdown receipt (deploy-receipts/*.md).
RECEIPT_TOKEN_SECRET_ID=""
RECEIPT_APP_DB_SECRET_NAME=""

# shellcheck source=/dev/null
source "$REPO_ROOT/deploy.sh"

prompt_default() {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "$prompt [$default]: " value
  if [[ -z "$value" ]]; then
    value="$default"
  fi
  echo "$value"
}

prompt_secret() {
  local prompt="$1"
  local value
  read -r -s -p "$prompt: " value
  # Keep the visual newline on the terminal even when called via $(...).
  printf '\n' >&2
  echo "$value"
}

prompt_required() {
  local prompt="$1"
  local value
  while true; do
    read -r -p "$prompt: " value
    if [[ -n "$value" ]]; then
      echo "$value"
      return 0
    fi
    echo "Error: $prompt is required." >&2
  done
}

prompt_secret_required() {
  local prompt="$1"
  local value
  while true; do
    value="$(prompt_secret "$prompt")"
    if [[ -n "$value" ]]; then
      echo "$value"
      return 0
    fi
    echo "Error: $prompt is required." >&2
  done
}

required_from_env_or_prompt() {
  local env_name="$1"
  local prompt="$2"
  local mode="${3:-plain}" # plain|secret
  local env_value="${!env_name:-}"
  if [[ -n "$env_value" ]]; then
    echo "Using $prompt from environment variable $env_name." >&2
    echo "$env_value"
    return 0
  fi
  if [[ "$mode" == "secret" ]]; then
    prompt_secret_required "$prompt"
  else
    prompt_required "$prompt"
  fi
}

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local answer
  local shown="y/N"
  [[ "$default" == "y" ]] && shown="Y/n"
  read -r -p "$prompt [$shown]: " answer
  if [[ -z "$answer" ]]; then
    answer="$default"
  fi
  [[ "$answer" =~ ^[Yy]$ ]]
}

ensure_aws_authenticated() {
  local profile active_profile sso_start_url sso_region
  profile="${AWS_PROFILE:-}"
  active_profile="$profile"
  if [[ -z "$active_profile" ]]; then
    active_profile="$(aws configure get profile 2>/dev/null || true)"
    [[ -z "$active_profile" ]] && active_profile="default"
  fi

  if aws sts get-caller-identity >/dev/null 2>&1; then
    return 0
  fi

  sso_start_url="$(aws configure get sso_start_url --profile "$active_profile" 2>/dev/null || true)"
  sso_region="$(aws configure get sso_region --profile "$active_profile" 2>/dev/null || true)"

  echo "AWS CLI is not authenticated."
  if [[ -n "$sso_start_url" && -n "$sso_region" ]]; then
    if prompt_yes_no "Run 'aws sso login --profile $active_profile' now?" "y"; then
      aws sso login --profile "$active_profile" || true
    fi
  else
    echo "No complete SSO config found for profile '$active_profile'."
    # Prefer the user's default interactive AWS login flow when available.
    if aws login help >/dev/null 2>&1; then
      if prompt_yes_no "Run 'aws login' now?" "y"; then
        aws login || true
      fi
    fi

    if ! aws sts get-caller-identity >/dev/null 2>&1; then
      if prompt_yes_no "Run 'aws configure sso --profile $active_profile' now?" "n"; then
        aws configure sso --profile "$active_profile" || true
        if prompt_yes_no "Run 'aws sso login --profile $active_profile' now?" "y"; then
          aws sso login --profile "$active_profile" || true
        fi
      else
        echo "Tip: use 'aws configure' if you authenticate with access keys."
      fi
    fi
  fi

  if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "Unable to authenticate AWS CLI."
    echo "Run one of the following, then rerun deploy:"
    echo "  aws login"
    echo "  aws configure sso [--profile <profile>]"
    echo "  aws sso login [--profile <profile>]"
    echo "  aws configure"
    exit 1
  fi
}

ensure_gh_authenticated() {
  if ! command -v gh >/dev/null 2>&1; then
    prereqs_hint_gh_cli >&2
    return 1
  fi
  if gh auth status >/dev/null 2>&1; then
    return 0
  fi
  echo "gh CLI is not authenticated."
  if prompt_yes_no "Run 'gh auth login' now?" "y"; then
    gh auth login || true
  fi
  if gh auth status >/dev/null 2>&1; then
    return 0
  fi
  echo "gh authentication is still missing. Skipping automatic GitHub setup."
  return 1
}

slack_manifest_json_compact() {
  local manifest_file="$1"
  python3 - "$manifest_file" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
print(json.dumps(data, separators=(",", ":")))
PY
}

slack_api_configure_from_manifest() {
  local manifest_file="$1"
  local install_url="$2"
  local token app_id team_id manifest_json api_resp ok

  echo
  echo "=== Slack App API ==="

  token="$(required_from_env_or_prompt "SLACK_API_TOKEN" "Slack API token (required scopes: apps.manifest:write)" "secret")"
  app_id="$(prompt_default "Slack App ID (optional; blank = create new app)" "${SLACK_APP_ID:-}")"
  team_id="$(prompt_default "Slack Team ID (optional; usually blank)" "${SLACK_TEAM_ID:-}")"

  manifest_json="$(slack_manifest_json_compact "$manifest_file" 2>/dev/null || true)"
  if [[ -z "$manifest_json" ]]; then
    echo "Could not parse manifest JSON automatically."
    echo "Ensure $manifest_file is valid JSON and Python 3 is installed."
    return 0
  fi

  if [[ -n "$app_id" ]]; then
    if [[ -n "$team_id" ]]; then
      api_resp="$(curl -sS -X POST \
        -H "Authorization: Bearer $token" \
        --data-urlencode "app_id=$app_id" \
        --data-urlencode "team_id=$team_id" \
        --data-urlencode "manifest=$manifest_json" \
        "https://slack.com/api/apps.manifest.update" || true)"
    else
      api_resp="$(curl -sS -X POST \
        -H "Authorization: Bearer $token" \
        --data-urlencode "app_id=$app_id" \
        --data-urlencode "manifest=$manifest_json" \
        "https://slack.com/api/apps.manifest.update" || true)"
    fi
    ok="$(python3 - "$api_resp" <<'PY'
import json,sys
try:
    data=json.loads(sys.argv[1])
except Exception:
    print("invalid-json")
    sys.exit(0)
print("ok" if data.get("ok") else f"error:{data.get('error','unknown_error')}")
PY
)"
    if [[ "$ok" == "ok" ]]; then
      echo "Slack app manifest updated for App ID: $app_id"
      echo "Open install URL: $install_url"
    else
      echo "Slack API update failed: ${ok#error:}"
      echo "Response (truncated):"
      slack_api_echo_truncated_body "$api_resp"
      echo "Hint: check token scopes (apps.manifest:write), manifest JSON, and api.slack.com methods apps.manifest.update"
    fi
    return 0
  fi

  # No App ID supplied: create a new Slack app from manifest.
  if [[ -n "$team_id" ]]; then
    api_resp="$(curl -sS -X POST \
      -H "Authorization: Bearer $token" \
      --data-urlencode "team_id=$team_id" \
      --data-urlencode "manifest=$manifest_json" \
      "https://slack.com/api/apps.manifest.create" || true)"
  else
    api_resp="$(curl -sS -X POST \
      -H "Authorization: Bearer $token" \
      --data-urlencode "manifest=$manifest_json" \
      "https://slack.com/api/apps.manifest.create" || true)"
  fi
  ok="$(python3 - "$api_resp" <<'PY'
import json,sys
try:
    data=json.loads(sys.argv[1])
except Exception:
    print("invalid-json")
    sys.exit(0)
if not data.get("ok"):
    print(f"error:{data.get('error','unknown_error')}")
    sys.exit(0)
app_id = data.get("app_id") or (data.get("app", {}) or {}).get("id") or ""
print(f"ok:{app_id}")
PY
)"
  if [[ "$ok" == ok:* ]]; then
    app_id="${ok#ok:}"
    echo "Slack app created successfully."
    [[ -n "$app_id" ]] && echo "New Slack App ID: $app_id"
    echo "Open install URL: $install_url"
  else
    echo "Slack API create failed: ${ok#error:}"
    echo "Response (truncated):"
    slack_api_echo_truncated_body "$api_resp"
    echo "Hint: check token scopes (apps.manifest:write), manifest JSON, and api.slack.com methods apps.manifest.create"
  fi
}

bootstrap_describe_outputs() {
  local stack_name="$1"
  local region="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
    --output text \
    --region "$region" 2>/dev/null || true
}

app_describe_outputs() {
  local stack_name="$1"
  local region="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
    --output text \
    --region "$region" 2>/dev/null || true
}

output_value() {
  local outputs="$1"
  local key="$2"
  echo "$outputs" | awk -F'\t' -v k="$key" '$1==k {print $2}'
}

configure_github_actions_aws() {
  # $1  Bootstrap stack outputs (tab-separated OutputKey / OutputValue)
  # $2  Bootstrap CloudFormation stack name (for OIDC drift check vs gh repo)
  # $3  AWS region for this deploy session (fallback if bootstrap has no BootstrapRegion output)
  # $4  App CloudFormation stack name
  # $5  Stage name (test|prod) — GitHub environment name
  # $6  Database schema name
  # $7  DB source mode: 1 = stack-managed RDS, 2 = external or existing host (matches SAM / prompts)
  # $8  Existing DB host (mode 2)
  # $9  Existing DB admin user (mode 2)
  # $10 Existing DB admin password (mode 2)
  # $11 Existing DB network mode: public | private
  # $12 Comma-separated subnet IDs for Lambda in private mode
  # $13 Lambda ENI security group id in private mode
  # $14 Database engine: mysql | postgresql
  local bootstrap_outputs="$1"
  local bootstrap_stack_name="$2"
  local aws_region="$3"
  local app_stack_name="$4"
  local deploy_stage="$5"
  local database_schema="$6"
  local db_mode="$7"
  local existing_db_host="$8"
  local existing_db_admin_user="$9"
  local existing_db_admin_password="${10}"
  local existing_db_network_mode="${11:-}"
  [[ -z "$existing_db_network_mode" ]] && existing_db_network_mode="public"
  local existing_db_subnet_ids_csv="${12:-}"
  local existing_db_lambda_sg_id="${13:-}"
  local database_engine="${14:-}"
  [[ -z "$database_engine" ]] && database_engine="mysql"
  local role bucket boot_region
  role="$(output_value "$bootstrap_outputs" "GitHubDeployRoleArn")"
  bucket="$(output_value "$bootstrap_outputs" "DeploymentBucketName")"
  boot_region="$(output_value "$bootstrap_outputs" "BootstrapRegion")"
  [[ -z "$boot_region" ]] && boot_region="$aws_region"
  local repo env_name
  env_name="$deploy_stage"

  echo
  echo "=== GitHub Actions (AWS) ==="
  echo "Detected bootstrap role:   $role"
  echo "Detected deploy bucket:    $bucket  (SAM/CI packaging for sam deploy — not Slack or app media)"
  echo "Detected bootstrap region: $boot_region"
  repo="$(prompt_github_repo_for_actions "$REPO_ROOT")"
  maybe_prompt_bootstrap_github_trust_update "$repo" "$bootstrap_stack_name" "$aws_region"

  if ! ensure_gh_authenticated; then
    echo
    echo "Set these GitHub Actions Variables manually (on the repo you intend):"
    echo "  AWS_ROLE_TO_ASSUME = $role"
    echo "  AWS_S3_BUCKET      = $bucket  (SAM deploy artifact bucket / DeploymentBucketName; not Slack file storage)"
    echo "  AWS_REGION         = $boot_region"
    echo "For environment '$env_name' also set AWS_STACK_NAME, STAGE_NAME, DATABASE_SCHEMA, DATABASE_ENGINE,"
    echo "and (if using existing RDS) EXISTING_DATABASE_* / private VPC vars — see docs/DEPLOYMENT.md."
    return 0
  fi

  if prompt_yes_no "Create/update GitHub environments 'test' and 'prod' now?" "y"; then
    gh api -X PUT "repos/$repo/environments/test" >/dev/null
    gh api -X PUT "repos/$repo/environments/prod" >/dev/null
    echo "GitHub environments ensured: test, prod."
  fi

  if prompt_yes_no "Set repo variables with gh now (AWS_ROLE_TO_ASSUME, AWS_S3_BUCKET, AWS_REGION)? AWS_S3_BUCKET is SAM/CI packaging only (DeploymentBucketName)." "y"; then
    [[ -n "$role" ]] && gh variable set AWS_ROLE_TO_ASSUME --body "$role" -R "$repo"
    [[ -n "$bucket" ]] && gh variable set AWS_S3_BUCKET --body "$bucket" -R "$repo"
    [[ -n "$boot_region" ]] && gh variable set AWS_REGION --body "$boot_region" -R "$repo"
    echo "GitHub repository variables updated."
  fi

  if prompt_yes_no "Set environment variables for '$env_name' now (AWS_STACK_NAME, STAGE_NAME, DATABASE_SCHEMA, DB host/user vars)?" "y"; then
    gh variable set AWS_STACK_NAME --env "$env_name" --body "$app_stack_name" -R "$repo"
    gh variable set STAGE_NAME --env "$env_name" --body "$deploy_stage" -R "$repo"
    gh variable set DATABASE_SCHEMA --env "$env_name" --body "$database_schema" -R "$repo"
    gh variable set DATABASE_ENGINE --env "$env_name" --body "$database_engine" -R "$repo"
    if [[ "$db_mode" == "2" ]]; then
      gh variable set EXISTING_DATABASE_HOST --env "$env_name" --body "$existing_db_host" -R "$repo"
      gh variable set EXISTING_DATABASE_ADMIN_USER --env "$env_name" --body "$existing_db_admin_user" -R "$repo"
      gh variable set EXISTING_DATABASE_NETWORK_MODE --env "$env_name" --body "$existing_db_network_mode" -R "$repo"
      if [[ "$existing_db_network_mode" == "private" ]]; then
        gh variable set EXISTING_DATABASE_SUBNET_IDS_CSV --env "$env_name" --body "$existing_db_subnet_ids_csv" -R "$repo"
        gh variable set EXISTING_DATABASE_LAMBDA_SECURITY_GROUP_ID --env "$env_name" --body "$existing_db_lambda_sg_id" -R "$repo"
      else
        gh variable set EXISTING_DATABASE_SUBNET_IDS_CSV --env "$env_name" --body "" -R "$repo"
        gh variable set EXISTING_DATABASE_LAMBDA_SECURITY_GROUP_ID --env "$env_name" --body "" -R "$repo"
      fi
    else
      # Clear existing-host vars for new-RDS mode to avoid stale CI config.
      gh variable set EXISTING_DATABASE_HOST --env "$env_name" --body "" -R "$repo"
      gh variable set EXISTING_DATABASE_ADMIN_USER --env "$env_name" --body "" -R "$repo"
      gh variable set EXISTING_DATABASE_NETWORK_MODE --env "$env_name" --body "public" -R "$repo"
      gh variable set EXISTING_DATABASE_SUBNET_IDS_CSV --env "$env_name" --body "" -R "$repo"
      gh variable set EXISTING_DATABASE_LAMBDA_SECURITY_GROUP_ID --env "$env_name" --body "" -R "$repo"
    fi
    echo "Environment variables updated for '$env_name'."
  fi

  if prompt_yes_no "Set environment secrets for '$env_name' now (Slack secrets + optional Existing DB admin password)?" "n"; then
    if [[ -z "${SLACK_SIGNING_SECRET:-}" ]]; then
      SLACK_SIGNING_SECRET_SOURCE="prompt"
      SLACK_SIGNING_SECRET="$(required_from_env_or_prompt "SLACK_SIGNING_SECRET" "SlackSigningSecret" "secret")"
    fi
    if [[ -z "${SLACK_CLIENT_SECRET:-}" ]]; then
      SLACK_CLIENT_SECRET_SOURCE="prompt"
      SLACK_CLIENT_SECRET="$(required_from_env_or_prompt "SLACK_CLIENT_SECRET" "SlackClientSecret" "secret")"
    fi
    gh secret set SLACK_SIGNING_SECRET --env "$env_name" --body "$SLACK_SIGNING_SECRET" -R "$repo"
    gh secret set SLACK_CLIENT_SECRET --env "$env_name" --body "$SLACK_CLIENT_SECRET" -R "$repo"
    if [[ "$db_mode" == "2" && -n "$existing_db_admin_password" ]]; then
      gh secret set EXISTING_DATABASE_ADMIN_PASSWORD --env "$env_name" --body "$existing_db_admin_password" -R "$repo"
    fi
    echo "Environment secrets updated for '$env_name'."
  fi
}

generate_stage_slack_manifest() {
  local stage="$1"
  local api_url="$2"
  local install_url="$3"
  local template="$REPO_ROOT/slack-manifest.json"
  local manifest_out="$REPO_ROOT/slack-manifest_${stage}.json"
  local events_url base_url oauth_redirect_url

  if [[ ! -f "$template" ]]; then
    echo "Slack manifest template not found at $template"
    return 0
  fi
  if [[ -z "$api_url" ]]; then
    echo "Could not determine API URL from stack outputs. Skipping Slack manifest generation."
    return 0
  fi

  events_url="${api_url%/}"
  base_url="${events_url%/slack/events}"
  oauth_redirect_url="${base_url}/slack/oauth_redirect"

  if ! python3 - "$template" "$manifest_out" "$events_url" "$oauth_redirect_url" <<'PY'
import json
import sys

template_path, out_path, events_url, redirect_url = sys.argv[1:5]
with open(template_path, "r", encoding="utf-8") as f:
    manifest = json.load(f)

manifest.setdefault("oauth_config", {}).setdefault("redirect_urls", [])
manifest["oauth_config"]["redirect_urls"] = [redirect_url]
manifest.setdefault("settings", {}).setdefault("event_subscriptions", {})
manifest["settings"]["event_subscriptions"]["request_url"] = events_url
manifest.setdefault("settings", {}).setdefault("interactivity", {})
manifest["settings"]["interactivity"]["request_url"] = events_url

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY
  then
    echo "Failed to generate stage Slack manifest from JSON template."
    return 0
  fi

  SLACK_MANIFEST_GENERATED_PATH="$manifest_out"

  echo "=== Slack Manifest (${stage}) ==="
  echo "Saved file: $manifest_out"
  echo "Install URL: $install_url"
  echo
  sed 's/^/  /' "$manifest_out"
}

secret_arn_by_name() {
  local secret_name="$1"
  local region="$2"
  aws secretsmanager describe-secret \
    --secret-id "$secret_name" \
    --region "$region" \
    --query 'ARN' \
    --output text 2>/dev/null || true
}

secret_value_by_id() {
  local secret_id="$1"
  local region="$2"
  aws secretsmanager get-secret-value \
    --secret-id "$secret_id" \
    --region "$region" \
    --query 'SecretString' \
    --output text 2>/dev/null || true
}

rds_lookup_admin_defaults() {
  local db_host="$1"
  local region="$2"
  aws rds describe-db-instances \
    --region "$region" \
    --query "DBInstances[?Endpoint.Address=='$db_host']|[0].[MasterUsername,MasterUserSecret.SecretArn]" \
    --output text 2>/dev/null || true
}

secret_password_by_id() {
  local secret_id="$1"
  local region="$2"
  local raw
  raw="$(secret_value_by_id "$secret_id" "$region")"
  if [[ -z "$raw" || "$raw" == "None" ]]; then
    return 1
  fi
  python3 - "$raw" <<'PY'
import json
import sys

raw = sys.argv[1]
if not raw or raw == "None":
    print("")
    raise SystemExit(0)

try:
    data = json.loads(raw)
except Exception:
    print(raw)
    raise SystemExit(0)

if isinstance(data, dict):
    password = data.get("password")
    if isinstance(password, str) and password:
        print(password)
    else:
        print("")
else:
    print("")
PY
}

wait_for_secret_deleted() {
  local secret_id="$1"
  local region="$2"
  local max_attempts="${3:-20}"
  local sleep_seconds="${4:-3}"
  local attempt
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    if ! aws secretsmanager describe-secret --secret-id "$secret_id" --region "$region" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  return 1
}

handle_orphan_app_db_secret_on_create() {
  local stack_status="$1"
  local secret_name="$2"
  local region="$3"
  local secret_arn reuse_value

  # Only needed for brand-new stack creates where a previous failed stack left the named secret.
  if [[ -n "$stack_status" && "$stack_status" != "None" ]]; then
    return 0
  fi

  secret_arn="$(secret_arn_by_name "$secret_name" "$region")"
  if [[ -z "$secret_arn" || "$secret_arn" == "None" ]]; then
    return 0
  fi

  echo "Detected existing app DB secret: $secret_name"
  if [[ -z "$APP_DB_PASSWORD_OVERRIDE" ]]; then
    if prompt_yes_no "Reuse existing app DB password value when recreating this secret?" "y"; then
      reuse_value="$(secret_password_by_id "$secret_arn" "$region" 2>/dev/null || true)"
      if [[ -n "$reuse_value" && "$reuse_value" != "None" ]]; then
        APP_DB_PASSWORD_OVERRIDE="$reuse_value"
        APP_DB_PASSWORD_REUSED_FROM_SECRET="$secret_name"
        echo "Will reuse existing app DB password value."
      else
        echo "Could not read existing app DB secret value; deploy will create a new app DB password."
      fi
    fi
  else
    echo "Using provided AppDbPasswordOverride for secret recreation."
    [[ -z "$APP_DB_PASSWORD_REUSED_FROM_SECRET" ]] && APP_DB_PASSWORD_REUSED_FROM_SECRET="provided-override"
  fi

  if ! prompt_yes_no "Delete detected secret now so create can continue?" "y"; then
    echo "Cannot create new stack while this secret name already exists." >&2
    echo "Delete it manually or choose a different stage/stack." >&2
    exit 1
  fi

  if ! aws secretsmanager delete-secret \
    --secret-id "$secret_arn" \
    --region "$region" \
    --force-delete-without-recovery >/dev/null 2>&1; then
    echo "Failed to delete secret '$secret_name'. Check IAM permissions and retry." >&2
    exit 1
  fi

  echo "Deleted secret '$secret_name'. Waiting for name to become available..."
  if ! wait_for_secret_deleted "$secret_arn" "$region"; then
    echo "Secret deletion is still propagating. Wait a minute and rerun deploy." >&2
    exit 1
  fi
}

write_deploy_receipt() {
  local provider="$1"
  local stage="$2"
  local project_or_stack="$3"
  local region="$4"
  local service_url="$5"
  local install_url="$6"
  local manifest_path="$7"
  local ts_human ts_file receipt_dir receipt_path

  ts_human="$(date -u +"%Y-%m-%d %H:%M:%S UTC")"
  ts_file="$(date -u +"%Y%m%dT%H%M%SZ")"
  receipt_dir="$REPO_ROOT/deploy-receipts"
  receipt_path="$receipt_dir/deploy-${provider}-${stage}-${ts_file}.md"

  mkdir -p "$receipt_dir"
  cat >"$receipt_path" <<EOF
# SyncBot Deploy Receipt

- Provider: $provider
- Stage: $stage
- Timestamp: $ts_human
- Project/Stack: $project_or_stack
- Region: $region
- Service URL: ${service_url:-n/a}
- Slack Install URL: ${install_url:-n/a}
- Slack Manifest: ${manifest_path:-n/a}

## Secrets Used
- SlackSigningSecret source: ${SLACK_SIGNING_SECRET_SOURCE:-unknown}
- SlackClientSecret source: ${SLACK_CLIENT_SECRET_SOURCE:-unknown}
- Existing DB admin password source: ${EXISTING_DB_ADMIN_PASSWORD_SOURCE:-n/a}
- Token secret id: ${RECEIPT_TOKEN_SECRET_ID:-n/a}
- App DB secret name: ${RECEIPT_APP_DB_SECRET_NAME:-n/a}
- Reused app DB password from existing secret: ${APP_DB_PASSWORD_REUSED_FROM_SECRET:-no}
EOF

  echo "Deploy receipt written: $receipt_path"
}

preflight_secrets_manager_access() {
  local region="$1"
  local token_secret_id="$2"
  local app_db_secret_name="$3"
  local existing_token_secret_arn="${4:-}"
  local current_secret_id describe_out get_out

  echo
  echo "=== Secrets Manager Access Preflight ==="
  echo "Verifying deploy principal can read required SyncBot secrets before SAM deploy..."

  # Validate current principal can read both known secret IDs that this deploy path may use.
  for current_secret_id in "$token_secret_id" "$app_db_secret_name"; do
    if [[ -z "$current_secret_id" ]]; then
      continue
    fi

    describe_out="$(aws secretsmanager describe-secret \
      --secret-id "$current_secret_id" \
      --region "$region" \
      --query 'ARN' \
      --output text 2>&1 || true)"
    if [[ "$describe_out" == *"AccessDenied"* || "$describe_out" == *"not authorized"* ]]; then
      echo "Secrets Manager preflight failed: missing DescribeSecret on '$current_secret_id'." >&2
      echo "Fix: re-deploy bootstrap stack to update syncbot deploy policy, then retry." >&2
      exit 1
    fi

    get_out="$(aws secretsmanager get-secret-value \
      --secret-id "$current_secret_id" \
      --region "$region" \
      --query 'ARN' \
      --output text 2>&1 || true)"
    if [[ "$get_out" == *"AccessDenied"* || "$get_out" == *"not authorized"* ]]; then
      echo "Secrets Manager preflight failed: missing GetSecretValue on '$current_secret_id'." >&2
      echo "This commonly breaks CloudFormation when Lambda environment uses dynamic secret references." >&2
      echo "Fix: re-deploy bootstrap stack to update syncbot deploy policy, then retry." >&2
      exit 1
    fi
  done

  # If explicitly reusing an ARN, validate direct access too.
  if [[ -n "$existing_token_secret_arn" ]]; then
    get_out="$(aws secretsmanager get-secret-value \
      --secret-id "$existing_token_secret_arn" \
      --region "$region" \
      --query 'ARN' \
      --output text 2>&1 || true)"
    if [[ "$get_out" == *"AccessDenied"* || "$get_out" == *"not authorized"* ]]; then
      echo "Secrets Manager preflight failed: missing GetSecretValue on '$existing_token_secret_arn'." >&2
      echo "Fix: re-deploy bootstrap stack to update syncbot deploy policy, then retry." >&2
      exit 1
    fi
  fi

  echo "Secrets Manager preflight passed."
}

rds_lookup_network_defaults() {
  local db_host="$1"
  local region="$2"
  aws rds describe-db-instances \
    --region "$region" \
    --query "DBInstances[?Endpoint.Address=='$db_host']|[0].[PubliclyAccessible,join(',',DBSubnetGroup.Subnets[].SubnetIdentifier),join(',',VpcSecurityGroups[].VpcSecurityGroupId),DBSubnetGroup.VpcId,DBInstanceIdentifier]" \
    --output text 2>/dev/null || true
}

ec2_subnet_vpc_ids() {
  local region="$1"
  shift
  aws ec2 describe-subnets \
    --region "$region" \
    --subnet-ids "$@" \
    --query 'Subnets[*].[SubnetId,VpcId]' \
    --output text 2>/dev/null || true
}

ec2_vpc_subnet_ids() {
  local vpc_id="$1"
  local region="$2"
  aws ec2 describe-subnets \
    --region "$region" \
    --filters "Name=vpc-id,Values=$vpc_id" \
    --query 'Subnets[].SubnetId' \
    --output text 2>/dev/null || true
}

ec2_security_group_vpc() {
  local sg_id="$1"
  local region="$2"
  aws ec2 describe-security-groups \
    --region "$region" \
    --group-ids "$sg_id" \
    --query 'SecurityGroups[0].VpcId' \
    --output text 2>/dev/null || true
}

ec2_sg_allows_from_sg_on_port() {
  local db_sg_id="$1"
  local source_sg_id="$2"
  local port="$3"
  local region="$4"
  local allowed_groups
  allowed_groups="$(aws ec2 describe-security-groups \
    --region "$region" \
    --group-ids "$db_sg_id" \
    --query "SecurityGroups[0].IpPermissions[?FromPort<=\`$port\` && ToPort>=\`$port\`].UserIdGroupPairs[].GroupId" \
    --output text 2>/dev/null || true)"
  [[ " $allowed_groups " == *" $source_sg_id "* ]]
}

ec2_subnet_route_table_id() {
  local subnet_id="$1"
  local vpc_id="$2"
  local region="$3"
  local rt_id
  rt_id="$(aws ec2 describe-route-tables \
    --region "$region" \
    --filters "Name=association.subnet-id,Values=$subnet_id" \
    --query 'RouteTables[0].RouteTableId' \
    --output text 2>/dev/null || true)"
  if [[ -z "$rt_id" || "$rt_id" == "None" ]]; then
    rt_id="$(aws ec2 describe-route-tables \
      --region "$region" \
      --filters "Name=vpc-id,Values=$vpc_id" "Name=association.main,Values=true" \
      --query 'RouteTables[0].RouteTableId' \
      --output text 2>/dev/null || true)"
  fi
  echo "$rt_id"
}

ec2_subnet_default_route_target() {
  local subnet_id="$1"
  local vpc_id="$2"
  local region="$3"
  local rt_id targets target
  rt_id="$(ec2_subnet_route_table_id "$subnet_id" "$vpc_id" "$region")"
  if [[ -z "$rt_id" || "$rt_id" == "None" ]]; then
    echo "none"
    return 0
  fi

  # Read all active default-route targets and pick the first concrete one.
  targets="$(aws ec2 describe-route-tables \
    --region "$region" \
    --route-table-ids "$rt_id" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0' && State=='active'].[NatGatewayId,GatewayId,TransitGatewayId,NetworkInterfaceId,VpcPeeringConnectionId]" \
    --output text 2>/dev/null || true)"
  for target in $targets; do
    [[ "$target" == "None" ]] && continue
    echo "$target"
    return 0
  done

  echo "none"
}

discover_private_lambda_subnets_for_db_vpc() {
  local vpc_id="$1"
  local region="$2"
  local subnet_ids subnet_id route_target out
  subnet_ids="$(ec2_vpc_subnet_ids "$vpc_id" "$region")"
  if [[ -z "$subnet_ids" || "$subnet_ids" == "None" ]]; then
    echo ""
    return 0
  fi

  out=""
  for subnet_id in $subnet_ids; do
    [[ -z "$subnet_id" ]] && continue
    route_target="$(ec2_subnet_default_route_target "$subnet_id" "$vpc_id" "$region")"
    # Lambda private-subnet candidates: active default route through NAT.
    if [[ "$route_target" == nat-* ]]; then
      if [[ -z "$out" ]]; then
        out="$subnet_id"
      else
        out="$out,$subnet_id"
      fi
    fi
  done
  echo "$out"
}

validate_private_existing_db_connectivity() {
  local region="$1"
  local engine="$2"
  local subnet_csv="$3"
  local lambda_sg="$4"
  local db_vpc="$5"
  local db_sgs_csv="$6"
  local db_host="$7"
  local db_port subnet_list subnet_vpcs first_vpc line subnet_id subnet_vpc db_sg_id lambda_sg_vpc db_sg_list route_target rt_id ingress_ok
  local -a no_nat_subnets

  db_port="3306"
  [[ "$engine" == "postgresql" ]] && db_port="5432"

  IFS=',' read -r -a subnet_list <<< "$subnet_csv"
  if [[ "${#subnet_list[@]}" -lt 1 ]]; then
    echo "Connectivity preflight failed: no subnet IDs provided for private mode." >&2
    return 1
  fi

  subnet_vpcs="$(ec2_subnet_vpc_ids "$region" "${subnet_list[@]}")"
  if [[ -z "$subnet_vpcs" || "$subnet_vpcs" == "None" ]]; then
    echo "Connectivity preflight failed: could not read VPC IDs for provided subnets." >&2
    return 1
  fi

  first_vpc=""
  while IFS=$'\t' read -r subnet_id subnet_vpc; do
    [[ -z "$subnet_id" || -z "$subnet_vpc" ]] && continue
    if [[ -z "$first_vpc" ]]; then
      first_vpc="$subnet_vpc"
    elif [[ "$subnet_vpc" != "$first_vpc" ]]; then
      echo "Connectivity preflight failed: subnets span multiple VPCs." >&2
      return 1
    fi
  done <<< "$subnet_vpcs"

  if [[ -z "$first_vpc" ]]; then
    echo "Connectivity preflight failed: unable to determine subnet VPC." >&2
    return 1
  fi

  if [[ -n "$db_vpc" && "$db_vpc" != "$first_vpc" ]]; then
    echo "Connectivity preflight failed: Lambda subnets are in $first_vpc but DB is in $db_vpc." >&2
    return 1
  fi

  lambda_sg_vpc="$(ec2_security_group_vpc "$lambda_sg" "$region")"
  if [[ -z "$lambda_sg_vpc" || "$lambda_sg_vpc" == "None" ]]; then
    echo "Connectivity preflight failed: Lambda security group '$lambda_sg' was not found." >&2
    return 1
  fi
  if [[ "$lambda_sg_vpc" != "$first_vpc" ]]; then
    echo "Connectivity preflight failed: Lambda security group is in $lambda_sg_vpc, expected $first_vpc." >&2
    return 1
  fi

  if [[ -n "$db_sgs_csv" ]]; then
    ingress_ok="false"
    IFS=',' read -r -a db_sg_list <<< "$db_sgs_csv"
    for db_sg_id in "${db_sg_list[@]}"; do
      db_sg_id="${db_sg_id// /}"
      [[ -z "$db_sg_id" ]] && continue
      if ec2_sg_allows_from_sg_on_port "$db_sg_id" "$lambda_sg" "$db_port" "$region"; then
        echo "Connectivity preflight passed: DB SG $db_sg_id allows Lambda SG $lambda_sg on port $db_port."
        ingress_ok="true"
        break
      fi
    done
    if [[ "$ingress_ok" != "true" ]]; then
      echo "Connectivity preflight failed: none of the DB security groups allow Lambda SG $lambda_sg on port $db_port." >&2
      echo "Fix: add an inbound SG rule on the DB security group from '$lambda_sg' to TCP $db_port." >&2
      return 1
    fi
  fi

  if [[ -z "$db_sgs_csv" ]]; then
    echo "Connectivity preflight warning: DB SGs could not be auto-detected for host $db_host." >&2
    echo "Cannot verify ingress rule automatically; continuing with subnet/VPC checks only." >&2
  fi

  no_nat_subnets=()
  for subnet_id in "${subnet_list[@]}"; do
    subnet_id="${subnet_id// /}"
    [[ -z "$subnet_id" ]] && continue
    route_target="$(ec2_subnet_default_route_target "$subnet_id" "$first_vpc" "$region")"
    if [[ "$route_target" != nat-* ]]; then
      no_nat_subnets+=("$subnet_id:$route_target")
    fi
  done

  if [[ "${#no_nat_subnets[@]}" -gt 0 ]]; then
    echo "Connectivity preflight failed: one or more selected private subnets do not have an active NAT default route." >&2
    for entry in "${no_nat_subnets[@]}"; do
      subnet_id="${entry%%:*}"
      route_target="${entry#*:}"
      rt_id="$(ec2_subnet_route_table_id "$subnet_id" "$first_vpc" "$region")"
      echo "  - Subnet $subnet_id (route table $rt_id) default route target: $route_target" >&2
    done
    echo "Fix before deploy:" >&2
    echo "  1) Use private subnets whose route table has 0.0.0.0/0 -> nat-xxxx" >&2
    echo "  2) Or update those route tables to point 0.0.0.0/0 to a NAT gateway" >&2
    echo "  3) Ensure DB SG allows Lambda SG '$lambda_sg' on TCP $db_port" >&2
    return 1
  fi

  echo "Connectivity preflight passed: private subnets have NAT egress."
  return 0
}

stack_status() {
  local stack_name="$1"
  local region="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$region" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || true
}

stack_parameters() {
  local stack_name="$1"
  local region="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$region" \
    --query 'Stacks[0].Parameters[*].[ParameterKey,ParameterValue]' \
    --output text 2>/dev/null || true
}

stack_param_value() {
  local params="$1"
  local key="$2"
  echo "$params" | awk -F'\t' -v k="$key" '$1==k {print $2}'
}

# Keep bootstrap stack aligned with the checked-in template so IAM/policy fixes
# (for example CloudFormation changeset permissions) apply before app deploy.
# Set SYNCBOT_SKIP_BOOTSTRAP_SYNC=1 to opt out.
sync_bootstrap_stack_from_repo() {
  local bootstrap_stack="$1"
  local aws_region="$2"
  local params github_repo create_oidc bucket_prefix

  if [[ "${SYNCBOT_SKIP_BOOTSTRAP_SYNC:-}" == "1" ]]; then
    echo "Skipping bootstrap template sync (SYNCBOT_SKIP_BOOTSTRAP_SYNC=1)."
    return 0
  fi

  params="$(stack_parameters "$bootstrap_stack" "$aws_region")"
  if [[ -z "$params" ]]; then
    echo "Could not read bootstrap stack parameters for '$bootstrap_stack' in $aws_region; skipping bootstrap template sync." >&2
    return 0
  fi

  github_repo="$(stack_param_value "$params" "GitHubRepository")"
  github_repo="${github_repo//$'\r'/}"
  github_repo="${github_repo#"${github_repo%%[![:space:]]*}"}"
  github_repo="${github_repo%"${github_repo##*[![:space:]]}"}"
  if [[ -z "$github_repo" ]]; then
    echo "Bootstrap stack has no GitHubRepository parameter; skipping bootstrap template sync." >&2
    return 0
  fi

  create_oidc="$(stack_param_value "$params" "CreateOIDCProvider")"
  bucket_prefix="$(stack_param_value "$params" "DeploymentBucketPrefix")"
  [[ -z "$create_oidc" ]] && create_oidc="true"
  [[ -z "$bucket_prefix" ]] && bucket_prefix="syncbot-deploy"

  echo
  echo "Syncing bootstrap stack with repo template..."
  aws cloudformation deploy \
    --template-file "$BOOTSTRAP_TEMPLATE" \
    --stack-name "$bootstrap_stack" \
    --parameter-overrides \
      "GitHubRepository=$github_repo" \
      "CreateOIDCProvider=$create_oidc" \
      "DeploymentBucketPrefix=$bucket_prefix" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset \
    --region "$aws_region"
}

# Compare GitHub owner/repo from bootstrap stack to the repo chosen for gh; offer to update OIDC trust.
maybe_prompt_bootstrap_github_trust_update() {
  local picked_repo="$1"
  local bootstrap_stack="$2"
  local aws_region="$3"
  local params trusted picked_lc trusted_lc create_oidc bucket_prefix

  if [[ -z "$bootstrap_stack" || -z "$picked_repo" ]]; then
    return 0
  fi

  params="$(stack_parameters "$bootstrap_stack" "$aws_region")"
  if [[ -z "$params" ]]; then
    echo "Could not read bootstrap stack parameters for '$bootstrap_stack' in $aws_region; skipping OIDC trust drift check." >&2
    return 0
  fi

  trusted="$(stack_param_value "$params" "GitHubRepository")"
  # CloudFormation / CLI sometimes surface trailing whitespace; normalize for compare + display.
  trusted="${trusted//$'\r'/}"
  trusted="${trusted#"${trusted%%[![:space:]]*}"}"
  trusted="${trusted%"${trusted##*[![:space:]]}"}"
  if [[ -z "$trusted" ]]; then
    echo "Bootstrap stack has no GitHubRepository parameter; skipping OIDC trust drift check." >&2
    return 0
  fi

  picked_lc="$(printf '%s' "$picked_repo" | tr '[:upper:]' '[:lower:]')"
  trusted_lc="$(printf '%s' "$trusted" | tr '[:upper:]' '[:lower:]')"
  if [[ "$picked_lc" == "$trusted_lc" ]]; then
    echo "Bootstrap OIDC: stack '$bootstrap_stack' has GitHubRepository=$trusted — matches your choice; no bootstrap update needed."
    return 0
  fi

  echo
  echo "Warning: Bootstrap stack '$bootstrap_stack' OIDC trust is scoped to:"
  echo "  GitHubRepository=$trusted"
  echo "You chose this repository for GitHub Actions variables:"
  echo "  $picked_repo"
  echo "GitHub Actions in '$picked_repo' cannot assume the deploy role until trust matches."
  echo
  if ! prompt_yes_no "Update bootstrap OIDC trust to '$picked_repo'? (CloudFormation stack update)" "n"; then
    echo "Leaving bootstrap GitHubRepository unchanged. Fix manually or update the bootstrap stack later." >&2
    return 0
  fi

  create_oidc="$(stack_param_value "$params" "CreateOIDCProvider")"
  bucket_prefix="$(stack_param_value "$params" "DeploymentBucketPrefix")"
  [[ -z "$create_oidc" ]] && create_oidc="true"
  [[ -z "$bucket_prefix" ]] && bucket_prefix="syncbot-deploy"

  echo "Updating bootstrap stack '$bootstrap_stack'..."
  aws cloudformation deploy \
    --template-file "$BOOTSTRAP_TEMPLATE" \
    --stack-name "$bootstrap_stack" \
    --parameter-overrides \
      "GitHubRepository=$picked_repo" \
      "CreateOIDCProvider=$create_oidc" \
      "DeploymentBucketPrefix=$bucket_prefix" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$aws_region"
  echo "Bootstrap OIDC trust updated to $picked_repo."
}

print_recent_stack_failures() {
  local stack_name="$1"
  local region="$2"
  echo "Recent failure events for $stack_name:"
  aws cloudformation describe-stack-events \
    --stack-name "$stack_name" \
    --region "$region" \
    --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" \
    --output table 2>/dev/null || true
}

handle_unhealthy_stack_state() {
  local stack_name="$1"
  local region="$2"
  local status
  status="$(stack_status "$stack_name" "$region")"
  if [[ -z "$status" || "$status" == "None" ]]; then
    return 0
  fi

  case "$status" in
    CREATE_FAILED|ROLLBACK_COMPLETE|ROLLBACK_FAILED|UPDATE_ROLLBACK_FAILED|DELETE_FAILED)
      echo
      echo "Stack $stack_name is in a failed state: $status"
      print_recent_stack_failures "$stack_name" "$region"
      echo
      if prompt_yes_no "Delete failed stack '$stack_name' now so deploy can continue?" "y"; then
        aws cloudformation delete-stack --stack-name "$stack_name" --region "$region"
        echo "Waiting for stack deletion to complete..."
        aws cloudformation wait stack-delete-complete --stack-name "$stack_name" --region "$region"
      else
        echo "Cannot continue deploy while stack is in $status."
        exit 1
      fi
      ;;
    *_IN_PROGRESS)
      echo "Error: stack $stack_name is currently $status. Wait for it to finish, then rerun." >&2
      exit 1
      ;;
    *)
      ;;
  esac
}

prereqs_require_cmd aws prereqs_hint_aws_cli
prereqs_require_cmd sam prereqs_hint_sam_cli
prereqs_require_cmd docker prereqs_hint_docker
prereqs_require_cmd python3 prereqs_hint_python3
prereqs_require_cmd curl prereqs_hint_curl

prereqs_print_cli_status_matrix "AWS" aws sam docker python3 curl

if [[ ! -f "$APP_TEMPLATE" ]]; then
  echo "Error: app template not found at $APP_TEMPLATE" >&2
  exit 1
fi
if [[ ! -f "$BOOTSTRAP_TEMPLATE" ]]; then
  echo "Error: bootstrap template not found at $BOOTSTRAP_TEMPLATE" >&2
  exit 1
fi

echo "=== SyncBot AWS Deploy ==="
echo

DEFAULT_REGION="${AWS_REGION:-us-east-2}"
REGION="$(prompt_default "AWS region" "$DEFAULT_REGION")"
ensure_aws_authenticated
BOOTSTRAP_STACK="$(prompt_default "Bootstrap stack name" "syncbot-bootstrap")"

BOOTSTRAP_OUTPUTS="$(bootstrap_describe_outputs "$BOOTSTRAP_STACK" "$REGION")"
if [[ -z "$BOOTSTRAP_OUTPUTS" ]]; then
  echo
  echo "Bootstrap stack not found (or has no outputs): $BOOTSTRAP_STACK in $REGION"
  if prompt_yes_no "Deploy bootstrap stack now?" "y"; then
    GITHUB_REPO="$(prompt_default "GitHub repository (owner/repo)" "REPLACE_ME_OWNER/REPLACE_ME_REPO")"
    CREATE_OIDC="$(prompt_default "Create OIDC provider (true/false)" "true")"
    BUCKET_PREFIX="$(prompt_default "Deployment bucket prefix" "syncbot-deploy")"
    echo
    echo "=== Bootstrap Stack ==="
    echo "Deploying bootstrap stack..."
    aws cloudformation deploy \
      --template-file "$BOOTSTRAP_TEMPLATE" \
      --stack-name "$BOOTSTRAP_STACK" \
      --parameter-overrides \
        "GitHubRepository=$GITHUB_REPO" \
        "CreateOIDCProvider=$CREATE_OIDC" \
        "DeploymentBucketPrefix=$BUCKET_PREFIX" \
      --capabilities CAPABILITY_NAMED_IAM \
      --region "$REGION"
    BOOTSTRAP_OUTPUTS="$(bootstrap_describe_outputs "$BOOTSTRAP_STACK" "$REGION")"
  else
    echo "Skipping bootstrap. You must provide deploy bucket manually."
  fi
fi

if [[ -n "$BOOTSTRAP_OUTPUTS" ]]; then
  sync_bootstrap_stack_from_repo "$BOOTSTRAP_STACK" "$REGION"
  BOOTSTRAP_OUTPUTS="$(bootstrap_describe_outputs "$BOOTSTRAP_STACK" "$REGION")"
fi

S3_BUCKET="$(output_value "$BOOTSTRAP_OUTPUTS" "DeploymentBucketName")"
if [[ -n "$S3_BUCKET" ]]; then
  echo "Detected deploy bucket from bootstrap: $S3_BUCKET"
else
  S3_BUCKET="$(prompt_default "Deployment S3 bucket name" "REPLACE_ME_DEPLOY_BUCKET")"
fi

SUGGESTED_TEST_STACK="$(output_value "$BOOTSTRAP_OUTPUTS" "SuggestedTestStackName")"
SUGGESTED_PROD_STACK="$(output_value "$BOOTSTRAP_OUTPUTS" "SuggestedProdStackName")"
[[ -z "$SUGGESTED_TEST_STACK" ]] && SUGGESTED_TEST_STACK="syncbot-test"
[[ -z "$SUGGESTED_PROD_STACK" ]] && SUGGESTED_PROD_STACK="syncbot-prod"

echo
STAGE="$(prompt_default "Deploy stage (test/prod)" "test")"
if [[ "$STAGE" != "test" && "$STAGE" != "prod" ]]; then
  echo "Error: stage must be 'test' or 'prod'." >&2
  exit 1
fi

DEFAULT_STACK="$SUGGESTED_TEST_STACK"
[[ "$STAGE" == "prod" ]] && DEFAULT_STACK="$SUGGESTED_PROD_STACK"
STACK_NAME="$(prompt_default "App stack name" "$DEFAULT_STACK")"
EXISTING_STACK_STATUS="$(stack_status "$STACK_NAME" "$REGION")"
IS_STACK_UPDATE="false"
EXISTING_STACK_PARAMS=""
PREV_EXISTING_DATABASE_HOST=""
PREV_EXISTING_DATABASE_ADMIN_USER=""
PREV_EXISTING_DATABASE_NETWORK_MODE=""
PREV_EXISTING_DATABASE_SUBNET_IDS_CSV=""
PREV_EXISTING_DATABASE_LAMBDA_SG_ID=""
PREV_DATABASE_ENGINE=""
PREV_DATABASE_SCHEMA=""
PREV_LOG_LEVEL=""
PREV_DATABASE_HOST_IN_USE=""
PREV_STACK_USES_EXISTING_DB="false"
if [[ -n "$EXISTING_STACK_STATUS" && "$EXISTING_STACK_STATUS" != "None" ]]; then
  echo "Detected existing CloudFormation stack: $STACK_NAME ($EXISTING_STACK_STATUS)"
  if ! prompt_yes_no "Continue and update this existing stack?" "y"; then
    echo "Aborted."
    exit 0
  fi
  IS_STACK_UPDATE="true"
  EXISTING_STACK_PARAMS="$(stack_parameters "$STACK_NAME" "$REGION")"
  PREV_EXISTING_DATABASE_HOST="$(stack_param_value "$EXISTING_STACK_PARAMS" "ExistingDatabaseHost")"
  PREV_EXISTING_DATABASE_ADMIN_USER="$(stack_param_value "$EXISTING_STACK_PARAMS" "ExistingDatabaseAdminUser")"
  PREV_EXISTING_DATABASE_NETWORK_MODE="$(stack_param_value "$EXISTING_STACK_PARAMS" "ExistingDatabaseNetworkMode")"
  PREV_EXISTING_DATABASE_SUBNET_IDS_CSV="$(stack_param_value "$EXISTING_STACK_PARAMS" "ExistingDatabaseSubnetIdsCsv")"
  PREV_EXISTING_DATABASE_LAMBDA_SG_ID="$(stack_param_value "$EXISTING_STACK_PARAMS" "ExistingDatabaseLambdaSecurityGroupId")"
  PREV_DATABASE_ENGINE="$(stack_param_value "$EXISTING_STACK_PARAMS" "DatabaseEngine")"
  PREV_DATABASE_SCHEMA="$(stack_param_value "$EXISTING_STACK_PARAMS" "DatabaseSchema")"
  PREV_LOG_LEVEL="$(stack_param_value "$EXISTING_STACK_PARAMS" "LogLevel")"
  EXISTING_STACK_OUTPUTS="$(app_describe_outputs "$STACK_NAME" "$REGION")"
  PREV_DATABASE_HOST_IN_USE="$(output_value "$EXISTING_STACK_OUTPUTS" "DatabaseHostInUse")"
  if [[ -n "$PREV_EXISTING_DATABASE_HOST" ]]; then
    PREV_STACK_USES_EXISTING_DB="true"
  fi
  if [[ -z "$PREV_EXISTING_DATABASE_HOST" && -n "$PREV_DATABASE_HOST_IN_USE" ]]; then
    PREV_EXISTING_DATABASE_HOST="$PREV_DATABASE_HOST_IN_USE"
  fi

  if prompt_yes_no "Skip infrastructure re-deploy and go directly to GitHub Actions setup?" "n"; then
    # Same semantics as DB_MODE (1 = stack RDS, 2 = existing host) for GitHub env vars only.
    GH_DB_MODE="1"
    if [[ "$PREV_STACK_USES_EXISTING_DB" == "true" ]]; then
      GH_DB_MODE="2"
    fi
    GH_DATABASE_SCHEMA="$PREV_DATABASE_SCHEMA"
    [[ -z "$GH_DATABASE_SCHEMA" ]] && GH_DATABASE_SCHEMA="syncbot_${STAGE}"

    # Initialize optional globals used only when user opts into setting secrets in GitHub setup.
    SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET:-}"
    SLACK_CLIENT_SECRET="${SLACK_CLIENT_SECRET:-}"

    echo
    echo "Skipping deploy. Opening GitHub Actions setup for existing stack..."
    [[ -z "$PREV_DATABASE_ENGINE" ]] && PREV_DATABASE_ENGINE="mysql"
    configure_github_actions_aws \
      "$BOOTSTRAP_OUTPUTS" \
      "$BOOTSTRAP_STACK" \
      "$REGION" \
      "$STACK_NAME" \
      "$STAGE" \
      "$GH_DATABASE_SCHEMA" \
      "$GH_DB_MODE" \
      "$PREV_EXISTING_DATABASE_HOST" \
      "$PREV_EXISTING_DATABASE_ADMIN_USER" \
      "${EXISTING_DATABASE_ADMIN_PASSWORD:-}" \
      "$PREV_EXISTING_DATABASE_NETWORK_MODE" \
      "$PREV_EXISTING_DATABASE_SUBNET_IDS_CSV" \
      "$PREV_EXISTING_DATABASE_LAMBDA_SG_ID" \
      "$PREV_DATABASE_ENGINE"
    echo "Done. No infrastructure changes were deployed."
    exit 0
  fi
fi

echo
echo "=== Database Source ==="
# DB_MODE / GH_DB_MODE: 1 = stack-managed RDS in this template; 2 = external or existing RDS host.
DB_MODE_DEFAULT="1"
if [[ "$IS_STACK_UPDATE" == "true" ]]; then
  if [[ "$PREV_STACK_USES_EXISTING_DB" == "true" ]]; then
    EXISTING_DB_LABEL="$PREV_EXISTING_DATABASE_HOST"
    [[ -z "$EXISTING_DB_LABEL" ]] && EXISTING_DB_LABEL="not set"
    DB_MODE_DEFAULT="2"
    echo "  1) Use stack-managed RDS"
    echo "  2) Use external or existing RDS host: $EXISTING_DB_LABEL (default)"
  else
    DB_MODE_DEFAULT="1"
    echo "  1) Use stack-managed RDS (default)"
    echo "  2) Use external or existing RDS host"
  fi
else
  echo "  1) Use stack-managed RDS (default)"
  echo "  2) Use external or existing RDS host"
fi
DB_MODE="$(prompt_default "Choose database source (1 or 2)" "$DB_MODE_DEFAULT")"
if [[ "$DB_MODE" != "1" && "$DB_MODE" != "2" ]]; then
  echo "Error: invalid database mode." >&2
  exit 1
fi
if [[ "$IS_STACK_UPDATE" == "true" && "$PREV_STACK_USES_EXISTING_DB" != "true" && "$DB_MODE" == "2" ]]; then
  echo
  echo "Warning: switching from stack-managed RDS to existing external DB will remove stack-managed RDS/VPC resources."
  if ! prompt_yes_no "Continue with this destructive migration?" "n"; then
    echo "Keeping stack-managed RDS mode for this deploy."
    DB_MODE="1"
  fi
fi

DATABASE_ENGINE="mysql"
DB_ENGINE_DEFAULT="1"
if [[ "$IS_STACK_UPDATE" == "true" && "$PREV_DATABASE_ENGINE" == "postgresql" ]]; then
  DATABASE_ENGINE="postgresql"
  DB_ENGINE_DEFAULT="2"
fi
echo
echo "=== Database Engine ==="
if [[ "$DB_ENGINE_DEFAULT" == "2" ]]; then
  echo "  1) MySQL"
  echo "  2) PostgreSQL (default; detected from current stack)"
else
  echo "  1) MySQL (default)"
  echo "  2) PostgreSQL"
fi
DB_ENGINE_MODE="$(prompt_default "Choose 1 or 2" "$DB_ENGINE_DEFAULT")"
if [[ "$DB_ENGINE_MODE" == "2" ]]; then
  DATABASE_ENGINE="postgresql"
elif [[ "$DB_ENGINE_MODE" != "1" ]]; then
  echo "Error: invalid database engine mode." >&2
  exit 1
fi

echo
echo "=== Slack App Credentials ==="
SLACK_SIGNING_SECRET_SOURCE="prompt"
[[ -n "${SLACK_SIGNING_SECRET:-}" ]] && SLACK_SIGNING_SECRET_SOURCE="env:SLACK_SIGNING_SECRET"
SLACK_CLIENT_SECRET_SOURCE="prompt"
[[ -n "${SLACK_CLIENT_SECRET:-}" ]] && SLACK_CLIENT_SECRET_SOURCE="env:SLACK_CLIENT_SECRET"
SLACK_SIGNING_SECRET="$(required_from_env_or_prompt "SLACK_SIGNING_SECRET" "SlackSigningSecret" "secret")"
SLACK_CLIENT_SECRET="$(required_from_env_or_prompt "SLACK_CLIENT_SECRET" "SlackClientSecret" "secret")"
SLACK_CLIENT_ID="$(required_from_env_or_prompt "SLACK_CLIENT_ID" "SlackClientID")"

ENV_EXISTING_DATABASE_HOST="${EXISTING_DATABASE_HOST:-}"
ENV_EXISTING_DATABASE_ADMIN_USER="${EXISTING_DATABASE_ADMIN_USER:-}"
ENV_EXISTING_DATABASE_ADMIN_PASSWORD="${EXISTING_DATABASE_ADMIN_PASSWORD:-}"
EXISTING_DB_ADMIN_PASSWORD_SOURCE="prompt"
EXISTING_DATABASE_HOST=""
EXISTING_DATABASE_ADMIN_USER=""
EXISTING_DATABASE_ADMIN_PASSWORD=""
EXISTING_DATABASE_NETWORK_MODE="public"
EXISTING_DATABASE_SUBNET_IDS_CSV=""
EXISTING_DATABASE_LAMBDA_SG_ID=""
DATABASE_SCHEMA=""
DATABASE_SCHEMA_DEFAULT="syncbot_${STAGE}"
if [[ "$IS_STACK_UPDATE" == "true" && -n "$PREV_DATABASE_SCHEMA" ]]; then
  DATABASE_SCHEMA_DEFAULT="$PREV_DATABASE_SCHEMA"
fi

if [[ "$DB_MODE" == "2" ]]; then
  echo
  echo "=== Existing Database Host ==="
  EXISTING_DATABASE_HOST_DEFAULT="REPLACE_ME_RDS_HOST"
  [[ -n "$PREV_EXISTING_DATABASE_HOST" ]] && EXISTING_DATABASE_HOST_DEFAULT="$PREV_EXISTING_DATABASE_HOST"
  EXISTING_DATABASE_ADMIN_USER_DEFAULT="admin"
  [[ -n "$PREV_EXISTING_DATABASE_ADMIN_USER" ]] && EXISTING_DATABASE_ADMIN_USER_DEFAULT="$PREV_EXISTING_DATABASE_ADMIN_USER"

  if [[ -n "$ENV_EXISTING_DATABASE_HOST" ]]; then
    echo "Using ExistingDatabaseHost from environment variable EXISTING_DATABASE_HOST."
    EXISTING_DATABASE_HOST="$ENV_EXISTING_DATABASE_HOST"
  else
    EXISTING_DATABASE_HOST="$(prompt_default "ExistingDatabaseHost (RDS endpoint hostname)" "$EXISTING_DATABASE_HOST_DEFAULT")"
  fi

  DETECTED_ADMIN_USER=""
  DETECTED_ADMIN_SECRET_ARN=""
  if [[ "$IS_STACK_UPDATE" == "true" ]]; then
    RDS_ADMIN_LOOKUP="$(rds_lookup_admin_defaults "$EXISTING_DATABASE_HOST" "$REGION")"
    if [[ -n "$RDS_ADMIN_LOOKUP" && "$RDS_ADMIN_LOOKUP" != "None" ]]; then
      IFS=$'\t' read -r DETECTED_ADMIN_USER DETECTED_ADMIN_SECRET_ARN <<< "$RDS_ADMIN_LOOKUP"
      [[ "$DETECTED_ADMIN_USER" == "None" ]] && DETECTED_ADMIN_USER=""
      [[ "$DETECTED_ADMIN_SECRET_ARN" == "None" ]] && DETECTED_ADMIN_SECRET_ARN=""
    fi
  fi

  if [[ -z "$EXISTING_DATABASE_ADMIN_USER_DEFAULT" || "$EXISTING_DATABASE_ADMIN_USER_DEFAULT" == "admin" ]]; then
    [[ -n "$DETECTED_ADMIN_USER" ]] && EXISTING_DATABASE_ADMIN_USER_DEFAULT="$DETECTED_ADMIN_USER"
  fi
  if [[ -n "$ENV_EXISTING_DATABASE_ADMIN_USER" ]]; then
    echo "Using ExistingDatabaseAdminUser from environment variable EXISTING_DATABASE_ADMIN_USER."
    EXISTING_DATABASE_ADMIN_USER="$ENV_EXISTING_DATABASE_ADMIN_USER"
  else
    EXISTING_DATABASE_ADMIN_USER="$(prompt_default "ExistingDatabaseAdminUser" "$EXISTING_DATABASE_ADMIN_USER_DEFAULT")"
  fi

  if [[ -n "$ENV_EXISTING_DATABASE_ADMIN_PASSWORD" ]]; then
    echo "Using ExistingDatabaseAdminPassword from environment variable EXISTING_DATABASE_ADMIN_PASSWORD."
    EXISTING_DATABASE_ADMIN_PASSWORD="$ENV_EXISTING_DATABASE_ADMIN_PASSWORD"
    EXISTING_DB_ADMIN_PASSWORD_SOURCE="env:EXISTING_DATABASE_ADMIN_PASSWORD"
  else
    if [[ "$IS_STACK_UPDATE" == "true" && -n "$DETECTED_ADMIN_SECRET_ARN" ]]; then
      EXISTING_DATABASE_ADMIN_PASSWORD="$(secret_password_by_id "$DETECTED_ADMIN_SECRET_ARN" "$REGION" 2>/dev/null || true)"
      if [[ -n "$EXISTING_DATABASE_ADMIN_PASSWORD" ]]; then
        echo "Detected existing DB admin password from AWS Secrets Manager for re-deploy."
        EXISTING_DB_ADMIN_PASSWORD_SOURCE="aws-secret:$DETECTED_ADMIN_SECRET_ARN"
      fi
    fi
    if [[ -z "$EXISTING_DATABASE_ADMIN_PASSWORD" ]]; then
      echo "Existing DB admin credentials couldn't be auto-detected. Please enter them manually."
      EXISTING_DATABASE_ADMIN_PASSWORD="$(prompt_secret_required "ExistingDatabaseAdminPassword")"
      EXISTING_DB_ADMIN_PASSWORD_SOURCE="prompt"
    fi
  fi

  DATABASE_SCHEMA="$(prompt_default "DatabaseSchema" "$DATABASE_SCHEMA_DEFAULT")"

  if [[ -z "$EXISTING_DATABASE_HOST" || "$EXISTING_DATABASE_HOST" == REPLACE_ME* ]]; then
    echo "Error: valid ExistingDatabaseHost is required for existing DB mode." >&2
    exit 1
  fi

  RDS_LOOKUP="$(rds_lookup_network_defaults "$EXISTING_DATABASE_HOST" "$REGION")"
  DETECTED_PUBLIC=""
  DETECTED_SUBNETS=""
  DETECTED_SGS=""
  DETECTED_VPC=""
  DETECTED_DB_ID=""
  if [[ -n "$RDS_LOOKUP" && "$RDS_LOOKUP" != "None" ]]; then
    IFS=$'\t' read -r DETECTED_PUBLIC DETECTED_SUBNETS DETECTED_SGS DETECTED_VPC DETECTED_DB_ID <<< "$RDS_LOOKUP"
    [[ "$DETECTED_PUBLIC" == "None" ]] && DETECTED_PUBLIC=""
    [[ "$DETECTED_SUBNETS" == "None" ]] && DETECTED_SUBNETS=""
    [[ "$DETECTED_SGS" == "None" ]] && DETECTED_SGS=""
    [[ "$DETECTED_VPC" == "None" ]] && DETECTED_VPC=""
    [[ "$DETECTED_DB_ID" == "None" ]] && DETECTED_DB_ID=""
    echo
    echo "Detected RDS instance details:"
    [[ -n "$DETECTED_DB_ID" ]] && echo "  DB instance:   $DETECTED_DB_ID"
    [[ -n "$DETECTED_VPC" ]] && echo "  VPC:           $DETECTED_VPC"
    [[ -n "$DETECTED_PUBLIC" ]] && echo "  Public access: $DETECTED_PUBLIC"
  else
    echo
    echo "Could not auto-detect existing RDS network settings from host."
    echo "You can still continue by entering network values manually."
  fi

  DEFAULT_EXISTING_DB_NETWORK_MODE="public"
  if [[ -n "$PREV_EXISTING_DATABASE_NETWORK_MODE" ]]; then
    DEFAULT_EXISTING_DB_NETWORK_MODE="$PREV_EXISTING_DATABASE_NETWORK_MODE"
  fi
  if [[ "$DETECTED_PUBLIC" == "False" ]]; then
    DEFAULT_EXISTING_DB_NETWORK_MODE="private"
  fi
  EXISTING_DATABASE_NETWORK_MODE="$(prompt_default "Existing DB network mode (public/private)" "$DEFAULT_EXISTING_DB_NETWORK_MODE")"
  if [[ "$EXISTING_DATABASE_NETWORK_MODE" != "public" && "$EXISTING_DATABASE_NETWORK_MODE" != "private" ]]; then
    echo "Error: existing DB network mode must be 'public' or 'private'." >&2
    exit 1
  fi

  if [[ "$EXISTING_DATABASE_NETWORK_MODE" == "private" ]]; then
    AUTO_PRIVATE_SUBNETS=""
    if [[ -n "$DETECTED_VPC" ]]; then
      AUTO_PRIVATE_SUBNETS="$(discover_private_lambda_subnets_for_db_vpc "$DETECTED_VPC" "$REGION")"
      if [[ -n "$AUTO_PRIVATE_SUBNETS" ]]; then
        echo "Detected private Lambda subnet candidates (NAT-routed): $AUTO_PRIVATE_SUBNETS"
      fi
    fi

    DEFAULT_SUBNETS="$AUTO_PRIVATE_SUBNETS"
    [[ -z "$DEFAULT_SUBNETS" && -n "$PREV_EXISTING_DATABASE_SUBNET_IDS_CSV" ]] && DEFAULT_SUBNETS="$PREV_EXISTING_DATABASE_SUBNET_IDS_CSV"
    [[ -z "$DEFAULT_SUBNETS" ]] && DEFAULT_SUBNETS="$DETECTED_SUBNETS"
    [[ -z "$DEFAULT_SUBNETS" ]] && DEFAULT_SUBNETS="REPLACE_ME_SUBNET_1,REPLACE_ME_SUBNET_2"
    DEFAULT_SG="${DETECTED_SGS%%,*}"
    [[ -n "$PREV_EXISTING_DATABASE_LAMBDA_SG_ID" ]] && DEFAULT_SG="$PREV_EXISTING_DATABASE_LAMBDA_SG_ID"
    [[ -z "$DEFAULT_SG" ]] && DEFAULT_SG="REPLACE_ME_LAMBDA_SG_ID"

    echo
    echo "Private DB mode selected: Lambdas will run in VPC."
    echo "Note: app Lambda needs Internet egress (usually NAT) to call Slack APIs."
    EXISTING_DATABASE_SUBNET_IDS_CSV="$(prompt_default "ExistingDatabaseSubnetIdsCsv (comma-separated)" "$DEFAULT_SUBNETS")"
    EXISTING_DATABASE_LAMBDA_SG_ID="$(prompt_default "ExistingDatabaseLambdaSecurityGroupId" "$DEFAULT_SG")"

    if [[ -z "$EXISTING_DATABASE_SUBNET_IDS_CSV" || "$EXISTING_DATABASE_SUBNET_IDS_CSV" == REPLACE_ME* ]]; then
      echo "Error: valid ExistingDatabaseSubnetIdsCsv is required for private mode." >&2
      exit 1
    fi
    if [[ -z "$EXISTING_DATABASE_LAMBDA_SG_ID" || "$EXISTING_DATABASE_LAMBDA_SG_ID" == REPLACE_ME* ]]; then
      echo "Error: valid ExistingDatabaseLambdaSecurityGroupId is required for private mode." >&2
      exit 1
    fi

    echo
    echo "Running private-connectivity preflight checks..."
    if ! validate_private_existing_db_connectivity \
      "$REGION" \
      "$DATABASE_ENGINE" \
      "$EXISTING_DATABASE_SUBNET_IDS_CSV" \
      "$EXISTING_DATABASE_LAMBDA_SG_ID" \
      "$DETECTED_VPC" \
      "$DETECTED_SGS" \
      "$EXISTING_DATABASE_HOST"; then
      echo "Fix network settings and rerun deploy." >&2
      exit 1
    fi
  fi
else
  echo
  echo "=== New RDS Database ==="
  echo "New RDS mode uses:"
  echo "  - admin user: syncbot_admin_${STAGE} (password auto-generated)"
  echo "  - app user:   syncbot_user_${STAGE} (password auto-generated)"
  DATABASE_SCHEMA="$(prompt_default "DatabaseSchema" "$DATABASE_SCHEMA_DEFAULT")"
fi

TOKEN_OVERRIDE="$(prompt_default "TokenEncryptionKeyOverride (optional for disaster recovery; leave blank for normal deploy)" "")"
EXISTING_TOKEN_SECRET_ARN=""
TOKEN_SECRET_NAME="syncbot-${STAGE}-token-encryption-key"
APP_DB_SECRET_NAME="syncbot-${STAGE}-app-db-password"
if [[ -z "$TOKEN_OVERRIDE" ]]; then
  DETECTED_TOKEN_SECRET_ARN="$(secret_arn_by_name "$TOKEN_SECRET_NAME" "$REGION")"
  if [[ -n "$DETECTED_TOKEN_SECRET_ARN" && "$DETECTED_TOKEN_SECRET_ARN" != "None" ]]; then
    echo "Detected existing token secret: $TOKEN_SECRET_NAME"
    if prompt_yes_no "Reuse detected secret ARN for this deploy?" "y"; then
      EXISTING_TOKEN_SECRET_ARN="$DETECTED_TOKEN_SECRET_ARN"
    fi
  fi
fi

LOG_LEVEL_DEFAULT="INFO"
if [[ "$IS_STACK_UPDATE" == "true" && -n "$PREV_LOG_LEVEL" ]]; then
  LOG_LEVEL_DEFAULT="$PREV_LOG_LEVEL"
fi

echo
echo "=== Log Level ==="
LOG_LEVEL="$(prompt_log_level "$LOG_LEVEL_DEFAULT")"

echo
echo "=== Deploy Summary ==="
echo "Region:           $REGION"
echo "Stack:            $STACK_NAME"
echo "Stage:            $STAGE"
echo "Log level:        $LOG_LEVEL"
echo "Deploy bucket:    $S3_BUCKET"
if [[ "$DB_MODE" == "2" ]]; then
  echo "DB mode:          existing host"
  echo "DB engine:        $DATABASE_ENGINE"
  echo "DB host:          $EXISTING_DATABASE_HOST"
  echo "DB network:       $EXISTING_DATABASE_NETWORK_MODE"
  if [[ "$EXISTING_DATABASE_NETWORK_MODE" == "private" ]]; then
    echo "DB subnets:       $EXISTING_DATABASE_SUBNET_IDS_CSV"
    echo "Lambda SG:        $EXISTING_DATABASE_LAMBDA_SG_ID"
  fi
  echo "DB schema:        $DATABASE_SCHEMA"
else
  echo "DB mode:          create new RDS"
  echo "DB engine:        $DATABASE_ENGINE"
  echo "DB admin user:    syncbot_admin_${STAGE} (auto password)"
  echo "DB app user:      syncbot_user_${STAGE} (auto password)"
  echo "DB schema:        $DATABASE_SCHEMA"
fi
if [[ -n "$TOKEN_OVERRIDE" ]]; then
  echo "DR key override:  YES (TokenEncryptionKeyOverride)"
else
  echo "DR key override:  NO (auto-generated TOKEN_ENCRYPTION_KEY)"
  if [[ -n "$EXISTING_TOKEN_SECRET_ARN" ]]; then
    echo "Token secret:     Reusing existing secret ARN"
  fi
fi
if [[ -n "$APP_DB_PASSWORD_OVERRIDE" ]]; then
  echo "App DB secret:    Reusing prior app DB password value"
fi
echo

if ! prompt_yes_no "Proceed with build + deploy?" "y"; then
  echo "Aborted."
  exit 0
fi

preflight_secrets_manager_access "$REGION" "$TOKEN_SECRET_NAME" "$APP_DB_SECRET_NAME" "$EXISTING_TOKEN_SECRET_ARN"

handle_orphan_app_db_secret_on_create "$EXISTING_STACK_STATUS" "$APP_DB_SECRET_NAME" "$REGION"

handle_unhealthy_stack_state "$STACK_NAME" "$REGION"

echo
echo "=== SAM Build ==="
echo "Building app..."
sam build -t "$APP_TEMPLATE" --use-container

PARAMS=(
  "Stage=$STAGE"
  "DatabaseEngine=$DATABASE_ENGINE"
  "SlackSigningSecret=$SLACK_SIGNING_SECRET"
  "SlackClientSecret=$SLACK_CLIENT_SECRET"
  "DatabaseSchema=$DATABASE_SCHEMA"
  "LogLevel=$LOG_LEVEL"
)

if [[ -n "$SLACK_CLIENT_ID" ]]; then
  PARAMS+=("SlackClientID=$SLACK_CLIENT_ID")
fi

if [[ "$DB_MODE" == "2" ]]; then
  PARAMS+=(
    "ExistingDatabaseHost=$EXISTING_DATABASE_HOST"
    "ExistingDatabaseAdminUser=$EXISTING_DATABASE_ADMIN_USER"
    "ExistingDatabaseAdminPassword=$EXISTING_DATABASE_ADMIN_PASSWORD"
    "ExistingDatabaseNetworkMode=$EXISTING_DATABASE_NETWORK_MODE"
  )
  if [[ "$EXISTING_DATABASE_NETWORK_MODE" == "private" ]]; then
    PARAMS+=(
      "ExistingDatabaseSubnetIdsCsv=$EXISTING_DATABASE_SUBNET_IDS_CSV"
      "ExistingDatabaseLambdaSecurityGroupId=$EXISTING_DATABASE_LAMBDA_SG_ID"
    )
  fi
else
  # Explicitly clear existing-host parameters on updates to avoid stale previous values.
  PARAMS+=(
    "ExistingDatabaseHost="
    "ExistingDatabaseAdminUser="
    "ExistingDatabaseAdminPassword="
    "ExistingDatabaseNetworkMode=public"
    "ExistingDatabaseSubnetIdsCsv="
    "ExistingDatabaseLambdaSecurityGroupId="
  )
fi

if [[ -n "$TOKEN_OVERRIDE" ]]; then
  PARAMS+=("TokenEncryptionKeyOverride=$TOKEN_OVERRIDE")
fi
if [[ -n "$APP_DB_PASSWORD_OVERRIDE" ]]; then
  PARAMS+=("AppDbPasswordOverride=$APP_DB_PASSWORD_OVERRIDE")
fi
if [[ -n "$EXISTING_TOKEN_SECRET_ARN" ]]; then
  PARAMS+=("ExistingTokenEncryptionKeySecretArn=$EXISTING_TOKEN_SECRET_ARN")
fi

echo "=== SAM Deploy ==="
echo "Deploying stack..."
sam deploy \
  -t .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --s3-bucket "$S3_BUCKET" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAMS[@]}"

APP_OUTPUTS="$(app_describe_outputs "$STACK_NAME" "$REGION")"
SYNCBOT_API_URL="$(output_value "$APP_OUTPUTS" "SyncBotApiUrl")"
SYNCBOT_INSTALL_URL="$(output_value "$APP_OUTPUTS" "SyncBotInstallUrl")"

echo
echo "Deploy complete."
generate_stage_slack_manifest "$STAGE" "$SYNCBOT_API_URL" "$SYNCBOT_INSTALL_URL"
if [[ -n "$SLACK_MANIFEST_GENERATED_PATH" ]]; then
  if prompt_yes_no "Configure Slack app via Slack API now (create or update from generated manifest)?" "n"; then
    slack_api_configure_from_manifest "$SLACK_MANIFEST_GENERATED_PATH" "$SYNCBOT_INSTALL_URL"
  fi
fi

# Prepare secret metadata/value so receipt and final backup output stay in sync.
if [[ -n "$TOKEN_OVERRIDE" ]]; then
  RECEIPT_TOKEN_SECRET_ID="TokenEncryptionKeyOverride"
  TOKEN_SECRET_ID="TokenEncryptionKeyOverride"
  TOKEN_SECRET_VALUE="$TOKEN_OVERRIDE"
else
  TOKEN_SECRET_ID="$TOKEN_SECRET_NAME"
  if [[ -n "$EXISTING_TOKEN_SECRET_ARN" ]]; then
    TOKEN_SECRET_ID="$EXISTING_TOKEN_SECRET_ARN"
  fi
  TOKEN_SECRET_VALUE="$(secret_value_by_id "$TOKEN_SECRET_ID" "$REGION")"
  RECEIPT_TOKEN_SECRET_ID="$TOKEN_SECRET_ID"
fi

APP_DB_SECRET_VALUE="$(secret_value_by_id "$APP_DB_SECRET_NAME" "$REGION")"
# RECEIPT_APP_DB_* mirror the deploy artifacts.
RECEIPT_APP_DB_SECRET_NAME="$APP_DB_SECRET_NAME"

if prompt_yes_no "Set up GitHub Actions configuration now?" "n"; then
  configure_github_actions_aws \
    "$BOOTSTRAP_OUTPUTS" \
    "$BOOTSTRAP_STACK" \
    "$REGION" \
    "$STACK_NAME" \
    "$STAGE" \
    "$DATABASE_SCHEMA" \
    "$DB_MODE" \
    "$EXISTING_DATABASE_HOST" \
    "$EXISTING_DATABASE_ADMIN_USER" \
    "$EXISTING_DATABASE_ADMIN_PASSWORD" \
    "$EXISTING_DATABASE_NETWORK_MODE" \
    "$EXISTING_DATABASE_SUBNET_IDS_CSV" \
    "$EXISTING_DATABASE_LAMBDA_SG_ID" \
    "$DATABASE_ENGINE"
fi

write_deploy_receipt \
  "aws" \
  "$STAGE" \
  "$STACK_NAME" \
  "$REGION" \
  "$SYNCBOT_API_URL" \
  "$SYNCBOT_INSTALL_URL" \
  "$SLACK_MANIFEST_GENERATED_PATH"

echo
echo "=== Backup Secrets (Disaster Recovery) ==="
# IMPORTANT: This deploy script must always print plaintext backup secrets at the end.
# Do not remove/redact this section; operators rely on it for DR copy-out immediately after deploy.
echo "Copy these values now and store them in your secure disaster-recovery vault."

echo "- TOKEN_ENCRYPTION_KEY source: $TOKEN_SECRET_ID"
if [[ -n "$TOKEN_SECRET_VALUE" && "$TOKEN_SECRET_VALUE" != "None" ]]; then
  echo "  TOKEN_ENCRYPTION_KEY: $TOKEN_SECRET_VALUE"
else
  echo "  TOKEN_ENCRYPTION_KEY: <UNAVAILABLE - check Secrets Manager access and retrieve manually>"
fi

echo "- DATABASE_PASSWORD source: $APP_DB_SECRET_NAME"
if [[ -n "$APP_DB_SECRET_VALUE" && "$APP_DB_SECRET_VALUE" != "None" ]]; then
  echo "  DATABASE_PASSWORD: $APP_DB_SECRET_VALUE"
else
  echo "  DATABASE_PASSWORD: <UNAVAILABLE - check Secrets Manager access and retrieve manually>"
fi
