# Outputs aligned with docs/INFRA_CONTRACT.md (bootstrap output contract)

output "service_url" {
  description = "Public base URL of the deployed app (for Slack app configuration)"
  value       = google_cloud_run_v2_service.syncbot.uri
}

output "region" {
  description = "Primary region for the deployment"
  value       = var.region
}

output "project_id" {
  description = "GCP project ID"
  value       = var.project_id
}

# Deploy contract: artifact_bucket equivalent (registry for container images)
output "artifact_registry_repository" {
  description = "Artifact Registry repository for container images (CI pushes here)"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.syncbot.repository_id}"
}

# Deploy contract: deploy_role equivalent (for Workload Identity Federation)
output "deploy_service_account_email" {
  description = "Service account email for CI/deploy (use with WIF)"
  value       = google_service_account.deploy.email
}

output "cloud_run_service_name" {
  description = "Cloud Run service name (for deploy targeting)"
  value       = google_cloud_run_v2_service.syncbot.name
}

output "cloud_run_service_location" {
  description = "Cloud Run service location (region)"
  value       = google_cloud_run_v2_service.syncbot.location
}

# Optional: DB connection info when Cloud SQL is created
output "database_connection_name" {
  description = "Cloud SQL connection name (when not using existing DB)"
  value       = var.use_existing_database ? null : (length(google_sql_database_instance.main) > 0 ? google_sql_database_instance.main[0].connection_name : null)
}
