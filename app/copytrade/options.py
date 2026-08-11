"""RW-16 — the options rule: a disclosed CONTRACT is a signal, never an instrument to copy.

PURE FUNCTIONS ONLY. No I/O, no clock (``today`` is passed in), no model, no randomness.

THE OPERATOR'S REQUIREMENT, VERBATIM
------------------------------------
    "for options trades do not copy them one for one as we get delayed information."

That is not a preference; it is the only defensible reading of the mechanics, and there are
THREE independent reasons, any one of which alone would be sufficient:

1. THE INSTRUMENT MAY NOT EXIST ANY MORE. A periodic transaction report lands 30-45 days
   after the trade (``Disclosure.disclosure_lag_days``). A congressional options position is
   frequently a weekly or a near-dated monthly. By the time we can read about it, the
   contract can be expired, deep out of the money, or trading at a price that has nothing to
   do with the price the discloser paid. Copying it "one for one" would buy a different
   instrument at a different point on its decay curve and call it the same trade.

2. THE RISK ENGINE DOES NOT SUPPORT OPTIONS AT ALL. ``risk.engine.SUPPORTED_ASSET_TYPES`` is
   exactly ``{"equity"}``, and the ``asset_type_supported`` check is HARD. An options
   proposal is therefore not a risky order — it is an order that cannot be adjudicated, and
   Invariant 1 says nothing reaches a broker except through that adjudication. Making options
   copyable would mean widening the order-path gate for a feed we do not control, which is
   the exact shape of change the invariants exist to forbid.

3. THE FILING DOES NOT SAY ENOUGH TO PRICE ONE. There is no premium, no quantity, no fill
   price — only a dollar BUCKET that lumps the premium paid in with everything else, and
   sometimes a strike and expiry scraped out of free text. There is no honest way to turn
   that into a contract count.

SO THE TRANSLATION, NOT THE COPY
---------------------------------
What survives the lag is the DIRECTION of the discloser's conviction, and the only vehicle
this system can legally and mechanically hold is the underlying equity. So:

    long calls   ->  a SMALL long position in the underlying (haircut)
    long puts    ->  bearish; we cannot short (``risk.engine``'s sell-backed check forbids a
                     naked short), so the only expressible action is EXITING a position we
                     already hold. If we hold nothing, the signal is structurally unusable
                     and is DROPPED — never inverted into an invented short.
    any close    ->  ambiguous (closing? rolling? assigned? expired worthless?), and every
                     ambiguity here resolves toward REDUCING exposure, never toward opening
                     one, because a wrong exit costs opportunity and a wrong entry costs cash.

The haircut is the honest part. A call buyer paid a premium for leverage; buying the
underlying at the same notional would take several times the delta they took, and buying it
at the same DELTA would need a contract count the filing does not disclose. So the position
is deliberately small (``policy.options_haircut``, default 0.25) and the number is a policy
knob an operator can read, not a model output.

AND THE HAIRCUT HAS TO SURVIVE THE CLAMP. Discounting only the raw size is not enough: above
some portfolio value the discounted size and the full size both exceed
``policy.max_notional_per_trade``, both clamp to it, and the two become byte-identical — the
requirement silently stops holding at exactly the account sizes where it matters most. So
``sizing.sized_copy_notional`` discounts the CEILING by the same multiplier and guarantees the
inequality by construction; the haircut is recorded on the emitted intent
(``sizing_notes``/``translated_from``) so an operator can see that it was applied.

WHY THIS MODULE CANNOT SEE THE PORTFOLIO
-----------------------------------------
``translate_option`` takes a disclosure and a policy and nothing else — it is pinned that way
by the build contract, and the shape is right: this module answers "what does this filing
MEAN", and ``engine.py`` answers "and can this account act on it". So a bearish translation
returns ``exit_underlying`` unconditionally, and the engine is the thing that turns an exit
of an unheld symbol into a counted, named skip (``option_exit_not_held``). Splitting it that
way keeps the meaning of a filing independent of who is reading it, which is what makes this
function replayable against a historical portfolio.

EVERY OUTCOME IS NAMED. There is no path out of ``translate_option`` that is not one of
``OPTION_ACTIONS`` carrying one of ``OPTION_REASONS``, and the engine surfaces the reason
verbatim on a ``Skip``. A translation layer that silently discards two thirds of its input is
indistinguishable from a broken one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from copytrade.models import (
    ASSET_OPTION_CALL,
    ASSET_OPTION_PUT,
    KNOWN_TX_TYPES,
    TX_EXCHANGE,
    TX_PURCHASE,
    TX_SALE,
    TX_SALE_PARTIAL,
    Disclosure,
)
from copytrade.policy import OPTIONS_IGNORE, CopyPolicy

# ---- the three things a translation can ask the engine to do -------------------------------
# Deliberately phrased as UNDERLYING actions. There is no vocabulary here for "buy the
# contract", so no future edit can express one without adding a constant that a reviewer will
# see (and that ``risk.engine.SUPPORTED_ASSET_TYPES`` would reject anyway).
ACTION_BUY_UNDERLYING = "buy_underlying"
ACTION_EXIT_UNDERLYING = "exit_underlying"
ACTION_DROP = "drop"
OPTION_ACTIONS = frozenset({ACTION_BUY_UNDERLYING, ACTION_EXIT_UNDERLYING, ACTION_DROP})

# ---- drop reasons (short snake_case codes; the engine puts these straight on a Skip) -------
REASON_POLICY_IGNORE = "options_policy_ignore"
"""The operator set ``options_policy=ignore``. Checked FIRST, before any fact about the
contract, so the reason the operator reads back is the switch they threw and not an incidental
property of one filing."""

REASON_NOT_AN_OPTION = "not_an_option"
"""Defensive: ``translate_option`` was handed an equity/other row. The engine never does this;
if it ever starts, the disclosure is dropped rather than translated on a guess."""

REASON_NO_UNDERLYING_SYMBOL = "option_no_underlying_symbol"
"""No ticker was mappable off the filing, so there is nothing to buy or sell. The engine gates
this earlier too; kept here so the function is safe called standalone."""

REASON_EXPIRED = "option_expired"
"""The disclosed expiry is in the past AS OF ``today``. This is the literal face of the
operator's requirement: the trade being described is over."""

REASON_NEAR_EXPIRY = "option_near_expiry"
"""The disclosed expiry is nearer than ``policy.options_min_days_to_expiry``. The discloser's
own thesis window is closing; translating it into a fresh position in the underlying would be
opening a NEW multi-week exposure on the strength of a signal whose author is about to be done
with it. Note the boundary: an expiry falling exactly on ``today`` reads as *near* expiry, not
*expired* — both drop, so nothing unsafe hangs on which side of the line it lands."""

REASON_UNKNOWN_TX_TYPE = "option_unknown_tx_type"
"""Defensive: a transaction code outside ``KNOWN_TX_TYPES``. Never guessed."""

OPTION_DROP_REASONS = frozenset({
    REASON_POLICY_IGNORE,
    REASON_NOT_AN_OPTION,
    REASON_NO_UNDERLYING_SYMBOL,
    REASON_EXPIRED,
    REASON_NEAR_EXPIRY,
    REASON_UNKNOWN_TX_TYPE,
})

# ---- acting reasons (recorded on the emitted intent, not on a skip) ------------------------
REASON_CALL_TO_UNDERLYING = "option_call_to_underlying"
"""Long calls -> a haircut long position in the underlying. The one translation that OPENS
exposure, and the only one that carries a haircut."""

REASON_PUT_BEARISH_EXIT = "option_put_bearish_exit"
"""Long puts -> bearish. Expressible only as an exit of something already held."""

REASON_CLOSE_AMBIGUOUS_EXIT = "option_close_ambiguous_exit"
"""A disclosed SALE or EXCHANGE of a contract. Could be taking profit, rolling forward, being
assigned, or closing a hedge — the filing does not distinguish them, and neither will we. It
resolves to an exit-if-held because that is the only reading of an ambiguous close that cannot
lose money the account does not already have at risk."""

OPTION_ACT_REASONS = frozenset({
    REASON_CALL_TO_UNDERLYING,
    REASON_PUT_BEARISH_EXIT,
    REASON_CLOSE_AMBIGUOUS_EXIT,
})

OPTION_REASONS = OPTION_DROP_REASONS | OPTION_ACT_REASONS

# An exit is never haircut. The haircut prices the leverage in a LONG option position against
# a cash position in the underlying; there is no equivalent argument for getting out, and a
# half-exit on a bearish signal leaves behind exactly the exposure the signal warned about.
_FULL = Decimal("1")
_NONE = Decimal("0")

# Every close-like transaction code. ``TX_EXCHANGE`` is here rather than in its own branch
# because an exchange of a contract is a close plus an open we cannot see.
_CLOSE_TX_TYPES = frozenset({TX_SALE, TX_SALE_PARTIAL, TX_EXCHANGE})


@dataclass(frozen=True)
class OptionTranslation:
    """What a disclosed options transaction means in instruments this account can hold.

    ``haircut`` is a multiplier to hand to ``sizing.intent_notional``. It is meaningful ONLY
    for ``buy_underlying``; exits carry 1 and drops carry 0. The zero on a drop is deliberate
    and enforced below: a caller that ignores ``action`` and sizes off ``haircut`` anyway gets
    a zero-dollar position rather than a full one, so the failure mode of misreading this
    object is "no trade", not "the wrong trade".
    """

    action: str
    haircut: Decimal
    reason: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.action not in OPTION_ACTIONS:
            raise ValueError(f"unknown option action {self.action!r}")
        if not isinstance(self.haircut, Decimal) or not self.haircut.is_finite():
            raise ValueError("option haircut must be a finite Decimal")
        if not (_NONE <= self.haircut <= _FULL):
            raise ValueError("option haircut must be within [0, 1]")
        if self.action == ACTION_DROP and self.haircut != _NONE:
            raise ValueError("a dropped translation must carry a zero haircut")
        if self.action == ACTION_BUY_UNDERLYING and self.haircut >= _FULL:
            # Structural restatement of the operator's requirement, at the type boundary: a
            # translation that OPENS exposure off an options filing may not be a 1:1 copy.
            # ``policy.CopyPolicy`` already refuses ``options_haircut == 1``; this makes the
            # rule true of every OptionTranslation, however it was constructed.
            raise ValueError("an option-derived buy must carry a haircut strictly below 1")
        if not self.reason:
            raise ValueError("every option translation must carry a reason code")

    @property
    def emits(self) -> bool:
        """True when the engine should go on to size something. Read this rather than
        comparing against ``ACTION_DROP`` in three places."""
        return self.action != ACTION_DROP

    @classmethod
    def drop(cls, reason: str, detail: str = "") -> "OptionTranslation":
        return cls(action=ACTION_DROP, haircut=_NONE, reason=reason, detail=detail)

    @classmethod
    def buy(cls, haircut: Decimal, reason: str, detail: str = "") -> "OptionTranslation":
        return cls(action=ACTION_BUY_UNDERLYING, haircut=haircut, reason=reason, detail=detail)

    @classmethod
    def exit(cls, reason: str, detail: str = "") -> "OptionTranslation":
        return cls(action=ACTION_EXIT_UNDERLYING, haircut=_FULL, reason=reason, detail=detail)


def translate_option(d: Disclosure, policy: CopyPolicy, *, today: date) -> OptionTranslation:
    """Translate ONE disclosed options transaction into an action on the underlying.

    PURE: ``today`` is a parameter, not a clock read, so a replay over a historical batch
    produces exactly the decisions the engine would have produced on the day.

    GATE ORDER, AND WHY IT IS THIS ORDER
    -------------------------------------
    1. ``options_policy == ignore`` — the operator's global off-switch outranks every fact
       about the contract. If they turned options off, the reason they read back should say
       so, not "near expiry".
    2. not an option / no underlying symbol — defensive shape checks. Both are also gated
       earlier in ``engine.plan``; they are repeated here so this function is safe to call on
       its own (and so a future caller cannot get a translation of something untranslatable).
    3. the EXPIRY WINDOW — the direct expression of "we get delayed information". Checked
       before the direction dispatch because an expired contract has no direction worth
       acting on regardless of whether it was a call or a put. An UNDISCLOSED expiry does not
       drop: the gate cannot be evaluated, the translation is already a haircut position in
       the underlying rather than a contract, and ``engine.plan`` records
       ``option_expiry_undisclosed`` on the intent so the operator sees which ones were
       admitted without this check.
    4. direction dispatch — purchase of calls / purchase of puts / any close.

    Returns a translation whose ``action`` is one of ``OPTION_ACTIONS`` and whose ``reason``
    is one of ``OPTION_REASONS``. There is no other exit from this function.
    """
    # 1 ---- the operator's switch --------------------------------------------------------
    if policy.options_policy == OPTIONS_IGNORE:
        return OptionTranslation.drop(
            REASON_POLICY_IGNORE,
            "options_policy=ignore: surfaced for awareness, never translated",
        )

    # 2 ---- shape ------------------------------------------------------------------------
    if not d.is_option:
        return OptionTranslation.drop(
            REASON_NOT_AN_OPTION,
            f"asset_type={d.asset_type!r} is not an options row",
        )
    symbol = (d.symbol or "").strip()
    if not symbol:
        return OptionTranslation.drop(
            REASON_NO_UNDERLYING_SYMBOL,
            "no underlying ticker was mappable from the filing",
        )
    if d.tx_type not in KNOWN_TX_TYPES:
        return OptionTranslation.drop(
            REASON_UNKNOWN_TX_TYPE,
            f"tx_type={d.tx_type!r} is outside {sorted(KNOWN_TX_TYPES)}",
        )

    # 3 ---- the expiry window ------------------------------------------------------------
    if d.option_expiry is not None:
        dte = (d.option_expiry - today).days
        if dte < 0:
            return OptionTranslation.drop(
                REASON_EXPIRED,
                f"contract expired {abs(dte)}d ago ({d.option_expiry.isoformat()}); the "
                f"disclosed trade is over before we could read about it",
            )
        if dte < policy.options_min_days_to_expiry:
            return OptionTranslation.drop(
                REASON_NEAR_EXPIRY,
                f"{dte}d to expiry ({d.option_expiry.isoformat()}), policy requires at least "
                f"{policy.options_min_days_to_expiry}d",
            )

    # 4 ---- direction --------------------------------------------------------------------
    if d.tx_type == TX_PURCHASE and d.asset_type == ASSET_OPTION_CALL:
        # The ONLY branch that opens exposure, and it opens it small.
        return OptionTranslation.buy(
            policy.options_haircut,
            REASON_CALL_TO_UNDERLYING,
            f"long calls on {symbol} read as bullish; copied as a long position in the "
            f"underlying at {policy.options_haircut} of an equivalent equity copy",
        )

    if d.tx_type == TX_PURCHASE and d.asset_type == ASSET_OPTION_PUT:
        # Bearish. A short is not on the menu — ``risk.engine``'s sell-backed check bounds
        # every sell by a reconciled holding — so the strongest legal expression is to get
        # out of what we hold. If we hold nothing the engine drops it; it does NOT invent a
        # short, and there is no code path here that could.
        return OptionTranslation.exit(
            REASON_PUT_BEARISH_EXIT,
            f"long puts on {symbol} read as bearish; shorting is forbidden, so this can only "
            f"be expressed as an exit of an existing position",
        )

    if d.tx_type in _CLOSE_TX_TYPES:
        # Sale or exchange of a contract, call or put. Genuinely ambiguous, resolved toward
        # less exposure (see REASON_CLOSE_AMBIGUOUS_EXIT).
        return OptionTranslation.exit(
            REASON_CLOSE_AMBIGUOUS_EXIT,
            f"{d.tx_type} of {d.asset_type} on {symbol} is ambiguous (close / roll / "
            f"assignment); treated as an exit signal, actioned only if the underlying is held",
        )

    # Unreachable given the checks above (purchase and every close code are handled, and
    # anything else was refused as an unknown tx_type). Kept because "unreachable" plus a
    # future edit is how silent fall-through happens, and a drop is the fail-closed landing.
    return OptionTranslation.drop(
        REASON_UNKNOWN_TX_TYPE,
        f"no translation rule for tx_type={d.tx_type!r} asset_type={d.asset_type!r}",
    )


__all__ = [
    "ACTION_BUY_UNDERLYING",
    "ACTION_DROP",
    "ACTION_EXIT_UNDERLYING",
    "OPTION_ACTIONS",
    "OPTION_ACT_REASONS",
    "OPTION_DROP_REASONS",
    "OPTION_REASONS",
    "OptionTranslation",
    "REASON_CALL_TO_UNDERLYING",
    "REASON_CLOSE_AMBIGUOUS_EXIT",
    "REASON_EXPIRED",
    "REASON_NEAR_EXPIRY",
    "REASON_NOT_AN_OPTION",
    "REASON_NO_UNDERLYING_SYMBOL",
    "REASON_POLICY_IGNORE",
    "REASON_PUT_BEARISH_EXIT",
    "REASON_UNKNOWN_TX_TYPE",
    "translate_option",
]
