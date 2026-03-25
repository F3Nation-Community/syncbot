# SyncBot
<img src="assets/icon.png" alt="SyncBot Icon" width="128">

SyncBot is a Slack app for replicating messages and replies across workspaces on the free tier. Once configured, messages, threads, edits, deletes, reactions, images, videos, and GIFs mirror to every channel in a Sync group.

> **Using SyncBot in Slack?** See the [User Guide](docs/USER_GUIDE.md).

---

## Branching (upstream vs downstream)

This repo is the **canonical** project. **Forks** (downstream installations) should use Git like this:

| Branch | Role |
|--------|------|
| **`main`** | Tracks upstream. Use it to merge PRs and to **sync with the upstream repository** (`git pull upstream main`, etc.). |
| **`test`** / **`prod`** | On your fork, use these for **deployments**: GitHub Actions deploy workflows run on **push** to `test` and `prod` (see [DEPLOYMENT.md](docs/DEPLOYMENT.md)). |

Typical flow: develop on a feature branch → open a PR to **`main`** → merge → when ready to deploy, merge **`main`** into **`test`** or **`prod`** on your fork.

---

## Deploy (AWS or GCP)

You can deploy in two ways:

1. **Download or clone and run the deploy script** — No GitHub Actions required. From the **repository root**, run `./deploy.sh` (or `.\deploy.ps1` on Windows). The script walks you through provider choice, cloud auth, and optional GitHub variable setup.
2. **Fork the repo and use CI/CD** — Configure repository variables and secrets (see [DEPLOYMENT.md](docs/DEPLOYMENT.md)), then push to **`test`** or **`prod`** on your fork to trigger automated deploys.

From the **repository root**, the infra-agnostic launcher is:

| OS | Command |
|----|---------|
| macOS / Linux | `./deploy.sh` |
| Windows (PowerShell) | `.\deploy.ps1` |

The launcher lists providers under `infra/<provider>/scripts/deploy.sh` (e.g. **aws**, **gcp**), prompts for a choice, and runs that script. Shortcuts: `./deploy.sh aws`, `./deploy.sh gcp`, `./deploy.sh 1`. On **Windows**, `deploy.ps1` checks for **Git Bash** or **WSL** bash, then runs the same `deploy.sh` paths (provider prerequisites are enforced inside those bash scripts).

If **Poetry** is on your `PATH`, the root launcher first runs `poetry update` and regenerates `syncbot/requirements.txt` from `poetry.lock` so deploys match the pinned Python deps (Poetry 2.x: install the export plugin once with `poetry self add poetry-plugin-export`). If Poetry is missing, the launcher skips this step and continues.

**GCP CI:** Interactive deploy via `./deploy.sh` → **gcp** is supported. The **GitHub Actions** workflow for GCP (`.github/workflows/deploy-gcp.yml`) is a stub until Workload Identity Federation and image build/push steps are wired — use the guided script for GCP until then.

### What to install first

| Tool | Why |
|------|-----|
| **Git** | Clone the repo; on Windows, **Git for Windows** supplies **Git Bash**, which the deploy scripts use. |
| **Bash** | Required for `./deploy.sh` and `infra/*/scripts/deploy.sh`. On Windows use Git Bash or **WSL** (then run `./deploy.sh` from Linux). |

**AWS** (`infra/aws/scripts/deploy.sh`): **AWS CLI v2**, **AWS SAM CLI**, **Docker** (for `sam build --use-container`), **Python 3** (`python3`), **`curl`** (Slack manifest API). **Optional:** **`gh`** (GitHub Actions setup); if `gh` is missing, the script shows install hints and asks whether to continue.

**GCP** (`infra/gcp/scripts/deploy.sh`): **Terraform**, **Google Cloud SDK (`gcloud`)**, **Python 3**, **`curl`**. **Optional:** **`gh`** — same behavior as AWS.

Full behavior, manual `sam` / Terraform steps, GitHub variables, and troubleshooting: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

---

## Slack app (before deploy or local dev)

1. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an app manifest** → paste [`slack-manifest.json`](slack-manifest.json).
2. Upload [`assets/icon.png`](assets/icon.png) under **Basic Information** → **Display Information**.
3. Copy **Signing Secret**, **Client ID**, and **Client Secret** (needed for deploy). For **local dev**, install the app under **OAuth & Permissions** and copy the **Bot User OAuth Token** (`xoxb-...`).

After deployment, point Event Subscriptions and Interactivity at your real HTTPS URL (the deploy script can generate a stage-specific `slack-manifest_<stage>.json` and optional Slack API updates). Details: [DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Local development

### Dev Container (recommended)

**Needs:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine on Linux) + [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) in VS Code.

1. `cp .env.example .env` and set `SLACK_BOT_TOKEN` (`xoxb-...`).
2. **Dev Containers: Reopen in Container** — Python, MySQL, and deps run inside the container.
3. `cd syncbot && python app.py` → app on **port 3000** (forwarded).
4. Expose to Slack with **cloudflared** or **ngrok** from the host; set Slack **Event Subscriptions** / **Interactivity** URLs to the public URL.

Optional **SQLite**: in `.env` set `DATABASE_BACKEND=sqlite` and `DATABASE_URL=sqlite:////app/syncbot/syncbot.db`.

### Docker Compose (no Dev Container)

```bash
cp .env.example .env   # set SLACK_BOT_TOKEN
docker compose up --build
```

App on port **3000**; restart the `app` service after code changes.

### Native Python

**Needs:** Python 3.12+, Poetry. Run MySQL locally (e.g. `docker run ... mysql:8`) or SQLite. See `.env.example` and [INFRA_CONTRACT.md](docs/INFRA_CONTRACT.md).

After `poetry add` / `poetry update`, regenerate the pinned file used by the Docker image and `pip-audit` in CI so it matches `poetry.lock`:

```bash
poetry self add poetry-plugin-export   # Poetry 2.x; once per Poetry install
poetry export -f requirements.txt --without-hashes -o syncbot/requirements.txt
```

---

## Configuration reference

- **[`.env.example`](.env.example)** — local env vars with comments.
- **[docs/INFRA_CONTRACT.md](docs/INFRA_CONTRACT.md)** — runtime contract for any cloud (DB, Slack, OAuth, production vs local).

---

## Further reading

| Doc | Contents |
|-----|----------|
| [USER_GUIDE.md](docs/USER_GUIDE.md) | End-user features (Home tab, syncs, groups) |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Guided + manual AWS/GCP deploy, CI, GitHub |
| [INFRA_CONTRACT.md](docs/INFRA_CONTRACT.md) | Environment variables and platform expectations |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Sync flow, AWS reference architecture |
| [BACKUP_AND_MIGRATION.md](docs/BACKUP_AND_MIGRATION.md) | Backup/restore and federation migration |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | HTTP routes and Slack events |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

### Project layout

```
syncbot/
├── syncbot/           # App (app.py); slack_manifest_scopes.py = bot/user OAuth scope lists (manifest + SLACK_BOT_SCOPES / SLACK_USER_SCOPES)
├── syncbot/db/alembic/  # Migrations (bundled with app for Lambda)
├── tests/
├── docs/
├── infra/aws/         # SAM, bootstrap stack
├── infra/gcp/         # Terraform
├── deploy.sh          # Root launcher (macOS / Linux / Git Bash)
├── deploy.ps1         # Windows launcher → Git Bash or WSL → infra/.../deploy.sh
├── slack-manifest.json
└── docker-compose.yml
```

## License

**AGPL-3.0** — see [LICENSE](LICENSE).
