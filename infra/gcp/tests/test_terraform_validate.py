"""Terraform validation for the module next to this package.

``terraform init -backend=false`` may need network access to download providers.
Uses ``TF_DATA_DIR`` in a temp directory so the repo tree is not modified.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

INFRA_GCP = Path(__file__).resolve().parent.parent


def _which(name: str) -> str | None:
    return shutil.which(name)


def test_terraform_validates() -> None:
    tf = _which("terraform")
    if not tf:
        pytest.skip("terraform not on PATH")
    assert INFRA_GCP.is_dir()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["TF_DATA_DIR"] = tmp
        init = subprocess.run(
            [tf, "init", "-backend=false", "-input=false"],
            cwd=INFRA_GCP,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        if init.returncode != 0:
            pytest.skip(
                "terraform init failed (terraform missing or no network for providers?):\n"
                f"{init.stdout}\n{init.stderr}"
            )
        validate = subprocess.run(
            [tf, "validate"],
            cwd=INFRA_GCP,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert validate.returncode == 0, f"terraform validate failed:\n{validate.stdout}\n{validate.stderr}"
