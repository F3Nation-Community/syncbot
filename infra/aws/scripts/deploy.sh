#!/usr/bin/env bash
# Interactive AWS deploy helper for SyncBot.
# Handles: bootstrap (optional), sam build, sam deploy (new RDS or existing RDS).
#
# Run from repo root:
#   ./infra/aws/scripts/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

BOOTSTRAP_TEMPLATE="$REPO_ROOT/infra/aws/template.bootstrap.yaml"
APP_TEMPLATE="$REPO_ROOT/infra/aws/template.yaml"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command '$1' not found in PATH." >&2
    exit 1
  fi
}

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

bootstrap_describe_outputs() {
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

secret_arn_by_name() {
  local secret_name="$1"
  local region="$2"
  aws secretsmanager describe-secret \
    --secret-id "$secret_name" \
    --region "$region" \
    --query 'ARN' \
    --output text 2>/dev/null || true
}

rds_lookup_network_defaults() {
  local db_host="$1"
  local region="$2"
  aws rds describe-db-instances \
    --region "$region" \
    --query "DBInstances[?Endpoint.Address=='$db_host']|[0].[PubliclyAccessible,join(',',DBSubnetGroup.Subnets[].SubnetIdentifier),join(',',VpcSecurityGroups[].VpcSecurityGroupId),DBSubnetGroup.VpcId,DBInstanceIdentifier]" \
    --output text 2>/dev/null || true
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

require_cmd aws
require_cmd sam

if [[ ! -f "$APP_TEMPLATE" ]]; then
  echo "Error: app template not found at $APP_TEMPLATE" >&2
  exit 1
fi
if [[ ! -f "$BOOTSTRAP_TEMPLATE" ]]; then
  echo "Error: bootstrap template not found at $BOOTSTRAP_TEMPLATE" >&2
  exit 1
fi

echo "=== SyncBot AWS Deploy Helper ==="
echo

DEFAULT_REGION="${AWS_REGION:-us-east-2}"
REGION="$(prompt_default "AWS region" "$DEFAULT_REGION")"
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

echo
echo "Database mode:"
echo "  1) Create new RDS in stack"
echo "  2) Use existing RDS host (deploy creates schema/app user)"
DB_MODE="$(prompt_default "Choose 1 or 2" "1")"
if [[ "$DB_MODE" != "1" && "$DB_MODE" != "2" ]]; then
  echo "Error: invalid database mode." >&2
  exit 1
fi

echo
SLACK_SIGNING_SECRET="$(prompt_secret "SlackSigningSecret")"
SLACK_CLIENT_SECRET="$(prompt_secret "SlackClientSecret")"
SLACK_CLIENT_ID="$(prompt_default "SlackClientID (optional; blank uses template stage default)" "")"

EXISTING_DATABASE_HOST=""
EXISTING_DATABASE_ADMIN_USER=""
EXISTING_DATABASE_ADMIN_PASSWORD=""
EXISTING_DATABASE_NETWORK_MODE="public"
EXISTING_DATABASE_SUBNET_IDS_CSV=""
EXISTING_DATABASE_LAMBDA_SG_ID=""
DATABASE_USER=""
DATABASE_PASSWORD=""
DATABASE_SCHEMA=""

if [[ "$DB_MODE" == "2" ]]; then
  EXISTING_DATABASE_HOST="$(prompt_default "ExistingDatabaseHost (RDS endpoint hostname)" "REPLACE_ME_RDS_HOST")"
  EXISTING_DATABASE_ADMIN_USER="$(prompt_default "ExistingDatabaseAdminUser" "admin")"
  EXISTING_DATABASE_ADMIN_PASSWORD="$(prompt_secret "ExistingDatabaseAdminPassword")"
  DATABASE_SCHEMA="$(prompt_default "DatabaseSchema" "syncbot_${STAGE}")"

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
  if [[ "$DETECTED_PUBLIC" == "False" ]]; then
    DEFAULT_EXISTING_DB_NETWORK_MODE="private"
  fi
  EXISTING_DATABASE_NETWORK_MODE="$(prompt_default "Existing DB network mode (public/private)" "$DEFAULT_EXISTING_DB_NETWORK_MODE")"
  if [[ "$EXISTING_DATABASE_NETWORK_MODE" != "public" && "$EXISTING_DATABASE_NETWORK_MODE" != "private" ]]; then
    echo "Error: existing DB network mode must be 'public' or 'private'." >&2
    exit 1
  fi

  if [[ "$EXISTING_DATABASE_NETWORK_MODE" == "private" ]]; then
    DEFAULT_SUBNETS="$DETECTED_SUBNETS"
    [[ -z "$DEFAULT_SUBNETS" ]] && DEFAULT_SUBNETS="REPLACE_ME_SUBNET_1,REPLACE_ME_SUBNET_2"
    DEFAULT_SG="${DETECTED_SGS%%,*}"
    [[ -z "$DEFAULT_SG" ]] && DEFAULT_SG="REPLACE_ME_LAMBDA_SG_ID"

    echo
    echo "Private DB mode selected: Lambdas will run in VPC."
    echo "Note: app Lambda needs internet egress (usually NAT) to call Slack APIs."
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
  fi
else
  DATABASE_USER="$(prompt_default "DatabaseUser (new RDS master username)" "syncbot_admin")"
  DATABASE_PASSWORD="$(prompt_secret "DatabasePassword (new RDS master password)")"
  DATABASE_SCHEMA="$(prompt_default "DatabaseSchema" "syncbot_${STAGE}")"
fi

TOKEN_OVERRIDE="$(prompt_default "TokenEncryptionKeyOverride (optional DR key; leave blank for normal deploy)" "")"
EXISTING_TOKEN_SECRET_ARN=""
TOKEN_SECRET_NAME="syncbot-${STAGE}-token-encryption-key"
if [[ -z "$TOKEN_OVERRIDE" ]]; then
  DETECTED_TOKEN_SECRET_ARN="$(secret_arn_by_name "$TOKEN_SECRET_NAME" "$REGION")"
  if [[ -n "$DETECTED_TOKEN_SECRET_ARN" && "$DETECTED_TOKEN_SECRET_ARN" != "None" ]]; then
    echo "Detected existing token secret: $TOKEN_SECRET_NAME"
    if prompt_yes_no "Reuse this existing token secret ARN to avoid name-collision failures?" "y"; then
      EXISTING_TOKEN_SECRET_ARN="$DETECTED_TOKEN_SECRET_ARN"
    fi
  fi
fi

echo
echo "=== Deploy Summary ==="
echo "Region:           $REGION"
echo "Stack:            $STACK_NAME"
echo "Stage:            $STAGE"
echo "Deploy bucket:    $S3_BUCKET"
if [[ "$DB_MODE" == "2" ]]; then
  echo "DB mode:          existing host"
  echo "DB host:          $EXISTING_DATABASE_HOST"
  echo "DB network:       $EXISTING_DATABASE_NETWORK_MODE"
  if [[ "$EXISTING_DATABASE_NETWORK_MODE" == "private" ]]; then
    echo "DB subnets:       $EXISTING_DATABASE_SUBNET_IDS_CSV"
    echo "Lambda SG:        $EXISTING_DATABASE_LAMBDA_SG_ID"
  fi
  echo "DB schema:        $DATABASE_SCHEMA"
else
  echo "DB mode:          create new RDS"
  echo "DB user:          $DATABASE_USER"
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
echo

if ! prompt_yes_no "Proceed with build + deploy?" "y"; then
  echo "Aborted."
  exit 0
fi

handle_unhealthy_stack_state "$STACK_NAME" "$REGION"

echo
echo "Building app..."
sam build -t "$APP_TEMPLATE" --use-container

PARAMS=(
  "Stage=$STAGE"
  "SlackSigningSecret=$SLACK_SIGNING_SECRET"
  "SlackClientSecret=$SLACK_CLIENT_SECRET"
  "DatabaseSchema=$DATABASE_SCHEMA"
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
  PARAMS+=(
    "DatabaseUser=$DATABASE_USER"
    "DatabasePassword=$DATABASE_PASSWORD"
  )
fi

if [[ -n "$TOKEN_OVERRIDE" ]]; then
  PARAMS+=("TokenEncryptionKeyOverride=$TOKEN_OVERRIDE")
fi
if [[ -n "$EXISTING_TOKEN_SECRET_ARN" ]]; then
  PARAMS+=("ExistingTokenEncryptionKeySecretArn=$EXISTING_TOKEN_SECRET_ARN")
fi

echo "Deploying stack..."
sam deploy \
  -t .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --s3-bucket "$S3_BUCKET" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAMS[@]}"

echo
echo "Deploy complete."
echo "IMPORTANT: back up TOKEN_ENCRYPTION_KEY from Secrets Manager."
echo "Expected secret name: syncbot-${STAGE}-token-encryption-key"
echo "Example read command:"
echo "  aws secretsmanager get-secret-value --secret-id syncbot-${STAGE}-token-encryption-key --query SecretString --output text --region $REGION"
