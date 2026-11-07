"""Route inventory, beta rubric and maturity projections (F25, grill Q1 and Q37).

The GUI does not invent routes. It shows the route records the control API returns,
re-checks each record's maturity with the rubric below, and lists every route class
Nov 11 needs that the control API did not report, as a gap with its blocker.

Rubric: a route is ``mature`` only when every bar (``BARS``) passed *and* names its
evidence. A failing, missing or evidence-less bar makes it ``beta``. A record that
claims ``mature`` while the rubric says ``beta`` is shown as beta with the claim marked
rejected; a record without a ``maturity`` field is shown as beta and flagged. There is
no score and no majority rule.
"""

from __future__ import annotations

import re

__all__ = ["BARS", "REQUIRED", "assess", "coverage_gaps", "project"]

BARS = ("launch_check", "floor", "conformance", "truthful_stop", "venue_switch", "xf_verify")

# What the Nov 11 inventory must cover (F25, PRODUCT.md 3.3, 7; Q42, 34b). Own-loop
# vendors and venue uses are this slice's reading of the briefing, flagged for review.
REQUIRED = {
    "engine": ("claude_code", "codex", "opencode", "grok", "antigravity", "cursor", "gemini_cli", "pi",
               "hermes"),
    "own_loop": ("anthropic", "openai", "google", "xai"),
    "venue_use": ("paper:own_trading", "kalshi:model_fed_read", "kalshi:model_fed_propose"),
    "surface": ("cli", "desktop", "phone"),
    "mcp": ("2025-06-18", "2026-07-28"),
    "plugin_panels": ("plugin_panels",),
}
_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,96}")


def _subject(record: dict) -> str | None:
    """The thing a record covers inside its class, e.g. the engine id or the MCP version."""
    cls = record.get("class")
    key = {"engine": "engine", "own_loop": "vendor", "surface": "surface", "mcp": "mcp_version"}.get(cls)
    if key:
        return record.get(key)
    if cls == "venue_use":
        return "%s:%s" % (record.get("venue"), record.get("use"))
    if cls == "plugin_panels":
        return "plugin_panels"
    return None


def assess(record: object) -> dict:
    """Return the record as the GUI shows it, with the rubric applied. Never upgrades."""
    if type(record) is not dict:
        return {"id": "invalid", "class": "invalid", "maturity": "beta", "claim": "invalid_record",
                "failed_bars": list(BARS), "blocker": "control API returned a malformed route record"}
    bars = record.get("bars") if type(record.get("bars")) is dict else {}
    failed = []
    for bar in BARS:
        entry = bars.get(bar)
        if not (type(entry) is dict and entry.get("pass") is True and type(entry.get("evidence")) is str
                and entry["evidence"].strip()):
            failed.append(bar)
    rubric = "beta" if failed else "mature"
    claimed = record.get("maturity")
    if claimed not in ("beta", "mature"):
        claim = "missing_maturity"
    elif claimed == "mature" and rubric == "beta":
        claim = "mature_claim_rejected"
    else:
        claim = "ok"
    route_id = record.get("id") if type(record.get("id")) is str and _ID.fullmatch(record["id"]) else "invalid"
    blocker = record.get("blocker") if type(record.get("blocker")) is str else ""
    if failed and not blocker:
        blocker = "not reported by control API"
    out = {
        "id": route_id, "class": str(record.get("class", "")), "subject": str(_subject(record) or ""),
        "sign_in": str(record.get("sign_in", "")), "configured": record.get("configured") is True,
        "maturity": "beta" if (rubric == "beta" or claim != "ok") else "mature",
        "claim": claim, "failed_bars": failed, "blocker": blocker,
    }
    return out


def coverage_gaps(records: list) -> list[dict]:
    """Required route classes the control API did not report. Each gap is beta with a blocker."""
    seen: dict[str, set] = {}
    for record in records:
        if type(record) is dict:
            seen.setdefault(record.get("class"), set()).add(_subject(record))
    gaps = []
    for cls, subjects in REQUIRED.items():
        for subject in subjects:
            if subject not in seen.get(cls, set()):
                gaps.append({"id": "gap:%s:%s" % (cls, subject), "class": cls, "subject": subject,
                             "sign_in": "", "configured": False, "maturity": "beta", "claim": "not_in_inventory",
                             "failed_bars": list(BARS),
                             "blocker": "upstream: control API did not report this route"})
    return gaps


def project(row: dict, surface: str) -> object:
    """The same maturity on every surface: CLI line, MCP object, GUI badge text."""
    if surface == "cli":
        return "%s maturity=%s blocker=%s" % (row["id"], row["maturity"], row["blocker"] or "-")
    if surface == "mcp":
        return {"id": row["id"], "maturity": row["maturity"], "failed_bars": list(row["failed_bars"]),
                "blocker": row["blocker"]}
    if surface == "gui":
        return "BETA" if row["maturity"] == "beta" else "MATURE"
    raise ValueError("unknown surface")
