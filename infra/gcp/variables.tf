# GCP Terraform variables for SyncBot (see docs/INFRA_CONTRACT.md)
#
# Sections: project / region / stage → database mode → Cloud Run → keep-warm →
# Secret Manager IDs and scope envs → optional overrides.

variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Primary region for Cloud Run and optional Cloud SQL"
}

variable "stage" {
  type        = string
  default     = "test"
  description = "Stage name (e.g. test, prod); used for resource naming"
}

# ---------------------------------------------------------------------------
# Database: use existing or create Cloud SQL
# ---------------------------------------------------------------------------

variable "use_existing_database" {
  type        = bool
  default     = false
  description = "If true, do not create Cloud SQL; app uses existing_db_host/schema/user/password"
}

variable "existing_db_host" {
  type        = string
  default     = ""
  description = "Existing MySQL host (required when use_existing_database = true)"
}

variable "existing_db_schema" {
  type        = string
  default     = "syncbot"
  description = "Existing MySQL schema name (when use_existing_database = true)"
}

variable "existing_db_user" {
  type        = string
  default     = ""
  description = "Existing MySQL user (when use_existing_database = true)"
}

# ---------------------------------------------------------------------------
# Cloud Run
# ---------------------------------------------------------------------------

variable "cloud_run_image" {
  type        = string
  default     = ""
  description = "Container image URL for Cloud Run (e.g. gcr.io/PROJECT/syncbot:latest). Set after first build or by CI."
}

variable "cloud_run_cpu" {
  type        = string
  default     = "1"
  description = "CPU allocation for Cloud Run service"
}

variable "cloud_run_memory" {
  type        = string
  default     = "512Mi"
  description = "Memory allocation for Cloud Run service"
}

variable "cloud_run_min_instances" {
  type        = number
  default     = 0
  description = "Minimum number of instances (0 allows scale-to-zero)"
}

variable "cloud_run_max_instances" {
  type        = number
  default     = 10
  description = "Maximum number of Cloud Run instances"
}

variable "log_level" {
  type        = string
  default     = "INFO"
  description = "Python logging level for the app (LOG_LEVEL). DEBUG, INFO, WARNING, ERROR, or CRITICAL."

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], var.log_level)
    error_message = "log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL."
  }
}

# ---------------------------------------------------------------------------
# Keep-warm (Cloud Scheduler)
# ---------------------------------------------------------------------------

variable "enable_keep_warm" {
  type        = bool
  default     = true
  description = "Create a Cloud Scheduler job that pings the service periodically"
}

variable "keep_warm_interval_minutes" {
  type        = number
  default     = 5
  description = "Interval in minutes for keep-warm ping"
}

# ---------------------------------------------------------------------------
# Secrets: names only; values are set outside Terraform (gcloud or console)
# ---------------------------------------------------------------------------

variable "secret_slack_signing_secret" {
  type        = string
  default     = "syncbot-slack-signing-secret"
  description = "Secret Manager secret ID for SLACK_SIGNING_SECRET"
}

variable "secret_slack_client_id" {
  type        = string
  default     = "syncbot-slack-client-id"
  description = "Secret Manager secret ID for SLACK_CLIENT_ID"
}

variable "secret_slack_client_secret" {
  type        = string
  default     = "syncbot-slack-client-secret"
  description = "Secret Manager secret ID for SLACK_CLIENT_SECRET"
}

variable "secret_slack_bot_scopes" {
  type        = string
  default     = "syncbot-slack-scopes"
  description = "Secret Manager secret ID whose value is comma-separated bot OAuth scopes (runtime env SLACK_BOT_SCOPES)"
}

variable "slack_user_scopes" {
  type        = string
  default     = "chat:write,channels:history,channels:read,files:read,files:write,groups:history,groups:read,groups:write,im:write,reactions:read,reactions:write,team:read,users:read,users:read.email"
  description = "Comma-separated user OAuth scopes for Cloud Run (SLACK_USER_SCOPES). Must match slack-manifest.json oauth_config.scopes.user and syncbot/slack_manifest_scopes.py USER_SCOPES; default matches repo standard (same string as AWS SAM SlackOauthUserScopes Default)."
}

variable "secret_token_encryption_key" {
  type        = string
  default     = "syncbot-token-encryption-key"
  description = "Secret Manager secret ID for TOKEN_ENCRYPTION_KEY"
}

variable "token_encryption_key_override" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Optional disaster-recovery override for TOKEN_ENCRYPTION_KEY. Leave empty for normal deploys."
}

variable "secret_db_password" {
  type        = string
  default     = "syncbot-db-password"
  description = "Secret Manager secret ID for DATABASE_PASSWORD (used when use_existing_database = true or with Cloud SQL)"
}
