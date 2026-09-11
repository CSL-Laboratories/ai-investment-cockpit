#!/usr/bin/env python3
"""Spawn a Devin leaf with the frozen argv profile. Refuse forbidden flags."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Measured 2026-09-11, Devin CLI 3000.10.21:
# `--sandbox always uses the autonomous permission mode; ignoring
# --permission-mode accept-edits`. Under that forced mode, `exec` runs
# (sandboxed) and `write` is rejected in print mode. Unattended filing leaves
# therefore cannot combine `--sandbox` with a filing write unless we take
# `dangerous` (forbidden). Frozen filing profile: accept-edits, no --sandbox.
# Exec is not auto-approved in this mode; print mode rejects it. `smart` /
# `dangerous` / `bypass` / `yolo` remain banned.
FROZEN_PERMISSION_MODE = "accept-edits"

FORBIDDEN = {
    "-r",
    "--resume",
    "-c",
    "--continue",
    "dangerous",
    "bypass",
    "yolo",
    "smart",
    "autonomous",
    "--sandbox",
    "--full-auto",
    "--dangerously-skip-permissions",
}

ALLOWED_MODELS = {"swe-2-medium", "swe-2-high", "swe-2-max", "swe"}


def frozen_argv(*, brief: Path, export: Path, model: str) -> list[str]:
    return [
        "devin",
        "--permission-mode",
        FROZEN_PERMISSION_MODE,
        "--model",
        model,
        "--respect-workspace-trust",
        "false",
        "--prompt-file",
        str(brief),
        "--export",
        str(export),
        "-p",
    ]


def forbidden_token(argv_cmd: list[str]) -> str | None:
    for token in argv_cmd:
        if token in FORBIDDEN:
            return token
        lowered = token.lower()
        if "dangerous" in lowered or "yolo" in lowered or "bypass" in lowered:
            return token
    return None


def refuse_permission_env() -> str | None:
    value = os.environ.get("DEVIN_PERMISSION_MODE", "")
    if value and value != FROZEN_PERMISSION_MODE:
        return value
    return None


def refuse_sandbox_env() -> str | None:
    value = os.environ.get("DEVIN_SANDBOX", "")
    if value.lower() in {"1", "true", "yes"}:
        return value
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--model", default="swe-2-medium")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print frozen argv and exit without spawning Devin.",
    )
    args = parser.parse_args(argv)

    if args.model not in ALLOWED_MODELS:
        sys.stderr.write(f"refusing model {args.model!r}\n")
        return 2
    env_mode = refuse_permission_env()
    if env_mode is not None:
        sys.stderr.write(f"refusing DEVIN_PERMISSION_MODE={env_mode!r}\n")
        return 2
    sandbox_env = refuse_sandbox_env()
    if sandbox_env is not None:
        sys.stderr.write(
            f"refusing DEVIN_SANDBOX={sandbox_env!r} "
            "(CLI forces autonomous and rejects write)\n"
        )
        return 2
    brief = args.brief.resolve()
    if not brief.is_file():
        sys.stderr.write(f"missing brief {brief}\n")
        return 2
    argv_cmd = frozen_argv(
        brief=brief,
        export=args.export.resolve(),
        model=args.model,
    )
    token = forbidden_token(argv_cmd)
    if token is not None:
        sys.stderr.write(f"forbidden flag {token} in argv\n")
        return 2
    print("spawn:", " ".join(argv_cmd), flush=True)
    if args.dry_run:
        return 0
    if shutil.which("devin") is None:
        sys.stderr.write("devin not on PATH\n")
        return 2
    args.export.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DEVIN_PERMISSION_MODE"] = FROZEN_PERMISSION_MODE
    env.pop("DEVIN_SANDBOX", None)
    return subprocess.call(argv_cmd, cwd=str(args.cwd), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
