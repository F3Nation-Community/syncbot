# GCP Terraform variables for SyncBot (see docs/INFRA_CONTRACT.md)

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
  description = "Secret Manager secret ID for ENV_SLACK_CLIENT_ID"
}

variable "secret_slack_client_secret" {
  type        = string
  default     = "syncbot-slack-client-secret"
  description = "Secret Manager secret ID for ENV_SLACK_CLIENT_SECRET"
}

variable "secret_slack_scopes" {
  type        = string
  default     = "syncbot-slack-scopes"
  description = "Secret Manager secret ID for ENV_SLACK_SCOPES"
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
