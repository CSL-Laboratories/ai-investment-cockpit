"""WP4 — the deterministic risk engine (the order-path gate).

PURE FUNCTIONS ONLY — no LLM, no I/O, no subprocess (Invariant 5; enforced by the
no-LLM-in-risk CI gate). evaluate() adjudicates a TradeProposal against a RiskContext and
returns a RiskDecision-shaped dict. HARD checks block; SOFT checks force human review; a
decision is 'approved' ONLY when every hard check passes and no soft flag is set — so no
input can yield an approved decision with a failed hard check (Invariant 1, property-tested).

Hardened after adversarial review (wf_dee65078): order size must be a **finite, positive**
Decimal (a negative/zero/absent size, or a non-positive quote, would otherwise make every
one-sided `<=` cap vacuously true); a SELL must be backed by a reconciled holding and bounded
by its market value (no naked shorts); asset_type/order_type are validated (market orders ->
human review since notional is only advisory); the gate sizes on the LARGER of divergent
notional/quantity fields; max_allowed_notional folds in concentration/positions/Reg-T; and
matched_rule (SEC-19) is advisory ONLY on an approved decision.

Post-PDT model (Inv 9): Reg-T $2,000 minimum equity + intraday buying power, NO day-trade
counts. SEC-07 pending-exposure cap. SEC-15 holdings/watchlist are the caller's reconciled
trust anchor.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

REG_T_MIN_EQUITY = Decimal("2000")
SUPPORTED_ASSET_TYPES = {"equity"}                       # options/crypto not live (re-verify)
KNOWN_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}


def _d(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    hard: bool
    detail: str = ""


@dataclass
class RiskContext:
    max_notional: Decimal
    max_positions: int
    concentration_pct: Decimal
    daily_loss_limit: Decimal
    quote_staleness_s: float
    max_spread_pct: Decimal
    pending_exposure_cap: Decimal
    cash: Decimal
    buying_power: Decimal
    holdings: dict                    # symbol -> {"market_value": Decimal, ...}
    open_positions: int
    realized_loss_today: Decimal
    pending_notional: Decimal
    watchlist: set
    quote_price: Decimal
    quote_age_s: float
    quote_spread_pct: Decimal
    mode: str = "paper"

    # ---- QT-06: execution verification (quant/verify.py). Appended AFTER `mode` because
    # every preceding field is non-defaulted, so a new field must carry a default or every
    # existing construction site breaks.
    quant_verdict: object | None = None
    """A ``quant.verify.QuantVerdict`` for THIS proposal, or None when the engine is running
    without a book. Typed as ``object`` so ``risk/`` keeps a single import of the quant
    package (the helpers below), rather than importing its types as well — the smaller the
    surface between the risk engine and anything else, the easier Invariant 5 is to hold."""

    require_quant_verdict: bool = False
    """When True, a missing or stale verdict forces human review instead of being ignored.
    Off by default for deliberately uncomposed callers; production composition arms the
    fail-closed posture with its market-data service."""

    max_execution_cost_bps: Decimal = Decimal("100")
    """HARD ceiling on modelled execution cost. Above this the order cannot pay for itself
    before the thesis has to be right about anything."""

    quant_verdict_max_age_ns: int = 5_000_000_000
    quant_book_seq: int | None = None
    """The book sequence the caller is about to trade against. When it has moved past the
    verdict's ``book_seq``, the verdict describes a book that no longer exists."""

    require_net_edge: bool = False
    """W-21 production posture.  When armed, an unavailable trusted edge forces human review
    (and therefore no Order row), while a present estimate below the relative threshold is a
    distinct HARD failure.  Off keeps legacy/uncomposed callers additive."""


def _verdict_state(ctx: RiskContext) -> tuple[str, str]:
    """Classify the attached execution verdict. PURE — reads the context, fetches nothing.

    Duck-typed rather than isinstance-checked against ``quant.verify.QuantVerdict``, so the
    risk engine depends on the SHAPE of the verdict and not on the quant package's class
    identity. That keeps Invariant 5's surface as small as it can be: a future caller can
    supply an equivalent record from anywhere, and this module never grows a second import.

    Returns ``(state, detail)`` where state is one of:
        absent | stale | insufficient_data | uneconomic | marginal | ok
    """
    verdict = ctx.quant_verdict
    if verdict is None:
        return "absent", "no execution verdict attached"

    seq = getattr(verdict, "book_seq", None)
    if ctx.quant_book_seq is not None and seq is not None and ctx.quant_book_seq > seq:
        return "stale", (
            f"verdict describes book seq {seq}, the order would trade against "
            f"{ctx.quant_book_seq}"
        )

    raw = getattr(verdict, "verdict", None)
    name = getattr(raw, "value", raw)
    if name in ("insufficient_data", "uneconomic", "marginal", "ok"):
        return str(name), ""
    return "insufficient_data", f"unrecognised verdict {name!r}"


def _verdict_cost_bps(ctx: RiskContext) -> Decimal:
    """Modelled execution cost in bps, or NaN when there is nothing to read.

    NaN rather than 0: a zero would compare favourably against every ceiling, so an absent
    or malformed verdict would read as a free trade — the exact direction a fail-closed
    control must never fail in.
    """
    verdict = ctx.quant_verdict
    if verdict is None:
        return Decimal("NaN")
    return _d(getattr(verdict, "expected_cost_bps", None))


def _verdict_edge_state(ctx: RiskContext) -> str:
    """Return the independently-computed W-21 edge state; never inspect the proposal."""
    verdict = ctx.quant_verdict
    if verdict is None:
        return "unavailable"
    state = str(getattr(verdict, "net_edge_state", "unavailable"))
    if state in {"unavailable", "insufficient", "sufficient", "not_applicable"}:
        return state
    return "unavailable"


def order_notional(proposal: dict, ctx: RiskContext) -> Decimal:
    """Size on the LARGER of a supplied notional and quantity*quote_price, so the gate can
    never be sized on the smaller of two divergent fields. Non-finite/garbage -> NaN."""
    candidates = []
    if proposal.get("notional") is not None:
        candidates.append(_d(proposal["notional"]))
    if proposal.get("quantity") is not None:
        candidates.append(_d(proposal["quantity"]) * ctx.quote_price)
    if not candidates:
        return Decimal("0")
    finite = [c for c in candidates if c.is_finite()]
    return max(finite) if finite else Decimal("NaN")


def _portfolio_value(ctx: RiskContext) -> Decimal:
    return ctx.cash + sum(_d(h.get("market_value", 0)) for h in ctx.holdings.values())


def _match_auto_rule(proposal: dict, notional: Decimal, rules) -> str | None:
    """SEC-19: a compiled rule matches only within its own explicit bound. 'key absent'
    means match-any; a present-but-empty scope or present-but-None cap is malformed and
    fails closed (skip). Advisory only — never overrides a hard fail."""
    for r in rules or []:
        pred = r.get("predicate", {}) or {}
        if pred.get("side") and pred["side"] != proposal["side"]:
            continue
        if "symbols_scope" in r or "symbols_scope" in pred:
            scope = r.get("symbols_scope", pred.get("symbols_scope"))
            if not scope or proposal["symbol"] not in scope:
                continue
        if "worst_case_notional" in r or "max_notional" in pred:
            cap = r.get("worst_case_notional", pred.get("max_notional"))
            if cap is None or not _d(cap).is_finite() or notional > _d(cap):
                continue
        return r.get("id") or r.get("rule_id")
    return None


def evaluate(proposal: dict, ctx: RiskContext, rules=None) -> dict:
    symbol, side = proposal["symbol"], proposal["side"]
    is_buy, is_sell = side == "buy", side == "sell"
    raw = order_notional(proposal, ctx)
    size_finite = raw.is_finite()
    notional = raw if size_finite else Decimal("0")     # 0 for non-finite -> safe comparisons
    opening_new = is_buy and symbol not in ctx.holdings
    # BUG-066: Decimal NaN COMPARISONS raise InvalidOperation, so a single poisoned input
    # (a broker snapshot holding with market_value null/garbage) used to crash evaluate()
    # instead of failing conservatively. Every non-finite monetary derivation normalizes to
    # its most conservative value BEFORE any comparison: unverifiable equity reads as NO
    # equity (fails reg_t_minimum, shrinks concentration headroom) and an unverifiable
    # holding value reads as 0 (cannot back a sell).
    port = _portfolio_value(ctx)
    if not port.is_finite():
        port = Decimal("0")
    held_val = _d(ctx.holdings.get(symbol, {}).get("market_value", 0))
    if not held_val.is_finite():
        held_val = Decimal("0")
    realized_loss = _d(ctx.realized_loss_today)
    loss_limit = _d(ctx.daily_loss_limit)
    daily_loss_ok = (realized_loss.is_finite() and loss_limit.is_finite()
                     and realized_loss <= loss_limit)

    concentration_ok = (not is_buy) or port <= 0 or ((held_val + notional) / port) <= ctx.concentration_pct

    # ---- QT-06: execution verification (Tier B). See quant/verify.py.
    #
    # HARD blocks only on a verdict that is PRESENT and says the trade cannot pay for itself.
    # A missing verdict is handled by the SOFT check below, because "we have no book" and
    # "the book says no" are different states and conflating them would either block every
    # order on a system with no feed, or wave through the ones that matter.
    verdict_state, verdict_detail = _verdict_state(ctx)
    cost_bps = _verdict_cost_bps(ctx)
    cost_over_ceiling = cost_bps.is_finite() and cost_bps > ctx.max_execution_cost_bps
    execution_cost_sane = verdict_state != "uneconomic" and not cost_over_ceiling

    # SOFT -> human review. Fires on a marginal trade AND, when a verdict is required, on a
    # missing / stale / unusable one. Fail closed (Invariant 7): stale data degrades to "a
    # human looks at this", never to "proceed without analysis".
    quality_unclear = verdict_state in ("marginal", "insufficient_data")
    if ctx.require_quant_verdict and verdict_state in ("absent", "stale"):
        quality_unclear = True
    execution_quality_ok = not quality_unclear

    # W-21's relative rule is separate from the absolute execution-cost ceiling.  A trusted
    # estimate that is PRESENT and below the threshold is a hard economic refusal.  Missing
    # instrumentation is different: it takes the fail-closed human/no-Order lane, matching
    # the existing treatment of an unavailable execution verdict.
    edge_state = _verdict_edge_state(ctx)
    edge_authoritative = verdict_state not in {"absent", "stale", "insufficient_data"}
    net_edge_sane = (
        not ctx.require_net_edge
        or not edge_authoritative
        or edge_state != "insufficient"
    )
    net_edge_available = (
        not ctx.require_net_edge
        or (edge_authoritative and edge_state in {"sufficient", "not_applicable"})
    )

    checks = [
        CheckResult("notional_positive", size_finite and notional > 0, True,
                    "order size must be a finite, positive amount"),
        CheckResult("quote_sane", ctx.quote_price > 0, True, "quote price must be > 0"),
        CheckResult("asset_type_supported", proposal.get("asset_type") in SUPPORTED_ASSET_TYPES, True,
                    f"asset_type in {sorted(SUPPORTED_ASSET_TYPES)}"),
        CheckResult("order_type_known", proposal.get("order_type") in KNOWN_ORDER_TYPES, True,
                    f"order_type in {sorted(KNOWN_ORDER_TYPES)}"),
        CheckResult("symbol_allowlist", symbol in ctx.holdings or symbol in ctx.watchlist, True,
                    "symbol must be held or on the reconciled watchlist (SEC-15)"),
        CheckResult("quote_freshness", ctx.quote_age_s <= ctx.quote_staleness_s, True,
                    f"quote age {ctx.quote_age_s}s <= {ctx.quote_staleness_s}s"),
        CheckResult("spread", ctx.quote_spread_pct <= ctx.max_spread_pct, True,
                    f"spread {ctx.quote_spread_pct} <= {ctx.max_spread_pct}"),
        CheckResult("max_notional", notional <= ctx.max_notional, True,
                    f"notional {notional} <= {ctx.max_notional}"),
        CheckResult("buying_power", (not is_buy) or notional <= ctx.buying_power, True,
                    f"buy notional {notional} <= buying_power {ctx.buying_power}"),
        # no naked shorts: a sell must be a reconciled holding, bounded by its value (SEC-15)
        CheckResult("sell_backed", (not is_sell) or (symbol in ctx.holdings and notional <= held_val), True,
                    "sell must be held and <= held market value"),
        CheckResult("max_positions", (not opening_new) or (ctx.open_positions + 1 <= ctx.max_positions), True,
                    f"open positions {ctx.open_positions}(+1) <= {ctx.max_positions}"),
        CheckResult("concentration", concentration_ok, True,
                    f"post-trade {symbol} concentration <= {ctx.concentration_pct}"),
        CheckResult("daily_loss_limit", daily_loss_ok, True,
                    f"realized loss {ctx.realized_loss_today} <= {ctx.daily_loss_limit}"),
        CheckResult("pending_exposure", ctx.pending_notional + notional <= ctx.pending_exposure_cap, True,
                    f"pending {ctx.pending_notional} + {notional} <= {ctx.pending_exposure_cap} (SEC-07)"),
        CheckResult("reg_t_minimum", port >= REG_T_MIN_EQUITY, True,
                    f"equity >= Reg-T ${REG_T_MIN_EQUITY} (post-PDT, no day-trade counts)"),
        CheckResult("mode_allows_order", ctx.mode != "read_only", True,
                    "orders are disabled in read_only mode"),
        CheckResult("execution_cost_sane", execution_cost_sane, True,
                    f"modelled execution cost {cost_bps} <= {ctx.max_execution_cost_bps}bps "
                    f"and ADV validity holds (W-21; verdict={verdict_state})"),
        CheckResult("net_edge_sane", net_edge_sane, True,
                    f"trusted net-edge state is {edge_state}; a present estimate must meet "
                    "the independently-modelled relative cost threshold (W-21)"),
        # SOFT -> human review
        CheckResult("sizing_not_max",
                    str(proposal.get("sizing_basis", "")).lower() not in ("max", "maximum", "all"),
                    False, "a max-sized proposal is forced to human review (SEC-15)"),
        CheckResult("priced_not_market", proposal.get("order_type") != "market", False,
                    "market orders lack a firm gate price -> human review"),
        CheckResult("execution_quality", execution_quality_ok, False,
                    f"execution verdict is {verdict_state}"
                    f"{': ' + verdict_detail if verdict_detail else ''} -> human review (QT-06)"),
        CheckResult("net_edge_available", net_edge_available, False,
                    f"trusted net-edge state is {edge_state}; unavailable edge cannot mint "
                    "an order until a deterministic strategy source exists (W-21)"),
    ]

    failed_hard = [c.name for c in checks if c.hard and not c.passed]
    warnings = [c.name for c in checks if not c.hard and not c.passed]
    if failed_hard:
        decision = "rejected"
    elif warnings:
        decision = "needs_human"
    else:
        decision = "approved"

    caps = [ctx.max_notional, ctx.pending_exposure_cap - ctx.pending_notional]
    if is_buy:
        caps.append(ctx.buying_power)
        if port > 0:
            caps.append(ctx.concentration_pct * port - held_val)     # concentration headroom
        if opening_new and ctx.open_positions + 1 > ctx.max_positions:
            caps.append(Decimal("0"))
    if is_sell:
        caps.append(held_val)
    if port < REG_T_MIN_EQUITY:
        caps.append(Decimal("0"))
    max_allowed = max(min(caps), Decimal("0"))

    return {
        "decision": decision,
        "checks": [{"name": c.name, "passed": c.passed, "hard": c.hard, "detail": c.detail} for c in checks],
        "failed_checks": failed_hard,
        "warnings": warnings,
        "max_allowed_notional": str(max_allowed),
        "human_required": decision == "needs_human",
        # SEC-19: only advertise a rule match on an APPROVED decision (never override a hard fail)
        "matched_rule": _match_auto_rule(proposal, notional, rules) if decision == "approved" else None,
    }
