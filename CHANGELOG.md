# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Cross-workspace channel links in synced messages use workspace archive URLs (`https://{domain}.slack.com/archives/{id}`) instead of `slack.com/app_redirect`, with a code-formatted `[#channel (Workspace)]` fallback when the domain cannot be resolved. Federation outbound messages resolve raw `<#C…>` references to archive links on the sender; the receiver rewrites archive links and any remaining channel tokens to **native `<#C>`** when that channel is in the same sync on the local workspace.
- Same-instance sync resolves channel references **per target workspace**: if the mentioned channel is part of the same sync as the destination channel, the message uses the local channel ID instead of an archive URL.
- Federation **inbound** `message` and `message/edit` handlers resolve `@` mentions on the receiving instance using `UserMapping` / `UserDirectory` (native `<@U>` when mapped, otherwise a code-formatted `[@Name (Workspace)]` fallback).
- `ENABLE_DB_RESET` is now a boolean (`true` / `1` / `yes`) instead of a Slack Team ID. Reset Database requires both `PRIMARY_WORKSPACE` to match the current workspace and `ENABLE_DB_RESET` to be truthy.

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
