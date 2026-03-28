#!/usr/bin/env bash
# Interactive GCP deploy helper (Terraform). Run from repo root:
#   ./infra/gcp/scripts/deploy.sh
# Or via: ./deploy.sh gcp
#
# Phases (main path):
#   1) Prerequisites (terraform, gcloud, python3, curl)
#   2) Project, region, stage; detect existing Cloud Run service
#   3) Deploy Tasks: multi-select menu (build/deploy, CI/CD, Slack API, backup secrets)
#   4) Configuration (if build/deploy): database, image, log level, terraform init/plan/apply
#   5) Post-tasks: Slack manifest/API, deploy receipt, print-bootstrap-outputs, GitHub Actions, DR secrets
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GCP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SLACK_MANIFEST_GENERATED_PATH=""

# shellcheck source=/dev/null
source "$REPO_ROOT/deploy.sh"

echo "=== Prerequisites ==="
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

ensure_gcloud_adc_authenticated() {
  if gcloud auth application-default print-access-token >/dev/null 2>&1; then
    return 0
  fi

  echo "Application Default Credentials (ADC) are not configured."
  if prompt_yn "Run 'gcloud auth application-default login' now?" "y"; then
    gcloud auth application-default login || true
  fi

  if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo "Unable to configure ADC. Run 'gcloud auth application-default login' and rerun." >&2
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

secret_has_active_version() {
  local project_id="$1"
  local secret_name="$2"
  local latest_state
  latest_state="$(gcloud secrets versions describe latest \
    --project "$project_id" \
    --secret "$secret_name" \
    --format='value(state)' 2>/dev/null || true)"
  [[ "$latest_state" == "ENABLED" ]]
}

secret_latest_value() {
  local project_id="$1"
  local secret_name="$2"
  gcloud secrets versions access latest \
    --project "$project_id" \
    --secret "$secret_name" 2>/dev/null || true
}

cloud_run_secret_name() {
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
        if e.get("name") != env_key:
            continue
        secret_ref = (((e.get("valueSource") or {}).get("secretKeyRef") or {}).get("secret")) or ""
        if not secret_ref:
            print("")
            raise SystemExit(0)
        # Accept either full resource names or plain secret IDs.
        print(secret_ref.split("/secrets/")[-1])
        raise SystemExit(0)
print("")
PY
}

preflight_existing_db_secret_readiness() {
  local project_id="$1"
  local stage="$2"
  local db_secret_name="syncbot-${stage}-syncbot-db-password"

  echo
  echo "=== Existing DB Secret Preflight ==="
  echo "Verifying required Secret Manager value exists for DATABASE_PASSWORD..."
  if ! secret_has_active_version "$project_id" "$db_secret_name"; then
    echo "Missing active secret version for '$db_secret_name'." >&2
    echo "Create one before deploy, for example:" >&2
    echo "  printf '%s' '<db_password>' | gcloud secrets versions add '$db_secret_name' --project '$project_id' --data-file=-" >&2
    exit 1
  fi
  echo "Secret preflight passed for: $db_secret_name"
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
  repo="$(prompt_github_repo_for_actions "$REPO_ROOT")"

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
    gh variable set GCP_PROJECT_ID --body "$gcp_project_id" -R "$repo"
    gh variable set GCP_REGION --body "$gcp_region" -R "$repo"
    [[ -n "$deploy_sa_email" ]] && gh variable set GCP_SERVICE_ACCOUNT --body "$deploy_sa_email" -R "$repo"
    gh variable set DEPLOY_TARGET --body "gcp" -R "$repo"
    echo "GitHub repository variables updated."
    echo "Remember to set GCP_WORKLOAD_IDENTITY_PROVIDER."
  fi

  if prompt_yn "Set environment variable STAGE_NAME for '$env_name' now?" "y"; then
    gh variable set STAGE_NAME --env "$env_name" --body "$deploy_stage" -R "$repo"
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
echo
echo "=== Authentication ==="
ensure_gcloud_authenticated
ensure_gcloud_adc_authenticated
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
prompt_deploy_tasks_gcp

if [[ "$TASK_BUILD_DEPLOY" != "true" ]]; then
  if [[ "$TASK_CICD" == "true" || "$TASK_SLACK_API" == "true" || "$TASK_BACKUP_SECRETS" == "true" ]]; then
    cd "$GCP_DIR"
    if ! terraform output -raw service_url &>/dev/null; then
      echo "Error: No Terraform outputs found in $GCP_DIR. Select task 1 (Build/Deploy) first." >&2
      exit 1
    fi
  fi
fi

if [[ "$TASK_BUILD_DEPLOY" == "true" ]]; then
echo
echo "=== Configuration ==="
DB_PORT="3306"
EXISTING_DB_CREATE_APP_USER="true"
EXISTING_DB_CREATE_SCHEMA="true"
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
  EXISTING_DB_APP_USERNAME_PREFIX="$(prompt_line "App username prefix (optional; e.g. TiDB Cloud abc123.; blank = enter full DB user next)" "")"
  if [[ -n "$EXISTING_DB_APP_USERNAME_PREFIX" ]]; then
    EXISTING_USER=""
  else
    EXISTING_USER="$(prompt_line "Database user" "$DETECTED_EXISTING_USER")"
  fi
  if [[ -z "$EXISTING_HOST" ]]; then
    echo "Error: Existing DB host is required when using existing database mode." >&2
    exit 1
  fi
  if [[ -z "$EXISTING_USER" && -z "$EXISTING_DB_APP_USERNAME_PREFIX" ]]; then
    echo "Error: Database user or app username prefix is required when using existing database mode." >&2
    exit 1
  fi

  echo
  echo "=== Existing database port and setup (operator / external DB) ==="
  DEFAULT_DB_PORT="3306"
  if [[ -n "$EXISTING_SERVICE_URL" ]]; then
    DETECTED_DB_PORT_EARLY="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_PORT")"
    [[ -n "$DETECTED_DB_PORT_EARLY" ]] && DEFAULT_DB_PORT="$DETECTED_DB_PORT_EARLY"
  fi
  DB_PORT="$(prompt_line "Database TCP port (DATABASE_PORT)" "$DEFAULT_DB_PORT")"
  if [[ -z "$DB_PORT" ]]; then
    echo "Error: Database TCP port is required when using existing database mode." >&2
    exit 1
  fi

  CREATE_APP_DEF="y"
  CREATE_SCHEMA_DEF="y"
  if prompt_yn "Create dedicated app DB user on the server (CREATE USER / grants)?" "$CREATE_APP_DEF"; then
    EXISTING_DB_CREATE_APP_USER="true"
  else
    EXISTING_DB_CREATE_APP_USER="false"
  fi
  if prompt_yn "Run CREATE DATABASE IF NOT EXISTS for DatabaseSchema (you or a hook)?" "$CREATE_SCHEMA_DEF"; then
    EXISTING_DB_CREATE_SCHEMA="true"
  else
    EXISTING_DB_CREATE_SCHEMA="false"
  fi
fi

DETECTED_CLOUD_IMAGE=""
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  DETECTED_CLOUD_IMAGE="$(cloud_run_image_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME")"
fi
echo
echo "=== Container Image ==="
CLOUD_IMAGE="$(prompt_line "cloud_run_image (required)" "$DETECTED_CLOUD_IMAGE")"
if [[ -z "$CLOUD_IMAGE" ]]; then
  echo "Error: cloud_run_image is required. Build and push the SyncBot image first, then rerun." >&2
  exit 1
fi

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

# Preserve optional runtime env on redeploy (Terraform defaults otherwise).
REQUIRE_ADMIN_DEFAULT="true"
SOFT_DELETE_DEFAULT="30"
SYNCBOT_PUBLIC_DEFAULT=""
SYNCBOT_FEDERATION_DEFAULT="false"
INSTANCE_ID_VAR=""
PRIMARY_WORKSPACE_VAR=""
ENABLE_DB_RESET_VAR=""
DB_TLS_VAR=""
DB_SSL_CA_VAR=""
DB_BACKEND="mysql"
if [[ -n "$EXISTING_SERVICE_URL" ]]; then
  DETECTED_RA="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "REQUIRE_ADMIN")"
  [[ -n "$DETECTED_RA" ]] && REQUIRE_ADMIN_DEFAULT="$DETECTED_RA"
  DETECTED_SD="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "SOFT_DELETE_RETENTION_DAYS")"
  if [[ "$DETECTED_SD" =~ ^[0-9]+$ ]]; then
    SOFT_DELETE_DEFAULT="$DETECTED_SD"
  fi
  SYNCBOT_PUBLIC_DEFAULT="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "SYNCBOT_PUBLIC_URL")"
  DETECTED_FED="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "SYNCBOT_FEDERATION_ENABLED")"
  if [[ "$DETECTED_FED" == "true" ]]; then
    SYNCBOT_FEDERATION_DEFAULT="true"
  elif [[ "$DETECTED_FED" == "false" ]]; then
    SYNCBOT_FEDERATION_DEFAULT="false"
  fi
  DETECTED_INSTANCE_ID="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "SYNCBOT_INSTANCE_ID")"
  INSTANCE_ID_VAR="${DETECTED_INSTANCE_ID:-}"
  DETECTED_PW="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "PRIMARY_WORKSPACE")"
  PRIMARY_WORKSPACE_VAR="${DETECTED_PW:-}"
  DETECTED_ER="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "ENABLE_DB_RESET")"
  ENABLE_DB_RESET_VAR="${DETECTED_ER:-}"
  DETECTED_DB_TLS="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_TLS_ENABLED")"
  DB_TLS_VAR="${DETECTED_DB_TLS:-}"
  DETECTED_DB_SSL_CA="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_SSL_CA_PATH")"
  DB_SSL_CA_VAR="${DETECTED_DB_SSL_CA:-}"
  DETECTED_DB_BACKEND="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_BACKEND")"
  [[ -n "$DETECTED_DB_BACKEND" ]] && DB_BACKEND="$DETECTED_DB_BACKEND"
  if [[ "$USE_EXISTING" != "true" ]]; then
    DETECTED_DB_PORT="$(cloud_run_env_value "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_PORT")"
    [[ -n "$DETECTED_DB_PORT" ]] && DB_PORT="$DETECTED_DB_PORT"
  fi
fi

echo
echo "=== App Settings ==="
REQUIRE_ADMIN_DEFAULT="$(prompt_require_admin "$REQUIRE_ADMIN_DEFAULT")"
SOFT_DELETE_DEFAULT="$(prompt_soft_delete_retention_days "$SOFT_DELETE_DEFAULT")"
PRIMARY_WORKSPACE_VAR="$(prompt_primary_workspace "$PRIMARY_WORKSPACE_VAR")"
SYNCBOT_FEDERATION_DEFAULT="$(prompt_federation_enabled "$SYNCBOT_FEDERATION_DEFAULT")"
if [[ "$SYNCBOT_FEDERATION_DEFAULT" == "true" ]]; then
  INSTANCE_ID_VAR="$(prompt_instance_id "$INSTANCE_ID_VAR")"
  SYNCBOT_PUBLIC_DEFAULT="$(prompt_public_url "$SYNCBOT_PUBLIC_DEFAULT")"
fi

echo
echo "=== Terraform Init ==="
echo "Running: terraform init"
cd "$GCP_DIR"
terraform init

# TF_VAR_* avoids shell parsing issues when the URL contains & or other metacharacters.
export TF_VAR_syncbot_public_url_override="$SYNCBOT_PUBLIC_DEFAULT"

VARS=(
  "-var=project_id=$PROJECT_ID"
  "-var=region=$REGION"
  "-var=stage=$STAGE"
  "-var=log_level=$LOG_LEVEL"
  "-var=require_admin=$REQUIRE_ADMIN_DEFAULT"
  "-var=soft_delete_retention_days=$SOFT_DELETE_DEFAULT"
  "-var=syncbot_federation_enabled=$SYNCBOT_FEDERATION_DEFAULT"
  "-var=syncbot_instance_id=${INSTANCE_ID_VAR:-}"
  "-var=primary_workspace=${PRIMARY_WORKSPACE_VAR:-}"
  "-var=enable_db_reset=${ENABLE_DB_RESET_VAR:-}"
  "-var=database_tls_enabled=${DB_TLS_VAR:-}"
  "-var=database_ssl_ca_path=${DB_SSL_CA_VAR:-}"
  "-var=database_backend=${DB_BACKEND:-mysql}"
  "-var=database_port=${DB_PORT:-3306}"
)

if [[ "$USE_EXISTING" == "true" ]]; then
  preflight_existing_db_secret_readiness "$PROJECT_ID" "$STAGE"
  VARS+=("-var=use_existing_database=true")
  VARS+=("-var=existing_db_host=$EXISTING_HOST")
  VARS+=("-var=existing_db_schema=$EXISTING_SCHEMA")
  VARS+=("-var=existing_db_user=$EXISTING_USER")
  VARS+=("-var=existing_db_app_username_prefix=$EXISTING_DB_APP_USERNAME_PREFIX")
  VARS+=("-var=existing_db_create_app_user=$EXISTING_DB_CREATE_APP_USER")
  VARS+=("-var=existing_db_create_schema=$EXISTING_DB_CREATE_SCHEMA")
else
  VARS+=("-var=use_existing_database=false")
  VARS+=("-var=existing_db_app_username_prefix=")
fi

VARS+=("-var=cloud_run_image=$CLOUD_IMAGE")

echo
echo "Require admin:    $REQUIRE_ADMIN_DEFAULT"
echo "Soft-delete days: $SOFT_DELETE_DEFAULT"
echo "Log level:        $LOG_LEVEL"
if [[ -n "$PRIMARY_WORKSPACE_VAR" ]]; then
  echo "Primary workspace: $PRIMARY_WORKSPACE_VAR"
else
  echo "Primary workspace: (not set — backup/restore hidden)"
fi
if [[ "$ENABLE_DB_RESET_VAR" == "true" ]]; then
  echo "DB reset:          enabled"
else
  echo "DB reset:          (disabled)"
fi
if [[ "$SYNCBOT_FEDERATION_DEFAULT" == "true" ]]; then
  echo "Federation:       enabled"
  [[ -n "$INSTANCE_ID_VAR" ]] && echo "Instance ID:      $INSTANCE_ID_VAR"
  [[ -n "$SYNCBOT_PUBLIC_DEFAULT" ]] && echo "Public URL:       $SYNCBOT_PUBLIC_DEFAULT"
fi
echo
echo "=== Terraform Plan ==="
terraform plan "${VARS[@]}"

echo
echo "=== Terraform Apply ==="
terraform apply -auto-approve "${VARS[@]}"

echo
echo "=== Apply Complete ==="
SERVICE_URL="$(terraform output -raw service_url 2>/dev/null || true)"

else
  echo
  echo "Skipping Build/Deploy (task 1 not selected)."
  cd "$GCP_DIR"
  SERVICE_URL="$(terraform output -raw service_url 2>/dev/null || true)"
fi

SYNCBOT_API_URL=""
SYNCBOT_INSTALL_URL=""
if [[ -n "$SERVICE_URL" ]]; then
  SYNCBOT_API_URL="${SERVICE_URL%/}/slack/events"
  SYNCBOT_INSTALL_URL="${SERVICE_URL%/}/slack/install"
fi

echo
echo "=== Post-Deploy ==="
if [[ "$TASK_BUILD_DEPLOY" == "true" ]]; then
  echo "Deploy complete."
fi

if [[ "$TASK_SLACK_API" == "true" || "$TASK_BUILD_DEPLOY" == "true" ]]; then
  generate_stage_slack_manifest "$STAGE" "$SYNCBOT_API_URL" "$SYNCBOT_INSTALL_URL"
fi

if [[ "$TASK_SLACK_API" == "true" ]] && [[ -n "${SLACK_MANIFEST_GENERATED_PATH:-}" ]]; then
  slack_api_configure_from_manifest "$SLACK_MANIFEST_GENERATED_PATH" "$SYNCBOT_INSTALL_URL"
fi

if [[ "$TASK_BUILD_DEPLOY" == "true" ]]; then
  echo
  echo "=== Deploy Receipt ==="
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
  echo "  2) Build and push container image; update cloud_run_image and re-apply when image changes."
  echo "  3) Run: ./infra/gcp/scripts/print-bootstrap-outputs.sh"
  bash "$SCRIPT_DIR/print-bootstrap-outputs.sh" || true
fi

if [[ "$TASK_CICD" == "true" ]]; then
  configure_github_actions_gcp "$PROJECT_ID" "$REGION" "$GCP_DIR" "$STAGE"
fi

TOKEN_SECRET_NAME=""
DB_SECRET_NAME=""
TOKEN_SECRET_VALUE=""
DB_SECRET_VALUE=""
if [[ "$TASK_BUILD_DEPLOY" == "true" || "$TASK_BACKUP_SECRETS" == "true" ]]; then
  cd "$GCP_DIR"
  TOKEN_SECRET_NAME="$(terraform output -raw token_encryption_secret_name 2>/dev/null || true)"
  TOKEN_SECRET_NAME="${TOKEN_SECRET_NAME##*/secrets/}"
  DB_SECRET_NAME="$(cloud_run_secret_name "$PROJECT_ID" "$REGION" "$SERVICE_NAME" "DATABASE_PASSWORD")"
  if [[ -n "$TOKEN_SECRET_NAME" ]]; then
    TOKEN_SECRET_VALUE="$(secret_latest_value "$PROJECT_ID" "$TOKEN_SECRET_NAME")"
  fi
  if [[ -n "$DB_SECRET_NAME" ]]; then
    DB_SECRET_VALUE="$(secret_latest_value "$PROJECT_ID" "$DB_SECRET_NAME")"
  fi
fi

if [[ "$TASK_BACKUP_SECRETS" == "true" ]]; then
  echo
  echo "=== Backup Secrets (Disaster Recovery) ==="
  # IMPORTANT: When Backup Secrets is selected, print plaintext backup secrets here.
  # Do not remove/redact this section; operators rely on it for DR copy-out.
  echo "Copy these values now and store them in your secure disaster-recovery vault."
  if [[ -n "$TOKEN_SECRET_NAME" ]]; then
    echo "- TOKEN_ENCRYPTION_KEY source: $TOKEN_SECRET_NAME"
  else
    echo "- TOKEN_ENCRYPTION_KEY source: <UNAVAILABLE>"
  fi
  if [[ -n "$TOKEN_SECRET_VALUE" ]]; then
    echo "  TOKEN_ENCRYPTION_KEY: $TOKEN_SECRET_VALUE"
  else
    echo "  TOKEN_ENCRYPTION_KEY: <UNAVAILABLE - check Secret Manager access and retrieve manually>"
  fi
  if [[ -n "$DB_SECRET_NAME" ]]; then
    echo "- DATABASE_PASSWORD source: $DB_SECRET_NAME"
  else
    echo "- DATABASE_PASSWORD source: <UNAVAILABLE>"
  fi
  if [[ -n "$DB_SECRET_VALUE" ]]; then
    echo "  DATABASE_PASSWORD: $DB_SECRET_VALUE"
  else
    echo "  DATABASE_PASSWORD: <UNAVAILABLE - check Secret Manager access and retrieve manually>"
  fi
fi
