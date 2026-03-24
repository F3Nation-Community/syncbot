"""Smoke-check deploy shell scripts parse with bash -n."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DEPLOY_SCRIPTS = [
    REPO_ROOT / "deploy.sh",
    REPO_ROOT / "infra" / "gcp" / "scripts" / "deploy.sh",
    REPO_ROOT / "infra" / "aws" / "scripts" / "deploy.sh",
]


@pytest.mark.parametrize("path", DEPLOY_SCRIPTS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_bash_syntax(path: Path) -> None:
    assert path.is_file(), f"missing {path}"
    subprocess.run(
        ["bash", "-n", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
