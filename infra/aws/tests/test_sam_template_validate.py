"""Structural SAM validation for templates next to this package (``sam validate``).

Requires the AWS SAM CLI on PATH; skipped when missing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

INFRA_AWS = Path(__file__).resolve().parent.parent


def _which(name: str) -> str | None:
    return shutil.which(name)


@pytest.mark.parametrize(
    "name",
    ["template.yaml", "template.bootstrap.yaml"],
)
def test_sam_template_validates(name: str) -> None:
    """Same class of checks as ``sam build``, without packaging."""
    sam = _which("sam")
    if not sam:
        pytest.skip("sam CLI not on PATH")
    template = INFRA_AWS / name
    assert template.is_file(), f"missing {template}"
    proc = subprocess.run(
        [sam, "validate", "-t", str(template), "--lint"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"sam validate failed:\n{proc.stdout}\n{proc.stderr}"
