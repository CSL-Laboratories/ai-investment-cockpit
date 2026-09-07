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

from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from schemas.models.risk_decision import RiskDecision

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


def _canonical_facts(risk_decision) -> dict:
    """The rendered risk facts in JSON-native canonical form — the comparison basis for
    the signed-audit reconcile (BUG-136). Mirrors exactly what the render functions show."""
    checks = getattr(risk_decision, "checks", {}) or {}
    failed = getattr(risk_decision, "failed_checks", []) or []
    warnings = getattr(risk_decision, "warnings", []) or []
    ceil = getattr(risk_decision, "max_allowed_notional", None)
    verdict = getattr(risk_decision, "execution_verdict", None)
    return {
        "decision": getattr(risk_decision, "decision", None),
        "checks": _check_status(_check_names(checks), failed),
        "failed_checks": list(failed),
        "warnings": list(warnings),
        "max_allowed_notional": None if ceil is None else str(ceil),
        "human_required": bool(getattr(risk_decision, "human_required", False)),
        "execution_verdict": verdict if isinstance(verdict, dict) else None,
    }


def _canonical_audit(audit_payload: dict) -> dict:
    """The signed copy in the same canonical shape. ``checks`` arrive as the WP4
    ``{name, passed, hard, detail}`` records; only name+pass/fail is operator-visible,
    so the comparison uses the same projection as the render."""
    payload = audit_payload or {}
    failed = list(payload.get("failed_checks") or [])
    names: list[str] = []
    seen: set[str] = set()
    for c in payload.get("checks") or []:
        name = str(c.get("name")) if isinstance(c, dict) and "name" in c else str(c)
        if name not in seen:
            seen.add(name)
            names.append(name)
    check_rows = [
        {"name": name, "status": "fail" if name in set(failed) else "pass"}
        for name in names
    ]
    ceil = payload.get("max_allowed_notional")
    verdict = payload.get("execution_verdict")
    return {
        "decision": payload.get("decision"),
        "checks": check_rows,
        "failed_checks": failed,
        "warnings": list(payload.get("warnings") or []),
        "max_allowed_notional": None if ceil is None else str(ceil),
        "human_required": bool(payload.get("human_required", False)),
        "execution_verdict": verdict if isinstance(verdict, dict) else None,
    }


def verified_risk_facts(st, risk_decision_id: str) -> dict | None:
    """The HMAC-verified signed risk-fact copy for a RiskDecision row, or None.

    Returns the payload of the LATEST verified ``risk_decision`` audit entry bound to
    this row (refs.model + refs.row_id, stamped by ``record_state_change``). None when
    no verified entry covers the row — which itself fails the render closed. Read-only.
    """
    if not risk_decision_id:
        return None
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == "risk_decision")
            .order_by(AuditEntry.seq.desc())
        ).scalars().all()
        wanted = [
            e for e in entries
            if (e.refs or {}).get("model") == RiskDecision.__tablename__
            and (e.refs or {}).get("row_id") == risk_decision_id
            and st.audit.verify_entry(e)
        ]
    if not wanted:
        return None
    return dict(wanted[0].payload or {})


def render_risk_section(risk_decision, *, audit_facts: dict | None = None) -> dict:
    """The risk block, sourced 100% from the deterministic ``RiskDecision`` (SEC-17).

    BUG-136: the row is MUTABLE — a keyless DB writer can repaint it after the signed
    ``risk_decision`` audit entry is written, and the operator would then see forged
    risk facts. When ``audit_facts`` (the HMAC-verified signed copy, see
    :func:`verified_risk_facts`) is supplied, the row is reconciled against it FIRST:
    any mismatch renders an UNVERIFIED refusal block instead of risk facts. ``None``
    keeps the legacy direct render (tests, same-tick paths) and is marked as such.
    """
    if audit_facts is not None and (
        "decision" not in (audit_facts or {})
        or _canonical_facts(risk_decision) != _canonical_audit(audit_facts)
    ):
        return {
            "decision": None,
            "checks": [],
            "failed_checks": [],
            "warnings": [],
            "max_allowed_notional": None,
            "human_required": True,
            "verified": False,
            "unverified": True,
            "reason": "risk facts disagree with the signed audit record — do not act",
        }
    checks = getattr(risk_decision, "checks", {}) or {}
    failed = getattr(risk_decision, "failed_checks", []) or []
    warnings = getattr(risk_decision, "warnings", []) or []
    ceil = getattr(risk_decision, "max_allowed_notional", None)
    out = {
        "decision": getattr(risk_decision, "decision", None),
        "checks": _check_status(_check_names(checks), failed),
        "failed_checks": list(failed),
        "warnings": list(warnings),
        "max_allowed_notional": None if ceil is None else str(ceil),
        "human_required": bool(getattr(risk_decision, "human_required", False)),
    }
    out["verified"] = audit_facts is not None
    return out


_VERDICT_LABEL = {
    "ok": "OK",
    "marginal": "MARGINAL — human review",
    "uneconomic": "UNECONOMIC — blocked",
    "insufficient_data": "NOT PRICEABLE — missing L1/ADV/volatility/borrow input",
}


def render_execution_section(risk_decision, *, audit_facts: dict | None = None) -> dict:
    """The execution-quality block (QT-06), sourced STRICTLY from the ``RiskDecision`` row.

    Same rule as ``render_risk_section`` and for the same reason: the operator decides from
    what they see, so nothing here may originate from a model-authored field. The verdict is
    computed server-side by ``quant/verify.py`` from a book snapshot and persisted on this
    row — it is a deterministic risk fact, and it is read from the deterministic record.

    This function DELIBERATELY takes the ``RiskDecision`` rather than a verdict dict. Given a
    verdict parameter, some future caller would eventually pass
    ``proposal["execution_verdict"]`` — a model-authored field on a model-authored object —
    and a hijacked agent could then paint "cost: 1bp, plenty of depth" over an order that has
    neither. Taking the row makes that call impossible to write.

    ``None`` when no verdict was computed, which is the normal state while no book feed is
    wired. Rendered as "not evaluated" — never as "evaluated and fine".

    BUG-136: when ``audit_facts`` is supplied, the row's verdict is reconciled against the
    signed copy first; a mismatch renders UNVERIFIED instead of execution facts.
    """
    verdict = getattr(risk_decision, "execution_verdict", None)
    if audit_facts is not None:
        signed = (audit_facts or {}).get("execution_verdict")
        signed_norm = signed if isinstance(signed, dict) else None
        row_norm = verdict if isinstance(verdict, dict) else None
        # No "decision" key means no genuine signed risk payload covers this row
        # (e.g. the verified-entry lookup came back empty) — refuse regardless.
        if row_norm != signed_norm or "decision" not in (audit_facts or {}):
            return {
                "evaluated": False,
                "summary": "UNVERIFIED — execution facts disagree with the signed "
                           "audit record — do not act",
                "verified": False,
                "unverified": True,
            }
    if not isinstance(verdict, dict) or not verdict:
        return {
            "evaluated": False,
            "summary": "not evaluated — no order book was available for this symbol",
        }

    name = str(verdict.get("verdict", ""))
    return {
        "evaluated": True,
        "verdict": name,
        "label": _VERDICT_LABEL.get(name, name or "unknown"),
        "expected_cost_bps": verdict.get("expected_cost_bps"),
        "expected_spread_bps": verdict.get("expected_spread_bps"),
        "expected_impact_bps": verdict.get("expected_impact_bps"),
        "expected_fee_bps": verdict.get("expected_fee_bps"),
        "expected_borrow_bps": verdict.get("expected_borrow_bps"),
        "borrow_state": verdict.get("borrow_state"),
        "adv_participation_rate": verdict.get("adv_participation_rate"),
        "net_edge_state": verdict.get("net_edge_state"),
        "expected_gross_return_bps": verdict.get("expected_gross_return_bps"),
        "expected_net_return_bps": verdict.get("expected_net_return_bps"),
        "net_cost_multiple": verdict.get("net_cost_multiple"),
        "min_net_cost_multiple": verdict.get("min_net_cost_multiple"),
        "first_negative_qty": verdict.get("first_negative_qty"),
        "first_negative_notional": verdict.get("first_negative_notional"),
        "cost_curve": list(verdict.get("cost_curve") or []),
        "book_seq": verdict.get("book_seq"),
        "metrics_as_of": verdict.get("metrics_as_of"),
        "reasons": list(verdict.get("reasons") or []),
    }


def render_proposal_embed(proposal: dict, risk_decision,
                          raw_excerpt_fn: Callable[[dict], str] | None = None,
                          audit_facts: dict | None = None) -> dict:
    """Build the operator-facing embed dict for a proposal + its RiskDecision.

    ``proposal`` is the TradeProposal dict (model-authored ORDER parameters + prose).
    ``risk_decision`` is the deterministic WP4 output — the ONLY source of the risk section.
    ``raw_excerpt_fn(source_ref) -> str`` returns a deterministic substring of the raw parquet
    item (by byte offset); it is the ONLY source of a source excerpt. If it is ``None`` the
    sources block is rendered as unavailable rather than falling back to model text (SEC-17).
    ``audit_facts`` is the HMAC-verified signed risk-fact copy (see
    :func:`verified_risk_facts`); when supplied, both risk sections reconcile the mutable
    row against it and refuse to display on mismatch (BUG-136)."""
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
        "risk": render_risk_section(risk_decision, audit_facts=audit_facts),
        # QT-06. Deterministic, from the RiskDecision row only — never from ``proposal``.
        "execution": render_execution_section(risk_decision, audit_facts=audit_facts),
        # model prose, explicitly labelled — informational, never a risk fact:
        "model_thesis": p.get("thesis"),
        "model_invalidation": p.get("invalidation"),
        # SEC-17: operator-facing provenance must be deterministic. ``generated_by`` is a
        # model-authored free-text object (a hijacked model can stuff a PII/position leak into
        # runtime/model), so it is NEVER echoed into the operator embed. A provenance line, if
        # wanted, must come from a server-stamped source (the AgentJob row), not the proposal.
        "sources": sources,
    }
