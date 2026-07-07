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
    port = _portfolio_value(ctx)
    held_val = _d(ctx.holdings.get(symbol, {}).get("market_value", 0))

    concentration_ok = (not is_buy) or port <= 0 or ((held_val + notional) / port) <= ctx.concentration_pct

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
        CheckResult("daily_loss_limit", ctx.realized_loss_today <= ctx.daily_loss_limit, True,
                    f"realized loss {ctx.realized_loss_today} <= {ctx.daily_loss_limit}"),
        CheckResult("pending_exposure", ctx.pending_notional + notional <= ctx.pending_exposure_cap, True,
                    f"pending {ctx.pending_notional} + {notional} <= {ctx.pending_exposure_cap} (SEC-07)"),
        CheckResult("reg_t_minimum", port >= REG_T_MIN_EQUITY, True,
                    f"equity >= Reg-T ${REG_T_MIN_EQUITY} (post-PDT, no day-trade counts)"),
        CheckResult("mode_allows_order", ctx.mode != "read_only", True,
                    "orders are disabled in read_only mode"),
        # SOFT -> human review
        CheckResult("sizing_not_max",
                    str(proposal.get("sizing_basis", "")).lower() not in ("max", "maximum", "all"),
                    False, "a max-sized proposal is forced to human review (SEC-15)"),
        CheckResult("priced_not_market", proposal.get("order_type") != "market", False,
                    "market orders lack a firm gate price -> human review"),
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
