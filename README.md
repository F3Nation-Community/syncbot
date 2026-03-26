# SyncBot
<img src="assets/icon.png" alt="SyncBot Icon" width="128">

SyncBot is a Slack app for syncing messages across workspaces. Once configured, this app will sync messages, threads, edits, deletes, reactions, images, videos, and GIFs to every channel in a SyncBot group.

> **Using SyncBot in Slack?** See the [User Guide](docs/USER_GUIDE.md).

---

## Slack app setup

Do this before you deploy or run locally:

1. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an app manifest** → paste [`slack-manifest.json`](slack-manifest.json).
2. Upload [`assets/icon.png`](assets/icon.png) under **Basic Information** → **Display Information**.
3. Copy **Signing Secret**, **Client ID**, and **Client Secret** (needed for deploy). For **local dev**, install the app under **OAuth & Permissions** and copy the **Bot User OAuth Token** (`xoxb-...`).

---

## Deploy

From the **repo root**, run the deploy script once for **`test`** and once for **`prod`** to automatically deploy to your infrastructure provider (currently AWS and GCP are supported).

| OS | Command |
|----|---------|
| macOS / Linux | `./deploy.sh` |
| Windows (PowerShell) | `.\deploy.ps1` |

You can also fork the repo, set GitHub variables/secrets, and push to **`test`** or **`prod`** to trigger CI — see [DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Prerequisites

In order for the deploy script to work, you need **Git** and **Bash** (on Windows, use **Git for Windows** / **Git Bash** or **WSL**).

**AWS:** AWS CLI v2, SAM CLI, Docker (for `sam build --use-container`), Python 3, and `curl`. Optional: `gh` for GitHub Actions setup.

**GCP:** Terraform, `gcloud`, Python 3, and `curl`. Optional: `gh`.

Full prerequisite checks, manual `sam` / Terraform, Slack URLs after deploy, and CI variables: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.


---

## Local development

See **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** for Dev Container, Docker Compose, native Python, project layout, and refreshing `syncbot/requirements.txt` after dependency changes.

---

## Further reading

| Doc | Contents |
|-----|----------|
| [USER_GUIDE.md](docs/USER_GUIDE.md) | End-user features (Home tab, syncs, groups) |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Guided + manual AWS/GCP deploy, CI, GitHub |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local dev, branching for forks, dependencies |
| [INFRA_CONTRACT.md](docs/INFRA_CONTRACT.md) | Environment variables and platform expectations |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Sync flow, AWS reference architecture |
| [BACKUP_AND_MIGRATION.md](docs/BACKUP_AND_MIGRATION.md) | Backup/restore and federation migration |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | HTTP routes and Slack events |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

## License

**AGPL-3.0** — see [LICENSE](LICENSE).
