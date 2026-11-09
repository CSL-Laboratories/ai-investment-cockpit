#!/usr/bin/env python3
"""Product-contract pin check: contract/PRODUCT.pin.json names exact csl-research bytes.

The product contract lives in csl-research; this repository only links to it by
repository, full commit, path and SHA-256 (CSL-585, CSL-796). Same
csl-product-pin/v1 format and checks as Recursion's tools/check_product_pin.py.
Deterministic and provider-free. Run from the repository root:

    python -X utf8 -B tools/check_product_pin.py
    python -X utf8 -B tools/check_product_pin.py --vault ../csl-research

The default run checks only the pin's shape. --vault reads the pinned blob from a
local csl-research clone and compares its SHA-256; it never touches the network.
A pass does not accept, admit or bind the contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = ROOT / "contract" / "PRODUCT.pin.json"

FORMAT = "csl-product-pin/v1"
REPOSITORY = "CSL-Laboratories/csl-research"
KEYS = {"format", "status", "repository", "path", "commit", "sha256", "claim", "limitations"}
PINNED_FIELDS = ("path", "commit", "sha256", "claim")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def errors(pin: object) -> list[str]:
    """Return every reason the pin is malformed; empty means the shape is valid."""
    if not isinstance(pin, dict):
        return ["pin must be a JSON object"]
    out = []
    missing = sorted(KEYS - pin.keys())
    extra = sorted(pin.keys() - KEYS)
    if missing:
        out.append(f"missing keys: {', '.join(missing)}")
    if extra:
        out.append(f"unknown keys: {', '.join(extra)}")
    if pin.get("format") != FORMAT:
        out.append(f"format must be {FORMAT!r}")
    if pin.get("repository") != REPOSITORY:
        out.append(f"repository must be {REPOSITORY!r}")
    limitations = pin.get("limitations")
    if not isinstance(limitations, str) or not limitations.strip():
        out.append("limitations must be a non-empty string")

    status = pin.get("status")
    if status == "awaiting":
        for key in PINNED_FIELDS:
            if pin.get(key) is not None:
                out.append(f"{key} must be null while status is awaiting")
    elif status == "pinned":
        path = pin.get("path")
        if not isinstance(path, str) or not path:
            out.append("path must be a non-empty string")
        else:
            parts = path.split("/")
            if path.startswith("/") or "\\" in path or {"", ".", ".."} & set(parts):
                out.append("path must be a relative posix path inside the repository")
        if not isinstance(pin.get("commit"), str) or not COMMIT.fullmatch(pin["commit"]):
            out.append("commit must be a full 40-hex lowercase SHA")
        if not isinstance(pin.get("sha256"), str) or not SHA256.fullmatch(pin["sha256"]):
            out.append("sha256 must be 64 lowercase hex characters")
        claim = pin.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            out.append("claim must be a non-empty string")
    else:
        out.append("status must be 'awaiting' or 'pinned'")
    return out


def vault_errors(pin: dict, vault: Path) -> list[str]:
    """Compare the pinned blob in a local csl-research clone with the pinned SHA-256."""
    if pin.get("status") != "pinned":
        return []
    spec = f"{pin['commit']}:{pin['path']}"
    try:
        blob = subprocess.run(
            ["git", "-C", str(vault), "show", spec],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip() if hasattr(exc, "stderr") else exc
        return [f"cannot read {spec} from {vault}: {detail}"]
    digest = hashlib.sha256(blob).hexdigest()
    if digest != pin["sha256"]:
        return [f"sha256 mismatch for {spec}: pinned {pin['sha256']}, vault has {digest}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pin", type=Path, default=PIN)
    parser.add_argument("--vault", type=Path, help="local csl-research clone to verify bytes")
    args = parser.parse_args(argv)

    try:
        pin = json.loads(args.pin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read {args.pin}: {exc}")
        return 1
    problems = errors(pin)
    if not problems and args.vault is not None:
        problems = vault_errors(pin, args.vault)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"PASS: product pin is {pin['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
