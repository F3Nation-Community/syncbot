#!/usr/bin/env bash
# Interactive GCP deploy helper (Terraform). Run from repo root:
#   ./infra/gcp/scripts/deploy.sh
# Or via: ./deploy.sh gcp
#
# Phases (main path):
#   1) Prerequisites (terraform, gcloud, python3, curl)
#   2) Project, region, stage; detect existing Cloud Run service
#   3) Database source: USE_EXISTING true = external DB only (skip Cloud SQL); false = Terraform-managed DB path
#   4) Container image var for Cloud Run
#   5) terraform init / plan / apply
#   6) Stage Slack manifest, optional Slack API configure
#   7) Deploy receipt, print-bootstrap-outputs, optional GitHub Actions vars
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GCP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SLACK_MANIFEST_GENERATED_PATH=""

# shellcheck source=/dev/null
source "$REPO_ROOT/deploy.sh"

prereqs_require_cmd terraform prereqs_hint_terraform
prereqs_require_cmd gcloud prereqs_hint_gcloud
prereqs_require_cmd python3 prereqs_hint_python3
prereqs_require_cmd curl prereqs_hint_curl

prereqs_print_cli_status_matrix "GCP" terraform gcloud python3 curl

prompt_line() {
  local p="$1"
  local d="${2:-}"
  local v
  if [[ -n "$d" ]]; then
    read -r -p "$p [$d]: " v
    echo "${v:-$d}"
  else
    read -r -p "$p: " v
    echo "$v"
  fi
}

prompt_secret() {
  local p="$1"
  local v
  read -r -s -p "$p: " v
  printf '\n' >&2
  echo "$v"
}

prompt_required() {
  local p="$1"
  local v
  while true; do
    read -r -p "$p: " v
    if [[ -n "$v" ]]; then
      echo "$v"
      return 0
    fi
    echo "Error: $p is required." >&2
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
    while true; do
      env_value="$(prompt_secret "$prompt")"
      if [[ -n "$env_value" ]]; then
        echo "$env_value"
        return 0
      fi
      echo "Error: $prompt is required." >&2
    done
  fi
  prompt_required "$prompt"
}

prompt_yn() {
  local p="$1"
  local def="${2:-y}"
  local a
  local hint="y/N"
  [[ "$def" == "y" ]] && hint="Y/n"
  read -r -p "$p [$hint]: " a
  if [[ -z "$a" ]]; then
    a="$def"
  fi
  [[ "$a" =~ ^[Yy]$ ]]
}

ensure_gcloud_authenticated() {
  local active_account
  active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
  if [[ -n "$active_account" ]]; then
    return 0
  fi
  echo "gcloud is not authenticated."
  if prompt_yn "Run 'gcloud auth login' now?" "y"; then
    gcloud auth login || true
  fi
  active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
  if [[ -z "$active_account" ]]; then
    echo "Unable to authenticate gcloud. Run 'gcloud auth login' and rerun."
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
  if prompt_yn "Run 'gh auth login' now?" "y"; then
    gh auth login || true
  fi
  if gh auth status >/dev/null 2>&1; then
    return 0
  fi
  echo "gh authentication is still missing. Skipping automatic GitHub setup."
  return 1
}

cloud_sql_instance_exists() {
  local project_id="$1"
  local instance_name="$2"
  gcloud sql instances describe "$instance_name" \
    --project "$project_id" \
    --format='value(name)' >/dev/null 2>&1
}

cloud_run_env_value() {
  local project_id="$1"
  local region="$2"
  local service_name="$3"
  local env_key="$4"
  gcloud run services describe "$service_name" \
    --project "$project_id" \
    --region "$region" \
    --format=json 2>/dev/null | python3 - "$env_key" <<'PY'
import json
import sys

env_key = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)

containers = (data.get("spec", {}) or {}).get("template", {}).get("spec", {}).get("containers", [])
for c in containers:
    for e in c.get("env", []) or []:
        if e.get("name") == env_key:
            print(e.get("value", ""))
            raise SystemExit(0)
print("")
PY
}

cloud_run_image_value() {
  local project_id="$1"
  local region="$2"
  local service_name="$3"
  gcloud run services describe "$service_name" \
    --project "$project_id" \
    --region "$region" \
    --format='value(spec.template.spec.containers[0].image)' 2>/dev/null || true
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
  app_id="$(prompt_line "Slack App ID (optional; blank = create new app)" "${SLACK_APP_ID:-}")"
  team_id="$(prompt_line "Slack Team ID (optional; usually blank)" "${SLACK_TEAM_ID:-}")"

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
    echo "Could not determine API URL from service outputs. Skipping Slack manifest generation."
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
EOF

  echo "Deploy receipt written: $receipt_path"
}

configure_github_actions_gcp() {
  # $1 GCP project ID
  # $2 GCP region (e.g. us-central1)
  # $3 Path to infra/gcp (terraform directory)
  # $4 Deploy stage (test|prod) — GitHub environment name
  local gcp_project_id="$1"
  local gcp_region="$2"
  local terraform_dir="$3"
  local deploy_stage="$4"
  local deploy_sa_email artifact_registry_url service_url
  local repo env_name
  env_name="$deploy_stage"

  deploy_sa_email="$(cd "$terraform_dir" && terraform output -raw deploy_service_account_email 2>/dev/null || true)"
  artifact_registry_url="$(cd "$terraform_dir" && terraform output -raw artifact_registry_repository 2>/dev/null || true)"
  service_url="$(cd "$terraform_dir" && terraform output -raw service_url 2>/dev/null || true)"

  echo
  echo "=== GitHub Actions (GCP) ==="
  echo "Detected project:         $gcp_project_id"
  echo "Detected region:          $gcp_region"
  echo "Detected service account: $deploy_sa_email"
  echo "Detected artifact repo:   $artifact_registry_url"
  echo "Detected service URL:     $service_url"
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [[ -z "$repo" ]]; then
    repo="$(prompt_line "GitHub repository (owner/repo) for environment setup" "")"
  else
    echo "Detected GitHub repository: $repo"
  fi

  if ! ensure_gh_authenticated; then
    echo
    echo "Set these GitHub Actions Variables manually:"
    echo "  GCP_PROJECT_ID   = $gcp_project_id"
    echo "  GCP_REGION       = $gcp_region"
    echo "  GCP_SERVICE_ACCOUNT = $deploy_sa_email"
    echo "  DEPLOY_TARGET    = gcp"
    echo "Also set GCP_WORKLOAD_IDENTITY_PROVIDER for deploy-gcp.yml."
    return 0
  fi

  if prompt_yn "Create/update GitHub environments 'test' and 'prod' now?" "y"; then
    gh api -X PUT "repos/$repo/environments/test" >/dev/null
    gh api -X PUT "repos/$repo/environments/prod" >/dev/null
    echo "GitHub environments ensured: test, prod."
  fi

  if prompt_yn "Set repo variables with gh now (GCP_PROJECT_ID, GCP_REGION, GCP_SERVICE_ACCOUNT, DEPLOY_TARGET=gcp)?" "y"; then
    gh variable set GCP_PROJECT_ID --body "$gcp_project_id"
    gh variable set GCP_REGION --body "$gcp_region"
    [[ -n "$deploy_sa_email" ]] && gh variable set GCP_SERVICE_ACCOUNT --body "$deploy_sa_email"
    gh variable set DEPLOY_TARGET --body "gcp"
    echo "GitHub repository variables updated."
    echo "Remember to set GCP_WORKLOAD_IDENTITY_PROVIDER."
  fi

  if prompt_yn "Set environment variable STAGE_NAME for '$env_name' now?" "y"; then
    gh variable set STAGE_NAME --env "$env_name" --body "$deploy_stage"
    echo "Environment variable STAGE_NAME updated for '$env_name'."
  fi
}

echo "=== SyncBot GCP Deploy ==="
echo "Working directory: $GCP_DIR"
echo

echo "=== Project And Region ==="
PROJECT_ID="$(prompt_line "GCP project_id" "${GCP_PROJECT_ID:-}")"
if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: project_id is required." >&2
  exit 1
fi

REGION="$(prompt_line "GCP region" "${GCP_REGION:-us-central1}")"
ensure_gcloud_authenticated
gcloud config set project "$PROJECT_ID" >/dev/null 2>&1 || true
STAGE="$(prompt_line "Stage (test/prod)" "${STAGE:-test}")"
if [[ "$STAGE" != "test" && "$STAGE" != "prod" ]]; then
  echo "Error: stage must be 'test' or 'prod'." >&2
  exit 1
fi
SERVICE_NAME="syncbot-${STAGE}"
EXISTING_SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(status.url)' 2>/dev/null || true)"
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  echo "Detected existing Cloud Run service: $SERVICE_NAME"
  if ! prompt_yn "Continue and update this existing deployment?" "y"; then
    echo "Aborted."
    exit 0
  fi
fi

echo
echo "=== Database Source ==="
# USE_EXISTING=true: point Terraform at an external DB only (use_existing_database); skip creating Cloud SQL.
# USE_EXISTING_DEFAULT: y/n default for the prompt when redeploying without a managed instance for this stage.
USE_EXISTING="false"
USE_EXISTING_DEFAULT="n"
DB_INSTANCE_NAME="${SERVICE_NAME}-db"
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  if cloud_sql_instance_exists "$PROJECT_ID" "$DB_INSTANCE_NAME"; then
    USE_EXISTING_DEFAULT="n"
    echo "Detected managed Cloud SQL instance: $DB_INSTANCE_NAME"
  else
    USE_EXISTING_DEFAULT="y"
    echo "No managed Cloud SQL instance found for stage; defaulting to existing DB mode."
  fi
fi
if prompt_yn "Use existing database host (skip Cloud SQL creation)?" "$USE_EXISTING_DEFAULT"; then
  USE_EXISTING="true"
fi

EXISTING_HOST=""
EXISTING_SCHEMA=""
EXISTING_USER=""
DETECTED_EXISTING_HOST=""
DETECTED_EXISTING_SCHEMA=""
DETECTED_EXISTING_USER=""
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  DETECTED_EXISTING_HOST="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_HOST")"
  DETECTED_EXISTING_SCHEMA="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_SCHEMA")"
  DETECTED_EXISTING_USER="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_USER")"
fi
if [[ "$USE_EXISTING" == "true" ]]; then
  EXISTING_HOST="$(prompt_line "Existing DB host" "$DETECTED_EXISTING_HOST")"
  EXISTING_SCHEMA="$(prompt_line "Database schema name" "${DETECTED_EXISTING_SCHEMA:-syncbot}")"
  EXISTING_USER="$(prompt_line "Database user" "$DETECTED_EXISTING_USER")"
  if [[ -z "$EXISTING_HOST" ]]; then
    echo "Error: Existing DB host is required when using existing database mode." >&2
    exit 1
  fi
  if [[ -z "$EXISTING_USER" ]]; then
    echo "Error: Database user is required when using existing database mode." >&2
    exit 1
  fi
fi

DETECTED_CLOUD_IMAGE=""
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  DETECTED_CLOUD_IMAGE="$(cloud_run_image_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME")"
fi
CLOUD_IMAGE="$(prompt_line "cloud_run_image (blank = placeholder until first build)" "$DETECTED_CLOUD_IMAGE")"

DETECTED_LOG_LEVEL=""
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  DETECTED_LOG_LEVEL="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "LOG_LEVEL")"
fi
LOG_LEVEL_DEFAULT="INFO"
if [[ -n "$DETECTED_LOG_LEVEL" ]]; then
  LOG_LEVEL_DEFAULT="$(normalize_log_level "$DETECTED_LOG_LEVEL")"
  if ! is_valid_log_level "$LOG_LEVEL_DEFAULT"; then
    LOG_LEVEL_DEFAULT="INFO"
  fi
fi

echo
echo "=== Log Level ==="
LOG_LEVEL="$(prompt_log_level "$LOG_LEVEL_DEFAULT")"

echo
echo "=== Terraform Init ==="
echo "Running: terraform init"
cd "$GCP_DIR"
terraform init

VARS=(
  "-var=project_id=$PROJECT_ID"
  "-var=region=$REGION"
  "-var=stage=$STAGE"
  "-var=log_level=$LOG_LEVEL"
)

if [[ "$USE_EXISTING" == "true" ]]; then
  VARS+=("-var=use_existing_database=true")
  VARS+=("-var=existing_db_host=$EXISTING_HOST")
  VARS+=("-var=existing_db_schema=$EXISTING_SCHEMA")
  VARS+=("-var=existing_db_user=$EXISTING_USER")
else
  VARS+=("-var=use_existing_database=false")
fi

if [[ -n "$CLOUD_IMAGE" ]]; then
  VARS+=("-var=cloud_run_image=$CLOUD_IMAGE")
fi

echo
echo "Log level: $LOG_LEVEL"
echo
echo "=== Terraform Plan ==="
if ! prompt_yn "Run terraform plan?" "y"; then
  echo "Skipped. Run manually from infra/gcp:"
  echo "  terraform plan ${VARS[*]}"
  exit 0
fi

terraform plan "${VARS[@]}"

echo
echo "=== Terraform Apply ==="
if ! prompt_yn "Apply changes (terraform apply)?" "y"; then
  echo "Aborted."
  exit 0
fi

terraform apply -auto-approve "${VARS[@]}"

echo
echo "=== Apply Complete ==="
SERVICE_URL="$(terraform output -raw service_url 2>/dev/null || true)"
SYNCBOT_API_URL=""
SYNCBOT_INSTALL_URL=""
if [[ -n "$SERVICE_URL" ]]; then
  SYNCBOT_API_URL="${SERVICE_URL%/}/slack/events"
  SYNCBOT_INSTALL_URL="${SERVICE_URL%/}/slack/install"
fi
generate_stage_slack_manifest "$STAGE" "$SYNCBOT_API_URL" "$SYNCBOT_INSTALL_URL"
if [[ -n "$SLACK_MANIFEST_GENERATED_PATH" ]]; then
  if prompt_yn "Configure Slack app via Slack API now (create or update from generated manifest)?" "n"; then
    slack_api_configure_from_manifest "$SLACK_MANIFEST_GENERATED_PATH" "$SYNCBOT_INSTALL_URL"
  fi
fi

write_deploy_receipt \
  "gcp" \
  "$STAGE" \
  "$PROJECT_ID" \
  "$REGION" \
  "$SERVICE_URL" \
  "$SYNCBOT_INSTALL_URL" \
  "$SLACK_MANIFEST_GENERATED_PATH"

echo "Next:"
echo "  1) Set Secret Manager values for Slack (see infra/gcp/README.md)."
echo "  2) Build and push container image; update cloud_run_image and re-apply if needed."
echo "  3) Run: ./infra/gcp/scripts/print-bootstrap-outputs.sh"
bash "$SCRIPT_DIR/print-bootstrap-outputs.sh" || true

if prompt_yn "Set up GitHub Actions configuration now?" "n"; then
  configure_github_actions_gcp "$PROJECT_ID" "$REGION" "$GCP_DIR" "$STAGE"
fi
