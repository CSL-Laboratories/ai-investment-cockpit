"""Test helper: the sha256 of a file as committed at an exact commit, or None."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def blob_sha256_at(root: Path, commit: str, path: str) -> str | None:
    shown = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, check=False,
    )
    if shown.returncode != 0:
        return None
    return hashlib.sha256(shown.stdout).hexdigest()
