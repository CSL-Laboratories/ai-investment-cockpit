"""Offline runner estimates from normalized Actions job exports; never billing proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

# CSL-255 planning assumptions, checked 2026-10-17. No credits deducted.
RATES = {
    "blacksmith-2vcpu-ubuntu-2404": Decimal("0.004"),
    "blacksmith-2vcpu-windows-2025": Decimal("0.008"),
}
PUBLIC_CORE = {"ubuntu-latest", "macos-latest"}
REPOSITORIES = {
    "CSL-Laboratories/ai-investment-cockpit",
    "CSL-Laboratories/recursion",
}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return result


def estimate(records: list[dict]) -> dict:
    """Keep all events/outcomes; reject contradictory duplicate job evidence."""
    if not isinstance(records, list) or not records:
        raise ValueError("expected a nonempty list of job records")
    seen = {}
    rows = []
    subtotal = Decimal(0)
    unknown = 0
    for record in records:
        if not isinstance(record, dict):
            raise TypeError("job record must be an object")
        repo = record["repository"]
        if repo not in REPOSITORIES:
            raise ValueError("repository is outside the public CSL core estimate")
        for field in ("run_id", "run_attempt", "job_id"):
            if type(record[field]) is not int or record[field] < 1:
                raise ValueError(f"{field} must be a positive integer")
        for field in ("event", "workflow", "name"):
            if not isinstance(record[field], str) or not record[field].strip():
                raise ValueError(f"missing {field}")
        if not re.fullmatch(r"[0-9a-f]{40}", record["head_sha"]):
            raise ValueError("invalid head SHA")
        labels = record["labels"]
        if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
            raise ValueError("labels must be a list of strings")
        conclusion = record["conclusion"]
        if conclusion is not None and (
            not isinstance(conclusion, str) or conclusion not in {
                "success", "failure", "neutral", "cancelled", "skipped",
                "timed_out", "action_required", "stale", "startup_failure",
            }
        ):
            raise ValueError("invalid job conclusion")
        for field in ("started_at", "completed_at"):
            value = record[field]
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"{field} must be a timestamp or null")
                timestamp(value)
        runner_id = record["runner_id"]
        if runner_id is not None and (type(runner_id) is not int or runner_id < 0):
            raise ValueError("runner_id must be a nonnegative integer or null")
        if not isinstance(record["steps"], list):
            raise TypeError("steps must be the exported job step list")
        # job_id is globally unique within the repository, including reruns.
        key = (repo, record["job_id"])
        if key in seen:
            if seen[key] != record:
                raise ValueError(f"conflicting duplicate job: {key}")
            continue
        seen[key] = record
        row = dict(record)
        row["url"] = (f"https://github.com/{repo}/actions/runs/"
                      f"{record['run_id']}/attempts/{record['run_attempt']}")
        row["estimate_usd"] = None
        row["rounded_minutes"] = None
        start, end = record["started_at"], record["completed_at"]
        # GitHub may stamp (even reverse by one second) skipped timestamps.
        # Require corroborating no-runner/no-step evidence, not outcome alone.
        if conclusion == "skipped" and not runner_id and not record["steps"]:
            row.update(estimate_usd="0.000", reason="not_started_skipped")
        elif not start or not end:
            row["reason"] = "incomplete_timing"
        else:
            elapsed = timestamp(end) - timestamp(start)
            seconds = (Decimal(elapsed.days * 86400 + elapsed.seconds)
                       + Decimal(elapsed.microseconds) / Decimal(1000000))
            if seconds < 0:
                raise ValueError("job ends before it starts")
            minutes = max(1, int((seconds / 60).to_integral_value(rounding=ROUND_CEILING)))
            row["rounded_minutes"] = minutes
            # Exact single requested label only: custom/larger/paid macOS is unknown.
            rate = RATES.get(labels[0]) if len(labels) == 1 else None
            if len(labels) == 1 and labels[0] in PUBLIC_CORE:
                rate = Decimal(0)
            if rate is None:
                row["reason"] = "unpriced_runner"
            else:
                row.update(estimate_usd=f"{minutes * rate:.3f}", reason="priced")
        if row["estimate_usd"] is None:
            unknown += 1
        else:
            subtotal += Decimal(row["estimate_usd"])
        rows.append(row)
    return {
        "basis": "estimated runner compute; not a billing receipt or qualification",
        "rate_date": "2026-10-17",
        "export_completeness": "not verified; includes only supplied jobs",
        "known_subtotal_usd": f"{subtotal:.3f}",
        "supplied_jobs_total_usd": None if unknown else f"{subtotal:.3f}",
        "unknown_jobs": unknown,
        "jobs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args()
    try:
        raw = args.ledger.read_bytes()
        result = estimate(json.loads(raw))
        result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"Invalid cost evidence: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 1 if result["unknown_jobs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
