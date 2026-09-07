"""Run bounded, offline research checks with recorded argv and isolated test state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "astra-hwm-resign-claim-shape-2026-09-06"


def main():
    label, *arguments = sys.argv[1:]
    if not label.replace("-", "").isalnum():
        raise SystemExit("invalid record label")
    run = OUT / label
    run.mkdir(parents=True, exist_ok=False)
    absent_config = run / "absent.env"
    env = {k: v for k, v in os.environ.items() if not k.startswith("COCKPIT_")}
    env.update(
        COCKPIT_ENV_FILE=str(absent_config),
        PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        HWM_RESEARCH_OBSERVATIONS=str(run / "observations"),
    )
    interpreter = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    argv = [str(interpreter), *arguments]
    if arguments[:2] == ["-m", "pytest"]:
        argv.extend([
            "-p", "no:cacheprovider", "--basetemp", str(run / "tmp"),
            "--junitxml", str(run / "junit.xml"),
        ])
    started = datetime.now(timezone.utc).isoformat()
    before = time.monotonic()
    with (run / "output.log").open("x", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                argv, cwd=ROOT / "app", env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=300, check=False,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write("\nResearch check exceeded its 300-second bound.\n")
    record = {
        "argv": argv, "cwd": str(ROOT / "app"), "started_utc": started,
        "elapsed_seconds": round(time.monotonic() - before, 3), "exit_code": code,
        "isolation": "synthetic databases only; null/in-memory keyring; absent dotenv; no network fixtures",
    }
    (run / "command.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))
    print((run / "output.log").read_text()[-16000:])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
