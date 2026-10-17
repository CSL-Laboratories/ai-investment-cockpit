"""Run the CSL-221 qualification evidence controls inside the app suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_qualification_evidence_controls() -> None:
    subprocess.run(
        (sys.executable, "-m", "unittest", "-q", "tools.test_qualification_evidence"),
        cwd=ROOT,
        check=True,
    )
