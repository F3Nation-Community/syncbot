# Development Guide

How to run SyncBot locally (Dev Container, Docker Compose, native Python) and manage dependencies. For **cloud deploy** and CI/CD, see [DEPLOYMENT.md](DEPLOYMENT.md). For runtime env vars in any environment, see [INFRA_CONTRACT.md](INFRA_CONTRACT.md).

## Branching (upstream vs downstream)

The **upstream** repository ([F3Nation-Community/syncbot](https://github.com/F3Nation-Community/syncbot)) is the shared codebase. Each deployment maintains its own **fork**:

| Branch | Role |
|--------|------|
| **`main`** | Tracks upstream. Use it to merge PRs and to **sync with the upstream repository** (`git pull upstream main`, etc.). |
| **`test`** / **`prod`** | On your fork, use these for **deployments**: GitHub Actions deploy workflows run on **push** to `test` and `prod` (see [DEPLOYMENT.md](DEPLOYMENT.md)). |

Typical flow: develop on a feature branch → open a PR to **`main`** → merge → when ready to deploy, merge **`main`** into **`test`** or **`prod`** on your fork.

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

**Needs:** Python 3.14+, Poetry. Run MySQL locally (e.g. `docker run ... mysql:8`) or SQLite. See [`.env.example`](../.env.example) and [INFRA_CONTRACT.md](INFRA_CONTRACT.md).

## Configuration reference

- **[`.env.example`](../.env.example)** — local env vars with comments.
- **[INFRA_CONTRACT.md](INFRA_CONTRACT.md)** — runtime contract for any cloud (DB, Slack, OAuth, production vs local).

## Project layout

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

## Dependency management

After `poetry add` / `poetry update`, regenerate the pinned file used by the Docker image and **`pip-audit`** in CI so it matches `poetry.lock`:

```bash
poetry self add poetry-plugin-export   # Poetry 2.x; once per Poetry install
poetry export -f requirements.txt --without-hashes -o syncbot/requirements.txt
```

The root **`./deploy.sh`** may run `poetry update` and regenerate `syncbot/requirements.txt` when Poetry is on your `PATH` (see [DEPLOYMENT.md](DEPLOYMENT.md)).

CI runs `pip-audit` on `syncbot/requirements.txt` and `infra/aws/db_setup/requirements.txt` (see [.github/workflows/ci.yml](../.github/workflows/ci.yml)).
