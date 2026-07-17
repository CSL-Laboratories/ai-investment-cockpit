"""WP8 — deep proposal drafting (contract Owner B 'proposals.py').

Implements docs/build-scaffold-ultracode.md WP8 (deep escalation -> validated TradeProposal
-> deterministic risk gate -> ProposalRecord/RiskDecision/Order rows + operator embed) and
the SEC closures on that path: SEC-09 (scrub thesis/invalidation/sizing_basis BEFORE
anything else; ``blocked`` == quarantined ProposalRecord, STOP — never post), SEC-14
(``evaluate_grounding`` fail-closed over the dispatched packet + the VERIFIED raw store;
a flagged proposal is review-only — grounding never clears), SEC-17 (the embed is
``bridge.embeds.render_proposal_embed``: risk facts only from the RiskDecision, source
excerpts only via the deterministic ``resolve_raw`` locator), SEC-15 (RiskContext from the
audited limits + latest PortfolioSnapshot; absent snapshot -> zeros -> risk fails
conservatively), and Invariant 5 (risk verdicts come ONLY from ``risk.engine.evaluate``).

This module resolves the canonical order tuple and creates the Order row (state
``proposed``) when — and only when — the risk decision is ``approved``. It NEVER posts and
never mints tokens: delivery and veto scheduling live in ``workflows.services`` / the
bridge (SEC-03).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, OK, TIMEOUT
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from bridge.embeds import render_proposal_embed
from core.ids import new_id
from ingest.cramer_signal import inverse_side
from ingest.normalize import escape_untrusted
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.limits import current_limits
from orchestrator.mode_ladder import ModeLadder
from risk.engine import RiskContext, evaluate, order_notional
from schemas.models.market_event import MarketEvent
from schemas.models.order import Order
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from workflows.agent_calls import call_agent
from workflows.constants import EVENT_PROPOSAL
from workflows.events import resolve_raw
from workflows.grounding import evaluate_grounding
from workflows.packets import Packet
from workflows.prompts import load_prompt
from workflows.scrubber import scrub_output_fields


@dataclass(frozen=True)
class ProposalOutcome:
    status: str  # drafted | flagged | needs_human | quarantined | degraded
    proposal: dict | None
    record: ProposalRecord | None
    risk_decision: RiskDecision | None
    order: Order | None
    embed: dict | None
    reasons: tuple[str, ...]


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _latest_snapshot(state) -> PortfolioSnapshot | None:
    with state.session_factory() as s:
        snap = s.execute(
            select(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.ts.desc(), PortfolioSnapshot.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snap is not None:
            s.expunge(snap)
    return snap


def _build_risk_context(state, *, quote, watchlist: frozenset[str]) -> RiskContext:
    """RiskContext from the AUDITED limits (SEC-15) + the latest PortfolioSnapshot row
    (absent -> zeros -> risk fails conservatively) + the injected quote (None ->
    quote_age_s=inf, price 0 -> quote_sane hard-fails)."""
    limits = current_limits(state)
    snap = _latest_snapshot(state)
    cash = _dec(snap.cash) if snap is not None else Decimal("0")
    buying_power = _dec(snap.buying_power) if snap is not None else Decimal("0")
    holdings: dict = {}
    for h in (snap.holdings if snap is not None else []) or []:
        if isinstance(h, dict) and h.get("symbol"):
            holdings[str(h["symbol"]).upper()] = {"market_value": _dec(h.get("market_value", 0))}
    q = quote or {}
    quote_price = _dec(q.get("price")) if q.get("price") is not None else Decimal("0")
    quote_age_s = float(q["age_s"]) if q.get("age_s") is not None else float("inf")
    quote_spread = _dec(q.get("spread_pct", 0)) if q else Decimal("0")
    return RiskContext(
        max_notional=_dec(limits["max_notional"]),
        max_positions=int(limits["max_positions"]),
        concentration_pct=_dec(limits["concentration_pct"]),
        daily_loss_limit=_dec(limits["daily_loss_limit"]),
        quote_staleness_s=120.0,
        max_spread_pct=Decimal("1"),
        pending_exposure_cap=_dec(getattr(state.settings, "pending_exposure_cap", "10000")),
        cash=cash,
        buying_power=buying_power,
        holdings=holdings,
        open_positions=len(holdings),
        realized_loss_today=Decimal("0"),
        pending_notional=Decimal("0"),
        watchlist=set(watchlist),
        quote_price=quote_price if quote_price.is_finite() else Decimal("0"),
        quote_age_s=quote_age_s,
        quote_spread_pct=quote_spread if quote_spread.is_finite() else Decimal("0"),
        mode=ModeLadder(state).current().value,
    )


def _persist_record(state, clock, *, proposal: dict, status: str, flagged: bool,
                    job_id: str | None, risk_decision_id: str | None = None,
                    order_id: str | None = None) -> ProposalRecord:
    now = clock.now()
    pid = str(proposal.get("proposal_id") or new_id("prop"))
    with state.session_factory() as s:
        if s.get(ProposalRecord, pid) is not None:     # a hostile model may replay an id
            pid = new_id("prop")
            proposal["proposal_id"] = pid
    record = ProposalRecord(
        id=pid, symbol=str(proposal.get("symbol", "")).upper(),
        side=str(proposal.get("side", "")), status=status, payload=proposal,
        job_id=job_id, risk_decision_id=risk_decision_id, order_id=order_id,
        flagged=flagged, created_at=now, updated_at=now,
    )
    state.audit.record_state_change(
        record, EVENT_PROPOSAL, actor="proposals",
        payload={"proposal_id": pid, "symbol": record.symbol, "status": status,
                 "flagged": flagged},
    )
    return record


def draft_proposal(state, clock=None, *, event: MarketEvent, packet: Packet,
                   transport=None, parquet_root=None, quote_fn=None,
                   holdings_symbols: frozenset[str] = frozenset(),
                   watchlist: frozenset[str] = frozenset(),
                   account: str = "paper", prompt_name: str = "trade_proposal",
                   forced_side: str | None = None) -> ProposalOutcome:
    """Draft ONE trade proposal for ``event`` over the dispatched ``packet``.

    ``quote_fn(symbol) -> {"price", "age_s", "spread_pct"} | None`` is the injected quote
    seam (no fetcher exists in the tree); None fails the risk gate conservatively.

    ``forced_side`` (Inverse Cramer) is a DETERMINISTIC override: when set, the returned
    proposal's ``side`` is ALWAYS ``forced_side`` regardless of what the deep model output —
    the side is decided by ingest-time code (``ingest/cramer_signal.py``), never by the
    model's reading of the (untrusted) article text. A model that disagrees is not an
    error; the disagreement is only recorded in ``reasons`` for visibility.

    Returns everything; the caller (services/bridge) owns delivery + veto scheduling."""
    clock = clock if clock is not None else state.clock
    reasons: list[str] = []

    # ---- ONE deep call (schema TradeProposal) -------------------------------------------
    tmpl = load_prompt(prompt_name)
    render_kwargs = {"symbol": event.symbol, "packet_block": packet.prompt_block()}
    if forced_side is not None:
        render_kwargs["forced_side"] = forced_side
        render_kwargs["cramer_side"] = inverse_side(forced_side)
    prompt = tmpl.render(**render_kwargs)
    result, job = call_agent(
        state, alias="deep", prompt_ref=tmpl.ref, prompt_hash=tmpl.sha256, prompt=prompt,
        schema_name="TradeProposal",
        input_refs={"event_id": event.id, "raw_refs": sorted(packet.dispatched_refs)},
        transport=transport,
    )
    if result.status in (LIMIT_HIT, TIMEOUT):
        return ProposalOutcome("degraded", None, None, None, None, None,
                               (f"deep call {result.status}",))
    if result.status != OK:
        # Inv 3: quarantined AgentJob; no ProposalRecord is minted from invalid output.
        return ProposalOutcome("quarantined", None, None, None, None, None,
                               (f"deep output quarantined ({result.error})",))

    proposal = dict(result.validated)
    if forced_side is not None and proposal.get("side") != forced_side:
        reasons.append(
            f"forced_side override: model proposed side={proposal.get('side')!r}, "
            f"overridden to {forced_side!r} (Inverse Cramer)"
        )
        proposal["side"] = forced_side
    pid = str(proposal.get("proposal_id", ""))

    # ---- guard: an order-shaped proposal needs a size -----------------------------------
    if proposal.get("quantity") is None and proposal.get("notional") is None:
        record = _persist_record(state, clock, proposal=proposal, status="needs_human",
                                 flagged=False, job_id=job.id)
        return ProposalOutcome("needs_human", proposal, record, None, None, None,
                               ("neither quantity nor notional present",))

    # ---- SEC-09: scrub model prose BEFORE store/audit/post -------------------------------
    # ``generated_by`` is a model-authored free-text object (runtime/model/session_id strings
    # are unconstrained — additionalProperties:false bounds KEYS, not string CONTENTS), so it
    # is scrubbed recursively too: a blocking hit there quarantines like a thesis leak.
    subject = frozenset({str(proposal.get("symbol", "")).upper()})
    proposal, blocked = scrub_output_fields(
        state, proposal, ("thesis", "invalidation", "sizing_basis", "generated_by"),
        subject_symbols=subject, target="trade_proposal",
    )
    if blocked:
        record = _persist_record(state, clock, proposal=proposal, status="quarantined",
                                 flagged=True, job_id=job.id)
        return ProposalOutcome("quarantined", proposal, record, None, None, None,
                               ("outbound scrub blocked (SEC-09)",))

    # ---- SEC-14: fail-closed grounding (flag-only; NEVER clears) -------------------------
    def _resolver(ref: str) -> dict:
        return resolve_raw(parquet_root, ref, state=state)

    grounding = evaluate_grounding(state, proposal, packet.dispatched_refs, _resolver,
                                   transport=transport,
                                   symbol_universe=holdings_symbols | watchlist)
    flagged = grounding.flagged
    if flagged:
        reasons.extend(grounding.reasons)

    # ---- deterministic risk gate (Invariant 5: evaluate() only) --------------------------
    quote = quote_fn(str(proposal.get("symbol", "")).upper()) if quote_fn is not None else None
    ctx = _build_risk_context(state, quote=quote, watchlist=watchlist)
    rd = evaluate(proposal, ctx)
    now = clock.now()
    rd_row = RiskDecision(
        id=new_id("rd"), proposal_id=pid, decision=rd["decision"], checks=rd["checks"],
        failed_checks=rd["failed_checks"], warnings=rd["warnings"],
        max_allowed_notional=_dec(rd["max_allowed_notional"]),
        human_required=bool(rd["human_required"]), evaluated_at=now, created_at=now,
    )
    state.audit.record_state_change(
        rd_row, "risk_decision", actor="risk",
        payload={"proposal_id": pid, "decision": rd["decision"]},
    )

    # ---- resolve the canonical order tuple ONLY on an approved, UNFLAGGED decision -------
    # SEC-14: a grounding-flagged proposal is review-only — never the order path, even if the
    # deterministic risk gate approved it. 'flagged => no Order row' is enforced here.
    status = "flagged" if flagged else "drafted"
    order_row: Order | None = None
    if rd["decision"] == "approved" and not flagged:
        if halt_is_set(default_halt_path()):
            status = "needs_human"
            reasons.append("HALT sentinel set (SEC-04); order not created")
        else:
            order_row, why = _resolve_order(state, clock, proposal=proposal, ctx=ctx,
                                            rd_id=rd_row.id, account=account)
            if order_row is None:
                status = "needs_human"
                reasons.append(why)

    record_status = {"drafted": "draft", "flagged": "flagged",
                     "needs_human": "needs_human"}[status]
    record = _persist_record(
        state, clock, proposal=proposal, status=record_status, flagged=flagged,
        job_id=job.id, risk_decision_id=rd_row.id,
        order_id=order_row.id if order_row is not None else None,
    )

    # ---- SEC-17 embed: risk facts from the RiskDecision; excerpts via resolve_raw only ---
    def _excerpt(ref) -> str:
        item = resolve_raw(parquet_root, str(ref), state=state)
        return escape_untrusted(str(item.get("headline", "")))[:200]

    embed = render_proposal_embed(record.payload, rd_row, raw_excerpt_fn=_excerpt)
    return ProposalOutcome(status, record.payload, record, rd_row, order_row, embed,
                           tuple(reasons))


def _resolve_order(state, clock, *, proposal: dict, ctx: RiskContext, rd_id: str,
                   account: str) -> tuple[Order | None, str]:
    """qty = proposal quantity, else floor(notional / quote price) (>= 1). Limit-family
    orders take the quote as the deterministic limit price. Fail -> (None, reason)."""
    qty: Decimal | None = None
    if proposal.get("quantity") is not None:
        q = _dec(proposal["quantity"])
        if q.is_finite() and q > 0:
            qty = q
    elif proposal.get("notional") is not None and ctx.quote_price > 0:
        q = (_dec(proposal["notional"]) / ctx.quote_price).to_integral_value(rounding=ROUND_FLOOR)
        if q.is_finite() and q >= 1:
            qty = q
    if qty is None:
        return None, "order quantity unresolvable (needs human)"

    order_type = str(proposal.get("order_type", ""))
    limit_price = None
    if order_type in ("limit", "stop_limit"):
        if ctx.quote_price <= 0:
            return None, "no quote to price the limit order (needs human)"
        limit_price = ctx.quote_price
    try:
        canon = canonical_order_tuple(
            symbol=str(proposal.get("symbol", "")), side=str(proposal.get("side", "")),
            qty=qty, order_type=order_type, limit_price=limit_price, tif="day",
        )
        oth = order_tuple_hash(canon)
    except ValueError as exc:
        return None, f"order tuple canonicalization failed: {exc}"
    order_row = create_order(
        state,
        proposal_id=str(proposal.get("proposal_id", "")),
        risk_decision_id=rd_id,
        account=account,
        symbol=canon["symbol"],
        side=canon["side"],
        qty=qty,
        order_type=canon["order_type"],
        tif=canon["tif"],
        order_tuple_hash=oth,
        limit_price=limit_price,
        notional=order_notional(proposal, ctx),
        actor="proposals",
        clock=clock,
    )
    return order_row, ""
