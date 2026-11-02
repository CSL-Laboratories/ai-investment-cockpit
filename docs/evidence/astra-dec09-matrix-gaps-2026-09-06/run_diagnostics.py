"""Run offline checks on an explicitly selected source; preserve nonzero results."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "astra-dec09-matrix-gaps-2026-09-06"


def source_hashes(source):
    return {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("app", "tools")
        for path in sorted((source / directory).rglob("*.py"))
    }


def main():
    label, source_arg, *arguments = sys.argv[1:]
    if not label.replace("-", "").isalnum():
        raise SystemExit("invalid evidence label")
    source = Path(source_arg).resolve(strict=True)
    if "cockpit-soak" in str(source).lower() or not (source / "app/pyproject.toml").is_file():
        raise SystemExit("refusing soak or non-application source")
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / f"{label}.json").exists():
        raise SystemExit("refusing to overwrite existing evidence")
    before = source_hashes(source)
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("COCKPIT_")
        and k not in {"PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS"}
    }
    env.update(
        COCKPIT_ENV_FILE=str(OUT / "absent-test-config.env"),
        PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    argv = [sys.executable, *arguments]
    start = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="dec09-") as temporary:
        env.update(TMP=temporary, TEMP=temporary, TMPDIR=temporary)
        with (OUT / f"{label}.log").open("x", encoding="utf-8") as log:
            result = subprocess.run(
                argv, cwd=source / "app", env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=900, check=False,
            )
    after = source_hashes(source)
    record = {
        "label": label, "argv": argv, "cwd": str(source / "app"),
        "started_utc": start,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "exit_code": result.returncode, "python": sys.version,
        "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
        "source_changed": before != after, "source_sha256": before,
        "packages": {
            distribution.metadata["Name"]: distribution.version
            for distribution in metadata.distributions()
        },
        "claim": "offline diagnostic only; no new DEC-09, selection or serving-process attestation",
        "isolation": "temporary fixture state, null/in-memory keyring, absent dotenv",
        "log_sha256": hashlib.sha256((OUT / f"{label}.log").read_bytes()).hexdigest(),
    }
    (OUT / f"{label}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in {"packages", "source_sha256"}}, indent=2))
    print((OUT / f"{label}.log").read_text()[-12000:])
    return result.returncode or int(before != after)


if __name__ == "__main__":
    raise SystemExit(main())
