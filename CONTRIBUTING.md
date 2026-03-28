# Contributing

Thanks for helping to improve SyncBot!

## Branching (upstream vs downstream)

The **upstream** repository ([F3Nation-Community/syncbot](https://github.com/F3Nation-Community/syncbot)) is the shared codebase. Each deployment maintains its own **fork**:

| Branch | Role |
|--------|------|
| **`main`** | Tracks upstream. Use it to merge PRs and to **sync with the upstream repository** (`git pull upstream main`, etc.). |
| **`test`** / **`prod`** | On your fork, use these for **deployments**: GitHub Actions deploy workflows run on **push** to `test` and `prod` (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)). |

Typical flow: develop a fix or new feature on a branch in your repo → test and deploy to your infra → open a PR to **`upstream/main`**.

### Branch Naming Conventions

Format: `<type>/<description>` or `<type>/<ticket>-<description>`

Types:

- feature/ New functionality
- bugfix/ Bug fixes for existing features
- hotfix/ Urgent production issues
- refactor/ Code improvements without behavior changes
- docs/ Documentation only changes
- chore/ Build process, dependency updates, etc.

Rules:

- Use lowercase
- Separate words with hyphens
- Keep descriptions under 50 characters
- Be specific: feature/user-auth not feature/auth

## Workflow

1. **Fork** the repository and create a branch from **`main`**.
2. Open a **pull request** targeting **`main`** on the upstream repo (or the repo you were asked to contribute to).
3. Keep application code **provider-neutral**: put cloud-specific logic only under `infra/<provider>/` and in `deploy-<provider>.yml` workflows. See [docs/INFRA_CONTRACT.md](docs/INFRA_CONTRACT.md) (Fork Compatibility Policy).

## Before you submit

- Run **`pre-commit run --all-files`** (install with `pip install pre-commit && pre-commit install` if needed).
- Ensure **CI passes**: requirements export check, SAM template lint, and tests (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).
- If you change dependencies in `pyproject.toml`, refresh the lockfile and `syncbot/requirements.txt` as described in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Questions

Use [GitHub Issues](https://github.com/F3Nation-Community/syncbot/issues) for bugs and feature ideas, or check [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for deploy-related questions.
