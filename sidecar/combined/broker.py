"""Simulated single order path in front of the cockpit risk gate.

Imports ``risk.engine`` from the untouched ``app/`` tree. Does not import the broker
package, does not read the OS credential store, and does not open a socket. An order
changes the simulated book only after the pure risk decision is ``approved``, the mode
is paper, and the limit is marketable at the next open.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# Shipped defaults. orchestrator/limits.py DEFAULT_LIMITS, proposals.build_risk_context
# quote ceilings, and the gateway's default pending-exposure cap.
MAX_NOTIONAL = Decimal("1000")
DAILY_LOSS_LIMIT = Decimal("500")
MAX_POSITIONS = 10
CONCENTRATION = Decimal("0.25")
PENDING_CAP = Decimal("10000")
QUOTE_STALENESS_S = 120.0
MAX_SPREAD = Decimal("1")
ASSUMED_SPREAD = Decimal("0.0001")
FLAT_COST_BPS = Decimal("1")
# Predeclared liquid-ETF schedule. Not fitted on this sample.
HALF_SPREAD = Decimal("0.00005")
IMPACT_ETA = Decimal("0.10")
APPROVAL_POLICY = "paper_approved_risk_only.v1"


def load_risk():
    """Import the pure risk gate. The app tree is inserted for this read only."""
    root = str(APP_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from risk.engine import RiskContext, evaluate

    return RiskContext, evaluate


@dataclass
class Book:
    cash: Decimal
    shares: dict[str, Decimal] = field(default_factory=dict)
    admitted: int = 0
    simulated_fills: int = 0
    expired_limits: int = 0
    refusals: Counter = field(default_factory=Counter)
    orders_submitted: int = 0

    def position(self, symbol: str) -> Decimal:
        return self.shares.get(symbol, Decimal(0))

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        held = sum(
            (qty * prices[symbol] for symbol, qty in self.shares.items() if qty > 0),
            Decimal(0),
        )
        return self.cash + held

    def holdings(self, prices: dict[str, Decimal]) -> dict:
        rows = {}
        for symbol, qty in self.shares.items():
            if qty <= 0:
                continue
            rows[symbol] = {
                "market_value": qty * prices[symbol],
                "qty": qty,
                "side": "long",
            }
        return rows


@dataclass(frozen=True)
class Admission:
    decision: str
    admitted: bool
    reason: str
    failed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _context(
    book: Book,
    *,
    prices: dict[str, Decimal],
    symbol: str,
    watchlist: set[str],
    pending_notional: Decimal,
    pending_sells: dict[str, Decimal],
    drawdown: Decimal,
    buying_power: Decimal,
    require_quant_verdict: bool,
    require_net_edge: bool,
    mode: str,
):
    RiskContext, _evaluate = load_risk()
    return RiskContext(
        max_notional=MAX_NOTIONAL,
        max_positions=MAX_POSITIONS,
        concentration_pct=CONCENTRATION,
        daily_loss_limit=DAILY_LOSS_LIMIT,
        quote_staleness_s=QUOTE_STALENESS_S,
        max_spread_pct=MAX_SPREAD,
        pending_exposure_cap=PENDING_CAP,
        cash=book.cash,
        buying_power=buying_power,
        holdings=book.holdings(prices),
        open_positions=sum(1 for qty in book.shares.values() if qty > 0),
        realized_loss_today=drawdown,
        pending_notional=pending_notional,
        watchlist=watchlist,
        quote_price=prices[symbol],
        quote_age_s=0.0,
        quote_spread_pct=ASSUMED_SPREAD,
        mode=mode,
        pending_sell_quantities=dict(pending_sells),
        require_quant_verdict=require_quant_verdict,
        require_net_edge=require_net_edge,
    )


def admit(
    book: Book,
    proposal: dict,
    *,
    prices: dict[str, Decimal],
    watchlist: set[str],
    pending_notional: Decimal,
    pending_sells: dict[str, Decimal],
    drawdown: Decimal,
    buying_power: Decimal,
    require_quant_verdict: bool = False,
    require_net_edge: bool = False,
    mode: str = "paper",
) -> tuple[Admission, dict]:
    """Risk, then mode, then the paper approval stand-in. The book does not change."""
    _RiskContext, evaluate = load_risk()
    symbol = proposal["symbol"]
    verdict = evaluate(
        proposal,
        _context(
            book,
            prices=prices,
            symbol=symbol,
            watchlist=watchlist,
            pending_notional=pending_notional,
            pending_sells=pending_sells,
            drawdown=drawdown,
            buying_power=buying_power,
            require_quant_verdict=require_quant_verdict,
            require_net_edge=require_net_edge,
            mode=mode,
        ),
    )
    decision = verdict["decision"]
    failed = tuple(verdict["failed_checks"])
    warnings = tuple(verdict["warnings"])
    if decision != "approved":
        return Admission(decision, False, decision, failed, warnings), verdict
    if mode != "paper":
        return Admission(decision, False, "mode", failed, warnings), verdict
    return Admission(decision, True, APPROVAL_POLICY, failed, warnings), verdict


def execution_price(
    *,
    side: str,
    arrival: Decimal,
    qty: Decimal,
    sigma: Decimal,
    adv: Decimal,
    cost_model: str,
) -> tuple[Decimal, Decimal]:
    """Fill price and fee. Flat cost keeps the next open and charges 1 bp.

    The TCA schedule worsens the price by a half-spread plus a square-root impact
    term and charges no extra fee. ``eta`` is an assumption, not a fitted coefficient.
    """
    if cost_model == "flat":
        fee = (qty * arrival * FLAT_COST_BPS / Decimal(10000)).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        return arrival, fee
    if cost_model != "tca":
        raise ValueError(cost_model)
    participation = qty / adv if adv > 0 else Decimal(1)
    if participation < 0:
        participation = Decimal(0)
    impact = IMPACT_ETA * sigma * participation.sqrt() if sigma > 0 else Decimal(0)
    worsen = HALF_SPREAD + impact
    if side == "buy":
        price = arrival * (1 + worsen)
    else:
        price = arrival * (1 - worsen)
    return price, Decimal(0)


def try_fill(
    book: Book,
    proposal: dict,
    *,
    arrival: Decimal,
    prices: dict[str, Decimal],
    watchlist: set[str],
    pending_notional: Decimal,
    pending_sells: dict[str, Decimal],
    drawdown: Decimal,
    buying_power: Decimal,
    sigma: Decimal,
    adv: Decimal,
    cost_model: str,
    decision_price: Decimal,
) -> dict | None:
    """Re-run the gate at the next open, then fill only a marketable limit."""
    limit = Decimal(proposal["notional"]) / Decimal(proposal["quantity"])
    side = proposal["side"]
    qty = Decimal(proposal["quantity"])
    fill_price, fee = execution_price(
        side=side, arrival=arrival, qty=qty, sigma=sigma, adv=adv, cost_model=cost_model
    )
    marketable = fill_price <= limit if side == "buy" else fill_price >= limit
    if not marketable:
        book.expired_limits += 1
        book.refusals["limit_not_marketable"] += 1
        return None
    live = dict(proposal)
    live["notional"] = qty * fill_price
    live_prices = dict(prices)
    live_prices[proposal["symbol"]] = fill_price
    admission, _verdict = admit(
        book,
        live,
        prices=live_prices,
        watchlist=watchlist,
        pending_notional=pending_notional,
        pending_sells=pending_sells,
        drawdown=drawdown,
        buying_power=buying_power,
    )
    if not admission.admitted:
        book.refusals[admission.reason] += 1
        return None
    cash_after = book.cash - (qty * fill_price + fee) if side == "buy" else book.cash + qty * fill_price - fee
    if cash_after < 0:
        book.refusals["cash"] += 1
        return None
    if side == "sell" and qty > book.position(proposal["symbol"]):
        book.refusals["sell_backed"] += 1
        return None
    book.cash = cash_after
    current = book.position(proposal["symbol"])
    if side == "buy":
        book.shares[proposal["symbol"]] = current + qty
    else:
        left = current - qty
        if left <= 0:
            book.shares.pop(proposal["symbol"], None)
        else:
            book.shares[proposal["symbol"]] = left
    book.simulated_fills += 1
    from sidecar.tca.shortfall import implementation_shortfall

    record = {
        "symbol": proposal["symbol"],
        "side": side,
        "qty": format(qty, "f"),
        "decision_price": format(decision_price, "f"),
        "arrival_mid": format(arrival, "f"),
        "fill_price": format(fill_price, "f"),
        "fee": format(fee, "f"),
        "sigma": format(sigma, "f"),
        "adv": format(adv, "f"),
        "predicted_cost_bps": format(FLAT_COST_BPS if cost_model == "flat" else HALF_SPREAD * Decimal(10000), "f"),
        "venue_label": "simulated_next_open",
    }
    record["shortfall"] = implementation_shortfall(record)
    return record
