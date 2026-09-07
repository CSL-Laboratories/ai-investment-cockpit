"""Run isolated offline diagnostics; never import application settings in this parent."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "astra-watchonly-durability-2026-09-06"


def main():
    label, *arguments = sys.argv[1:]
    if not label.replace("-", "").isalnum():
        raise SystemExit("invalid log label")
    OUT.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("COCKPIT_")}
    env.update(
        COCKPIT_ENV_FILE=str(OUT / "absent-test-config.env"),
        PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    argv = [str(ROOT / ".venv" / "bin" / "python"), *arguments]
    start = datetime.now(timezone.utc).isoformat()
    before = time.monotonic()
    with (OUT / f"{label}.log").open("x", encoding="utf-8") as log:
        result = subprocess.run(
            argv, cwd=ROOT / "app", env=env, stdout=log,
            stderr=subprocess.STDOUT, timeout=600, check=False,
        )
    record = {
        "label": label, "argv": argv, "cwd": str(ROOT / "app"), "started_utc": start,
        "elapsed_seconds": round(time.monotonic() - before, 3), "exit_code": result.returncode,
        "isolation": "synthetic temporary databases, null/in-memory keyring, absent dotenv",
    }
    (OUT / f"{label}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))
    print((OUT / f"{label}.log").read_text()[-20000:])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
