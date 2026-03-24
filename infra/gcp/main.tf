# SyncBot on GCP — minimal Terraform scaffold
# Satisfies docs/INFRA_CONTRACT.md (Cloud Run, secrets, optional Cloud SQL, keep-warm)

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name_prefix = "syncbot-${var.stage}"
  secret_ids = [
    var.secret_slack_signing_secret,
    var.secret_slack_client_id,
    var.secret_slack_client_secret,
    var.secret_slack_bot_scopes,
    var.secret_token_encryption_key,
    var.secret_db_password,
  ]
  # Map deploy-contract env var names to Secret Manager secret variable keys (used in app_secrets)
  env_to_secret_key = {
    "SLACK_SIGNING_SECRET" = var.secret_slack_signing_secret
    "SLACK_CLIENT_ID"      = var.secret_slack_client_id
    "SLACK_CLIENT_SECRET"  = var.secret_slack_client_secret
    "SLACK_BOT_SCOPES"     = var.secret_slack_bot_scopes
    "TOKEN_ENCRYPTION_KEY" = var.secret_token_encryption_key
    "DATABASE_PASSWORD"    = var.secret_db_password
  }
  # Runtime DB connection: existing host or Cloud SQL public IP after create
  db_host = var.use_existing_database ? var.existing_db_host : (
    length(google_sql_database_instance.main) > 0 ? google_sql_database_instance.main[0].public_ip_address : ""
  )
  db_schema = var.use_existing_database ? var.existing_db_schema : "syncbot"
  db_user   = var.use_existing_database ? var.existing_db_user : "syncbot_app"
  # Image: variable or placeholder until first image push
  cloud_run_image_effective = var.cloud_run_image != "" ? var.cloud_run_image : "us-docker.pkg.dev/cloudrun/container/hello"
}

# ---------------------------------------------------------------------------
# APIs
# ---------------------------------------------------------------------------

resource "google_project_service" "run" {
  project            = var.project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sqladmin" {
  count              = var.use_existing_database ? 0 : 1
  project            = var.project_id
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "scheduler" {
  count              = var.enable_keep_warm ? 1 : 0
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifact_registry" {
  project            = var.project_id
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Secret Manager: placeholder secrets (values set via gcloud or console)
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "app_secrets" {
  for_each  = toset(local.secret_ids)
  project   = var.project_id
  secret_id = "${local.name_prefix}-${each.key}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

# ---------------------------------------------------------------------------
# Artifact Registry repository for container images (deploy contract: artifact_bucket equivalent)
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "syncbot" {
  location      = var.region
  repository_id = "${local.name_prefix}-images"
  description   = "SyncBot container images"
  format        = "DOCKER"

  depends_on = [google_project_service.artifact_registry]
}

# ---------------------------------------------------------------------------
# Service account for Cloud Run (runtime)
# ---------------------------------------------------------------------------

resource "google_service_account" "cloud_run" {
  project      = var.project_id
  account_id   = "${replace(local.name_prefix, "-", "")}-run"
  display_name = "SyncBot Cloud Run runtime (${var.stage})"
}

# Grant Cloud Run SA access to read the app secrets
resource "google_project_iam_member" "cloud_run_secret_access" {
  for_each = toset(local.secret_ids)
  project  = var.project_id
  role     = "roles/secretmanager.secretAccessor"
  member   = "serviceAccount:${google_service_account.cloud_run.email}"
}

# ---------------------------------------------------------------------------
# Deploy service account (CI / Workload Identity Federation)
# ---------------------------------------------------------------------------

resource "google_service_account" "deploy" {
  project      = var.project_id
  account_id   = "${replace(local.name_prefix, "-", "")}-deploy"
  display_name = "SyncBot deploy (CI) (${var.stage})"
}

resource "google_project_iam_member" "deploy_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# ---------------------------------------------------------------------------
# Cloud SQL (optional): minimal MySQL instance
# ---------------------------------------------------------------------------

resource "random_password" "db" {
  count   = var.use_existing_database ? 0 : 1
  length  = 24
  special = false
}

resource "random_password" "token_encryption_key" {
  length  = 48
  special = false
}

resource "google_sql_database_instance" "main" {
  count            = var.use_existing_database ? 0 : 1
  project          = var.project_id
  name             = "${local.name_prefix}-db"
  database_version = "MYSQL_8_0"
  region           = var.region

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_type         = "PD_SSD"

    database_flags {
      name  = "cloudsql_iam_authentication"
      value = "on"
    }

    ip_configuration {
      ipv4_enabled    = true
      private_network = null
    }
  }

  deletion_protection = false

  depends_on = [google_project_service.sqladmin]
}

resource "google_sql_database" "schema" {
  count    = var.use_existing_database ? 0 : 1
  name     = "syncbot"
  instance = google_sql_database_instance.main[0].name
}

resource "google_sql_user" "app" {
  count    = var.use_existing_database ? 0 : 1
  name     = "syncbot_app"
  instance = google_sql_database_instance.main[0].name
  host     = "%"
  password = random_password.db[0].result
}

# Store Cloud SQL password in Secret Manager for Cloud Run
resource "google_secret_manager_secret_version" "db_password" {
  count       = var.use_existing_database ? 0 : 1
  secret      = google_secret_manager_secret.app_secrets[var.secret_db_password].id
  secret_data = random_password.db[0].result
}

# Generate TOKEN_ENCRYPTION_KEY once and persist in Secret Manager.
resource "google_secret_manager_secret_version" "token_encryption_key" {
  secret      = google_secret_manager_secret.app_secrets[var.secret_token_encryption_key].id
  secret_data = var.token_encryption_key_override != "" ? var.token_encryption_key_override : random_password.token_encryption_key.result
}

# ---------------------------------------------------------------------------
# Cloud Run service
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "syncbot" {
  project  = var.project_id
  name     = local.name_prefix
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      image = local.cloud_run_image_effective

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }

      env {
        name  = "DATABASE_HOST"
        value = local.db_host
      }
      env {
        name  = "DATABASE_USER"
        value = local.db_user
      }
      env {
        name  = "DATABASE_SCHEMA"
        value = local.db_schema
      }
      # Runtime user OAuth scopes — must match slack-manifest.json and USER_SCOPES in slack_manifest_scopes.py
      env {
        name  = "SLACK_USER_SCOPES"
        value = var.slack_user_scopes
      }
      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }

      dynamic "env" {
        for_each = local.env_to_secret_key
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app_secrets[env.value].name
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.run,
    google_secret_manager_secret.app_secrets,
  ]
}

# Allow unauthenticated invocations (Slack calls the URL; use IAP or Cloud Armor in prod if needed)
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = google_cloud_run_v2_service.syncbot.project
  location = google_cloud_run_v2_service.syncbot.location
  name     = google_cloud_run_v2_service.syncbot.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Cloud Scheduler (keep-warm)
# ---------------------------------------------------------------------------

resource "google_cloud_scheduler_job" "keep_warm" {
  count            = var.enable_keep_warm ? 1 : 0
  project          = var.project_id
  name             = "${local.name_prefix}-keep-warm"
  region           = var.region
  schedule         = "*/${var.keep_warm_interval_minutes} * * * *"
  time_zone        = "UTC"
  attempt_deadline = "60s"

  http_target {
    uri         = "${google_cloud_run_v2_service.syncbot.uri}/health"
    http_method = "GET"
    oidc_token {
      service_account_email = google_service_account.cloud_run.email
    }
  }

  depends_on = [
    google_project_service.scheduler,
    google_cloud_run_v2_service.syncbot,
  ]
}
