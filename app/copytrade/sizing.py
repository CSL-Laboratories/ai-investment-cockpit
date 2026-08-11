"""RW-16 — turning a disclosed dollar BUCKET into a size for YOUR account.

PURE FUNCTIONS ONLY. No I/O, no clock, no model.

THE CENTRAL PROBLEM. A periodic transaction report says "$1,001 - $15,000" or
"$1,000,001 - $5,000,000". It never says how many shares, never says at what price, and the
range is often two orders of magnitude wide. It is also a number about someone else's
balance sheet: a $250k purchase may be 0.5% of one member's portfolio and 40% of another's.

So the disclosed amount is used for exactly ONE thing here — a bounded CONVICTION WEIGHT —
and the dollar size comes from the reader's own portfolio. Treating the bucket midpoint as a
position size (the intuitive move) would copy a stranger's risk budget into your account.

WHY A TABLE AND NOT A FORMULA. The tier weights below are an explicit, greppable table
rather than a log-scaled curve. A reviewer can read the whole sizing policy in nine lines and
say whether they agree with it; a formula would need to be simulated to be understood. The
weights are also flat-topped on purpose: above $1M the marginal information in "even bigger"
is small, and letting the weight keep climbing would let one outlier filing dominate.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from copytrade.models import AmountRange

# The STOCK Act disclosure tiers, low bound -> conviction weight. Looked up by the LOW bound
# of the disclosed range (the only bound present on every filing — the top tier is open-ended).
#
# Weight 1.0 is the baseline: a copy at exactly ``policy.copy_fraction`` of portfolio value.
_TIER_WEIGHTS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("1001"), Decimal("0.50")),
    (Decimal("15001"), Decimal("0.75")),
    (Decimal("50001"), Decimal("1.00")),
    (Decimal("100001"), Decimal("1.25")),
    (Decimal("250001"), Decimal("1.50")),
    (Decimal("500001"), Decimal("1.75")),
    (Decimal("1000001"), Decimal("2.00")),
    (Decimal("5000001"), Decimal("2.00")),
    (Decimal("25000001"), Decimal("2.00")),
    (Decimal("50000001"), Decimal("2.00")),
)
MAX_TIER_WEIGHT = Decimal("2.00")
_CENTS = Decimal("0.01")
_ONE = Decimal("1")

# ---- clamp / sizing notes (short machine codes, recorded on the emitted intent) --------------
# Named constants rather than bare literals because ``engine.py`` maps two of them onto SKIP
# reasons and the operator surface aggregates on the strings.
NOTE_ZERO_SIZE = "zero_size"
NOTE_BELOW_MIN = "below_min_notional"
NOTE_CAPPED_AT_MAX = "capped_at_max_notional"
NOTE_CAPPED_AT_HAIRCUT_CEILING = "capped_at_haircut_ceiling"
"""A haircut intent hit ITS ceiling — ``maximum * haircut`` — not the policy's full ceiling.
Distinct from ``capped_at_max_notional`` on purpose: an operator reading "capped at max" on an
options-derived intent would reasonably conclude the haircut had not been applied."""
NOTE_HAIRCUT_FLOORED = "haircut_floored_below_full_copy"
"""Sub-cent rounding would have made the haircut size equal the full-copy size, so the size was
stepped one cent below it. Rare (it needs a haircut within ~0.04% of 1), recorded because a size
that did not come straight out of the formula must never be silent."""


def tier_weight(amount: AmountRange) -> Decimal:
    """Conviction weight for a disclosed range. Bounded to ``[0, MAX_TIER_WEIGHT]``.

    An amount below the smallest tier weighs 0 — the caller drops it rather than trading a
    sub-threshold disclosure at a token size.
    """
    weight = Decimal("0")
    for low, w in _TIER_WEIGHTS:
        if amount.low >= low:
            weight = w
    return min(weight, MAX_TIER_WEIGHT)


def staleness_factor(age_days: int, *, max_age_days: int, decay: Decimal) -> Decimal:
    """Linear size decay across the staleness window, in ``[1 - decay, 1]``.

    Age is measured from the TRADE, not the filing (see ``Disclosure.age_days``). At
    ``age_days == 0`` this is 1; at ``age_days == max_age_days`` it is ``1 - decay``.

    Out-of-window ages are NOT handled here — they are refused outright by the engine's
    staleness gate. This function clamps anyway, because a sizing helper that silently
    returns a negative multiplier on unexpected input is a bug that shows up as a
    nonsensical order rather than an error.
    """
    if max_age_days <= 0:
        return Decimal("1")
    age = max(0, min(int(age_days), max_age_days))
    return Decimal("1") - (decay * Decimal(age) / Decimal(max_age_days))


def portfolio_value(cash: Decimal, holdings: dict) -> Decimal:
    """Cash + reconciled holdings market value. Non-finite inputs read as ZERO.

    Conservative on purpose and for the same reason ``risk.engine`` does it: an
    unverifiable portfolio value must shrink the size we are willing to take, never inflate
    it. A broker snapshot with one garbage ``market_value`` should cost us that holding's
    contribution, not produce a NaN that compares favourably against every cap.
    """
    total = cash if _finite(cash) else Decimal("0")
    for h in (holdings or {}).values():
        mv = h.get("market_value", 0) if isinstance(h, dict) else 0
        mv = _dec(mv)
        if _finite(mv):
            total += mv
    return total if total > 0 else Decimal("0")


def intent_notional(
    *,
    amount: AmountRange,
    age_days: int,
    portfolio: Decimal,
    copy_fraction: Decimal,
    max_age_days: int,
    staleness_decay: Decimal,
    haircut: Decimal = Decimal("1"),
) -> Decimal:
    """The dollar size of one copied trade, before the policy's min/max clamp.

    ``portfolio * copy_fraction * tier_weight * staleness_factor * haircut``

    Every factor is in ``[0, 2]`` and the product is monotonic in each, so the size cannot be
    surprised by an interaction between two knobs. Rounded DOWN to the cent: rounding a
    position size up is free money the account does not have.
    """
    if portfolio <= 0:
        return Decimal("0")
    raw = (
        portfolio
        * copy_fraction
        * tier_weight(amount)
        * staleness_factor(age_days, max_age_days=max_age_days, decay=staleness_decay)
        * haircut
    )
    if not _finite(raw) or raw <= 0:
        return Decimal("0")
    return raw.quantize(_CENTS, rounding=ROUND_DOWN)


def clamp_notional(
    notional: Decimal, *, minimum: Decimal, maximum: Decimal
) -> tuple[Decimal, str | None]:
    """Apply the per-trade floor and ceiling.

    Returns ``(size, note)``. A size under the floor returns ``(0, "below_min_notional")`` —
    DROPPED, not rounded up to the floor, because rounding up would mean the policy's own
    minimum silently became its sizing rule for every small signal.
    """
    if notional <= 0:
        return Decimal("0"), NOTE_ZERO_SIZE
    if notional < minimum:
        return Decimal("0"), NOTE_BELOW_MIN
    if notional > maximum:
        return maximum.quantize(_CENTS, rounding=ROUND_DOWN), NOTE_CAPPED_AT_MAX
    return notional, None


def haircut_ceiling(maximum: Decimal, *, haircut: Decimal) -> Decimal:
    """The per-trade ceiling a HAIRCUT intent is allowed to reach: ``maximum * haircut``.

    THIS IS THE WHOLE POINT OF THE HAIRCUT SURVIVING THE CLAMP. A haircut applied only to the
    raw size is erased the moment both the full-size and the discounted size exceed the same
    ceiling — at a large enough portfolio EVERY options-derived intent and EVERY equity intent
    land on ``max_notional_per_trade`` and the operator's one explicit requirement ("for options
    trades do not copy them one for one") silently stops holding. Discounting the CEILING by the
    same multiplier makes the discount a property of the policy band, not of where on the curve
    this particular disclosure happened to fall.

    Rounded DOWN to the cent, like every other size here.
    """
    if not _finite(maximum) or maximum <= 0:
        return Decimal("0")
    if not _finite(haircut) or haircut >= _ONE:
        return maximum.quantize(_CENTS, rounding=ROUND_DOWN)
    if haircut <= 0:
        return Decimal("0")
    return (maximum * haircut).quantize(_CENTS, rounding=ROUND_DOWN)


def sized_copy_notional(
    *,
    amount: AmountRange,
    age_days: int,
    portfolio: Decimal,
    copy_fraction: Decimal,
    max_age_days: int,
    staleness_decay: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    haircut: Decimal = _ONE,
) -> tuple[Decimal, str | None]:
    """Size AND clamp one copied buy in one place, so a haircut cannot be lost between them.

    Returns ``(size, note)`` with the same vocabulary ``clamp_notional`` uses, plus the two
    haircut notes above. ``size == 0`` always carries a note naming why.

    THE INVARIANT THIS FUNCTION EXISTS TO GUARANTEE
    -----------------------------------------------
        for identical inputs, a haircut size (``haircut < 1``) is STRICTLY LESS than the
        full-copy size (``haircut == 1``) — at every portfolio value, including ones where
        one clamps, and ones where both would clamp.

    It is guaranteed by construction rather than hoped for, in three layers, because "the
    numbers happen to differ" is exactly the property that quietly stopped holding before:

      1. the raw size is discounted (``intent_notional(haircut=...)``);
      2. the CEILING is discounted too (``haircut_ceiling``) — the layer that was missing;
      3. the full-copy size is computed alongside and the discounted size is stepped a cent
         below it if sub-cent rounding ever made them equal.

    A ``haircut >= 1`` is not a haircut and is handled as the plain full-copy path — so a
    misconfigured multiplier above 1 can never INFLATE a size above the policy band.
    """
    full_raw = intent_notional(
        amount=amount,
        age_days=age_days,
        portfolio=portfolio,
        copy_fraction=copy_fraction,
        max_age_days=max_age_days,
        staleness_decay=staleness_decay,
        haircut=_ONE,
    )
    full_size, full_note = clamp_notional(full_raw, minimum=minimum, maximum=maximum)
    if not _finite(haircut) or haircut >= _ONE:
        return full_size, full_note

    raw = intent_notional(
        amount=amount,
        age_days=age_days,
        portfolio=portfolio,
        copy_fraction=copy_fraction,
        max_age_days=max_age_days,
        staleness_decay=staleness_decay,
        haircut=haircut,
    )
    ceiling = haircut_ceiling(maximum, haircut=haircut)
    size, note = clamp_notional(raw, minimum=minimum, maximum=ceiling)
    if note == NOTE_CAPPED_AT_MAX:
        note = NOTE_CAPPED_AT_HAIRCUT_CEILING
    if size > 0 and size < minimum:
        # The discounted ceiling sits UNDER the policy floor, so no compliant haircut size
        # exists at all. Dropped and named rather than emitted below the floor the operator set.
        return Decimal("0"), NOTE_BELOW_MIN
    if size > 0 and full_size > 0 and size >= full_size:
        # Layer 3. Only reachable when the discount is smaller than the cent both sides round
        # to. Fail-closed direction: step DOWN, never let the two meet.
        stepped = full_size - _CENTS
        if stepped < minimum or stepped <= 0:
            return Decimal("0"), NOTE_BELOW_MIN
        return stepped, NOTE_HAIRCUT_FLOORED
    return size, note


def exit_notional(held_market_value: Decimal, *, fraction: Decimal = Decimal("1")) -> Decimal:
    """Size a mirrored SALE against what you actually hold.

    Bounded by the holding, always: the risk engine refuses a sell larger than its reconciled
    market value (no naked shorts), so producing one here would only manufacture a rejected
    proposal and a wasted human approval.
    """
    mv = _dec(held_market_value)
    if not _finite(mv) or mv <= 0:
        return Decimal("0")
    size = mv * fraction
    return min(size, mv).quantize(_CENTS, rounding=ROUND_DOWN)


def _dec(x) -> Decimal:
    from decimal import InvalidOperation

    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _finite(x) -> bool:
    d = _dec(x)
    return d.is_finite()
