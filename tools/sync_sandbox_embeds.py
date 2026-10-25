#!/usr/bin/env python3
"""Refuse to re-inline scripts/ci sources into claude.yml (CSL-474 size fix).

Trusted jobs copy these files from github.workflow_sha. Re-embedding them
crosses GitHub's 500 KB workflow-file limit and disables Claude PR Fixer.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YML = ROOT / ".github" / "workflows" / "claude.yml"

BANNED = (
    "<<'CSL_C_",
    "<<'CSL_E3_",
    "<<'CLAUDE_FIXER_OUTBOX'",
    "<<'CLAUDE_FIXER_RECEIPTS'",
    "<<'CLAUDE_FIXER_PUBLISH'",
    "<<'CLAUDE_FIXER_PREFLIGHT'",
    "<<'CLAUDE_PR_FIXER_GATE'",
    "<<'CLAUDE_DENIALS_REPORT'",
)


def main() -> None:
    text = YML.read_text(encoding="utf-8")
    hits = [tag for tag in BANNED if tag in text]
    if hits:
        raise SystemExit(
            "claude.yml re-inlined trusted sources; copy from "
            f"github.workflow_sha instead: {hits}"
        )
    if "ref: ${{ github.workflow_sha }}" not in text:
        raise SystemExit("claude.yml missing github.workflow_sha checkout")
    print("ok", YML, "bytes", YML.stat().st_size)


if __name__ == "__main__":
    main()
