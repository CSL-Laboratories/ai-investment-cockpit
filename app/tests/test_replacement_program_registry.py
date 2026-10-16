from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_replacement_program_registry() -> None:
    commands = (
        (sys.executable, "tools/check_replacement_program.py"),
        (sys.executable, "-m", "unittest", "-q", "tools.test_replacement_program"),
    )
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
