"""Compose a useful empty-run research update from retained slice A inputs.

RW-35: acquisition → retained cited evidence → case → concise delivered
update through the existing compose/deliver path. No LLM, no broker, no
watchlist seed, and no lowered 30-identity gate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from feasibility.inspect import load_cases
from feasibility.screens import load_screens
from feasibility.validate import load_freeze

CLAIM_ID = "RW-35"
UPDATE_KIND = "research_update"
COHORT_MINIMUM = 30


def compose_empty_run_update() -> dict[str, Any]:
    freeze = load_freeze()
    screens = load_screens()
    cases = load_cases()
    latest = screens["screens"][-1]
    counts = latest["counts"]
    classes = Counter(item["failure_class"] for item in cases["cases"])
    kinds = Counter(item["kind"] for item in cases["cases"])
    cited_cases = [
        {
            "case_id": item["case_id"],
            "kind": item["kind"],
            "failure_class": item["failure_class"],
            "source": item["source"],
            "as_of": item["as_of"],
            "next_trigger": item["evidence_that_would_change_it"],
        }
        for item in cases["cases"]
    ]
    cash_hits = counts["cash_parse_hits"]
    cash_symbols = latest.get("cash_parse_hit_symbols") or ()
    if cash_hits:
        hit_list = ", ".join(cash_symbols) if cash_symbols else "cited"
        opposing = (
            f"{counts['supported_heads']} supported SEC heads were reachable, and "
            f"cash_parse_hits={cash_hits} ({hit_list}), but a cash-parse hit is "
            "not eligibility: selection-ready and date-ready remain 0."
        )
    else:
        opposing = (
            f"{counts['supported_heads']} supported SEC heads were reachable, but "
            f"cash_parse_hits={cash_hits}, so mechanics succeeded "
            "while eligibility/extraction did not."
        )
    unknowns = [
        item["id"] for item in cases["missing_decisions"]
    ]
    next_trigger = screens["cohort_plan"]["next_owner_action"]
    summary = (
        f"Latest GET-only board {latest['id']} is empty/date-not-ready: "
        f"pool {counts['pool']}, supported {counts['supported_heads']}, "
        f"cash {counts['cash_parse_hits']}, selection-ready "
        f"{counts['selection_ready']}, date-ready {counts['date_ready']}, "
        f"cohort {counts['cohort']}≪{COHORT_MINIMUM}. qualify_count stays "
        f"{screens['qualify_count']}. This is useful research, not a launch."
    )
    return {
        "kind": UPDATE_KIND,
        "claim_id": CLAIM_ID,
        "empirical_pass_claimed": False,
        "qualify_count": screens["qualify_count"],
        "meets_cohort_gate": False,
        "cohort_minimum": COHORT_MINIMUM,
        "freeze_schema_version": freeze["schema_version"],
        "latest_screen_id": latest["id"],
        "as_of": latest["screen_date"],
        "source": latest["evidence_note"],
        "significance": summary,
        "supporting_evidence": (
            f"Cited GET-only screen {latest['id']} (claim {screens['claim_id']}) "
            f"and RW-29 cases "
            f"({kinds['empty']} empty, {kinds['rejected']} rejected, "
            f"{kinds['useful']} useful, {kinds['unverified']} unverified)."
        ),
        "opposing_evidence": opposing,
        "unknowns": unknowns,
        "next_trigger": next_trigger,
        "failure_classes": dict(classes),
        "cases": cited_cases,
        "held_out_rule": screens["held_out_rule"],
        "raw_packet_status": latest["raw_packet_status"],
    }


def render_empty_run_update(payload: dict[str, Any]) -> str:
    lines = [
        f"**Empty-run research update — {payload.get('as_of', '?')}**",
        payload.get("significance") or "",
        f"__Opposing__\n{payload.get('opposing_evidence') or ''}",
        f"__Next trigger__\n{payload.get('next_trigger') or ''}",
        (
            f"_qualify_count={payload.get('qualify_count')}; "
            f"cohort gate unmet; empirical_pass_claimed=false_"
        ),
    ]
    return "\n\n".join(line for line in lines if line)
