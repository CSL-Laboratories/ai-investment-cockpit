#!/usr/bin/env python3
"""Offline route preview. Historical filename; this program cannot spawn a model.

The original draft confused proposed argv with a qualified execution adapter.
RW-33 removes that execution path. --dry-run produces a bounded JSON preview;
omitting it refuses before opening inputs, creating outputs or loading a CLI.
Cloud qualification is a separate claimed package, described in the handoff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BRIEFS_DIR = ROOT / "docs/research/swarm-briefs"
RUNS_DIR = ROOT / ".swarm-runs"
MAX_BRIEF_BYTES = 64 * 1024
RUNTIMES = {"cursor", "codex", "opencode"}
REQUIRED_QUALIFICATION = [
    "exact-host executable/version and supported model/profile",
    "claim, attempt, frozen inputs, policy and disclosure admission",
    "capability/config/credential isolation and acquisition allowlist",
    "atomic reservation, aggregate cap, deadline and cancellation",
    "exclusive outputs, bounded transport parsing and schema intake",
    "usage receipt, conservative unknown settlement and family separation",
]


class RefusalParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default error echoes unknown arguments, including secrets.
        self.exit(2, "refusing invalid arguments; use --help for the preview interface\n")


def _within(path: Path, base: Path) -> bool:
    return path != base and path.is_relative_to(base)


def main(argv: list[str] | None = None) -> int:
    parser = RefusalParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--model", required=True, help="requested pin; availability is unverified")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run:
        sys.stderr.write("refusing dispatch: all CLI adapters remain unqualified; preview only\n")
        return 2
    if args.runtime not in RUNTIMES:
        sys.stderr.write("refusing unsupported runtime\n")
        return 2
    if not args.model or len(args.model) > 128 or any(c.isspace() for c in args.model):
        sys.stderr.write("refusing invalid requested model pin\n")
        return 2
    try:
        brief = args.brief.resolve(strict=True)
        export = args.export.resolve()
        if not _within(brief, BRIEFS_DIR.resolve()) or not brief.is_file():
            raise ValueError("brief outside approved directory")
        if not _within(export, RUNS_DIR.resolve()) or export.exists():
            raise ValueError("export outside runs or already exists")
        with brief.open("rb") as source:
            prompt = source.read(MAX_BRIEF_BYTES + 1)
        if not prompt or len(prompt) > MAX_BRIEF_BYTES:
            raise ValueError("empty or oversized brief")
    except (OSError, RuntimeError, ValueError):
        sys.stderr.write("refusing invalid brief/export path or brief size\n")
        return 2
    print(json.dumps({
        "kind": "unqualified-route-preview",
        "runtime": args.runtime,
        "requested_model": args.model,
        "dispatch_enabled": False,
        "model_available": None,
        "empirical_pass_claimed": False,
        "brief_sha256": hashlib.sha256(prompt).hexdigest(),
        "brief_bytes": len(prompt),
        "output_created": False,
        "required_qualification": REQUIRED_QUALIFICATION,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
