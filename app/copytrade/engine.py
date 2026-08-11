"""RW-16 — the copy engine: disclosures in, a deterministic ``CopyPlan`` out.

PURE FUNCTION. ``plan()`` reads no clock (``ctx.today``), touches no disk, opens no socket,
calls no model, and uses no randomness or iteration-order-dependent structure. Two runs over
the same inputs produce byte-identical output, in the same order, forever. That is not a
stylistic preference: this is the module that decides what a human is asked to approve, and a
planner whose output depends on the order a database happened to return rows in cannot be
replayed, audited, or argued with.

NO MODEL IS INVOLVED, ON PURPOSE. The whole thesis of copy-trading is that the SIGNAL already
exists — a person with an information advantage already made the decision, and it is on a
public form. Adding an LLM here would add a second, unverifiable opinion to a signal we chose
precisely because it needed no opinion, on the order path, in violation of the standing rule
that execution stays model-free. What this engine does instead is arithmetic and gating.

WHAT COMES OUT IS NOT AN ORDER. A ``CopyIntent`` is a proposal-shaped fact. It still has to
survive ``risk.engine``, the mode ladder, and a human (Invariant 1) before a broker sees
anything. ``workflows/copytrade_orders.py`` is the only module that carries an intent toward
that path, and it stops at an ``Order`` row in state ``proposed``.

THE GATE ORDER IS LOAD-BEARING
-------------------------------
Gates run in the order below, and the order is chosen so that the reason an operator reads
back is the MOST FUNDAMENTAL one that applies, and so that the cheapest and most absolute
gates cannot be reached around by a later one:

  1. already_copied ...... idempotency, and it outranks everything. A disclosure we have
                           already acted on must be a no-op on re-run even if the policy has
                           changed underneath it — otherwise a policy edit silently re-trades
                           history.
  2. duplicate key ....... the same ``Disclosure.key`` twice in one batch is one disclosure.
  3. follow list ......... the allowlist. An empty follow list follows NOBODY (see
                           ``policy.CopyPolicy.follows``), so a config oversight trades
                           nothing rather than several hundred symbols.
  4. unknown tx_type ..... never guessed.
  5. asset type / symbol . ``other`` (bonds, funds, real estate, crypto) is never traded, and
                           an unmappable ticker is nothing to trade.
  6. min_amount_low ...... the disclosed bucket floor.
  7. staleness ........... age measured from the TRADE (``Disclosure.age_days``). Also
                           refuses a tx_date in the FUTURE, which is a real filing typo and
                           would otherwise get the freshest possible sizing.
  8. options translation . ``copytrade.options.translate_option`` — the operator's "do not
                           copy options one for one" rule, in its own module.
  9. side resolution ..... buy / exit, with sells gated on ``mirror_sells`` AND on actually
                           holding the thing.
 10. sizing + clamp ...... ``copytrade.sizing``, off YOUR portfolio, never off their bucket.
                           An options-derived buy is discounted in the RAW SIZE AND IN THE
                           CEILING, so the haircut cannot be erased by the clamp.
 11. cross-intent passes . one intent per (symbol, side); a symbol proposed on BOTH sides in
                           one plan is dropped entirely; and a symbol CONTESTED anywhere in the
                           batch never produces a buy, however the opposing disclosure fared.

EVERY NON-EMITTING OUTCOME IS COUNTED AND NAMED. ``ALL_SKIP_REASONS`` is the closed
vocabulary; ``CopyPlan.summary()`` histograms it. "No trades today" has to be a positive
statement — a copy engine that silently drops most of its input is indistinguishable from one
that is broken.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from copytrade import sizing
from copytrade.models import (
    ASSET_EQUITY,
    ASSET_OPTION_CALL,
    ASSET_OPTION_PUT,
    KNOWN_TX_TYPES,
    TX_EXCHANGE,
    TX_PURCHASE,
    TX_SALE,
    TX_SALE_PARTIAL,
    CopyIntent,
    CopyPlan,
    Disclosure,
    Skip,
)
from copytrade.options import (
    ACTION_BUY_UNDERLYING,
    ACTION_EXIT_UNDERLYING,
    OPTION_DROP_REASONS,
    translate_option,
)
from copytrade.policy import OPTIONS_IGNORE, CopyPolicy

SIDE_BUY = "buy"
SIDE_SELL = "sell"

# Asset types this engine will consider at all. ``ASSET_OTHER`` — and anything a future parser
# invents — is refused rather than defaulted into equity handling.
_TRADEABLE_ASSET_TYPES = frozenset({ASSET_EQUITY, ASSET_OPTION_CALL, ASSET_OPTION_PUT})

# ---- the closed skip vocabulary ------------------------------------------------------------
SKIP_ALREADY_COPIED = "already_copied"
SKIP_DUPLICATE_KEY = "duplicate_disclosure_key"
SKIP_NOT_FOLLOWED = "filer_not_followed"
SKIP_UNKNOWN_TX_TYPE = "unknown_tx_type"
SKIP_UNSUPPORTED_ASSET = "unsupported_asset_type"
SKIP_NO_SYMBOL = "no_symbol"
SKIP_AMOUNT_BELOW_MIN = "amount_below_min"
SKIP_FUTURE_TX_DATE = "future_tx_date"
SKIP_STALE = "stale_disclosure"
SKIP_EXCHANGE_AMBIGUOUS = "exchange_ambiguous"
SKIP_SELLS_NOT_MIRRORED = "sells_not_mirrored"
SKIP_NOT_HELD = "not_held"
SKIP_OPTION_EXIT_NOT_HELD = "option_exit_not_held"
SKIP_HOLDING_NOT_VALUED = "holding_not_valued"
SKIP_NO_PORTFOLIO_VALUE = "no_portfolio_value"
SKIP_BELOW_MIN_NOTIONAL = "below_min_notional"
SKIP_ZERO_SIZE = "zero_size"
SKIP_DUPLICATE_SYMBOL_SIDE = "duplicate_symbol_side"
SKIP_CONFLICTING_SIDES = "conflicting_sides"
SKIP_CONTESTED_SYMBOL = "contested_symbol"

ALL_SKIP_REASONS = frozenset({
    SKIP_ALREADY_COPIED,
    SKIP_DUPLICATE_KEY,
    SKIP_NOT_FOLLOWED,
    SKIP_UNKNOWN_TX_TYPE,
    SKIP_UNSUPPORTED_ASSET,
    SKIP_NO_SYMBOL,
    SKIP_AMOUNT_BELOW_MIN,
    SKIP_FUTURE_TX_DATE,
    SKIP_STALE,
    SKIP_EXCHANGE_AMBIGUOUS,
    SKIP_SELLS_NOT_MIRRORED,
    SKIP_NOT_HELD,
    SKIP_OPTION_EXIT_NOT_HELD,
    SKIP_HOLDING_NOT_VALUED,
    SKIP_NO_PORTFOLIO_VALUE,
    SKIP_BELOW_MIN_NOTIONAL,
    SKIP_ZERO_SIZE,
    SKIP_DUPLICATE_SYMBOL_SIDE,
    SKIP_CONFLICTING_SIDES,
    SKIP_CONTESTED_SYMBOL,
}) | OPTION_DROP_REASONS
"""Every reason this engine can decline to act, including the ones ``options.py`` owns. The
operator surface aggregates on these strings, so they are a contract: rename one and a
dashboard silently loses a category. Adding one is fine; changing one is a breaking change."""

# Sizing notes are short machine codes recorded on the emitted intent, NOT prose.
NOTE_OPTIONS_HAIRCUT = "options_haircut"
"""Recorded as ``options_haircut=<multiplier>`` on every options-derived buy, so the discount
the operator's requirement demands is legible on the intent a human approves rather than
inferred from the fact that ``translated_from`` is set."""
NOTE_OPTION_EXPIRY_UNDISCLOSED = "option_expiry_undisclosed"
NOTE_FULL_EXIT_ON_PARTIAL_SALE = "full_exit_on_partial_sale"
NOTE_EXIT_UNCAPPED = "exit_not_capped_by_max_notional"

_HUMAN_VERBS = {
    TX_PURCHASE: "purchased",
    TX_SALE: "sold",
    TX_SALE_PARTIAL: "partially sold",
    TX_EXCHANGE: "exchanged",
}
_HUMAN_INSTRUMENT = {
    ASSET_EQUITY: "",
    ASSET_OPTION_CALL: " calls",
    ASSET_OPTION_PUT: " puts",
}


@dataclass(frozen=True)
class PlanContext:
    """Everything about THIS account that the plan depends on, passed in rather than fetched.

    ``today`` is a value, not a clock. That is what makes ``plan()`` replayable: a batch of
    disclosures from six weeks ago can be re-planned against the date they arrived and produce
    the decisions that were actually made, which is the difference between an audit trail and
    a story about one.

    ``portfolio_value`` is the caller's reconciled total (``sizing.portfolio_value`` builds it
    from cash + holdings). It is passed in rather than derived here so that the engine cannot
    disagree with the number the risk engine will use.

    ``already_copied`` holds ``Disclosure.key`` values that have been acted on in ANY previous
    cycle. It is the idempotency spine: the poller is at-least-once by design (a re-filed
    amendment re-enters as new evidence), so without this a restart would re-propose
    everything in the window.
    """

    today: date
    portfolio_value: Decimal
    holdings: dict                       # symbol -> {"market_value": Decimal, ...}
    already_copied: frozenset[str] = field(default_factory=frozenset)

    def holding_value(self, symbol: str) -> Decimal | None:
        """Reconciled market value of ``symbol``; ``None`` when the symbol is not held.

        ``None`` and a bad value are DIFFERENT outcomes and get different skip reasons: "we
        do not own it" is a normal, expected result of mirroring someone else's sale, while
        "we own it and the broker snapshot did not say what it is worth" is a reconciliation
        problem the operator needs to see rather than have folded into the first bucket.

        Case-tolerant on lookup (exact, then upper, then lower) because holdings come from a
        broker snapshot and tickers come from a filing parser, and a case mismatch between the
        two would silently read as "not held" — which fails OPEN for a sale we should have
        mirrored. Bounded to three exact lookups, so it stays O(1) and order-independent.
        """
        book = self.holdings or {}
        if not isinstance(book, dict) or not symbol:
            return None
        row = book.get(symbol)
        if row is None:
            row = book.get(symbol.upper(), book.get(symbol.lower()))
        if row is None:
            return None
        raw = row.get("market_value") if isinstance(row, dict) else row
        return _dec(raw)


def plan(disclosures: Iterable[Disclosure], policy: CopyPolicy, ctx: PlanContext) -> CopyPlan:
    """Turn disclosures into a deterministic, fully-accounted ``CopyPlan``.

    PURE. No clock, no I/O, no randomness, no model. See the module docstring for the gate
    order and why it is that order.

    DETERMINISM. The input is sorted by ``(tx_date, symbol, key)`` BEFORE anything is decided,
    not merely on the way out. Sorting only the output would leave the cross-intent passes
    below (which are order-sensitive: the first intent for a pair wins) depending on the order
    the caller happened to iterate a database in.

    ONE INTENT PER (SYMBOL, SIDE) — FIRST WINS, THE DUPLICATE IS SKIPPED AND COUNTED.
    Two followed members buying the same ticker in one window is corroboration, not a licence
    to take the position twice. The alternative — MERGING the two into one larger intent — was
    rejected for three reasons: (a) the summed size is a number no sizing rule authorized, and
    re-clamping it would quietly make ``max_notional_per_trade`` the size of every corroborated
    signal; (b) a ``CopyIntent`` carries the provenance of exactly ONE filing
    (``source_key`` / ``source_refs``), and a merged intent would have to misattribute itself;
    (c) "first in a stable total order" is a tie-break a reviewer can reproduce by hand. The
    winner is the EARLIEST trade date, which — all else equal — carries the most staleness
    decay and is therefore the SMALLEST of the candidates, so the tie-break leans toward less
    exposure rather than more. The loser is not lost: it is a ``Skip`` naming the key that won.

    A SYMBOL CONTESTED IN THE BATCH NEVER PRODUCES A BUY (``conflicting_sides`` when both
    sides reached an intent, ``contested_symbol`` when only one did). One followed member
    bought it and another sold it inside the same window; that is not a plan, it is a
    disagreement, and asking a human to approve a buy of a name another followed member was
    exiting is the failure this pass exists to prevent. The EXIT is not suppressed — see
    ``_drop_conflicting_sides`` for why the asymmetry is the whole point and why making the
    exit depend on which intents happened to survive put the calendar back into the answer.

    AND THE DISAGREEMENT IS READ OFF THE EVIDENCE, NOT OFF THE SURVIVING INTENTS
    (``contested_symbol``). Two disclosures that disagree do not stop disagreeing because one
    of them aged past ``max_age_days``, or was copied in an earlier cycle, or sized under the
    floor. Resolving the conflict on intents alone let it evaporate on a date change and emit a
    one-sided BUY of a symbol a followed member had exited. ``_contested_sides`` therefore
    computes the contest from the whole batch, ahead of every time-varying gate, and the buy
    side of a contested symbol is suppressed for as long as the opposing filing is in the
    window. See ``_drop_conflicting_sides`` for why only the buy side.
    """
    already = ctx.already_copied or frozenset()
    portfolio = _positive(ctx.portfolio_value)

    intents: list[CopyIntent] = []
    skips: list[Skip] = []
    seen_keys: set[str] = set()

    batch = sorted(disclosures, key=_sort_key)
    contested = _contested_sides(batch, policy)

    for d in batch:
        key = d.key
        symbol = (d.symbol or "").strip().upper()

        def skip(reason: str, detail: str = "") -> None:
            skips.append(Skip(key=key, symbol=symbol or None, reason=reason, detail=detail))

        # 1 ---- idempotency ---------------------------------------------------------------
        if key in already:
            skip(SKIP_ALREADY_COPIED, "acted on in an earlier cycle")
            continue

        # 2 ---- the same line twice in one batch ------------------------------------------
        if key in seen_keys:
            skip(SKIP_DUPLICATE_KEY, "same chamber:doc_id:line_no already seen in this batch")
            continue
        seen_keys.add(key)

        # 3 ---- the allowlist -------------------------------------------------------------
        if not policy.follows(d.filer_slug):
            skip(SKIP_NOT_FOLLOWED, f"filer_slug={d.filer_slug!r} is not on the follow list")
            continue

        # 4 ---- transaction code ----------------------------------------------------------
        if d.tx_type not in KNOWN_TX_TYPES:
            skip(SKIP_UNKNOWN_TX_TYPE, f"tx_type={d.tx_type!r} is not a known code")
            continue

        # 5 ---- instrument ----------------------------------------------------------------
        if d.asset_type not in _TRADEABLE_ASSET_TYPES:
            skip(SKIP_UNSUPPORTED_ASSET, f"asset_type={d.asset_type!r} is never traded here")
            continue
        if not symbol:
            skip(SKIP_NO_SYMBOL, "no ticker was mappable from the filing")
            continue

        # 6 ---- the disclosed bucket floor -------------------------------------------------
        if d.amount.low < policy.min_amount_low:
            skip(
                SKIP_AMOUNT_BELOW_MIN,
                f"disclosed low bound {d.amount.low} is under the {policy.min_amount_low} floor",
            )
            continue

        # 7 ---- staleness, measured from the TRADE ------------------------------------------
        age = d.age_days(ctx.today)
        if age < 0:
            # A filing whose trade date is in the future is a typo or a parse error. It fails
            # CLOSED here because the sizing curve rewards freshness, so a future date is the
            # single most dangerous value this field can hold.
            skip(SKIP_FUTURE_TX_DATE, f"tx_date {d.tx_date.isoformat()} is after {ctx.today}")
            continue
        if age > policy.max_age_days:
            skip(SKIP_STALE, f"{age}d since the trade, policy allows {policy.max_age_days}d")
            continue

        # 8 ---- options: translate, never copy one for one ---------------------------------
        notes: list[str] = []
        haircut = Decimal("1")
        translated_from: str | None = None
        if d.is_option:
            t = translate_option(d, policy, today=ctx.today)
            if not t.emits:
                skip(t.reason, t.detail)
                continue
            action = t.action
            haircut = t.haircut
            translated_from = d.asset_type
            notes.append(t.reason)
            if d.option_expiry is None:
                # Admitted without the expiry-window check because the filing never disclosed
                # one. Recorded so the operator can see which intents skipped that gate.
                notes.append(NOTE_OPTION_EXPIRY_UNDISCLOSED)
        else:
            action = _equity_action(d.tx_type)
            if action is None:
                skip(
                    SKIP_EXCHANGE_AMBIGUOUS,
                    "an exchange discloses neither a direction nor a counter-leg",
                )
                continue

        # 9/10 ---- side resolution and sizing ----------------------------------------------
        if action == ACTION_BUY_UNDERLYING:
            intent = _size_buy(d, policy, symbol=symbol, age=age, portfolio=portfolio,
                               haircut=haircut, translated_from=translated_from, notes=notes,
                               skip=skip)
        else:
            intent = _size_exit(d, policy, ctx, symbol=symbol, age=age,
                                translated_from=translated_from, notes=notes, skip=skip)
        if intent is not None:
            intents.append(intent)

    intents, skips = _dedupe_symbol_side(intents, skips)
    intents, skips = _drop_conflicting_sides(intents, skips, contested)

    intents.sort(key=lambda i: (i.tx_date, i.symbol, i.source_key))
    skips.sort(key=lambda s: (s.key, s.reason, s.symbol or ""))
    return CopyPlan(intents=tuple(intents), skips=tuple(skips))


# ---------------------------------------------------------------------------------------------
# sizing paths
# ---------------------------------------------------------------------------------------------
def _size_buy(d, policy, *, symbol, age, portfolio, haircut, translated_from, notes,
              skip) -> CopyIntent | None:
    """Size a copied BUY off the reader's own portfolio. Returns None (having recorded a
    named skip) when the policy's own floor refuses the size.

    The disclosed bucket never becomes a dollar amount here — it becomes a conviction weight
    inside ``sizing.intent_notional``, and the dollars come from ``portfolio``. Copying the
    bucket would import a stranger's risk budget into this account.
    """
    if portfolio <= 0:
        skip(SKIP_NO_PORTFOLIO_VALUE, "portfolio value is zero, unknown or unreconciled")
        return None

    # ONE call sizes AND clamps. Splitting the two here is what let the options haircut be
    # erased: the discount was applied to the raw size and the ceiling was not, so above a
    # certain portfolio value the discounted size and the full-copy size both clamped to
    # ``max_notional_per_trade`` and came out identical. ``sizing.sized_copy_notional``
    # discounts the ceiling too and guarantees the strict inequality.
    size, note = sizing.sized_copy_notional(
        amount=d.amount,
        age_days=age,
        portfolio=portfolio,
        copy_fraction=policy.copy_fraction,
        max_age_days=policy.max_age_days,
        staleness_decay=policy.staleness_decay,
        minimum=policy.min_notional_per_trade,
        maximum=policy.max_notional_per_trade,
        haircut=haircut,
    )
    if size <= 0:
        # ``sized_copy_notional`` already named it: below_min_notional or zero_size. Both are in
        # ALL_SKIP_REASONS; the fallback exists so a future clamp note cannot come through
        # unnamed.
        reason = note if note in ALL_SKIP_REASONS else SKIP_ZERO_SIZE
        skip(
            reason,
            f"sized to {size} at haircut {haircut}; the floor is "
            f"{policy.min_notional_per_trade} and the ceiling is "
            f"{sizing.haircut_ceiling(policy.max_notional_per_trade, haircut=haircut)}",
        )
        return None
    if haircut < Decimal("1"):
        # The haircut is VISIBLE on the intent, not merely implied by ``translated_from``. An
        # operator approving this order can read the multiplier that produced it, and a future
        # regression that erased the discount would also erase this note.
        notes.append(f"{NOTE_OPTIONS_HAIRCUT}={haircut}")
    if note:
        notes.append(note)

    return CopyIntent(
        symbol=symbol,
        side=SIDE_BUY,
        notional=size,
        reason=_reason_text(d, symbol, SIDE_BUY, age, translated_from),
        source_key=d.key,
        source_refs=_refs(d),
        filer_display=d.filer_display,
        tx_date=d.tx_date,
        filed_date=d.filed_date,
        age_days=age,
        amount_label=d.amount.label,
        translated_from=translated_from,
        sizing_notes=tuple(notes),
    )


def _size_exit(d, policy, ctx, *, symbol, age, translated_from, notes, skip) -> CopyIntent | None:
    """Size a mirrored EXIT against what this account actually holds.

    Three gates, three different reasons, because they mean three different things:
      * ``mirror_sells`` off      -> the operator chose buys-only (a portfolio that never
                                     sells is a real, deliberate, and bad configuration, so it
                                     is worth counting).
      * not held                  -> normal. Mirroring a stranger's sale of something we never
                                     bought is a no-op, not an error. Option-derived exits get
                                     their OWN reason (``option_exit_not_held``) because the
                                     count answers a question the operator specifically has:
                                     how much bearish congressional signal is structurally
                                     unusable to an account that cannot short.
      * held but unvalued         -> a reconciliation problem, surfaced rather than swallowed.

    THE PER-TRADE CEILING IS DELIBERATELY NOT APPLIED TO EXITS. ``max_notional_per_trade``
    exists to bound how much NEW exposure a misconfiguration can take on; capping an exit
    instead bounds how much exposure we are allowed to SHED, which is the opposite of a
    safety property — it would leave a residual position we had already decided to be out of.
    The floor still applies (a $3 exit is all cost and still burns a human approval). If an
    exit exceeds the operator's risk-engine caps it will be refused there, visibly, as a
    rejected proposal — the interaction fails loud, not silent. The choice is recorded on
    every exit intent as ``exit_not_capped_by_max_notional`` so it is never a surprise.
    """
    if not policy.mirror_sells:
        skip(SKIP_SELLS_NOT_MIRRORED, "policy.mirror_sells is off")
        return None

    mv = ctx.holding_value(symbol)
    if mv is None:
        if translated_from:
            skip(
                SKIP_OPTION_EXIT_NOT_HELD,
                f"bearish/ambiguous {translated_from} signal on {symbol}, but nothing is held "
                f"to exit and shorting is forbidden",
            )
        else:
            skip(SKIP_NOT_HELD, f"{symbol} is not in the reconciled holdings")
        return None
    if not mv.is_finite() or mv <= 0:
        skip(SKIP_HOLDING_NOT_VALUED, f"{symbol} is held but its market value reads {mv}")
        return None

    size = sizing.exit_notional(mv)
    if size <= 0:
        skip(SKIP_HOLDING_NOT_VALUED, f"{symbol} sized to {size} against a {mv} holding")
        return None
    if size < policy.min_notional_per_trade:
        skip(
            SKIP_BELOW_MIN_NOTIONAL,
            f"exit of {size} is under the {policy.min_notional_per_trade} floor",
        )
        return None

    notes.append(NOTE_EXIT_UNCAPPED)
    if d.tx_type == TX_SALE_PARTIAL:
        # A partial sale exits the WHOLE position here. There is no policy knob for a partial
        # exit fraction and inventing a constant would be an un-reviewable number on the order
        # path. Between the two available wrong answers, the one that reduces exposure to a
        # position whose discloser is trimming it cannot lose money the account does not
        # already have at risk. Recorded so the operator sees the choice on every such intent.
        notes.append(NOTE_FULL_EXIT_ON_PARTIAL_SALE)

    return CopyIntent(
        symbol=symbol,
        side=SIDE_SELL,
        notional=size,
        reason=_reason_text(d, symbol, SIDE_SELL, age, translated_from),
        source_key=d.key,
        source_refs=_refs(d),
        filer_display=d.filer_display,
        tx_date=d.tx_date,
        filed_date=d.filed_date,
        age_days=age,
        amount_label=d.amount.label,
        translated_from=translated_from,
        sizing_notes=tuple(notes),
    )


# ---------------------------------------------------------------------------------------------
# cross-intent passes
# ---------------------------------------------------------------------------------------------
def _dedupe_symbol_side(intents: list[CopyIntent],
                        skips: list[Skip]) -> tuple[list[CopyIntent], list[Skip]]:
    """One intent per ``(symbol, side)``; the first in the deterministic order wins.

    See ``plan``'s docstring for why first-wins and not merge. The loser becomes a Skip that
    names the winning key, so corroboration is visible in the plan rather than deleted from it.
    """
    kept: list[CopyIntent] = []
    winner: dict[tuple[str, str], str] = {}
    for it in intents:
        pair = (it.symbol, it.side)
        if pair in winner:
            skips.append(Skip(
                key=it.source_key,
                symbol=it.symbol,
                reason=SKIP_DUPLICATE_SYMBOL_SIDE,
                detail=f"{it.side} {it.symbol} already proposed by {winner[pair]}",
            ))
            continue
        winner[pair] = it.source_key
        kept.append(it)
    return kept, skips


def _contested_sides(batch: list[Disclosure],
                     policy: CopyPolicy) -> dict[str, dict[str, str]]:
    """Which symbols a followed member has disclosed in BOTH directions, over the whole batch.

    ``{symbol: {side: earliest_disclosure_key}}``, built from the already-sorted batch, first
    key wins, so it is deterministic.

    WHY THIS IS A PRE-PASS OVER DISCLOSURES AND NOT A PASS OVER INTENTS
    -------------------------------------------------------------------
    Because a pass over intents is not STABLE OVER TIME, and the instability resolves in the
    dangerous direction. Member A discloses a SALE of AAPL and member B a PURCHASE. While both
    disclosures still produce intents, ``_drop_conflicting_sides`` drops both and nothing
    happens. But the two age out at different moments: the day A's sale crosses
    ``max_age_days``, it stops producing an intent, the disagreement silently disappears, and
    the plan emits a one-sided BUY of the exact symbol a followed member was exiting. Nothing
    about the evidence changed — only the calendar. The same silent flip happens when the
    opposing side stops emitting for any other reason: it was already copied in an earlier
    cycle, it sized under the floor, or (for a sell) the account does not hold the symbol.

    So the contest is computed from the EVIDENCE, ahead of every gate whose outcome moves with
    time or with account state. Only the time-invariant, evidence-shaped facts are consulted:
    is the filer followed, is there a symbol, is the instrument one we trade, does the
    transaction code carry a direction, and — because it is an operator switch rather than a
    clock — is ``options_policy`` set to ignore. Staleness, idempotency, the amount floor,
    holdings and sizing are all deliberately NOT consulted: they decide whether we ACT on a
    disclosure, not whether it happened.

    The window is therefore the caller's: a disclosure contests only while it is still in the
    batch handed to ``plan``. Callers load a window at least as wide as ``max_age_days`` (see
    ``schemas.models.congress_disclosure.load_disclosures``), so an opposing filing outlives
    the staleness gate it would otherwise have escaped through.
    """
    sides: dict[str, dict[str, str]] = {}
    for d in batch:
        side = _implied_side(d, policy)
        if side is None:
            continue
        symbol = (d.symbol or "").strip().upper()
        sides.setdefault(symbol, {}).setdefault(side, d.key)
    return {s: v for s, v in sides.items() if len(v) > 1}


def _implied_side(d: Disclosure, policy: CopyPolicy) -> str | None:
    """The direction a disclosure carries, ignoring every time-varying gate. ``None`` when it
    carries no direction we could ever act on (unfollowed filer, no ticker, untraded asset
    class, unknown or directionless transaction code, options switched off).

    Options are read directionally rather than through ``translate_option``, on purpose:
    ``translate_option`` consults ``today`` (the expiry window), and a contest that evaporates
    when a contract expires is exactly the time-dependence this function exists to remove. A
    long put and any close of a contract both read as an EXIT signal, which is what they mean
    regardless of whether we can express them.
    """
    if not policy.follows(d.filer_slug):
        return None
    if not (d.symbol or "").strip():
        return None
    if d.asset_type not in _TRADEABLE_ASSET_TYPES:
        return None
    if d.tx_type not in KNOWN_TX_TYPES:
        return None
    if d.is_option:
        if policy.options_policy == OPTIONS_IGNORE:
            return None
        if d.tx_type == TX_PURCHASE:
            return SIDE_BUY if d.asset_type == ASSET_OPTION_CALL else SIDE_SELL
        return SIDE_SELL
    action = _equity_action(d.tx_type)
    if action is None:
        return None
    return SIDE_BUY if action == ACTION_BUY_UNDERLYING else SIDE_SELL


def _drop_conflicting_sides(intents: list[CopyIntent], skips: list[Skip],
                            contested: dict[str, dict[str, str]],
                            ) -> tuple[list[CopyIntent], list[Skip]]:
    """Resolve a symbol that followed members disclosed in both directions.

    ONE RULE, TWO NAMES. A contested symbol never produces a BUY, and never suppresses an EXIT.
    The two reasons say how loud the contest was and nothing more:

    ``conflicting_sides`` — both sides made it to an intent in THIS plan (the sharpest form of
    the disagreement, worth its own count).
    ``contested_symbol``  — only one side made it to an intent, but the batch also holds an
    opposing disclosure that did not (it aged out, was copied in an earlier cycle, sized under
    the floor, or named a symbol we do not hold).

    WHY THE ASYMMETRY IS THE POINT. The two sides are not symmetric in consequence. Letting a
    contested BUY through opens a NEW position in a symbol a followed member was selling —
    which is the failure this whole pass exists to prevent, and the one that used to appear on
    its own as soon as the opposing sale aged out. Suppressing a contested EXIT does the
    opposite: it keeps exposure the evidence argues against, on the strength of a purchase we
    chose not to copy. Every other ambiguity in this package resolves toward less exposure, and
    so does this one.

    AND THAT ASYMMETRY NOW COVERS THE BOTH-LIVE CASE TOO. It did not, and the exception put the
    calendar back into the answer the pass exists to take it out of. ``both_live`` is computed
    from the SURVIVING INTENTS — the exact time-varying quantity ``_contested_sides`` was
    introduced to stop consulting — and it dropped the SELL as well. Same evidence, three
    consecutive days: with a sale of AAPL at 2026-08-05 and an opposing purchase at 2026-06-27,
    the exit was suppressed on 2026-08-10 and on 2026-08-11 and then taken on 2026-08-12,
    purely because the buy had crossed ``max_age_days`` and stopped producing an intent. The
    same flip was reachable through account state (``already_copied`` on the buy). An exit that
    fires 45 days late because of a filing we did not act on is not a fail-closed outcome.

    So the EXIT is now a function of the evidence only: a contested symbol always exits if it
    can, on every date, and only the buy side is ever suppressed. Both outcomes are counted
    under their own reason, so "we did not buy it" stays a positive statement.
    """
    buys = {i.symbol for i in intents if i.side == SIDE_BUY}
    sells = {i.symbol for i in intents if i.side == SIDE_SELL}
    both_live = buys & sells

    kept: list[CopyIntent] = []
    for it in intents:
        if it.side == SIDE_BUY:
            opposing = contested.get(it.symbol, {}).get(SIDE_SELL)
            if it.symbol in both_live:
                skips.append(Skip(
                    key=it.source_key,
                    symbol=it.symbol,
                    reason=SKIP_CONFLICTING_SIDES,
                    detail=(
                        f"{it.symbol} was proposed on both sides in this plan; the buy is not "
                        f"sent and the exit is"
                    ),
                ))
                continue
            if opposing and opposing != it.source_key:
                skips.append(Skip(
                    key=it.source_key,
                    symbol=it.symbol,
                    reason=SKIP_CONTESTED_SYMBOL,
                    detail=(
                        f"a followed member disclosed an exit of {it.symbol} ({opposing}) in "
                        f"this batch; the buy stays suppressed for as long as that disclosure "
                        f"is in the window, whether or not it still produces an intent of its "
                        f"own"
                    ),
                ))
                continue
        kept.append(it)
    return kept, skips


# ---------------------------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------------------------
def _sort_key(d: Disclosure) -> tuple:
    """Total order over disclosures: ``(tx_date, symbol, key)``.

    ``key`` is last and is unique, so the order is total — no two disclosures can compare
    equal and let the input order leak into the result.
    """
    return (d.tx_date, (d.symbol or "").upper(), d.key)


def _equity_action(tx_type: str) -> str | None:
    """Map an EQUITY transaction code to an underlying action, or None when it is not
    actionable. An exchange is not a direction: the filing shows one leg of a swap and never
    the other, so there is no trade to mirror."""
    if tx_type == TX_PURCHASE:
        return ACTION_BUY_UNDERLYING
    if tx_type in (TX_SALE, TX_SALE_PARTIAL):
        return ACTION_EXIT_UNDERLYING
    return None


def _refs(d: Disclosure) -> tuple[str, ...]:
    """Raw-store locators for the citation gate (Invariant 8). Empty when the parser recorded
    none — an intent with no citation is still emitted, because refusing to act on a filing we
    demonstrably read would be theatre, but the empty tuple is what the citation check sees."""
    return (d.raw_ref,) if d.raw_ref else ()


def _reason_text(d: Disclosure, symbol: str, side: str, age: int,
                 translated_from: str | None) -> str:
    """One deterministic sentence. No model text ever reaches this field.

    ``filer_display`` is already escaped at parse time (SEC-02), so it is safe to compose into
    display text here; nothing in this string is re-parsed for meaning by anything downstream.

    ``age`` IS THE AGE OF THE TRADE, NOT THE FILING DELAY, and the sentence now says so. It
    arrives as ``Disclosure.age_days(today)`` — tx date to TODAY — but was rendered as "{age}d
    after the trade", i.e. as the disclosure LAG, which is a different and smaller number
    (filed date minus tx date). Two facts, one label. That was always wrong, and RW-17 made it
    operator-facing and self-contradictory: ``copytrade/notify.py`` prints this sentence under
    "Why this size" directly beneath a correctly-computed "Disclosure lag: N days", so the same
    message stated the filing arrived both 11 and 17 days after the trade. The queued message
    is now the ONLY interface the operator has; two numbers disagreeing inside it is not a
    typo, it is the surface losing the authority to be believed.
    """
    verb = _HUMAN_VERBS.get(d.tx_type, d.tx_type)
    instrument = _HUMAN_INSTRUMENT.get(d.asset_type, "")
    base = (
        f"{d.filer_display} {verb} {symbol}{instrument} {d.amount.label} on "
        f"{d.tx_date.isoformat()} (filed {d.filed_date.isoformat()}; the trade is {age}d old)"
        f" -> {side} {symbol}"
    )
    if translated_from:
        base += f" [translated from {translated_from}; the contract itself is never copied]"
    return base


def _dec(x) -> Decimal:
    """Coerce to Decimal, returning NaN rather than raising. NaN is a VALUE the callers test
    for (``holding_not_valued``), so a garbage broker field becomes a named skip instead of an
    exception that takes the whole cycle down."""
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _positive(x) -> Decimal:
    """A portfolio value that is not a finite positive number reads as ZERO — the fail-closed
    direction, identical to ``sizing.portfolio_value``'s. An unverifiable account value must
    shrink the size we are willing to take, never inflate it."""
    d = _dec(x)
    return d if d.is_finite() and d > 0 else Decimal("0")


__all__ = [
    "ALL_SKIP_REASONS",
    "NOTE_EXIT_UNCAPPED",
    "NOTE_FULL_EXIT_ON_PARTIAL_SALE",
    "NOTE_OPTIONS_HAIRCUT",
    "NOTE_OPTION_EXPIRY_UNDISCLOSED",
    "PlanContext",
    "SIDE_BUY",
    "SIDE_SELL",
    "SKIP_ALREADY_COPIED",
    "SKIP_AMOUNT_BELOW_MIN",
    "SKIP_BELOW_MIN_NOTIONAL",
    "SKIP_CONFLICTING_SIDES",
    "SKIP_CONTESTED_SYMBOL",
    "SKIP_DUPLICATE_KEY",
    "SKIP_DUPLICATE_SYMBOL_SIDE",
    "SKIP_EXCHANGE_AMBIGUOUS",
    "SKIP_FUTURE_TX_DATE",
    "SKIP_HOLDING_NOT_VALUED",
    "SKIP_NOT_FOLLOWED",
    "SKIP_NOT_HELD",
    "SKIP_NO_PORTFOLIO_VALUE",
    "SKIP_NO_SYMBOL",
    "SKIP_OPTION_EXIT_NOT_HELD",
    "SKIP_SELLS_NOT_MIRRORED",
    "SKIP_STALE",
    "SKIP_UNKNOWN_TX_TYPE",
    "SKIP_UNSUPPORTED_ASSET",
    "SKIP_ZERO_SIZE",
    "plan",
]
