"""RW-16 — the PURE copy-trade core: policy, sizing, options translation, ``plan()``.

No network, no clock, no disk, no model. Every date is a value passed in, so each assertion
below is reproducible by hand from the disclosure fixtures at the top of the file.

WHAT THESE TESTS ARE ACTUALLY DEFENDING
---------------------------------------
The copy engine is the one place in this system where a signal originating OUTSIDE the
operator's head turns into something a human is asked to approve. Every property pinned here
is a safety property first and a correctness property second:

  * the follow list is an ALLOWLIST that fails closed on an empty config (a config oversight
    must trade nothing, not several hundred symbols);
  * size comes from YOUR portfolio, never from the disclosed bucket (that bucket describes a
    stranger's balance sheet);
  * a stale signal is refused, and an in-window-but-old one is sized SMALLER, never equal;
  * an options filing is TRANSLATED, never copied one-for-one — and a bearish options signal
    can only ever become an exit of something already held, never an invented short;
  * ``plan()`` is a pure function: same inputs, byte-identical output, in the same order,
    regardless of the order the caller iterated its input in.

Where a documented design choice looks like a missing cap (exits are deliberately NOT bounded
by ``max_notional_per_trade``), the test PINS the carve-out and asserts the compensating
bound instead of quietly widening the assertion — see ``test_exit_is_deliberately_uncapped``.
"""
from __future__ import annotations

import ast
import pathlib
import random
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from copytrade import options as opt
from copytrade import sizing
from copytrade.engine import (
    ALL_SKIP_REASONS,
    NOTE_EXIT_UNCAPPED,
    NOTE_FULL_EXIT_ON_PARTIAL_SALE,
    NOTE_OPTIONS_HAIRCUT,
    NOTE_OPTION_EXPIRY_UNDISCLOSED,
    SIDE_BUY,
    SIDE_SELL,
    SKIP_ALREADY_COPIED,
    SKIP_AMOUNT_BELOW_MIN,
    SKIP_BELOW_MIN_NOTIONAL,
    SKIP_CONFLICTING_SIDES,
    SKIP_CONTESTED_SYMBOL,
    SKIP_DUPLICATE_KEY,
    SKIP_DUPLICATE_SYMBOL_SIDE,
    SKIP_EXCHANGE_AMBIGUOUS,
    SKIP_FUTURE_TX_DATE,
    SKIP_NOT_FOLLOWED,
    SKIP_NOT_HELD,
    SKIP_NO_PORTFOLIO_VALUE,
    SKIP_OPTION_EXIT_NOT_HELD,
    SKIP_SELLS_NOT_MIRRORED,
    SKIP_STALE,
    SKIP_UNSUPPORTED_ASSET,
    PlanContext,
    plan,
)
from copytrade.models import (
    ASSET_EQUITY,
    ASSET_OPTION_CALL,
    ASSET_OPTION_PUT,
    ASSET_OTHER,
    TX_EXCHANGE,
    TX_PURCHASE,
    TX_SALE,
    TX_SALE_PARTIAL,
    AmountRange,
    Disclosure,
)
from copytrade.policy import (
    MAX_FOLLOW_ENTRIES,
    OPTIONS_IGNORE,
    OPTIONS_UNDERLYING,
    CopyPolicy,
    PolicyError,
    normalize_filer,
    parse_follow_list,
)

# ---------------------------------------------------------------------------------------------
# wire-shaped fixtures
#
# One realistic PTR line, and a builder that overrides exactly the field under test. Everything
# is anchored to TODAY so ages are readable ("this is the 46-day-old one") rather than arithmetic
# a reader has to redo.
# ---------------------------------------------------------------------------------------------
TODAY = date(2026, 8, 10)
FOLLOWED = normalize_filer("Pelosi, Nancy")          # -> "pelosi-nancy"
UNFOLLOWED = normalize_filer("Tuberville, Tommy")

# $50,001 - $100,000 sits on the weight-1.00 tier, so a baseline copy is exactly
# portfolio * copy_fraction * staleness — the easiest bucket to reason about by hand.
BASELINE_BUCKET = AmountRange(Decimal("50001"), Decimal("100000"))
BOTTOM_BUCKET = AmountRange(Decimal("1001"), Decimal("15000"))
TOP_BUCKET = AmountRange(Decimal("50000001"), None)

PORTFOLIO = Decimal("10000")
"""Chosen so a baseline copy (2% of 10k = $200, decayed) lands strictly inside the policy's
[$25, $500] band — every sizing assertion below is therefore about the sizing rule and not
about a clamp accidentally doing the work."""


def _disclosure(**over) -> Disclosure:
    """One PTR transaction line. Override only what the test is about."""
    fields = dict(
        chamber="house",
        doc_id="20260810-A",
        line_no=0,
        filer_slug=FOLLOWED,
        filer_display="Pelosi, Nancy",
        tx_date=TODAY - timedelta(days=1),
        filed_date=TODAY,
        tx_type=TX_PURCHASE,
        asset_type=ASSET_EQUITY,
        amount=BASELINE_BUCKET,
        symbol="AAPL",
        asset_description="Apple Inc Common Stock",
        raw_ref="house:20260810-A#0",
        content_hash="a" * 64,
    )
    fields.update(over)
    return Disclosure(**fields)


def _aged(days: int, **over) -> Disclosure:
    """A disclosure whose TRADE is ``days`` old as of ``TODAY``."""
    tx = TODAY - timedelta(days=days)
    over.setdefault("doc_id", f"aged-{days}")
    return _disclosure(tx_date=tx, filed_date=min(TODAY, tx + timedelta(days=2)), **over)


@pytest.fixture()
def policy() -> CopyPolicy:
    return CopyPolicy(follow=frozenset({FOLLOWED}))


@pytest.fixture()
def ctx() -> PlanContext:
    """Funded account holding nothing."""
    return PlanContext(today=TODAY, portfolio_value=PORTFOLIO, holdings={})


@pytest.fixture()
def ctx_holding() -> PlanContext:
    """Funded account that actually holds AAPL, so exits are expressible."""
    return PlanContext(
        today=TODAY,
        portfolio_value=PORTFOLIO,
        holdings={"AAPL": {"market_value": Decimal("1234.567")}},
    )


def _only_skip(result):
    assert result.intents == (), f"expected no intents, got {result.intents}"
    assert len(result.skips) == 1, result.skips
    return result.skips[0]


# =============================================================================================
# normalize_filer — the identity join between a config line and a filing
# =============================================================================================
@pytest.mark.parametrize(
    "spelling",
    [
        "Pelosi, Nancy",
        "Pelosi, Nancy P.",
        "PELOSI, NANCY",
        "pelosi,nancy",
        "  Pelosi ,  Nancy  P .  ",
        "Hon. Nancy Pelosi",
        "Nancy Pelosi",
        "Mrs. Nancy Patricia Pelosi",
    ],
)
def test_normalize_filer_collides_across_spellings(spelling):
    """Honorific, suffix, middle name, case, punctuation and word order must all fold away.

    These are not hypothetical: the House clerk and the Senate eFD spell the same person
    differently, and a follow list written by a human spells them a third way. If they do not
    collide, the operator's follow entry silently matches nothing and the feature is dead in a
    way that looks exactly like "no trades today".
    """
    assert normalize_filer(spelling) == "pelosi-nancy"


def test_normalize_filer_folds_accents_to_one_person():
    assert normalize_filer("Núñez, María") == normalize_filer("Nunez, Maria") == "nunez-maria"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Pelosi, Nancy P.", "Pelosi, N."),          # the required non-collision
        ("Pelosi, Nancy", "Pelosi, Paul"),           # spouse, different filer
        ("Pelosi, Nancy", "Peloski, Nancy"),
        ("Smith, John", "Smith, Jane"),
        ("Pelosi, Nancy", "Pelosi"),                 # surname alone is not the person
    ],
)
def test_normalize_filer_does_not_collide_across_different_people(a, b):
    """A bare initial carries too little to disambiguate, so "Pelosi, N." must NOT match
    "Pelosi, Nancy P.". Over-collapsing here would follow a person the operator never chose,
    which is the failure mode the allowlist exists to prevent."""
    assert normalize_filer(a) != normalize_filer(b)


@pytest.mark.parametrize("junk", ["", "   ", None, "Hon.", "Mr.", "!!!", "123", 42, object()])
def test_normalize_filer_fails_closed_on_unusable_input(junk):
    """An unparseable filer normalizes to "" — and "" never matches a follow entry, so the
    failure direction is "not followed" rather than "matches everyone"."""
    slug = normalize_filer(junk)
    assert slug == ""
    assert CopyPolicy(follow=frozenset({FOLLOWED, ""})).follows(slug) is False


def test_parse_follow_list_splits_on_semicolons_not_commas():
    """Comma-splitting would tear "Pelosi, Nancy" in half — the reason the separator is ';'."""
    assert parse_follow_list("Pelosi, Nancy; Hon. Tommy Tuberville") == frozenset(
        {"pelosi-nancy", "tuberville-tommy"}
    )


def test_parse_follow_list_empty_config_is_an_empty_set_not_a_wildcard():
    for raw in ("", "   ", None, ";;;"):
        assert parse_follow_list(raw) == frozenset()


def test_parse_follow_list_refuses_a_bad_entry_rather_than_dropping_it():
    with pytest.raises(PolicyError):
        parse_follow_list("Pelosi, Nancy; !!!")


def test_parse_follow_list_is_bounded():
    with pytest.raises(PolicyError):
        parse_follow_list(";".join(f"Surname{i}, First{i}" for i in range(MAX_FOLLOW_ENTRIES + 1)))


# =============================================================================================
# the allowlist — an empty follow list follows NOBODY (safety property, asserted hard)
# =============================================================================================
@pytest.mark.parametrize(
    "slug",
    ["", "pelosi-nancy", "tuberville-tommy", "anyone", "*", "all", None, "-", "a-b"],
)
def test_empty_follow_list_follows_nobody(slug):
    """There is deliberately no "follow everyone" value. An empty allowlist must return False
    for EVERY input including the wildcard-looking ones a reader might assume are special."""
    assert CopyPolicy(follow=frozenset()).follows(slug) is False


def test_empty_follow_list_emits_no_intents_from_any_disclosure(ctx_holding):
    """The plan-level version of the same property, over one of every actionable shape.

    Nothing here is exotic: these are the exact rows that DO produce intents once a filer is
    followed (see the sizing/options sections). With an empty follow list every single one must
    come back as a counted ``filer_not_followed`` skip and zero intents.
    """
    empty = CopyPolicy(follow=frozenset())
    batch = [
        _disclosure(doc_id="b1", tx_type=TX_PURCHASE, asset_type=ASSET_EQUITY),
        _disclosure(doc_id="b2", tx_type=TX_SALE, asset_type=ASSET_EQUITY),
        _disclosure(doc_id="b3", tx_type=TX_SALE_PARTIAL, asset_type=ASSET_EQUITY),
        _disclosure(doc_id="b4", asset_type=ASSET_OPTION_CALL,
                    option_expiry=TODAY + timedelta(days=120)),
        _disclosure(doc_id="b5", asset_type=ASSET_OPTION_PUT,
                    option_expiry=TODAY + timedelta(days=120)),
        _disclosure(doc_id="b6", filer_slug="", filer_display="???"),
        _disclosure(doc_id="b7", filer_slug=UNFOLLOWED, filer_display="Tuberville, Tommy"),
    ]
    result = plan(batch, empty, ctx_holding)

    assert result.intents == ()
    assert len(result.skips) == len(batch)
    assert {s.reason for s in result.skips} == {SKIP_NOT_FOLLOWED}


def test_follow_list_matches_the_whole_slug_not_a_prefix():
    """"pelosi" must not admit "pelosi-nancy", and vice versa — an allowlist that matched on a
    prefix would turn one surname into every member of a family."""
    surname_only = CopyPolicy(follow=frozenset({"pelosi"}))
    assert surname_only.follows("pelosi-nancy") is False
    full = CopyPolicy(follow=frozenset({"pelosi-nancy"}))
    assert full.follows("pelosi") is False
    assert full.follows("pelosi-nancy") is True


def test_an_unfollowed_filer_in_a_mixed_batch_is_skipped_not_traded(policy, ctx):
    followed = _disclosure(doc_id="yes", symbol="AAPL")
    stranger = _disclosure(doc_id="no", symbol="MSFT", filer_slug=UNFOLLOWED,
                           filer_display="Tuberville, Tommy")
    result = plan([followed, stranger], policy, ctx)

    assert [i.symbol for i in result.intents] == ["AAPL"]
    assert [(s.key, s.reason) for s in result.skips] == [("house:no:0", SKIP_NOT_FOLLOWED)]


# =============================================================================================
# the staleness gate
# =============================================================================================
def test_a_46_day_old_trade_is_skipped_with_a_named_reason(policy, ctx):
    """45 days is the STOCK Act deadline; a 46-day-old trade means the filing itself was late.

    The gate measures from the TRADE, not the filing — measuring from the filing would make a
    maximally-late disclosure look maximally fresh, which is exactly backwards.
    """
    skip = _only_skip(plan([_aged(46)], policy, ctx))
    assert skip.reason == SKIP_STALE
    assert skip.reason in ALL_SKIP_REASONS
    assert "46d" in skip.detail and "45" in skip.detail


def test_the_boundary_day_is_admitted_and_maximally_decayed(policy, ctx):
    """Day 45 is IN the window (the gate is ``age > max_age_days``) and is sized at exactly
    ``1 - staleness_decay`` of a same-day copy. Pinned so a future edit cannot turn a
    strict comparison into a non-strict one without a test going red."""
    at_limit = plan([_aged(45)], policy, ctx).intents[0]
    same_day = plan([_aged(0)], policy, ctx).intents[0]
    assert at_limit.age_days == 45
    assert at_limit.notional == (same_day.notional * (Decimal("1") - policy.staleness_decay))


def test_a_44_day_old_trade_is_sized_smaller_than_a_1_day_old_one(policy, ctx):
    """Staleness monotonicity, end to end. Same filer, same bucket, same portfolio — the ONLY
    difference is age, so the size difference is entirely the decay curve."""
    fresh = plan([_aged(1)], policy, ctx).intents[0]
    old = plan([_aged(44)], policy, ctx).intents[0]

    assert fresh.age_days == 1 and old.age_days == 44
    assert old.notional < fresh.notional
    # ...and neither was silently doing something else: both are inside the policy band, so no
    # clamp is responsible for the ordering.
    for i in (fresh, old):
        assert policy.min_notional_per_trade <= i.notional <= policy.max_notional_per_trade
        assert i.sizing_notes == ()


def test_staleness_factor_is_monotonic_non_increasing_and_bounded():
    """Unit-level version, across the whole window plus out-of-range inputs.

    ``staleness_factor`` clamps rather than returning a negative multiplier on unexpected
    input: a sizing helper that quietly goes negative shows up as a nonsensical order rather
    than an error.
    """
    decay = Decimal("0.5")
    values = [
        sizing.staleness_factor(age, max_age_days=45, decay=decay) for age in range(-5, 60)
    ]
    assert all(b <= a for a, b in zip(values, values[1:]))
    assert all(Decimal("1") - decay <= v <= Decimal("1") for v in values)
    assert sizing.staleness_factor(0, max_age_days=45, decay=decay) == Decimal("1")
    assert sizing.staleness_factor(45, max_age_days=45, decay=decay) == Decimal("1") - decay


def test_a_future_trade_date_fails_closed(policy, ctx):
    """A tx_date after today is a filing typo — and it is the single most dangerous value the
    field can hold, because the sizing curve rewards freshness."""
    skip = _only_skip(plan([_disclosure(tx_date=TODAY + timedelta(days=3))], policy, ctx))
    assert skip.reason == SKIP_FUTURE_TX_DATE


# =============================================================================================
# tier weight, clamps, and the "size comes from YOUR portfolio" rule
# =============================================================================================
_TIER_LOWS = [
    "0", "1000", "1001", "15000", "15001", "50001", "100001",
    "250001", "500001", "1000001", "5000001", "25000001", "50000001",
]


def test_tier_weight_is_monotonic_and_bounded_at_two():
    weights = [sizing.tier_weight(AmountRange(Decimal(lo), None)) for lo in _TIER_LOWS]
    assert all(b >= a for a, b in zip(weights, weights[1:])), weights
    assert all(Decimal("0") <= w <= Decimal("2") for w in weights)
    assert max(weights) == Decimal("2")
    # Flat-topped on purpose: an absurd bucket must not let one outlier filing dominate.
    assert sizing.tier_weight(AmountRange(Decimal("999999999999"), None)) == Decimal("2")


def test_tier_weight_below_the_lowest_tier_is_zero():
    """Below the bottom disclosure tier there is no conviction to read, so the weight is 0 and
    the caller drops it rather than trading a sub-threshold filing at a token size."""
    assert sizing.tier_weight(AmountRange(Decimal("0"), Decimal("1000"))) == Decimal("0")
    assert sizing.tier_weight(AmountRange(Decimal("1000"), Decimal("1000"))) == Decimal("0")


@pytest.mark.parametrize("portfolio", ["10000", "250000", "1000000", "50000000"])
@pytest.mark.parametrize("bucket", [BOTTOM_BUCKET, BASELINE_BUCKET, TOP_BUCKET])
def test_a_buy_never_exceeds_max_notional_per_trade(policy, portfolio, bucket):
    """The per-trade ceiling holds across every combination of portfolio size and disclosed
    tier — including the open-ended top bucket in a large account, which is precisely the
    combination a misconfigured ``copy_fraction`` would blow up."""
    ctx = PlanContext(today=TODAY, portfolio_value=Decimal(portfolio), holdings={})
    result = plan([_disclosure(amount=bucket)], policy, ctx)
    for intent in result.intents:
        assert intent.side == SIDE_BUY
        assert intent.notional <= policy.max_notional_per_trade
        if intent.notional == policy.max_notional_per_trade:
            assert "capped_at_max_notional" in intent.sizing_notes


def test_a_size_under_the_floor_is_dropped_not_rounded_up(policy):
    """$9.88 does not become $25. Rounding up would make ``min_notional_per_trade`` the sizing
    rule for every small signal, which is the opposite of what a floor is for."""
    tiny = PlanContext(today=TODAY, portfolio_value=Decimal("1000"), holdings={})
    skip = _only_skip(plan([_disclosure(amount=BOTTOM_BUCKET)], policy, tiny))
    assert skip.reason == SKIP_BELOW_MIN_NOTIONAL
    assert skip.reason in ALL_SKIP_REASONS


def test_clamp_notional_drops_below_the_floor_and_never_rounds_up():
    lo, hi = Decimal("25"), Decimal("500")
    assert sizing.clamp_notional(Decimal("24.99"), minimum=lo, maximum=hi) == (
        Decimal("0"), "below_min_notional")
    assert sizing.clamp_notional(Decimal("0"), minimum=lo, maximum=hi) == (
        Decimal("0"), "zero_size")
    assert sizing.clamp_notional(Decimal("25"), minimum=lo, maximum=hi) == (Decimal("25"), None)
    assert sizing.clamp_notional(Decimal("10000"), minimum=lo, maximum=hi) == (
        hi, "capped_at_max_notional")


def test_the_disclosed_bucket_below_the_policy_floor_is_refused_before_sizing(policy, ctx):
    high_floor = CopyPolicy(follow=frozenset({FOLLOWED}), min_amount_low=Decimal("100000"))
    skip = _only_skip(plan([_disclosure(amount=BOTTOM_BUCKET)], high_floor, ctx))
    assert skip.reason == SKIP_AMOUNT_BELOW_MIN


def test_sizing_is_driven_by_your_portfolio_not_the_disclosed_bucket(policy):
    """THE central rule of this module.

    The same disclosure — same filer, same bucket, same date — planned against two accounts
    produces sizes in proportion to those ACCOUNTS. The disclosed bucket ($50,001-$100,000)
    never appears as a dollar amount anywhere; if it did, both accounts would get the same
    number and a stranger's risk budget would have been copied into a portfolio it does not fit.
    """
    disclosure = _disclosure()
    small = plan([disclosure], policy, PlanContext(
        today=TODAY, portfolio_value=Decimal("10000"), holdings={})).intents[0]
    large = plan([disclosure], policy, PlanContext(
        today=TODAY, portfolio_value=Decimal("20000"), holdings={})).intents[0]

    assert large.notional > small.notional
    # Proportional to the portfolio, to within the cent both sides are rounded DOWN to.
    assert abs(large.notional - (small.notional * 2)) <= Decimal("0.01")
    # Neither is the disclosed bucket, or anything derived from its midpoint.
    for i in (small, large):
        assert i.notional < disclosure.amount.low
        assert i.amount_label == "$50,001 - $100,000"     # carried for display, not for sizing


def test_the_same_portfolio_sizes_a_bigger_disclosure_bigger(policy, ctx):
    """The bucket is still a signal — it just becomes a bounded conviction WEIGHT, not dollars."""
    bottom = plan([_disclosure(doc_id="lo", amount=BOTTOM_BUCKET)], policy, ctx).intents[0]
    baseline = plan([_disclosure(doc_id="mid", amount=BASELINE_BUCKET)], policy, ctx).intents[0]
    assert bottom.notional < baseline.notional


def test_an_unfunded_account_proposes_nothing(policy):
    """Zero / unknown / garbage portfolio value all read as zero — the fail-closed direction."""
    for value in (Decimal("0"), Decimal("-5"), Decimal("NaN"), None, "not-a-number"):
        broke = PlanContext(today=TODAY, portfolio_value=value, holdings={})
        skip = _only_skip(plan([_disclosure()], policy, broke))
        assert skip.reason == SKIP_NO_PORTFOLIO_VALUE


# =============================================================================================
# OPTIONS — "do not copy them one for one as we get delayed information"
# =============================================================================================
FAR_EXPIRY = TODAY + timedelta(days=120)


def _call(**over) -> Disclosure:
    over.setdefault("option_expiry", FAR_EXPIRY)
    return _disclosure(asset_type=ASSET_OPTION_CALL, tx_type=TX_PURCHASE, **over)


def _put(**over) -> Disclosure:
    over.setdefault("option_expiry", FAR_EXPIRY)
    return _disclosure(asset_type=ASSET_OPTION_PUT, tx_type=TX_PURCHASE, **over)


def test_call_purchase_buys_the_underlying_at_a_haircut(policy, ctx):
    """A long call is bullish; the only vehicle this system can hold is the underlying, and it
    holds a deliberately SMALL amount of it. A call buyer paid a premium for leverage — matching
    their notional would take several times their delta."""
    intent = plan([_call()], policy, ctx).intents[0]

    assert intent.side == SIDE_BUY
    assert intent.symbol == "AAPL"                       # the UNDERLYING, never a contract
    assert intent.translated_from == ASSET_OPTION_CALL
    assert opt.REASON_CALL_TO_UNDERLYING in intent.sizing_notes
    assert "the contract itself is never copied" in intent.reason


def test_an_option_derived_buy_is_strictly_smaller_than_the_equivalent_equity_copy(policy, ctx):
    """The haircut, asserted as an INEQUALITY against the real equity path rather than against a
    recomputed constant — so a change to either sizing path that erased the discount would fail
    here even if both sides moved together."""
    equity = plan([_disclosure(doc_id="eq")], policy, ctx).intents[0]
    option = plan([_call(doc_id="opt")], policy, ctx).intents[0]

    assert option.notional < equity.notional
    assert abs(option.notional - (equity.notional * policy.options_haircut)) <= Decimal("0.01")
    assert policy.options_haircut < Decimal("1")


def test_put_purchase_with_no_holding_is_dropped_and_never_shorted(policy, ctx):
    """Bearish signal, nothing to sell. Shorting is forbidden (the risk engine bounds every sell
    by a reconciled holding), so the signal is structurally unusable and is COUNTED as such —
    it is never inverted into an invented short position."""
    result = plan([_put()], policy, ctx)
    skip = _only_skip(result)

    assert skip.reason == SKIP_OPTION_EXIT_NOT_HELD
    assert skip.reason in ALL_SKIP_REASONS
    assert result.intents == ()                          # no short, of any size, ever


def test_put_purchase_with_a_holding_becomes_an_exit(policy, ctx_holding):
    intent = plan([_put()], policy, ctx_holding).intents[0]

    assert intent.side == SIDE_SELL
    assert intent.translated_from == ASSET_OPTION_PUT
    assert opt.REASON_PUT_BEARISH_EXIT in intent.sizing_notes
    # Bounded by what is actually held — an exit can never become a short by overshooting.
    assert intent.notional <= Decimal("1234.567")


def test_an_already_expired_contract_is_dropped(policy, ctx):
    """The literal face of the operator's requirement: by the time the PTR is readable the
    trade being described can already be over."""
    skip = _only_skip(plan([_call(option_expiry=TODAY - timedelta(days=1))], policy, ctx))
    assert skip.reason == opt.REASON_EXPIRED


@pytest.mark.parametrize("dte", [0, 1, 7, 29])
def test_a_contract_inside_the_min_days_to_expiry_window_is_dropped(policy, ctx, dte):
    """The discloser's own thesis window is closing. Translating that into a fresh multi-week
    position in the underlying would be opening NEW exposure on a signal whose author is about
    to be done with it. Note ``dte == 0`` reads as near-expiry, not expired — both drop, so
    nothing unsafe hangs on which side of the line it lands."""
    skip = _only_skip(plan([_call(option_expiry=TODAY + timedelta(days=dte))], policy, ctx))
    assert skip.reason == opt.REASON_NEAR_EXPIRY
    assert skip.reason in ALL_SKIP_REASONS


def test_the_expiry_boundary_admits_exactly_min_days_to_expiry(policy, ctx):
    """Day 30 with ``options_min_days_to_expiry=30`` is admitted (the gate is strict ``<``).
    Pinned in both directions so the boundary cannot drift silently."""
    admitted = plan(
        [_call(option_expiry=TODAY + timedelta(days=policy.options_min_days_to_expiry))],
        policy, ctx)
    assert len(admitted.intents) == 1
    refused = _only_skip(plan(
        [_call(option_expiry=TODAY + timedelta(days=policy.options_min_days_to_expiry - 1))],
        policy, ctx))
    assert refused.reason == opt.REASON_NEAR_EXPIRY


def test_an_undisclosed_expiry_is_admitted_but_flagged(policy, ctx):
    """The gate cannot be evaluated when the filing never disclosed an expiry. The translation
    is a haircut position in the UNDERLYING (not a contract), so it is admitted — and the
    intent records that it skipped the check, so the operator can see which ones did."""
    intent = plan([_call(option_expiry=None)], policy, ctx).intents[0]
    assert NOTE_OPTION_EXPIRY_UNDISCLOSED in intent.sizing_notes


@pytest.mark.parametrize("asset", [ASSET_OPTION_CALL, ASSET_OPTION_PUT])
@pytest.mark.parametrize("tx", [TX_PURCHASE, TX_SALE, TX_SALE_PARTIAL, TX_EXCHANGE])
@pytest.mark.parametrize("expiry", [None, FAR_EXPIRY, TODAY + timedelta(days=1)])
def test_options_policy_ignore_always_drops(ctx_holding, asset, tx, expiry):
    """The operator's global off-switch outranks every fact about the contract — including the
    ones that would otherwise produce an EXIT of a held position. Asserted over the full
    cross-product (both directions, every transaction code, every expiry state) against an
    account that DOES hold the underlying, because "held" is the state in which the other
    branches would have emitted something."""
    ignoring = CopyPolicy(follow=frozenset({FOLLOWED}), options_policy=OPTIONS_IGNORE)
    result = plan(
        [_disclosure(asset_type=asset, tx_type=tx, option_expiry=expiry)], ignoring, ctx_holding)

    assert result.intents == ()
    assert _only_skip(result).reason == opt.REASON_POLICY_IGNORE


def test_translate_option_is_pure_and_names_every_outcome(policy):
    """No path out of ``translate_option`` is unnamed, and calling it twice on the same inputs
    gives the same answer (it reads no clock — ``today`` is a parameter)."""
    cases = [
        _call(), _put(),
        _call(option_expiry=TODAY - timedelta(days=1)),
        _call(option_expiry=TODAY + timedelta(days=5)),
        _call(option_expiry=None),
        _disclosure(asset_type=ASSET_OPTION_PUT, tx_type=TX_SALE, option_expiry=FAR_EXPIRY),
        _disclosure(asset_type=ASSET_OPTION_CALL, tx_type=TX_EXCHANGE, option_expiry=FAR_EXPIRY),
        _disclosure(asset_type=ASSET_EQUITY),                       # defensive: not an option
        _call(symbol=None),                                         # defensive: no underlying
        _disclosure(asset_type=ASSET_OPTION_CALL, tx_type="gift", option_expiry=FAR_EXPIRY),
    ]
    for d in cases:
        first = opt.translate_option(d, policy, today=TODAY)
        second = opt.translate_option(d, policy, today=TODAY)
        assert first == second
        assert first.action in opt.OPTION_ACTIONS
        assert first.reason in opt.OPTION_REASONS
        if first.action == opt.ACTION_DROP:
            assert first.haircut == Decimal("0")
            assert first.reason in ALL_SKIP_REASONS      # the engine surfaces it verbatim
        else:
            assert Decimal("0") < first.haircut <= Decimal("1")


def test_no_options_vocabulary_can_reach_an_order(policy, ctx_holding):
    """Structural: whatever an options row does, the emitted intent describes the UNDERLYING.

    There is no constant in ``options.py`` meaning "buy the contract", and this asserts the
    consequence at the plan level — every intent an options filing can produce names a plain
    ticker and a plain buy/sell, which is all ``risk.engine`` (equity-only) can adjudicate.
    """
    batch = [
        _call(doc_id="c1"),
        _put(doc_id="p1", symbol="MSFT"),
        _disclosure(doc_id="s1", symbol="MSFT", asset_type=ASSET_OPTION_CALL,
                    tx_type=TX_SALE, option_expiry=FAR_EXPIRY),
    ]
    holds_both = PlanContext(
        today=TODAY, portfolio_value=PORTFOLIO,
        holdings={"AAPL": {"market_value": Decimal("900")},
                  "MSFT": {"market_value": Decimal("800")}},
    )
    result = plan(batch, policy, holds_both)
    assert result.intents, "the fixture must actually emit option-derived intents"
    for intent in result.intents:
        assert intent.side in (SIDE_BUY, SIDE_SELL)
        assert intent.symbol.isalpha() and intent.symbol.isupper()
        # A CopyIntent has no vocabulary for a contract at all: no strike, no expiry, no
        # multiplier. There is nothing here for a downstream adapter to turn into one.
        assert not hasattr(intent, "option_expiry")
        assert not hasattr(intent, "option_strike")
        assert intent.translated_from in (ASSET_OPTION_CALL, ASSET_OPTION_PUT)


# =============================================================================================
# exits: mirrored sales are bounded by what you actually hold — never a short
# =============================================================================================
def test_a_sale_of_a_symbol_not_held_is_skipped_not_shorted(policy, ctx):
    result = plan([_disclosure(tx_type=TX_SALE)], policy, ctx)
    skip = _only_skip(result)
    assert skip.reason == SKIP_NOT_HELD
    assert result.intents == ()


def test_a_sale_of_a_held_symbol_exits_it(policy, ctx_holding):
    intent = plan([_disclosure(tx_type=TX_SALE)], policy, ctx_holding).intents[0]
    assert intent.side == SIDE_SELL
    assert intent.notional == Decimal("1234.56")      # rounded DOWN to the cent, never up
    assert intent.notional <= Decimal("1234.567")


def test_a_partial_sale_exits_the_whole_position_and_says_so(policy, ctx_holding):
    """There is no policy knob for a partial exit fraction, and inventing a constant would put
    an un-reviewable number on the order path. The choice is recorded on the intent."""
    intent = plan([_disclosure(tx_type=TX_SALE_PARTIAL)], policy, ctx_holding).intents[0]
    assert NOTE_FULL_EXIT_ON_PARTIAL_SALE in intent.sizing_notes


def test_exit_is_deliberately_uncapped_but_still_bounded_by_the_holding(policy):
    """``max_notional_per_trade`` is NOT applied to exits — by design, and this pins it.

    The cap bounds how much NEW exposure a misconfiguration can take on. Applying it to an exit
    would instead bound how much exposure we are allowed to SHED, leaving a residue of a
    position we had already decided to be out of. That is the opposite of a safety property, so
    the test asserts the carve-out EXISTS, that it is disclosed on the intent, and that the
    compensating bound (the reconciled holding) still holds.
    """
    rich = PlanContext(today=TODAY, portfolio_value=Decimal("100000"),
                       holdings={"AAPL": {"market_value": Decimal("9000")}})
    intent = plan([_disclosure(tx_type=TX_SALE)], policy, rich).intents[0]

    assert intent.notional > policy.max_notional_per_trade      # the carve-out, made explicit
    assert intent.notional <= Decimal("9000")                   # still bounded by the holding
    assert NOTE_EXIT_UNCAPPED in intent.sizing_notes


def test_a_tiny_holding_is_not_exited_below_the_floor(policy):
    dust = PlanContext(today=TODAY, portfolio_value=PORTFOLIO,
                       holdings={"AAPL": {"market_value": Decimal("3.00")}})
    skip = _only_skip(plan([_disclosure(tx_type=TX_SALE)], policy, dust))
    assert skip.reason == SKIP_BELOW_MIN_NOTIONAL


def test_mirror_sells_off_is_a_counted_choice_not_a_silent_one(policy, ctx_holding):
    buys_only = CopyPolicy(follow=frozenset({FOLLOWED}), mirror_sells=False)
    skip = _only_skip(plan([_disclosure(tx_type=TX_SALE)], buys_only, ctx_holding))
    assert skip.reason == SKIP_SELLS_NOT_MIRRORED


def test_no_sell_intent_anywhere_exceeds_its_reconciled_holding(policy):
    """The no-short property, asserted over a matrix rather than one case.

    Every sell-producing shape (equity sale, partial sale, bearish put, ambiguous option close)
    crossed with held / not-held. Not one of them may emit a sell for a symbol the account does
    not hold, or for more than it holds.
    """
    holdings = {
        "AAPL": {"market_value": Decimal("1500")},
        "MSFT": {"market_value": Decimal("40")},
    }
    ctx = PlanContext(today=TODAY, portfolio_value=PORTFOLIO, holdings=holdings)
    shapes = [
        dict(tx_type=TX_SALE, asset_type=ASSET_EQUITY),
        dict(tx_type=TX_SALE_PARTIAL, asset_type=ASSET_EQUITY),
        dict(tx_type=TX_PURCHASE, asset_type=ASSET_OPTION_PUT, option_expiry=FAR_EXPIRY),
        dict(tx_type=TX_SALE, asset_type=ASSET_OPTION_CALL, option_expiry=FAR_EXPIRY),
        dict(tx_type=TX_EXCHANGE, asset_type=ASSET_OPTION_PUT, option_expiry=FAR_EXPIRY),
    ]
    batch = []
    for n, shape in enumerate(shapes):
        for symbol in ("AAPL", "MSFT", "NVDA"):          # NVDA is not held at all
            batch.append(_disclosure(doc_id=f"m{n}-{symbol}", symbol=symbol, **shape))

    result = plan(batch, policy, ctx)
    sells = [i for i in result.intents if i.side == SIDE_SELL]
    assert sells, "the matrix must actually produce exits, or it proves nothing"
    for intent in sells:
        assert intent.symbol in holdings, f"short proposed on unheld {intent.symbol}"
        assert intent.notional <= holdings[intent.symbol]["market_value"]
    # NVDA is held by nobody in this fixture, so it may never appear on the sell side at all.
    assert all(i.side == SIDE_BUY for i in result.intents if i.symbol == "NVDA")
    assert {s.reason for s in result.skips} >= {SKIP_NOT_HELD, SKIP_OPTION_EXIT_NOT_HELD}


def test_a_held_but_unvalued_position_is_a_reconciliation_skip_not_a_guess(policy):
    """"We do not own it" and "we own it and the broker did not say what it is worth" are
    different problems and get different reasons; folding the second into the first would hide
    a reconciliation failure inside a normal, expected outcome."""
    broken = PlanContext(today=TODAY, portfolio_value=PORTFOLIO,
                         holdings={"AAPL": {"market_value": "not-a-number"}})
    skip = _only_skip(plan([_disclosure(tx_type=TX_SALE)], policy, broken))
    assert skip.reason == "holding_not_valued"
    assert skip.reason in ALL_SKIP_REASONS


# =============================================================================================
# instrument / transaction gates
# =============================================================================================
@pytest.mark.parametrize("asset", [ASSET_OTHER, "crypto", "real_estate", ""])
def test_untradeable_asset_types_are_refused_not_defaulted_to_equity(policy, ctx, asset):
    skip = _only_skip(plan([_disclosure(asset_type=asset)], policy, ctx))
    assert skip.reason == SKIP_UNSUPPORTED_ASSET


@pytest.mark.parametrize("tx", ["gift", "gifted", "gain", "", "gift; purchase"])
def test_an_unknown_transaction_code_is_never_guessed(policy, ctx, tx):
    skip = _only_skip(plan([_disclosure(tx_type=tx)], policy, ctx))
    assert skip.reason == "unknown_tx_type"


def test_an_unmappable_ticker_is_nothing_to_trade(policy, ctx):
    for symbol in (None, "", "   "):
        skip = _only_skip(plan([_disclosure(symbol=symbol)], policy, ctx))
        assert skip.reason == "no_symbol"
        assert skip.symbol is None


def test_an_equity_exchange_discloses_no_direction(policy, ctx_holding):
    skip = _only_skip(plan([_disclosure(tx_type=TX_EXCHANGE)], policy, ctx_holding))
    assert skip.reason == SKIP_EXCHANGE_AMBIGUOUS


# =============================================================================================
# purity, determinism, idempotency
# =============================================================================================
def _mixed_batch() -> list[Disclosure]:
    """A batch that exercises most gates at once — intents AND skips, both chambers, options
    and equity, followed and unfollowed filers."""
    return [
        _disclosure(doc_id="d1", symbol="AAPL", tx_date=TODAY - timedelta(days=3)),
        _disclosure(doc_id="d2", symbol="MSFT", tx_type=TX_SALE),
        _disclosure(doc_id="d3", symbol="NVDA", chamber="senate",
                    tx_date=TODAY - timedelta(days=10)),
        _call(doc_id="d4", symbol="GOOG"),
        _put(doc_id="d5", symbol="MSFT"),
        _aged(46, symbol="TSLA"),
        _disclosure(doc_id="d7", symbol="AMZN", filer_slug=UNFOLLOWED),
        _disclosure(doc_id="d8", symbol="F", asset_type=ASSET_OTHER),
        _disclosure(doc_id="d9", symbol="AAPL", line_no=1, tx_date=TODAY - timedelta(days=2)),
    ]


@pytest.fixture()
def mixed_ctx() -> PlanContext:
    return PlanContext(
        today=TODAY,
        portfolio_value=PORTFOLIO,
        holdings={"MSFT": {"market_value": Decimal("2000")},
                  "NVDA": {"market_value": Decimal("800")}},
    )


def test_plan_is_pure_same_inputs_give_identical_output(policy, mixed_ctx):
    """Same inputs twice => identical output, INCLUDING ordering. ``CopyPlan`` is a frozen
    dataclass of tuples, so equality here is order-sensitive by construction."""
    batch = _mixed_batch()
    first = plan(batch, policy, mixed_ctx)
    second = plan(batch, policy, mixed_ctx)

    assert first == second
    assert [i.source_key for i in first.intents] == [i.source_key for i in second.intents]
    assert [(s.key, s.reason) for s in first.skips] == [(s.key, s.reason) for s in second.skips]
    assert first.summary() == second.summary()
    assert first.intents, "the determinism fixture must actually emit intents"


def test_plan_does_not_depend_on_the_order_of_its_input(policy, mixed_ctx):
    """The input is sorted by ``(tx_date, symbol, key)`` BEFORE anything is decided.

    This matters because the cross-intent passes are order-sensitive (first intent for a
    (symbol, side) pair wins), so a planner that only sorted its OUTPUT would let the order a
    database happened to return rows in decide which trade a human is asked to approve.
    """
    batch = _mixed_batch()
    reference = plan(batch, policy, mixed_ctx)

    rng = random.Random(20260810)
    for _ in range(12):
        shuffled = list(batch)
        rng.shuffle(shuffled)
        assert plan(shuffled, policy, mixed_ctx) == reference


def test_plan_does_not_mutate_its_input(policy, mixed_ctx):
    batch = _mixed_batch()
    snapshot = list(batch)
    plan(batch, policy, mixed_ctx)
    assert batch == snapshot


def test_plan_accepts_any_iterable_including_a_one_shot_generator(policy, mixed_ctx):
    batch = _mixed_batch()
    from_list = plan(batch, policy, mixed_ctx)
    from_generator = plan((d for d in batch), policy, mixed_ctx)
    assert from_generator == from_list


def test_already_copied_keys_are_not_re_emitted(policy, mixed_ctx):
    """Idempotency. The poller is at-least-once by design (an amended filing re-enters as new
    evidence), so a restart must not re-propose a key it already acted on."""
    batch = _mixed_batch()
    first = plan(batch, policy, mixed_ctx)
    assert first.intents, "the idempotency fixture must actually emit intents"

    acted = frozenset(i.source_key for i in first.intents)
    replayed = plan(batch, policy, PlanContext(
        today=mixed_ctx.today, portfolio_value=mixed_ctx.portfolio_value,
        holdings=mixed_ctx.holdings, already_copied=acted))

    assert acted.isdisjoint({i.source_key for i in replayed.intents})
    assert acted <= {s.key for s in replayed.skips}
    for key in acted:
        assert any(s.key == key and s.reason == SKIP_ALREADY_COPIED for s in replayed.skips)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "RW-16 DEFECT (reported, not patched — engine.py/workflows are owned elsewhere this "
        "phase): a duplicate_symbol_side LOSER is re-emitted on the next cycle, because only "
        "the WINNER's key enters already_copied. Delete this marker when it is fixed."
    ),
)
def test_a_corroborating_duplicate_is_not_re_taken_on_the_next_cycle(policy, ctx):
    """The cross-cycle half of "corroboration is not a licence to take the position twice".

    Cycle 1: two followed members bought AAPL inside the window. ``_dedupe_symbol_side`` takes
    the position ONCE and records the loser as a ``duplicate_symbol_side`` skip.

    Cycle 2: the same window is re-planned (``load_disclosures`` is a rolling window and the
    poller is at-least-once). The winner's key is now in ``already_copied`` — but the LOSER's
    key never was, because nothing acted on it. So the loser wins its own pass and proposes a
    SECOND buy of AAPL, which is exactly the outcome the dedupe pass exists to prevent. The
    per-cycle rule holds; the property does not survive a cycle boundary.

    ``workflows.copytrade_orders`` does not close this either: its ``already_copied`` is
    derived from EXISTING PROPOSAL IDS keyed on ``Disclosure.key``, and the loser never
    produced a proposal. The narrowest fix is to mark ``duplicate_symbol_side`` losers as
    consumed alongside the winner, so a corroborating filing stays corroboration forever.

    Human approval and the risk engine's position caps still stand between this and a broker
    (Invariant 1), so the blast radius is a duplicate approval request and, if approved, a
    doubled position — not an ungated trade.
    """
    early = _disclosure(doc_id="early", tx_date=TODAY - timedelta(days=20))
    late = _disclosure(doc_id="late", tx_date=TODAY - timedelta(days=1))

    first = plan([early, late], policy, ctx)
    assert [i.source_key for i in first.intents] == [early.key]

    second = plan([early, late], policy, PlanContext(
        today=TODAY, portfolio_value=PORTFOLIO, holdings={},
        already_copied=frozenset({early.key})))

    assert second.intents == (), (
        "AAPL was already bought on the corroborated signal; the losing duplicate must not "
        "buy it again"
    )


def test_already_copied_outranks_a_policy_change(ctx):
    """Idempotency is checked FIRST, before the allowlist and before sizing — otherwise editing
    the policy would silently re-trade history."""
    d = _disclosure()
    seen = PlanContext(today=TODAY, portfolio_value=PORTFOLIO, holdings={},
                       already_copied=frozenset({d.key}))
    for pol in (CopyPolicy(follow=frozenset({FOLLOWED})),
                CopyPolicy(follow=frozenset({FOLLOWED}), copy_fraction=Decimal("0.5")),
                CopyPolicy(follow=frozenset())):
        assert _only_skip(plan([d], pol, seen)).reason == SKIP_ALREADY_COPIED


def test_the_same_line_twice_in_one_batch_is_one_disclosure(policy, ctx):
    d = _disclosure()
    result = plan([d, d, d], policy, ctx)
    assert len(result.intents) == 1
    assert [s.reason for s in result.skips] == [SKIP_DUPLICATE_KEY, SKIP_DUPLICATE_KEY]


def test_two_members_buying_the_same_ticker_take_the_position_once(policy, ctx):
    """Corroboration is not a licence to take the position twice, and the two are not merged
    into a size no sizing rule authorized. First in the deterministic order wins — that is the
    EARLIEST trade date, which carries the most staleness decay and is therefore the SMALLEST
    of the candidates, so the tie-break leans toward less exposure."""
    early = _disclosure(doc_id="early", tx_date=TODAY - timedelta(days=20))
    late = _disclosure(doc_id="late", tx_date=TODAY - timedelta(days=1))
    solo_early = plan([early], policy, ctx).intents[0].notional
    solo_late = plan([late], policy, ctx).intents[0].notional
    assert solo_early < solo_late

    result = plan([late, early], policy, ctx)
    assert len(result.intents) == 1
    assert result.intents[0].source_key == early.key
    assert result.intents[0].notional == solo_early
    assert [(s.key, s.reason) for s in result.skips] == [
        (late.key, SKIP_DUPLICATE_SYMBOL_SIDE)]


def test_a_symbol_proposed_on_both_sides_drops_the_buy_and_keeps_the_exit(policy, ctx_holding):
    """One member bought it and another sold it inside the same window. That is a disagreement,
    and it resolves the way every other ambiguity in this package resolves: toward LESS exposure.

    This used to drop BOTH sides, which sounds fail-closed and is not. ``both_live`` is read off
    the surviving INTENTS — the time-varying quantity ``_contested_sides`` exists to stop
    consulting — so the exit's outcome flipped with the calendar the day the opposing buy aged
    out (see ``test_a_contested_exit_does_not_flip_with_the_calendar``). The buy is suppressed
    and counted under its own reason; the exit runs.
    """
    buy = _disclosure(doc_id="buy", tx_type=TX_PURCHASE)
    sell = _disclosure(doc_id="sell", tx_type=TX_SALE)
    result = plan([buy, sell], policy, ctx_holding)

    assert [(i.side, i.source_key) for i in result.intents] == [(SIDE_SELL, sell.key)]
    assert [(s.key, s.reason) for s in result.skips] == [(buy.key, SKIP_CONFLICTING_SIDES)]


def test_every_skip_reason_comes_from_the_closed_vocabulary(policy, mixed_ctx):
    """``ALL_SKIP_REASONS`` is a contract the operator surface aggregates on. A reason that
    escapes it silently loses a category on a dashboard."""
    everything = _mixed_batch() + [
        _disclosure(doc_id="x1", tx_type="gift"),
        _disclosure(doc_id="x2", symbol=None),
        _disclosure(doc_id="x3", amount=AmountRange(Decimal("0"), Decimal("1000"))),
        _disclosure(doc_id="x4", tx_date=TODAY + timedelta(days=1)),
        _disclosure(doc_id="x5", tx_type=TX_EXCHANGE),
        _call(doc_id="x6", option_expiry=TODAY - timedelta(days=2)),
        _call(doc_id="x7", option_expiry=TODAY + timedelta(days=2)),
        _put(doc_id="x8", symbol="ZZZZ"),
    ]
    result = plan(everything, policy, mixed_ctx)
    unknown = {s.reason for s in result.skips} - ALL_SKIP_REASONS
    assert unknown == set()
    assert result.summary().startswith("copytrade: ")


def test_a_plan_over_an_empty_batch_is_an_empty_plan(policy, ctx):
    result = plan([], policy, ctx)
    assert result.intents == () and result.skips == ()
    assert result.summary() == "copytrade: 0 intent(s), 0 skip(s)"


def test_the_engine_never_reads_a_clock(policy):
    """``ctx.today`` is a value, which is what makes a historical replay reproduce the decisions
    that were actually made. Planning the same batch against two different "todays" changes the
    ages, and therefore the sizes, deterministically in one direction."""
    d = _disclosure(tx_date=date(2026, 7, 1), filed_date=date(2026, 7, 3))
    early = plan([d], policy, PlanContext(
        today=date(2026, 7, 5), portfolio_value=PORTFOLIO, holdings={}))
    later = plan([d], policy, PlanContext(
        today=date(2026, 8, 1), portfolio_value=PORTFOLIO, holdings={}))

    assert early.intents[0].age_days == 4
    assert later.intents[0].age_days == 31
    assert later.intents[0].notional < early.intents[0].notional


# =============================================================================================
# static gate — the pure core stays pure
# =============================================================================================
_PURE_MODULES = ("models.py", "policy.py", "sizing.py", "options.py", "engine.py")
"""The five modules this file tests. ``universe.py`` and ``workflows/copytrade_orders.py`` are
deliberately excluded: they are the I/O + audit layer and are supposed to import the world."""

_ALLOWED_IMPORT_ROOTS = frozenset({
    "__future__", "collections", "copytrade", "dataclasses", "datetime",
    "decimal", "enum", "math", "re", "typing", "unicodedata",
})

_CLOCK_OR_RANDOM = re.compile(
    r"\b(?:datetime\.now|date\.today|datetime\.utcnow|time\.time|time\.monotonic|"
    r"random\.\w+|uuid[0-9]?\()"
)


def _pure_core_sources():
    import copytrade

    root = pathlib.Path(copytrade.__file__).resolve().parent
    return [(name, (root / name).read_text(encoding="utf-8")) for name in _PURE_MODULES]


def test_the_pure_core_imports_no_model_no_io_and_no_clock():
    """Static gate, in the spirit of ``tests/test_invariants.py``'s call-site greps.

    "Execution stays model-free" is a standing rule, and the whole argument for copy-trading is
    that the SIGNAL already exists — a person with an information advantage already decided, on
    a public form. An LLM here would add a second, unverifiable opinion to a signal chosen
    precisely because it needed none, on the path that decides what a human is asked to approve.

    The same allowlist bars I/O and clock reads, which is what makes ``plan()`` replayable: a
    module that can open a socket or read ``date.today()`` cannot be re-run over a historical
    batch and produce the decisions that were actually made.
    """
    offenders: list[str] = []
    for name, src in _pure_core_sources():
        tree = ast.parse(src, filename=name)
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # ``level > 0`` is a relative import, which can only reach inside copytrade/.
                roots = [(node.module or "").split(".")[0]] if not node.level else []
            for root in roots:
                if root and root not in _ALLOWED_IMPORT_ROOTS:
                    offenders.append(f"{name}: imports {root!r}")
    assert not offenders, f"the pure copy-trade core must import nothing else: {offenders}"


def test_the_pure_core_reads_no_clock_and_no_randomness():
    offenders = [
        f"{name}: {m.group(0)}"
        for name, src in _pure_core_sources()
        for m in _CLOCK_OR_RANDOM.finditer(src)
    ]
    assert not offenders, f"``today`` is a parameter, never a clock read: {offenders}"


def test_the_policy_refuses_a_configuration_it_cannot_run():
    """Fail closed on a bad policy rather than falling back to a default that trades."""
    for bad in (
        dict(options_policy="copy_the_contract"),
        dict(max_age_days=0),
        dict(staleness_decay=Decimal("1.5")),
        dict(copy_fraction=Decimal("0")),
        dict(options_haircut=Decimal("0")),
        dict(options_haircut=Decimal("2")),
        dict(min_notional_per_trade=Decimal("600")),
        dict(max_universe_symbols=0),
    ):
        with pytest.raises(PolicyError):
            CopyPolicy(follow=frozenset({FOLLOWED}), **bad)

    # ...and the shipped default is the conservative one.
    assert CopyPolicy().options_policy == OPTIONS_UNDERLYING
    assert CopyPolicy().follow == frozenset()


# =============================================================================================
# RW-16 adversarial round, finding 6 — THE OPTIONS HAIRCUT MUST SURVIVE THE PER-TRADE CLAMP
#
# The operator's one explicitly stated requirement is "for options trades do not copy them one
# for one as we get delayed information." Before this round the haircut was applied to the RAW
# size only, so above a certain portfolio value an options-derived buy and a full 1:1 equity
# copy of the SAME disclosure both clamped to ``max_notional_per_trade`` and came out
# byte-identical. The requirement stopped holding at exactly the account sizes where the copy is
# biggest, and nothing in the plan said so.
#
# The invariant these tests pin, stated once:
#
#     for identical inputs, an options-derived notional is STRICTLY LESS than the
#     equity-derived one — at EVERY portfolio value, including ones where one side clamps and
#     ones where both do.
# =============================================================================================
BIG_BUCKET = AmountRange(Decimal("1000001"), Decimal("5000000"))
"""The tier from the finding's repro: weight 2.0, the top of the conviction table. Paired with a
same-day trade (no staleness decay) it makes every number below checkable by hand."""


def _today_equity(**over) -> Disclosure:
    over.setdefault("amount", BIG_BUCKET)
    return _disclosure(tx_date=TODAY, filed_date=TODAY, **over)


def _today_call(**over) -> Disclosure:
    over.setdefault("amount", BIG_BUCKET)
    over.setdefault("option_expiry", FAR_EXPIRY)
    return _disclosure(tx_date=TODAY, filed_date=TODAY, asset_type=ASSET_OPTION_CALL,
                       tx_type=TX_PURCHASE, **over)


@pytest.mark.parametrize(
    ("portfolio", "regime"),
    [
        (Decimal("10000"), "neither side clamps"),
        (Decimal("30000"), "the equity side clamps, the options side does not"),
        (Decimal("60000"), "both sides clamp — the exact case from the finding"),
        (Decimal("5000000"), "both sides clamp, far above the band"),
    ],
)
def test_an_options_buy_is_strictly_smaller_than_the_equity_copy_at_every_portfolio_size(
    policy, portfolio, regime
):
    """The whole requirement, as an inequality, across all three clamp regimes.

    ``regime`` is carried only so a failure names which one broke. The assertion is identical in
    all four: same filer, same bucket, same dates, same account — only the instrument differs,
    and the options side must come out SMALLER. It used to be equal in the last two.
    """
    ctx = PlanContext(today=TODAY, portfolio_value=portfolio, holdings={})
    equity = plan([_today_equity(doc_id="eq")], policy, ctx).intents[0]
    option = plan([_today_call(doc_id="opt")], policy, ctx).intents[0]

    assert option.notional < equity.notional, f"haircut erased when {regime}"
    # ...and it is under the HAIRCUT ceiling, not merely under the policy ceiling.
    assert option.notional <= policy.max_notional_per_trade * policy.options_haircut
    assert equity.notional <= policy.max_notional_per_trade


def test_the_finding_6_repro_numbers_exactly(policy):
    """The reproduction from the adversarial round, pinned to the cent.

    portfolio 60,000, tier ($1,000,001-$5,000,000) -> weight 2.0, age 0:
      equity  60000 * 0.02 * 2.0            = 2400.00 -> clamped to 500.00
      options 2400.00 * 0.25                =  600.00 -> ALSO clamped to 500.00   (the bug)
      options ceiling 500.00 * 0.25         =  125.00                             (the fix)
    """
    ctx = PlanContext(today=TODAY, portfolio_value=Decimal("60000"), holdings={})
    equity = plan([_today_equity(doc_id="eq")], policy, ctx).intents[0]
    option = plan([_today_call(doc_id="opt")], policy, ctx).intents[0]

    assert equity.notional == Decimal("500.00")
    assert option.notional == Decimal("125.00")


def test_the_haircut_is_visible_on_the_intent_a_human_approves(policy):
    """An operator must be able to SEE that the discount was applied, not infer it.

    Both channels are asserted: ``translated_from`` says the intent did not come from an equity
    row, and ``sizing_notes`` carries the multiplier itself. A regression that erased the
    discount while leaving the note would be caught by the inequality tests above; this one
    catches the opposite failure, where the size is right but the plan does not say why.
    """
    ctx = PlanContext(today=TODAY, portfolio_value=Decimal("60000"), holdings={})
    option = plan([_today_call(doc_id="opt")], policy, ctx).intents[0]

    assert option.translated_from == ASSET_OPTION_CALL
    assert f"{NOTE_OPTIONS_HAIRCUT}={policy.options_haircut}" in option.sizing_notes
    assert sizing.NOTE_CAPPED_AT_HAIRCUT_CEILING in option.sizing_notes
    # The plain "capped at max" note would tell the operator the FULL ceiling applied, which
    # would be false and is exactly how the erased haircut used to look.
    assert sizing.NOTE_CAPPED_AT_MAX not in option.sizing_notes

    equity = plan([_today_equity(doc_id="eq")], policy, ctx).intents[0]
    assert equity.translated_from is None
    assert not [n for n in equity.sizing_notes if n.startswith(NOTE_OPTIONS_HAIRCUT)]


def test_the_haircut_ceiling_is_the_policy_ceiling_discounted():
    """The unit behind the fix. ``maximum`` is discounted by the same multiplier as the size,
    so the discount is a property of the whole policy band rather than of where on the sizing
    curve one disclosure happened to land."""
    assert sizing.haircut_ceiling(Decimal("500"), haircut=Decimal("0.25")) == Decimal("125.00")
    # Rounded DOWN to the cent like every other size here.
    assert sizing.haircut_ceiling(Decimal("100"), haircut=Decimal("0.333")) == Decimal("33.30")
    # A multiplier of 1 or more is not a haircut and must never INFLATE the ceiling.
    assert sizing.haircut_ceiling(Decimal("500"), haircut=Decimal("1")) == Decimal("500.00")
    assert sizing.haircut_ceiling(Decimal("500"), haircut=Decimal("4")) == Decimal("500.00")
    assert sizing.haircut_ceiling(Decimal("500"), haircut=Decimal("0")) == Decimal("0")


@pytest.mark.parametrize("haircut", ["0.9", "0.999", "0.9999"])
def test_the_sizing_layer_holds_the_inequality_even_at_a_haircut_policy_now_refuses(haircut):
    """The nastiest arithmetic case, exercised where it still lives.

    A haircut so close to 1 that the discount is smaller than the cent both sides round DOWN to
    is now unreachable through ``CopyPolicy`` (``MAX_OPTIONS_HAIRCUT`` is 0.5 — see the share-grid
    test below), but ``sizing.sized_copy_notional`` is a public function that still has to
    guarantee the inequality for any multiplier below 1, and layer 3 of that guarantee — step one
    cent below the full copy rather than let the two meet — only fires up here. So the sweep moves
    down to the sizing layer instead of being deleted with the configuration that reached it.
    """
    kw = dict(amount=BIG_BUCKET, age_days=0, copy_fraction=Decimal("0.02"), max_age_days=45,
              staleness_decay=Decimal("0.5"), minimum=Decimal("25"), maximum=Decimal("500"))
    seen_both = False
    for pv in ("500", "1000", "10000", "24999", "25000", "60000", "1000000", "50000000"):
        full, _ = sizing.sized_copy_notional(portfolio=Decimal(pv), haircut=Decimal("1"), **kw)
        cut, _ = sizing.sized_copy_notional(portfolio=Decimal(pv), haircut=Decimal(haircut), **kw)
        if full <= 0:
            assert cut <= 0, f"portfolio {pv}: the options side survived where equity did not"
            continue
        if cut <= 0:
            continue                                   # dropped under the floor: still smaller
        seen_both = True
        assert cut < full, f"haircut {haircut} erased at portfolio {pv}"
    assert seen_both, "the sweep never exercised a case where BOTH sides emitted"


@pytest.mark.parametrize("haircut", ["0.05", "0.25", "0.4", "0.5"])
def test_the_strict_inequality_holds_for_every_haircut_and_every_portfolio(policy, haircut):
    """A property sweep, because the fix must not hold merely for the shipped 0.25.

    Sizes that fall under the policy floor are DROPPED, and a dropped options intent trivially
    satisfies "smaller than the equity one"; the assertion below refuses to be satisfied
    vacuously on both sides at once.
    """
    pol = CopyPolicy(follow=frozenset({FOLLOWED}), options_haircut=Decimal(haircut))
    seen_both = False
    for pv in ("500", "1000", "10000", "24999", "25000", "60000", "1000000", "50000000"):
        ctx = PlanContext(today=TODAY, portfolio_value=Decimal(pv), holdings={})
        eq = plan([_today_equity(doc_id="eq")], pol, ctx).intents
        op = plan([_today_call(doc_id="opt")], pol, ctx).intents
        if not eq:
            assert not op, f"portfolio {pv}: the options side survived where equity did not"
            continue
        if not op:
            continue                                   # dropped under the floor: still smaller
        seen_both = True
        assert op[0].notional < eq[0].notional, f"haircut {haircut} erased at portfolio {pv}"
    assert seen_both, "the sweep never exercised a case where BOTH sides emitted"


def test_a_haircut_of_one_is_not_an_available_setting():
    """1.0 IS copying one for one, which the operator's requirement forbids outright, and it
    would make the strict inequality above false. Refused at both boundaries: the policy that
    configures it, and the translation object that carries it."""
    with pytest.raises(PolicyError):
        CopyPolicy(follow=frozenset({FOLLOWED}), options_haircut=Decimal("1"))
    with pytest.raises(ValueError):
        opt.OptionTranslation.buy(Decimal("1"), opt.REASON_CALL_TO_UNDERLYING)
    # An exit still carries a full multiplier — the haircut prices leverage on the way IN, and
    # a half-exit on a bearish signal leaves behind the exposure the signal warned about.
    assert opt.OptionTranslation.exit(opt.REASON_PUT_BEARISH_EXIT).haircut == Decimal("1")


def test_the_inequality_survives_SHARE_QUANTIZATION_not_only_the_notional():
    """RE-ATTACK REGRESSION. The audited inequality was on ``CopyIntent.notional``; the number
    that reaches the human gate and the broker is ``floor(notional / quote_price)``, and ``floor``
    is not injective.

    At ``options_haircut=0.75`` (which ``Settings`` and ``CopyPolicy`` both used to accept), a
    $60k portfolio and the top bucket, the equity intent is $500.00 and the options intent
    $375.00 — and at every integer quote price in 167..375 both floor to the SAME share count, so
    the two orders are byte-identical in qty, limit price and order-tuple hash. That is finding
    6's failure displaced to the layer that actually trades.

    ``MAX_OPTIONS_HAIRCUT`` closes it by construction: a collision needs ``h/f > 1/2``, and the
    ceiling is discounted by the same multiplier, so ``h <= 0.5 * f`` makes it unreachable. The
    test proves both halves — the bound is enforced, and under the bound no price collides.
    """
    with pytest.raises(PolicyError):
        CopyPolicy(follow=frozenset({FOLLOWED}), options_haircut=Decimal("0.75"))

    for haircut in ("0.05", "0.25", "0.4", "0.5"):
        pol = CopyPolicy(follow=frozenset({FOLLOWED}), options_haircut=Decimal(haircut))
        for pv in ("10000", "30000", "60000", "5000000"):
            ctx = PlanContext(today=TODAY, portfolio_value=Decimal(pv), holdings={})
            eq = plan([_today_equity(doc_id="eq")], pol, ctx).intents
            op = plan([_today_call(doc_id="opt")], pol, ctx).intents
            if not eq or not op:
                continue
            for price in range(1, 1001):
                p = Decimal(price)
                eq_qty = int(eq[0].notional / p)
                op_qty = int(op[0].notional / p)
                assert op_qty < eq_qty or op_qty == 0, (
                    f"haircut {haircut}, portfolio {pv}, price {price}: an options-derived order "
                    f"quantizes to the same {op_qty} shares as the equity one"
                )


def test_the_near_expiry_gate_cannot_be_configured_away():
    """``options.REASON_NEAR_EXPIRY`` claims "an expiry falling exactly on today reads as near
    expiry, not expired — both drop". That claim was true only of the DEFAULT: the field had no
    validation at all, and ``Settings`` accepted 0, at which a 0-DTE contract passed both gates
    and became a fresh multi-week long in the underlying."""
    with pytest.raises(PolicyError):
        CopyPolicy(follow=frozenset({FOLLOWED}), options_min_days_to_expiry=0)
    with pytest.raises(PolicyError):
        CopyPolicy(follow=frozenset({FOLLOWED}), options_min_days_to_expiry=-5)
    # ... and at the smallest legal value a contract expiring TODAY still drops.
    pol = CopyPolicy(follow=frozenset({FOLLOWED}), options_min_days_to_expiry=1)
    ctx = PlanContext(today=TODAY, portfolio_value=PORTFOLIO, holdings={})
    result = plan([_call(option_expiry=TODAY)], pol, ctx)
    assert result.intents == ()
    assert [s.reason for s in result.skips] == [opt.REASON_NEAR_EXPIRY]


def test_an_options_intent_that_cannot_clear_the_floor_under_its_own_ceiling_is_dropped(policy):
    """A haircut ceiling BELOW ``min_notional_per_trade`` has no compliant size in it. The
    intent is dropped and named rather than emitted under the operator's own floor — which is
    what a clamp against the full ceiling would have done."""
    pol = CopyPolicy(
        follow=frozenset({FOLLOWED}),
        min_notional_per_trade=Decimal("100"),
        max_notional_per_trade=Decimal("200"),
        options_haircut=Decimal("0.25"),                # ceiling 50, floor 100
    )
    ctx = PlanContext(today=TODAY, portfolio_value=Decimal("1000000"), holdings={})
    assert plan([_today_equity(doc_id="eq")], pol, ctx).intents[0].notional == Decimal("200.00")

    skip = _only_skip(plan([_today_call(doc_id="opt")], pol, ctx))
    assert skip.reason == SKIP_BELOW_MIN_NOTIONAL
    assert skip.reason in ALL_SKIP_REASONS


# =============================================================================================
# RW-16 adversarial round, finding 7 — A CONFLICT MUST NOT AGE OUT INTO A ONE-SIDED BUY
#
# Member A discloses a SALE of AAPL, member B a PURCHASE nine days later. While both still
# produce intents the plan drops both (``conflicting_sides``). But they age out at different
# moments, and the day A's sale crossed ``max_age_days`` the disagreement silently disappeared
# and the plan emitted a BUY of the exact symbol a followed member had exited. Nothing about the
# evidence had changed — only the calendar.
# =============================================================================================
SALE_TX = date(2026, 6, 1)
BUY_TX = date(2026, 6, 10)


def _contested_pair() -> tuple[Disclosure, Disclosure]:
    """A's sale (older) and B's purchase (newer) of the same ticker, filed the same day."""
    sale = _disclosure(doc_id="A-sale", filer_slug=FOLLOWED, filer_display="Member A",
                       tx_type=TX_SALE, tx_date=SALE_TX, filed_date=date(2026, 7, 1))
    buy = _disclosure(doc_id="B-buy", filer_slug=UNFOLLOWED, filer_display="Member B",
                      tx_type=TX_PURCHASE, tx_date=BUY_TX, filed_date=date(2026, 7, 1))
    return sale, buy


def _both_followed() -> CopyPolicy:
    return CopyPolicy(follow=frozenset({FOLLOWED, UNFOLLOWED}))


@pytest.mark.parametrize(
    ("today", "regime"),
    [
        (date(2026, 7, 5), "both sides live: sale 34d, purchase 25d"),
        (date(2026, 7, 17), "the sale has aged out (46d), the purchase has not (37d)"),
        (date(2026, 7, 25), "the sale is long gone (54d), the purchase is at the window edge (45d)"),
    ],
)
def test_a_conflicted_symbol_never_ages_out_into_a_one_sided_buy(today, regime):
    """THE regression. The outcome for the BUY side is the same on every one of these dates.

    Before the fix the third and second rows emitted a buy of a symbol a followed member had
    exited, purely because the opposing disclosure had crossed the staleness line. The
    suppression is COUNTED under its own reason so "we did not buy it" is a positive statement
    the operator can read, not an absence.
    """
    sale, buy = _contested_pair()
    ctx = PlanContext(today=today, portfolio_value=PORTFOLIO,
                      holdings={"AAPL": {"market_value": Decimal("1234.567")}})
    result = plan([sale, buy], _both_followed(), ctx)

    assert [i for i in result.intents if i.side == SIDE_BUY] == [], f"one-sided buy when {regime}"
    reasons = {s.key: s.reason for s in result.skips}
    assert reasons[buy.key] in (SKIP_CONFLICTING_SIDES, SKIP_CONTESTED_SYMBOL)
    assert set(reasons.values()) <= ALL_SKIP_REASONS
    assert SKIP_CONTESTED_SYMBOL in ALL_SKIP_REASONS


def test_the_contest_survives_the_opposing_sale_being_copied_in_an_earlier_cycle():
    """The other way the conflict used to evaporate: the sale is not stale, it was ACTED ON.

    ``already_copied`` stops the sale producing an intent, so a pass over surviving intents sees
    no disagreement at all. The contest is computed from the disclosures instead, so it does not
    care which side has already been consumed.
    """
    sale, buy = _contested_pair()
    ctx = PlanContext(
        today=date(2026, 7, 5), portfolio_value=PORTFOLIO,
        holdings={"AAPL": {"market_value": Decimal("1234.567")}},
        already_copied=frozenset({sale.key}),
    )
    result = plan([sale, buy], _both_followed(), ctx)

    assert result.intents == ()
    assert {s.key: s.reason for s in result.skips} == {
        sale.key: SKIP_ALREADY_COPIED,
        buy.key: SKIP_CONTESTED_SYMBOL,
    }


def test_a_contested_buy_is_suppressed_even_when_the_exit_is_structurally_unusable():
    """The sale cannot be mirrored — the account does not hold the symbol — so it produces a
    skip, not an intent. That is still evidence a followed member was getting out, and it still
    suppresses the buy. Holdings are account state; they do not change what the filing said."""
    sale, buy = _contested_pair()
    ctx = PlanContext(today=date(2026, 7, 5), portfolio_value=PORTFOLIO, holdings={})
    result = plan([sale, buy], _both_followed(), ctx)

    assert result.intents == ()
    assert {s.key: s.reason for s in result.skips} == {
        sale.key: SKIP_NOT_HELD,
        buy.key: SKIP_CONTESTED_SYMBOL,
    }


def test_a_contested_exit_is_still_taken_because_the_asymmetry_is_the_point():
    """The deliberate carve-out, pinned so it is a decision and not an oversight.

    Reverse the pair: the PURCHASE is the one that aged out and the SALE is live. The exit runs.
    Suppressing it would hold exposure the newer evidence argues against, on the strength of a
    purchase we chose not to copy — every other ambiguity in this package resolves toward less
    exposure, and so does this one. Only the BUY side is suppressed by a contest.
    """
    old_buy = _disclosure(doc_id="B-buy", filer_slug=UNFOLLOWED, filer_display="Member B",
                          tx_type=TX_PURCHASE, tx_date=date(2026, 6, 1),
                          filed_date=date(2026, 7, 1))
    new_sale = _disclosure(doc_id="A-sale", filer_slug=FOLLOWED, filer_display="Member A",
                           tx_type=TX_SALE, tx_date=date(2026, 7, 10),
                           filed_date=date(2026, 7, 12))
    ctx = PlanContext(today=date(2026, 7, 20), portfolio_value=PORTFOLIO,
                      holdings={"AAPL": {"market_value": Decimal("1234.567")}})
    result = plan([old_buy, new_sale], _both_followed(), ctx)

    assert [(i.side, i.source_key) for i in result.intents] == [(SIDE_SELL, new_sale.key)]
    assert {s.key: s.reason for s in result.skips} == {old_buy.key: SKIP_STALE}


@pytest.mark.parametrize("today", [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)])
def test_a_contested_exit_does_not_flip_with_the_calendar(today):
    """RE-ATTACK REGRESSION. Finding 7's fix removed the calendar from the BUY side only.

    A sale of AAPL (2026-08-05) and an opposing purchase (2026-06-27), same evidence on three
    consecutive days. ``_drop_conflicting_sides`` used to compute ``both_live`` from the surviving
    INTENTS and drop the SELL as well, so on 2026-08-10 and 2026-08-11 the exit was suppressed
    (``conflicting_sides``) and on 2026-08-12 — the day the buy crossed ``max_age_days`` and
    stopped producing an intent — it was suddenly taken. Nothing about the evidence changed, only
    the date, and the exit fired 45 days late.

    The exit is now a function of the evidence: it runs on every one of these dates.
    """
    sale = _disclosure(doc_id="A-sale", filer_slug=FOLLOWED, filer_display="Member A",
                       tx_type=TX_SALE, tx_date=date(2026, 8, 5), filed_date=date(2026, 8, 6))
    buy = _disclosure(doc_id="B-buy", filer_slug=UNFOLLOWED, filer_display="Member B",
                      tx_type=TX_PURCHASE, tx_date=date(2026, 6, 27),
                      filed_date=date(2026, 7, 1))
    ctx = PlanContext(today=today, portfolio_value=PORTFOLIO,
                      holdings={"AAPL": {"market_value": Decimal("5000")}})
    result = plan([sale, buy], _both_followed(), ctx)

    assert [(i.side, i.notional) for i in result.intents] == [(SIDE_SELL, Decimal("5000.00"))]
    assert [i for i in result.intents if i.side == SIDE_BUY] == []


def test_a_contested_exit_does_not_flip_on_account_state_either():
    """The same flip was reachable without the calendar: mark the opposing BUY already-copied and
    it stops producing an intent, which used to be enough to un-suppress the exit. Both dates and
    both account states now give the same answer."""
    sale = _disclosure(doc_id="A-sale", filer_slug=FOLLOWED, filer_display="Member A",
                       tx_type=TX_SALE, tx_date=date(2026, 8, 5), filed_date=date(2026, 8, 6))
    buy = _disclosure(doc_id="B-buy", filer_slug=UNFOLLOWED, filer_display="Member B",
                      tx_type=TX_PURCHASE, tx_date=date(2026, 6, 27),
                      filed_date=date(2026, 7, 1))
    holdings = {"AAPL": {"market_value": Decimal("5000")}}
    plain = plan([sale, buy], _both_followed(),
                 PlanContext(today=date(2026, 8, 10), portfolio_value=PORTFOLIO,
                             holdings=holdings))
    copied = plan([sale, buy], _both_followed(),
                  PlanContext(today=date(2026, 8, 10), portfolio_value=PORTFOLIO,
                              holdings=holdings, already_copied=frozenset({buy.key})))
    assert [(i.side, i.notional) for i in plain.intents] == [(SIDE_SELL, Decimal("5000.00"))]
    assert [(i.side, i.notional) for i in copied.intents] == [(SIDE_SELL, Decimal("5000.00"))]


def test_an_uncontested_buy_is_untouched_by_the_new_pass(policy, ctx):
    """The guard against over-reach: a contest needs a real opposing disclosure from a FOLLOWED
    filer on the SAME symbol. A sale by someone we do not follow, or of a different ticker, is
    not evidence this engine acts on and must not suppress anything."""
    buy = _disclosure(doc_id="buy", tx_type=TX_PURCHASE)
    unfollowed_sale = _disclosure(doc_id="other", filer_slug=UNFOLLOWED,
                                  filer_display="Not followed", tx_type=TX_SALE)
    other_symbol_sale = _disclosure(doc_id="msft", tx_type=TX_SALE, symbol="MSFT")
    result = plan([buy, unfollowed_sale, other_symbol_sale], policy, ctx)

    assert [(i.side, i.symbol) for i in result.intents] == [(SIDE_BUY, "AAPL")]
    assert {s.reason for s in result.skips} == {SKIP_NOT_FOLLOWED, SKIP_NOT_HELD}


def test_an_options_exit_signal_contests_a_plain_equity_buy():
    """A long PUT is bearish evidence on the underlying even when it is structurally unusable
    (nothing held to exit, shorting forbidden). It still contests a buy of the same symbol.

    Read directionally, NOT through ``translate_option``: that function consults ``today`` for
    the expiry window, and a contest that evaporates when a contract expires is precisely the
    time-dependence this pass exists to remove — so an EXPIRED put contests too.
    """
    pol = _both_followed()
    ctx = PlanContext(today=TODAY, portfolio_value=PORTFOLIO, holdings={})
    buy = _disclosure(doc_id="buy", filer_slug=UNFOLLOWED, filer_display="Member B",
                      tx_type=TX_PURCHASE)
    for expiry in (FAR_EXPIRY, TODAY - timedelta(days=5)):
        put = _disclosure(doc_id=f"put-{expiry}", asset_type=ASSET_OPTION_PUT,
                          tx_type=TX_PURCHASE, option_expiry=expiry)
        result = plan([buy, put], pol, ctx)
        assert result.intents == (), f"buy survived an opposing put expiring {expiry}"
        assert result.skips[0].reason in ALL_SKIP_REASONS
        assert SKIP_CONTESTED_SYMBOL in {s.reason for s in result.skips}

    # ...but with options switched off entirely, an options row is not evidence this engine
    # reads at all, and it must not suppress an equity buy either.
    off = CopyPolicy(follow=frozenset({FOLLOWED, UNFOLLOWED}), options_policy=OPTIONS_IGNORE)
    put = _disclosure(doc_id="put-off", asset_type=ASSET_OPTION_PUT, tx_type=TX_PURCHASE,
                      option_expiry=FAR_EXPIRY)
    result = plan([buy, put], off, ctx)
    assert [(i.side, i.symbol) for i in result.intents] == [(SIDE_BUY, "AAPL")]
    assert {s.reason for s in result.skips} == {opt.REASON_POLICY_IGNORE}


def test_the_contested_pass_keeps_plan_deterministic_under_input_shuffling():
    """The new pass runs over the batch, so it gets the same determinism treatment as the rest:
    the batch is sorted BEFORE anything is decided, and the contest map takes the first key in
    that total order. Shuffling the input cannot change a single byte of the output."""
    sale, buy = _contested_pair()
    extra = _disclosure(doc_id="msft", tx_type=TX_PURCHASE, symbol="MSFT")
    batch = [sale, buy, extra]
    ctx = PlanContext(today=date(2026, 7, 17), portfolio_value=PORTFOLIO,
                      holdings={"AAPL": {"market_value": Decimal("1234.567")}})
    baseline = plan(batch, _both_followed(), ctx)

    rng = random.Random(20260810)
    for _ in range(25):
        shuffled = batch[:]
        rng.shuffle(shuffled)
        result = plan(shuffled, _both_followed(), ctx)
        assert result.intents == baseline.intents
        assert result.skips == baseline.skips
