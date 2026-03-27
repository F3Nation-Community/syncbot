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

variable "existing_db_create_app_user" {
  type        = bool
  default     = true
  description = "When use_existing_database: operator note — whether a dedicated app DB user exists (no Cloud SQL user resource; app uses existing_db_user / secret)."
}

variable "existing_db_create_schema" {
  type        = bool
  default     = true
  description = "When use_existing_database: operator note — whether DatabaseSchema was created manually (Terraform does not create schema for existing host)."
}

# ---------------------------------------------------------------------------
# Cloud Run
# ---------------------------------------------------------------------------

variable "cloud_run_image" {
  type        = string
  default     = ""
  description = "Container image URL for Cloud Run (e.g. gcr.io/PROJECT/syncbot:latest). Set after first build or by CI."

  validation {
    condition     = trimspace(var.cloud_run_image) != ""
    error_message = "cloud_run_image is required. Build/push the SyncBot image and pass -var=cloud_run_image=<image>."
  }
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

# ---------------------------------------------------------------------------
# Runtime plain env (Cloud Run) — parity with infra/aws/template.yaml
# ---------------------------------------------------------------------------

variable "database_backend" {
  type        = string
  default     = "mysql"
  description = "DATABASE_BACKEND; Cloud SQL in this stack is MySQL 8."

  validation {
    condition     = contains(["mysql", "postgresql"], var.database_backend)
    error_message = "database_backend must be mysql or postgresql."
  }
}

variable "database_port" {
  type        = string
  default     = "3306"
  description = "DATABASE_PORT for MySQL (default 3306)."
}

variable "require_admin" {
  type        = string
  default     = "true"
  description = "REQUIRE_ADMIN: true or false."

  validation {
    condition     = contains(["true", "false"], var.require_admin)
    error_message = "require_admin must be true or false."
  }
}

variable "soft_delete_retention_days" {
  type        = number
  default     = 30
  description = "SOFT_DELETE_RETENTION_DAYS (minimum 1)."

  validation {
    condition     = var.soft_delete_retention_days >= 1
    error_message = "soft_delete_retention_days must be at least 1."
  }
}

variable "syncbot_federation_enabled" {
  type        = bool
  default     = false
  description = "SYNCBOT_FEDERATION_ENABLED (maps to string true/false in env)."
}

variable "syncbot_instance_id" {
  type        = string
  default     = ""
  description = "SYNCBOT_INSTANCE_ID; leave empty for app auto-generation."
}

variable "syncbot_public_url_override" {
  type        = string
  default     = ""
  description = "SYNCBOT_PUBLIC_URL (HTTPS base, no path). Set after first deploy if using federation; empty omits the env var."
}

variable "primary_workspace" {
  type        = string
  default     = ""
  description = "PRIMARY_WORKSPACE Slack Team ID; required for backup/restore to appear. Empty omits the env var and hides backup/restore."
}

variable "enable_db_reset" {
  type        = string
  default     = ""
  description = "ENABLE_DB_RESET: set to \"true\" for Reset Database when PRIMARY_WORKSPACE matches; empty omits."
}

variable "database_tls_enabled" {
  type        = string
  default     = ""
  description = "DATABASE_TLS_ENABLED; empty = app default (TLS on outside local dev)."

  validation {
    condition     = contains(["", "true", "false"], var.database_tls_enabled)
    error_message = "database_tls_enabled must be empty, true, or false."
  }
}

variable "database_ssl_ca_path" {
  type        = string
  default     = ""
  description = "DATABASE_SSL_CA_PATH when TLS is on; empty omits (app default CA path)."
}
