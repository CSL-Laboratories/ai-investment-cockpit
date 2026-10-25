from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cockpit_research_register() -> None:
    commands = (
        (sys.executable, "tools/check_cockpit_research_register.py"),
        (sys.executable, "-m", "unittest", "-q", "tools.test_cockpit_research_register"),
    )
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
