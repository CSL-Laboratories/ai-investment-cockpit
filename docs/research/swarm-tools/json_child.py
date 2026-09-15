#!/usr/bin/env python3
"""Admitted no-tools JSON child for RW-34.

Reads one stdin envelope and prints one JSONL final object. It does not import
subprocess, socket, urllib, or write files. The controller owns exclusive filings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

MAX_STDIN = 64 * 1024
ALLOWED_ROLES = {"leaf", "first-gate-review"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _excerpts(envelope: dict) -> list[dict]:
    items = envelope.get("excerpts")
    if not isinstance(items, list) or not items:
        raise ValueError("missing excerpts")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid excerpt")
        path = item.get("path")
        digest = item.get("sha256")
        text = item.get("text")
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(text, str):
            raise ValueError("invalid excerpt")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            raise ValueError("excerpt digest mismatch")
        out.append({"path": path, "sha256": digest, "text": text})
    return out


def _leaf(envelope: dict, excerpts: list[dict]) -> dict:
    findings = [
        {
            "id": f"ex{index + 1}",
            "claim": f"Retained excerpt {item['path']} digest {item['sha256'][:12]}",
            "source_path": item["path"],
        }
        for index, item in enumerate(excerpts[:8])
    ]
    return {
        "job_id": envelope["job_id"],
        "claim_id": envelope["claim_id"],
        "attempt": envelope["attempt"],
        "role": "leaf",
        "brief_path": envelope["brief_path"],
        "status": "success",
        "empirical_pass_claimed": False,
        "result": {
            "summary": (
                "No-tools JSON child echoed approved excerpts only. "
                "This is a route-qualification fixture, not empirical PASS."
            ),
            "findings": findings,
        },
        "route_family": "untrusted-child-claim",
    }


def _review(envelope: dict, excerpts: list[dict]) -> dict:
    return {
        "job_id": envelope["job_id"],
        "claim_id": envelope["claim_id"],
        "attempt": envelope["attempt"],
        "role": "first-gate-review",
        "subject_filing_path": envelope.get("subject_filing_path") or excerpts[0]["path"],
        "status": "success",
        "empirical_pass_claimed": False,
        "verdict": "pass-with-dissent",
        "result": {
            "summary": (
                "First-gate review of the admitted fixture. Same-family review is "
                "useful, not independent. No empirical PASS."
            ),
            "findings": [
                {
                    "id": "family",
                    "severity": "note",
                    "claim": "Controller must bind route_family from the admitted profile.",
                    "source_path": excerpts[0]["path"],
                }
            ],
        },
        "route_family": "untrusted-child-claim",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--role", required=True, choices=sorted(ALLOWED_ROLES))
    parser.add_argument("--output-limit", required=True, type=int)
    args = parser.parse_args(argv)
    if args.output_limit < 32 or args.output_limit > MAX_STDIN * 4:
        sys.stderr.write("refusing invalid output limit\n")
        return 2
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    if not raw or len(raw) > MAX_STDIN:
        sys.stderr.write("refusing empty or oversized stdin\n")
        return 2
    try:
        envelope = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(envelope, dict):
            raise ValueError("envelope must be an object")
        excerpts = _excerpts(envelope)
        if args.role == "leaf":
            payload = _leaf(envelope, excerpts)
        else:
            payload = _review(envelope, excerpts)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        sys.stderr.write("refusing invalid envelope\n")
        return 2
    line = json.dumps({"type": "final", "payload": payload}, separators=(",", ":"))
    encoded = (line + "\n").encode("utf-8")
    if len(encoded) > args.output_limit:
        sys.stderr.write("refusing oversized final object\n")
        return 2
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
