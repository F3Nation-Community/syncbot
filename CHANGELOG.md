# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ExistingDatabasePort`, `ExistingDatabaseCreateAppUser`, and `ExistingDatabaseCreateSchema` deploy parameters for external DB providers (e.g. TiDB Cloud)

### Changed

- Bumped GitHub Actions: `actions/checkout` v6, `actions/setup-python` v6, `actions/upload-artifact` v7, `actions/download-artifact` v8, `aws-actions/configure-aws-credentials` v6
- Dependabot: ignore semver-major updates for the Docker `python` image (keeps base image on Python 3.12.x line)
- AWS Lambda: Alembic migrations now run via a post-deploy invoke instead of on every cold start, fixing Slack ack timeouts after deployment; Cloud Run and local dev unchanged
- AWS Lambda memory increased from 128 MB to 256 MB for faster cold starts
- EventBridge keep-warm invokes now return a clean JSON response instead of falling through to Slack Bolt
- AWS bootstrap deploy policy: added `lambda:InvokeFunction` -- **re-run the deploy script (Bootstrap task) or `aws cloudformation deploy` the bootstrap stack to pick up this permission**

### Fixed

- Replaced deprecated `datetime.utcnow()` with `datetime.now(UTC)` in backup/migration export helpers

## [1.0.1] - 2026-03-26

### Changed

- Cross-workspace `#channel` links resolve to native local channels when the channel is part of the same sync; otherwise use workspace archive URLs with a code-formatted fallback
- `@mentions` and `#channel` links in federated messages are now resolved on the receiving instance (native tags when mapped/synced, fallbacks otherwise)
- `ENABLE_DB_RESET` is now a boolean (`true` / `1` / `yes`) instead of a Slack Team ID; requires `PRIMARY_WORKSPACE` to match

### Added

- `PRIMARY_WORKSPACE` env var: must be set to a Slack Team ID for backup/restore to appear. Also scopes DB reset to that workspace.

## [1.0.0] - 2026-03-25

### Added

- Multi-workspace message sync: messages, threads, edits, deletes, reactions, images, videos, and GIFs
- Cross-workspace @mention resolution (email, name, and manual matching)
- Workspace Groups with invite codes (many-to-many collaboration; direct and group-wide sync modes)
- Pause, resume, and stop per-channel sync controls
- App Home tab for configuration (no slash commands)
- Cross-instance federation (optional, HMAC-authenticated)
- Backup/restore and workspace data migration
- Bot token encryption at rest (Fernet)
- AWS deployment (SAM/CloudFormation) with optional CI/CD via GitHub Actions
- GCP deployment (Terraform/Cloud Run) with interactive deploy script; GitHub Actions workflow for GCP is not yet fully wired
- Dev Container and Docker Compose for local development
- Structured JSON logging with correlation IDs and CloudWatch alarms (AWS)
- PostgreSQL, MySQL, and SQLite database backends
- Alembic-managed schema migrations applied at startup
