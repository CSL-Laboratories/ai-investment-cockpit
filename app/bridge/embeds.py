"""WP6 — deterministic operator-facing rendering (SEC-17).

The operator decides from what they SEE. So no risk value or source fact an operator reads may
originate from a model-emitted field: it would let a prompt-injected agent paint a benign
picture over a dangerous order.

Two rules, enforced structurally here:
  * the RISK section is rendered STRICTLY from the deterministic ``RiskDecision`` (WP4 output) —
    each check as pass/fail, the notional ceiling, the failed checks and warnings — NEVER from
    any field on the proposal (which is model-authored);
  * any SOURCE excerpt is produced by ``raw_excerpt_fn`` — a deterministic substring of the raw
    parquet item by stored byte offset — NEVER the ``source_refs`` excerpt text the model may
    have written.

The model's own prose (``thesis`` / ``invalidation``) IS shown, but explicitly labelled as
model output and kept OUT of the risk section: it informs, it never establishes a risk fact.

``render_proposal_embed`` returns a plain ``dict`` (no discord dependency) so it is fully
unit-testable; ``bridge/bot.py`` turns it into a ``discord.Embed``.
"""
from __future__ import annotations

from typing import Callable

# Deterministic LOCATOR keys we may echo from a source_ref. Anything else (e.g. a model-written
# ``excerpt``/``summary``/``text`` field) is DROPPED — only the raw-corpus resolver may produce
# operator-facing source text (SEC-17).
_LOCATOR_KEYS = frozenset({
    "parquet", "path", "uri", "source_id", "id", "offset", "byte_offset", "length", "line", "ts",
})


def _sanitize_ref(ref) -> dict:
    if not isinstance(ref, dict):
        return {"ref": str(ref)}
    return {k: v for k, v in ref.items() if k in _LOCATOR_KEYS}


def _check_status(check_names, failed_checks) -> list[dict]:
    """pass/fail per check, derived ONLY from the RiskDecision. A check is 'fail' iff its name
    is in the deterministic ``failed_checks`` list — never from a proposal-supplied verdict."""
    failed = set(failed_checks or [])
    rows = []
    for name in check_names:
        rows.append({"name": name, "status": "fail" if name in failed else "pass"})
    return rows


def _check_names(checks) -> list[str]:
    if isinstance(checks, dict):
        return sorted(checks.keys())
    if isinstance(checks, (list, tuple)):
        # list of {name: ...} or bare names
        out = []
        for c in checks:
            if isinstance(c, dict) and "name" in c:
                out.append(str(c["name"]))
            else:
                out.append(str(c))
        return out
    return []


def render_risk_section(risk_decision) -> dict:
    """The risk block, sourced 100% from the deterministic ``RiskDecision`` (SEC-17)."""
    checks = getattr(risk_decision, "checks", {}) or {}
    failed = getattr(risk_decision, "failed_checks", []) or []
    warnings = getattr(risk_decision, "warnings", []) or []
    ceil = getattr(risk_decision, "max_allowed_notional", None)
    return {
        "decision": getattr(risk_decision, "decision", None),
        "checks": _check_status(_check_names(checks), failed),
        "failed_checks": list(failed),
        "warnings": list(warnings),
        "max_allowed_notional": None if ceil is None else str(ceil),
        "human_required": bool(getattr(risk_decision, "human_required", False)),
    }


def render_proposal_embed(proposal: dict, risk_decision,
                          raw_excerpt_fn: Callable[[dict], str] | None = None) -> dict:
    """Build the operator-facing embed dict for a proposal + its RiskDecision.

    ``proposal`` is the TradeProposal dict (model-authored ORDER parameters + prose).
    ``risk_decision`` is the deterministic WP4 output — the ONLY source of the risk section.
    ``raw_excerpt_fn(source_ref) -> str`` returns a deterministic substring of the raw parquet
    item (by byte offset); it is the ONLY source of a source excerpt. If it is ``None`` the
    sources block is rendered as unavailable rather than falling back to model text (SEC-17)."""
    p = proposal or {}

    # ORDER parameters — the concrete order the operator is authorizing. These are proposal
    # fields (there is deliberately no execute_now/notional_override in the schema), rendered
    # verbatim; they are NOT risk facts.
    order = {
        "symbol": p.get("symbol"),
        "side": p.get("side"),
        "asset_type": p.get("asset_type"),
        "order_type": p.get("order_type"),
        "quantity": p.get("quantity"),
        "notional": p.get("notional"),
        "time_horizon": p.get("time_horizon"),
        "sizing_basis": p.get("sizing_basis"),
    }

    # SOURCE excerpts — deterministic substrings of the raw corpus, NEVER the model's
    # source_refs excerpt text.
    sources = []
    for ref in (p.get("source_refs") or []):
        excerpt = None
        if raw_excerpt_fn is not None:
            try:
                excerpt = raw_excerpt_fn(ref)
            except Exception as exc:                       # noqa: BLE001 - render must not crash
                excerpt = f"[excerpt unavailable: {exc}]"
        else:
            excerpt = "[excerpt unavailable: no raw-source resolver]"
        # Echo only the deterministic locator (never a model-written excerpt on the ref).
        sources.append({"ref": _sanitize_ref(ref), "excerpt": excerpt})

    side = (p.get("side") or "").upper()
    symbol = p.get("symbol") or "?"
    otype = p.get("order_type") or "?"

    return {
        "title": f"{side} {symbol} — {otype}",
        "proposal_id": p.get("proposal_id"),
        "order": order,
        "risk": render_risk_section(risk_decision),
        # model prose, explicitly labelled — informational, never a risk fact:
        "model_thesis": p.get("thesis"),
        "model_invalidation": p.get("invalidation"),
        # SEC-17: operator-facing provenance must be deterministic. ``generated_by`` is a
        # model-authored free-text object (a hijacked model can stuff a PII/position leak into
        # runtime/model), so it is NEVER echoed into the operator embed. A provenance line, if
        # wanted, must come from a server-stamped source (the AgentJob row), not the proposal.
        "sources": sources,
    }
