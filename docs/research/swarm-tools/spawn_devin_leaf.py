#!/usr/bin/env python3
"""Spawn a Devin leaf with the frozen argv profile. Refuse forbidden flags."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FORBIDDEN = {
    "-r",
    "--resume",
    "-c",
    "--continue",
    "dangerous",
    "bypass",
    "yolo",
}

ALLOWED_MODELS = {"swe-2-medium", "swe-2-high", "swe-2-max", "swe"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--model", default="swe-2-medium")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if args.model not in ALLOWED_MODELS:
        sys.stderr.write(f"refusing model {args.model!r}\n")
        return 2
    brief = args.brief.resolve()
    if not brief.is_file():
        sys.stderr.write(f"missing brief {brief}\n")
        return 2
    rel_brief = os.path.relpath(brief, args.cwd.resolve())
    argv_cmd = [
        "devin",
        "--sandbox",
        "--permission-mode",
        "autonomous",
        "--model",
        args.model,
        "--respect-workspace-trust",
        "false",
        "--prompt-file",
        str(brief),
        "--export",
        str(args.export.resolve()),
        "-p",
    ]
    joined = " ".join(argv_cmd)
    for token in FORBIDDEN:
        if token in argv_cmd:
            sys.stderr.write(f"forbidden flag {token} in argv\n")
            return 2
    if "dangerous" in joined or "bypass" in joined or "yolo" in joined:
        sys.stderr.write("forbidden permission mode\n")
        return 2
    if shutil.which("devin") is None:
        sys.stderr.write("devin not on PATH\n")
        return 2
    args.export.parent.mkdir(parents=True, exist_ok=True)
    print("spawn:", " ".join(argv_cmd), flush=True)
    return subprocess.call(argv_cmd, cwd=str(args.cwd))


if __name__ == "__main__":
    raise SystemExit(main())
