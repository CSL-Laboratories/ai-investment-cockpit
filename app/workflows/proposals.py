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

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from sqlalchemy import select

from agent_runner.result import LIMIT_HIT, OK, TIMEOUT
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from bridge.embeds import render_proposal_embed
from core.ids import new_id
from core.proposal_authority import proposal_payload_hash
from ingest.normalize import escape_untrusted
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.limits import current_limits
from orchestrator.mode_ladder import ModeLadder
from quant.events import PriceScale, to_ns
from quant.verify import (
    BorrowQuote,
    EdgeEstimate,
    MarketImpactInputs,
    PositionEffect,
    QuantVerdict,
    VerifyParams,
    proposal_quantity,
    verify_proposal,
)
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


log = logging.getLogger(__name__)

DEFAULT_VERIFY_PARAMS = VerifyParams(scale=PriceScale(tick_size=Decimal("0.01")))
"""QT-05 defaults: a penny tick grid. Injectable per call, because a sub-dollar name trades
on a finer grid and a verdict computed on the wrong scale misreads every price."""


def build_risk_context(state, *, quote, watchlist: frozenset[str],
                       quant_verdict=None, quant_book_seq=None,
                       quant_verdict_now_ns: int | None = None,
                       require_quant_verdict: bool = False,
                       require_net_edge: bool = False, snapshot=None) -> RiskContext:
    """RiskContext from the AUDITED limits (SEC-15) + the latest PortfolioSnapshot row
    (absent -> zeros -> risk fails conservatively) + the injected quote (None ->
    quote_age_s=inf, price 0 -> quote_sane hard-fails)."""
    limits = current_limits(state)
    snap = snapshot if snapshot is not None else _latest_snapshot(state)
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
    performance = getattr(state, "paper_performance", None)
    performance_fresh = False
    if performance is not None and getattr(performance, "ok", False):
        from brokers.paper_performance import EASTERN

        at = getattr(performance, "as_of", None)
        now = state.clock.now()
        performance_fresh = (
            at is not None and at.tzinfo is not None
            and 0 <= (now - at).total_seconds() <= 120
            and performance.trading_day == now.astimezone(EASTERN).date().isoformat()
        )
    if performance_fresh:
        equity_drawdown = _dec(getattr(performance, "equity_drawdown_today", None))
    elif getattr(state, "paper_performance_required", False):
        equity_drawdown = Decimal("NaN")
    else:
        equity_drawdown = Decimal("0")
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
        # RiskContext retains its historical field name; W-22 supplies the one canonical
        # daily-loss measure (intraday equity drawdown), not a second P&L calculation.
        realized_loss_today=equity_drawdown,
        pending_notional=Decimal("0"),
        watchlist=set(watchlist),
        quote_price=quote_price if quote_price.is_finite() else Decimal("0"),
        quote_age_s=quote_age_s,
        quote_spread_pct=quote_spread if quote_spread.is_finite() else Decimal("0"),
        mode=ModeLadder(state).current().value,
        # Deliberately uncomposed callers retain additive defaults. Production passes the
        # market-data seams and arms both W-21 requirements at the call site below.
        quant_verdict=quant_verdict,
        quant_book_seq=quant_book_seq,
        # BUG-128: arm the verdict-age ceiling with the composition clock. A verdict
        # priced minutes ago against an unmoved book must read stale, not fresh.
        quant_verdict_now_ns=quant_verdict_now_ns,
        require_quant_verdict=require_quant_verdict,
        require_net_edge=require_net_edge,
    )


def _market_impact_inputs(metric, *, symbol: str, now) -> MarketImpactInputs | None:
    """Convert the I/O-owning RW-14 record to quant's immutable pure boundary type."""
    if metric is None:
        return None
    as_of = getattr(metric, "as_of", None)
    age_days = (now.date() - as_of).days if as_of is not None else -1
    return MarketImpactInputs(
        symbol=str(getattr(metric, "symbol", symbol)).upper(),
        as_of=as_of,
        median_daily_dollar_volume=_dec(
            getattr(metric, "median_daily_dollar_volume", None)
        ),
        annualized_realized_volatility=_dec(
            getattr(metric, "realized_volatility", None)
        ),
        observations=int(getattr(metric, "observations", 0)),
        age_days=age_days,
    )


def build_execution_verdict(
    proposal: dict,
    *,
    quote_price: Decimal,
    clock,
    book_fn=None,
    market_observation=None,
    observation_fn=None,
    metrics_fn=None,
    verify_params: VerifyParams | None = None,
    expected_edge: EdgeEstimate | None = None,
    edge_provider=None,
    quote: dict | None = None,
    borrow_quote: BorrowQuote | None = None,
    position_effect: PositionEffect | None = None,
    quantity_override=None,
) -> tuple[QuantVerdict | None, int | None, int | None, tuple[str, ...]]:
    """Build the W-21 verdict from injected leaves for draft and approval-time risk.

    The shared helper is deliberate: ``PaperExecutionService._fresh_risk`` must re-derive the
    same authority-bearing facts immediately before approval instead of evaluating a context
    with no verdict and accidentally treating the missing cost model as free.
    """
    # Drafting resolves a notional instruction once. Approval-time risk passes the already
    # persisted Order quantity so the fresh cost fact describes the exact tuple awaiting
    # authorization even if the quote moved after drafting (BUG-044/W-21).
    resolved_qty = (
        proposal_quantity(proposal, quote_price)
        if quantity_override is None
        else proposal_quantity({"quantity": quantity_override}, quote_price)
    )
    verification_proposal = {**proposal, "quantity": resolved_qty, "notional": None}
    if book_fn is None and market_observation is None:
        return None, None, resolved_qty, ()

    symbol = str(proposal.get("symbol", "")).upper()
    try:
        if edge_provider is not None and market_observation is None:
            raise ValueError("the edge provider requires one atomic market observation")
        if market_observation is not None:
            if str(market_observation.symbol).upper() != symbol:
                raise ValueError("market observation symbol mismatch")
            book = market_observation.book
            quote = dict(market_observation.quote)
            if _dec(quote.get("price")) != quote_price:
                raise ValueError("quote price diverges from the atomic market observation")
        else:
            book = book_fn(symbol)
        if book is None:
            return None, None, resolved_qty, ()
        # A preliminary timestamp is enough to normalize the cached metric.  The authority
        # timestamp passed to ``verify_proposal`` is sampled only after the edge provider
        # returns; otherwise a real advancing clock makes the provider's freshly-computed edge
        # appear microseconds in the future (frozen-clock tests cannot expose that ordering).
        now = clock.now()
        metric = metrics_fn(symbol) if metrics_fn is not None else None
        inputs = _market_impact_inputs(metric, symbol=symbol, now=now)
        # W-23: invoke the one production edge provider only after the exact symbol, side,
        # horizon, resolved quantity, and quote are known. ``expected_edge`` remains an
        # explicit unit-test seam; production composition never supplies it.
        verified_edge = expected_edge
        if verified_edge is None and edge_provider is not None and quote is not None:
            verified_edge = edge_provider.estimate(
                verification_proposal, quote=quote, market_metric=metric
            )
        now = clock.now()
        inputs = _market_impact_inputs(metric, symbol=symbol, now=now)
        effect = position_effect
        if effect is None:
            effect = (
                PositionEffect.OPEN_LONG
                if proposal.get("side") == "buy"
                else PositionEffect.CLOSE_LONG
            )
        candidate = verify_proposal(
            verification_proposal,
            book,
            verify_params or DEFAULT_VERIFY_PARAMS,
            metrics=inputs,
            position_effect=effect,
            expected_edge=verified_edge,
            borrow_quote=borrow_quote,
            now_ns=to_ns(now),
        )
        # The second observation makes book-sequence movement reachable at the risk gate.
        if observation_fn is not None:
            current_observation = observation_fn(symbol)
            book_seq = current_observation.book.seq
            if current_observation.identity != market_observation.identity and book_seq <= book.seq:
                book_seq = book.seq + 1
        else:
            current = book_fn(symbol) if book_fn is not None else book
            book_seq = current.seq if current is not None else book.seq
        reasons = tuple(f"execution: {reason}" for reason in candidate.reasons)
        return candidate, book_seq, resolved_qty, reasons
    except Exception as exc:  # noqa: BLE001 - unavailable authority must fail closed upstream
        log.warning(
            "execution verification unavailable for %s: %s: %s",
            symbol, type(exc).__name__, exc,
        )
        return None, None, resolved_qty, ()


def _resolve_proposal_id(state, proposal: dict) -> str:
    """BUG-093: resolve the FINAL proposal id BEFORE any other row captures it. A hostile
    model can replay an existing proposal_id; the remint used to live only inside
    ``_persist_record`` — which runs AFTER the RiskDecision and Order rows were created —
    so exactly when the replay guard fired, the record got a fresh id while the risk/order
    rows still referenced the OLD (other proposal's) id, linking the approval chain to the
    wrong source material. Mutates ``proposal['proposal_id']`` to the resolved id."""
    pid = str(proposal.get("proposal_id") or new_id("prop"))
    with state.session_factory() as s:
        if s.get(ProposalRecord, pid) is not None:     # a hostile model may replay an id
            pid = new_id("prop")
    proposal["proposal_id"] = pid
    return pid


def _persist_record(state, clock, *, proposal: dict, status: str, flagged: bool,
                    job_id: str | None, risk_decision_id: str | None = None,
                    order_id: str | None = None) -> ProposalRecord:
    now = clock.now()
    # Backstop only: draft_proposal resolves the id up front (BUG-093); direct callers and
    # a new_id-collision race still get the collision check here.
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
    tuple_hash = None
    if order_id is not None:
        with state.session_factory() as session:
            order = session.get(Order, order_id)
            if order is None:
                raise ValueError("cannot bind a proposal to a missing order")
            tuple_hash = order_tuple_hash(canonical_order_tuple(
                symbol=order.symbol, side=order.side, qty=order.qty,
                order_type=order.order_type, limit_price=order.limit_price, tif=order.tif,
            ))
            if tuple_hash != order.order_tuple_hash:
                raise ValueError("cannot bind a proposal to a changed order tuple")
    state.audit.record_state_change(
        record, EVENT_PROPOSAL, actor="proposals",
        payload={"proposal_id": pid, "symbol": record.symbol, "status": status,
                 "flagged": flagged, "side": record.side, "job_id": job_id,
                 "proposal_payload_hash": proposal_payload_hash(proposal),
                 "order_tuple_hash": tuple_hash,
                 "risk_decision_id": risk_decision_id, "order_id": order_id},
    )
    return record


def draft_proposal(state, clock=None, *, event: MarketEvent, packet: Packet,
                   transport=None, parquet_root=None, quote_fn=None, book_fn=None,
                   observation_fn=None, metrics_fn=None,
                   verify_params: VerifyParams | None = None,
                   expected_edge: EdgeEstimate | None = None,
                   edge_provider=None,
                   borrow_quote: BorrowQuote | None = None,
                   holdings_symbols: frozenset[str] = frozenset(),
                   watchlist: frozenset[str] = frozenset(),
                   account: str = "paper") -> ProposalOutcome:
    """Draft ONE trade proposal for ``event`` over the dispatched ``packet``.

    ``quote_fn(symbol) -> {"price", "age_s", "spread_pct"} | None`` is the injected quote
    seam (no fetcher exists in the tree); None fails the risk gate conservatively.

    ``book_fn`` supplies L1 prices and ``metrics_fn`` supplies RW-14 ADV/volatility. Both are
    injected and never fetched here. ``expected_edge`` is a test seam; production uses W-23's
    injected deterministic provider, which returns None unless immutable evidence verifies.
    No model-authored proposal return field is read.
    Returns everything; the caller (services/bridge) owns delivery + veto scheduling."""
    clock = clock if clock is not None else state.clock
    reasons: list[str] = []

    # ---- ONE deep call (trade_proposal.v1, schema TradeProposal) ------------------------
    tmpl = load_prompt("trade_proposal")
    prompt = tmpl.render(symbol=event.symbol, packet_block=packet.prompt_block())
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
    pid = _resolve_proposal_id(state, proposal)        # BUG-093: final id before any row uses it

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
    symbol = str(proposal.get("symbol", "")).upper()
    quote = None
    market_observation = None
    if observation_fn is not None:
        try:
            market_observation = observation_fn(symbol)
            quote = dict(market_observation.quote)
        except Exception as exc:  # noqa: BLE001 - absent data must reject, not abort awareness
            log.warning("atomic market observation unavailable for %s: %s: %s",
                        symbol, type(exc).__name__, exc)
            reasons.append("market observation unavailable; deterministic risk fails closed")
    elif quote_fn is not None:
        try:
            quote = quote_fn(symbol)
        except Exception as exc:  # noqa: BLE001 - absent data must reject, not abort awareness
            log.warning("market quote unavailable for %s: %s: %s",
                        symbol, type(exc).__name__, exc)
            reasons.append("market quote unavailable; deterministic risk fails closed")
    raw_quote_price = _dec((quote or {}).get("price"))
    quote_price = raw_quote_price if raw_quote_price.is_finite() else Decimal("0")
    # BUG-044 + W-21: one shared builder resolves quantity once and supplies the identical
    # verdict path to proposal drafting and approval-time fresh risk.
    verdict, book_seq, resolved_qty, verification_reasons = build_execution_verdict(
        proposal,
        quote_price=quote_price,
        clock=clock,
        book_fn=book_fn,
        market_observation=market_observation,
        observation_fn=observation_fn,
        metrics_fn=metrics_fn,
        verify_params=verify_params,
        expected_edge=expected_edge,
        edge_provider=edge_provider,
        quote=quote,
        borrow_quote=borrow_quote,
    )
    reasons.extend(verification_reasons)

    ctx = build_risk_context(
        state, quote=quote, watchlist=watchlist,
        quant_verdict=verdict, quant_book_seq=book_seq,
        quant_verdict_now_ns=to_ns(clock.now()),
        require_quant_verdict=(
            book_fn is not None or observation_fn is not None or metrics_fn is not None
        ),
        require_net_edge=metrics_fn is not None,
    )
    rd = evaluate(proposal, ctx)
    now = clock.now()
    rd_row = RiskDecision(
        id=new_id("rd"), proposal_id=pid, decision=rd["decision"], checks=rd["checks"],
        failed_checks=rd["failed_checks"], warnings=rd["warnings"],
        # BUG-136: sign and persist identical cents; a rounded-up display must never
        # advertise more headroom than the deterministic risk calculation allowed.
        max_allowed_notional=_dec(rd["max_allowed_notional"]).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        ),
        human_required=bool(rd["human_required"]), evaluated_at=now, created_at=now,
        # Stored on the ROW, not only in the audit payload — see the column's docstring. This
        # is what lets the operator embed show execution quality at all under SEC-17: the
        # RiskDecision is the only source the embed is permitted to read a risk fact from.
        execution_verdict=verdict.as_dict() if verdict is not None else None,
    )
    # The verdict rides in the audit payload as EVIDENCE, so a later reader can reconstruct
    # why a decision went the way it did. Serialised via ``as_dict`` (JSON-native only) —
    # the audit chain's canonical encoder has no ``default=`` and fails closed on anything
    # it cannot encode rather than guessing.
    # BUG-136: the payload ALSO carries the full rendered risk-fact set (checks, failed
    # checks, warnings, ceiling, human flag). The operator embed renders from the mutable
    # RiskDecision ROW, so without a signed copy of those facts a keyless DB writer could
    # repaint the row and the operator would see forged risk facts. bridge/embeds
    # reconciles the row against this signed copy at render time and refuses to display
    # on mismatch.
    rd_payload = {
        "proposal_id": pid,
        "decision": rd["decision"],
        "checks": rd["checks"],
        "failed_checks": rd["failed_checks"],
        "warnings": rd["warnings"],
        "max_allowed_notional": str(rd_row.max_allowed_notional),
        "human_required": bool(rd["human_required"]),
    }
    if verdict is not None:
        rd_payload["execution_verdict"] = verdict.as_dict()
    state.audit.record_state_change(rd_row, "risk_decision", actor="risk", payload=rd_payload)

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
                                            resolved_qty=resolved_qty,
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

    embed = render_proposal_embed(record.payload, rd_row, raw_excerpt_fn=_excerpt,
                                    # BUG-136: same-tick signed copy (just written above);
                                    # the later Discord read in bridge/bot.py reconciles
                                    # against the verified chain copy instead.
                                    audit_facts=rd_payload)
    # Server-derived id consumed by the authenticated Discord /paper surface. The model
    # cannot select, omit, or rewrite this binding.
    embed["order_id"] = order_row.id if order_row is not None else None
    return ProposalOutcome(status, record.payload, record, rd_row, order_row, embed,
                           tuple(reasons))


def _resolve_order(state, clock, *, proposal: dict, ctx: RiskContext,
                   resolved_qty: int | None, rd_id: str,
                   account: str) -> tuple[Order | None, str]:
    """Create the tuple from the quantity already priced by execution verification.

    Limit-family orders take the quote as the deterministic limit price. Fail ->
    ``(None, reason)``. Quantity is never re-derived here: two sizing authorities were
    BUG-044, and could make the verified order smaller than the order actually created.
    """
    if resolved_qty is None:
        return None, "order quantity unresolvable (needs human)"
    qty = Decimal(resolved_qty)

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
