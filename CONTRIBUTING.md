# Contributing

Thanks for helping improve SyncBot.

## Workflow

1. **Fork** the repository and create a branch from **`main`**.
2. Open a **pull request** targeting **`main`** on the upstream repo (or the repo you were asked to contribute to).
3. Keep application code **provider-neutral**: put cloud-specific logic only under `infra/<provider>/` and in `deploy-<provider>.yml` workflows. See [docs/INFRA_CONTRACT.md](docs/INFRA_CONTRACT.md) (Fork Compatibility Policy).

## Before you submit

- Run **`pre-commit run --all-files`** (install with `pip install pre-commit && pre-commit install` if needed).
- Ensure **CI passes**: requirements export check, SAM template lint, and tests (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).
- If you change dependencies in `pyproject.toml`, refresh the lockfile and `syncbot/requirements.txt` as described in the README.

## Questions

Use [GitHub Issues](https://github.com/F3Nation-Community/syncbot/issues) for bugs and feature ideas, or check [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for deploy-related questions.
