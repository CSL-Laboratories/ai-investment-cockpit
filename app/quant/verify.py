"""QT-05 — pre-trade execution verification. What a proposal will actually COST.

WHAT THIS ANSWERS
------------------
The cockpit's existing pipeline decides WHETHER a trade is a good idea. Nothing in it has
ever been able to say what the trade costs — there is no book, no depth, no fill model
anywhere else in the tree. So a 40,000-share order in a name that shows 800 shares at the
touch passes every check in ``risk/engine.py`` today, because none of them can see the book.

``verify_proposal`` walks the actual book and returns the number: expected slippage, the
participation rate, the fees, and the total in basis points. That is Tier A — advisory,
attached to a proposal as evidence, with no authority over anything. Tier B
(``risk/engine.py``) turns the same verdict into a real pre-trade check.

PURITY IS A HARD CONSTRAINT HERE, NOT A PREFERENCE
----------------------------------------------------
``risk/`` imports this module, and Invariant 5 requires the risk engine to be pure
deterministic functions — no LLM, no I/O, no subprocess. So:

    * stdlib and ``decimal`` only. No numpy, no pandas, no network, no filesystem, no clock.
    * every input arrives as an argument; nothing is fetched.
    * the same inputs always produce the same verdict, which is what makes a risk decision
      reproducible from an audit record.

``tests/test_invariants.py`` asserts this rather than trusting it. If you need something
this module cannot compute from its arguments, compute it in the caller and pass it in.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does not produce a fill probability for a RESTING order. That number is exactly the
``exp(-k*delta)`` shortcut this whole engine exists to replace, and inventing one here would
smuggle it back in through the verifier — where it would be even more damaging, because a
risk check carries more authority than a backtest.

What it reports instead is ``queue_ahead_shares``: how much size must trade before a passive
order at that price would. That is a MEASUREMENT, not a model. A caller who wants to convert
it into a probability must supply their own turnover assumption and own the consequence.

FAIL CLOSED
------------
A one-sided book, an empty book, a stale snapshot or an unparseable size yields
``INSUFFICIENT_DATA`` — never a confident ``OK``. Invariant 7 governs: stale data degrades to
"do not proceed without a human", never to "proceed without analysis".
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from quant.book.lob import BookSnapshot
from quant.costs import FeeSchedule
from quant.events import Liquidity, PriceScale, Side


class Verdict(enum.Enum):
    OK = "ok"
    MARGINAL = "marginal"
    UNECONOMIC = "uneconomic"
    INSUFFICIENT_DATA = "insufficient_data"

    @property
    def is_actionable(self) -> bool:
        return self is Verdict.OK


@dataclass(frozen=True, slots=True)
class VerifyParams:
    """Thresholds and assumptions. Explicit because every one of them is a judgement call."""

    scale: PriceScale
    schedule: FeeSchedule = field(default_factory=FeeSchedule)

    marginal_cost_bps: Decimal = Decimal("15")
    uneconomic_cost_bps: Decimal = Decimal("50")
    """Above this the trade cannot plausibly pay for itself. 50bps is half a percent of
    round-trip cost before the thesis has to be right about anything, which is a high bar
    for a discretionary equity idea and a fatal one for a short-horizon trade."""

    max_participation: Decimal = Decimal("0.25")
    """Fraction of DISPLAYED far-side depth this order may consume before being flagged.
    Beyond roughly a quarter of the visible book, the walk-the-book slippage estimate stops
    being an estimate of anything: the book refills, other participants react, and the
    realised cost depends on behaviour no snapshot can show."""

    max_staleness_ns: int = 5_000_000_000
    """A snapshot older than this yields INSUFFICIENT_DATA. Fail closed (Invariant 7)."""


@dataclass(frozen=True, slots=True)
class QuantVerdict:
    """The execution-quality opinion on one proposal. Serialisable, auditable, advisory.

    Carries ``book_seq`` and ``computed_at_ns`` so a consumer can tell whether the verdict
    still describes the book it is about to trade against. A verdict without that provenance
    would be indistinguishable from a stale one, and a stale verdict that reads OK is worse
    than no verdict at all.
    """

    symbol: str
    side: str
    qty: int
    verdict: Verdict
    expected_slippage_bps: Decimal
    expected_fee_bps: Decimal
    expected_cost_bps: Decimal
    participation_rate: Decimal
    fillable_now: int
    queue_ahead_shares: int | None
    touch_ticks: int | None
    reasons: tuple[str, ...]
    book_seq: int
    computed_at_ns: int

    @property
    def is_actionable(self) -> bool:
        return self.verdict.is_actionable

    @property
    def fully_fillable(self) -> bool:
        return self.fillable_now >= self.qty

    def as_dict(self) -> dict:
        """Plain JSON types only — this lands in an audit payload and a WebSocket frame,
        and the audit chain's canonical encoder has no ``default=`` (it fails closed on any
        non-JSON-native value rather than guessing)."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "verdict": self.verdict.value,
            "expected_slippage_bps": str(self.expected_slippage_bps),
            "expected_fee_bps": str(self.expected_fee_bps),
            "expected_cost_bps": str(self.expected_cost_bps),
            "participation_rate": str(self.participation_rate),
            "fillable_now": self.fillable_now,
            "queue_ahead_shares": self.queue_ahead_shares,
            "touch_ticks": self.touch_ticks,
            "reasons": list(self.reasons),
            "book_seq": self.book_seq,
            "computed_at_ns": self.computed_at_ns,
        }


def _insufficient(symbol: str, side: str, qty: int, book_seq: int, ts: int, reason: str) -> QuantVerdict:
    zero = Decimal("0")
    return QuantVerdict(
        symbol=symbol, side=side, qty=qty, verdict=Verdict.INSUFFICIENT_DATA,
        expected_slippage_bps=zero, expected_fee_bps=zero, expected_cost_bps=zero,
        participation_rate=zero, fillable_now=0, queue_ahead_shares=None,
        touch_ticks=None, reasons=(reason,), book_seq=book_seq, computed_at_ns=ts,
    )


def proposal_quantity(proposal: dict, reference_price: Decimal) -> int | None:
    """Shares implied by a ``TradeProposal``: ``quantity``, else ``notional / price``.

    Returns None rather than guessing when neither is usable. The schema makes both nullable
    (sizing may be expressed either way), so "neither present" is a real state and must not
    silently become zero — a zero-share order would sail through every cost check.
    """
    quantity = proposal.get("quantity")
    if quantity is not None:
        try:
            shares = int(Decimal(str(quantity)))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return shares if shares > 0 else None

    notional = proposal.get("notional")
    if notional is None or reference_price <= 0:
        return None
    try:
        shares = int(Decimal(str(notional)) / reference_price)
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return None
    return shares if shares > 0 else None


def verify_proposal(proposal: dict, book: BookSnapshot, params: VerifyParams,
                    *, now_ns: int | None = None) -> QuantVerdict:
    """Walk the book and price the proposal's execution. Pure; no I/O, no clock.

    ``now_ns`` is passed in rather than read, both because this module may not touch a clock
    and because the staleness test must be reproducible from an audit record — a verdict that
    depends on when it is re-examined cannot be re-checked.
    """
    symbol = proposal.get("symbol", book.symbol)
    side_raw = proposal.get("side", "")
    ts = now_ns if now_ns is not None else book.ts

    if side_raw not in ("buy", "sell"):
        return _insufficient(symbol, str(side_raw), 0, book.seq, ts, f"unknown side {side_raw!r}")
    side = Side.BUY if side_raw == "buy" else Side.SELL

    mid = book.mid_ticks
    if mid is None:
        return _insufficient(symbol, side_raw, 0, book.seq, ts,
                             "one-sided or empty book: no mid to price against")
    if now_ns is not None and now_ns - book.ts > params.max_staleness_ns:
        return _insufficient(symbol, side_raw, 0, book.seq, ts,
                             f"book snapshot is {(now_ns - book.ts) / 1e9:.1f}s stale")

    scale = params.scale
    mid_price = Decimal(str(mid)) * scale.tick_size
    qty = proposal_quantity(proposal, mid_price)
    if qty is None:
        return _insufficient(symbol, side_raw, 0, book.seq, ts,
                             "proposal carries neither a usable quantity nor a notional")

    # Walk the FAR side: a buy consumes asks, a sell consumes bids.
    levels = book.asks if side is Side.BUY else book.bids
    displayed = sum(level.qty for level in levels)
    if displayed <= 0:
        return _insufficient(symbol, side_raw, qty, book.seq, ts,
                             "no displayed liquidity on the side this order must consume")

    remaining = qty
    filled = 0
    notional_ticks = Decimal("0")
    for level in levels:
        if remaining <= 0:
            break
        take = min(remaining, level.qty)
        notional_ticks += Decimal(level.price_ticks) * Decimal(take)
        filled += take
        remaining -= take

    reasons: list[str] = []
    touch = levels[0].price_ticks
    participation = (Decimal(qty) / Decimal(displayed)).quantize(Decimal("0.0001"))

    if filled == 0:
        return _insufficient(symbol, side_raw, qty, book.seq, ts, "book shows no fillable size")

    vwap_ticks = notional_ticks / Decimal(filled)
    mid_ticks = Decimal(str(mid))
    # Signed so that a BUY paying above the mid and a SELL receiving below it are BOTH a
    # positive cost. Unsigned would make every sell look free.
    slip_ticks = (vwap_ticks - mid_ticks) if side is Side.BUY else (mid_ticks - vwap_ticks)
    slippage_bps = (slip_ticks / mid_ticks * Decimal("10000")).quantize(Decimal("0.01"))

    # A proposal that must cross is a TAKER. Assuming maker treatment here would credit a
    # rebate the order will not earn and understate the cost by the full maker/taker spread.
    fee = params.schedule.fee_for(side=side, qty=filled,
                                  price=scale.to_decimal(int(vwap_ticks)),
                                  liquidity=Liquidity.TAKER)
    fee_notional = scale.to_decimal(int(vwap_ticks)) * Decimal(filled)
    fee_bps = ((fee / fee_notional * Decimal("10000")).quantize(Decimal("0.01"))
               if fee_notional > 0 else Decimal("0"))

    total_bps = slippage_bps + fee_bps

    if remaining > 0:
        reasons.append(
            f"book shows only {filled} of {qty} shares within the visible depth; "
            f"{remaining} would rest or chase"
        )
    if participation > params.max_participation:
        reasons.append(
            f"participation {participation} exceeds {params.max_participation} of displayed "
            "depth — the walk-the-book estimate stops being reliable above this"
        )

    # Queue depth if this order rested at the touch instead of crossing. A MEASUREMENT, not
    # a fill probability — see the module docstring for why no probability is produced.
    near = book.bids if side is Side.BUY else book.asks
    queue_ahead = near[0].qty if near else None

    # ESCALATION ON PARTICIPATION IS INDEPENDENT OF THE COST NUMBER, AND MUST BE.
    #
    # ``slippage_bps`` prices only the FILLABLE portion, because a snapshot cannot say what
    # the unfillable remainder would cost — the book refills, other participants react, and
    # the true answer depends on behaviour no snapshot contains. That makes the cost figure
    # systematically OPTIMISTIC exactly when the order is too large: 40,000 shares against
    # 3,500 of displayed depth prices out at a comfortable few bps, because it silently
    # reports the cost of the 3,500 it could actually do.
    #
    # So an order that exceeds all displayed depth is judged UNECONOMIC on that ground
    # alone. The visible market cannot absorb it, and no cost estimate derived from this
    # book is entitled to say otherwise.
    if participation > Decimal("1"):
        verdict = Verdict.UNECONOMIC
        reasons.append(
            f"order is {participation}x ALL displayed depth on the side it must consume; "
            f"the {total_bps}bps estimate prices only the {filled} shares the book can fill "
            "and understates the true cost by an unknown amount"
        )
    elif total_bps >= params.uneconomic_cost_bps:
        verdict = Verdict.UNECONOMIC
        reasons.append(f"expected cost {total_bps}bps at or above the uneconomic threshold "
                       f"{params.uneconomic_cost_bps}bps")
    elif total_bps >= params.marginal_cost_bps or remaining > 0 or participation > params.max_participation:
        verdict = Verdict.MARGINAL
        if total_bps >= params.marginal_cost_bps:
            reasons.append(f"expected cost {total_bps}bps at or above the marginal threshold "
                           f"{params.marginal_cost_bps}bps")
    else:
        verdict = Verdict.OK

    return QuantVerdict(
        symbol=symbol, side=side_raw, qty=qty, verdict=verdict,
        expected_slippage_bps=slippage_bps, expected_fee_bps=fee_bps,
        expected_cost_bps=total_bps, participation_rate=participation,
        fillable_now=filled, queue_ahead_shares=queue_ahead, touch_ticks=touch,
        reasons=tuple(reasons), book_seq=book.seq, computed_at_ns=ts,
    )
