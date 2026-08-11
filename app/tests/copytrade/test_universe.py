"""RW-16 — the copy-trade UNIVERSE: the audited promotion lane in front of SEC-15.

``risk.engine``'s ``symbol_allowlist`` hard check is ``symbol in ctx.holdings or symbol in
ctx.watchlist``. A copy-traded congressional name is, by definition, on neither — so without
this module the whole feature would be a generator of rejected proposals, and the three ways
to "fix" that are: widen the operator's hand-maintained watchlist (turns an allowlist into a
suggestion box), weaken the risk check (Invariant 5 forbids touching ``risk/`` at all, and a
check with a per-caller exemption is not a check), or build a bounded, expiring, SIGNED
promotion lane. This module is the third; these tests pin the properties that make it the
third and not the first.

WHAT IS PINNED HERE, AND WHY EACH ONE IS A SAFETY PROPERTY
-----------------------------------------------------------
  * **promotion is strictly weaker than approval** — it moves a symbol from "rejected on
    sight" to "the risk engine may now judge it". Every other hard check still runs. The tests
    assert against ``risk.engine.evaluate`` itself, never against a paraphrase of it.
  * **the audit chain IS the store** — the promoted set is an INPUT TO A RISK CHECK, so
    widening it must require the HMAC key. A tampered entry, or a chain that does not verify,
    can only ever SHRINK the universe.
  * **the bounds can only prevent a NEW BUY, never block an EXIT** — the cap and the TTL are
    only safe because ``symbol_allowlist`` is ``holdings OR watchlist``. Both tests below that
    exercise a bound also assert that a held position stays sellable, because a control that
    could strand a position would be worse than no control.
  * **nothing is ever silently dropped** — every refused symbol is counted and named in the
    signed payload.

Hermetic: tmp SQLite, an explicit test-advanced clock, no network, no model.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from config.settings import Mode, Settings
from copytrade.policy import CopyPolicy
from copytrade.universe import (
    EVENT_COPY_UNIVERSE,
    MAX_SYMBOLS_PER_CALL,
    current_universe,
    promote,
)
from core.clock import Clock
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import OrchestratorState
from risk.engine import evaluate
from schemas.models.audit_entry import AuditEntry
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from storage.engine import create_all, make_engine, make_session_factory
from workflows.proposals import build_risk_context

KEY = b"rw16-universe-key"
T0 = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)     # 10:00 ET, market open
HELD = "AAPL"


class StepClock(Clock):
    """A clock the TEST advances explicitly.

    ``FrozenClock`` cannot express "and then 121 days passed", and TTL expiry is the one
    behaviour here that only exists across time. Advancing a value beats patching
    ``datetime.now``: the promotion timestamps that ``current_universe`` reads are the audit
    chain's own SIGNED ``payload['_ts']`` (SEC-12), which is stamped from this clock, so a
    test that advances it exercises the real signed-timestamp path rather than a stub.
    """

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, *, days: int = 0, seconds: int = 0) -> "StepClock":
        self._at = self._at + timedelta(days=days, seconds=seconds)
        return self

    def at(self, *, days: int = 0, seconds: int = 0) -> "StepClock":
        """A SEPARATE clock offset from this one — for asking 'what would the universe be on
        day N' without moving the clock the audit chain is stamping with."""
        return StepClock(self._at + timedelta(days=days, seconds=seconds))


@pytest.fixture(autouse=True)
def _isolate_halt(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    yield


@pytest.fixture
def clock():
    return StepClock(T0)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw16-universe.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    state = OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(), clock=clock)
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    snap = PortfolioSnapshot(id="snap-1", account="paper", cash=Decimal("100000"),
                             buying_power=Decimal("200000"), exposure=None,
                             holdings=[{"symbol": HELD, "market_value": "5000"}],
                             open_orders=[], ts=clock.now(), created_at=clock.now())
    state.audit.record_state_change(snap, "portfolio_snapshot", actor="test")
    return state


@pytest.fixture
def policy():
    return CopyPolicy(follow=frozenset({"pelosi-nancy"}), max_universe_symbols=3,
                      universe_ttl_days=120)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def _payloads(st):
    """Payloads of VERIFIED ``copy_universe`` entries, oldest first. Entries that fail
    ``verify_entry`` are dropped, so no assertion below can rest on a forged record."""
    with st.session_factory() as s:
        rows = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_COPY_UNIVERSE)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    return [dict(r.payload or {}) for r in rows if st.audit.verify_entry(r)]


def _proposal(symbol: str, side: str = "buy", notional: float = 500.0) -> dict:
    return {"proposal_id": "p1", "symbol": symbol, "side": side, "asset_type": "equity",
            "order_type": "limit", "quantity": None, "notional": notional,
            "time_horizon": "long", "thesis": "t", "invalidation": "i",
            "sizing_basis": "congressional copy", "source_refs": []}


def _verdict(st, policy, symbol, *, side="buy", notional=500.0, clock=None):
    """Run the REAL risk engine against the REAL universe-derived watchlist.

    Deliberately not a paraphrase of ``symbol_allowlist``: what the universe is FOR is the
    answer this function returns, and a test that reimplemented the check would keep passing
    after the check changed."""
    ctx = build_risk_context(
        st, quote={"price": "100", "age_s": 1.0, "spread_pct": "0.01"},
        watchlist=current_universe(st, policy, clock=clock),
    )
    return evaluate(_proposal(symbol, side=side, notional=notional), ctx)


# ---------------------------------------------------------------------------------------------
# promotion: what it does, and what it deliberately does not do
# ---------------------------------------------------------------------------------------------
def test_promotion_is_audited_and_is_the_only_thing_that_makes_a_symbol_judgeable(st, policy,
                                                                                 clock):
    """Before/after against ``risk.engine.evaluate`` itself.

    The 'before' half is the point: a name nobody admitted into the system is REJECTED on
    ``symbol_allowlist``, which is exactly the SEC-15 property the copy feed would otherwise
    have had to weaken."""
    assert current_universe(st, policy, clock=clock) == frozenset()
    before = _verdict(st, policy, "NVDA", clock=clock)
    assert before["decision"] == "rejected"
    assert "symbol_allowlist" in before["failed_checks"]

    assert promote(st, ["NVDA"], reason="followed-filer disclosure: house:D1:0",
                   clock=clock, policy=policy) == ["NVDA"]

    payload = _payloads(st)[-1]
    assert payload["eligible"] == ["NVDA"] and payload["new"] == ["NVDA"]
    assert payload["refreshed"] == [] and payload["refused_over_cap"] == []
    assert payload["invalid_symbol"] == [] and payload["refused_batch_bound"] == []
    assert payload["reason"] == "followed-filer disclosure: house:D1:0"
    assert payload["cap"] == 3 and payload["ttl_days"] == 120
    assert st.audit.verify_chain().ok

    after = _verdict(st, policy, "NVDA", clock=clock)
    assert "symbol_allowlist" not in after["failed_checks"]
    assert after["decision"] == "approved"


def test_promotion_is_not_approval_and_widens_nothing_else(st, policy, clock):
    """PROMOTION IS STRICTLY WEAKER THAN APPROVAL. A promoted symbol still has to pass every
    other hard check; here the size blows through ``max_notional`` and the verdict is still a
    rejection. If a future edit ever makes promotion imply anything more than 'judgeable',
    this test is the one that notices."""
    promote(st, ["NVDA"], reason="disclosure", clock=clock, policy=policy)
    rd = _verdict(st, policy, "NVDA", notional=999_999.0, clock=clock)
    assert rd["decision"] == "rejected"
    assert "symbol_allowlist" not in rd["failed_checks"]
    assert "max_notional" in rd["failed_checks"]


def test_an_empty_request_writes_no_audit_entry_but_a_refusal_always_does(st, policy, clock):
    """The audit chain is append-only, so 'nothing was asked for' must not cost a row — while
    'something was asked for and refused' must always leave one. Silence and refusal have to
    be distinguishable after the fact."""
    assert promote(st, [], reason="nothing", clock=clock, policy=policy) == []
    assert _payloads(st) == []

    assert promote(st, ["not a ticker!"], reason="junk", clock=clock, policy=policy) == []
    assert len(_payloads(st)) == 1
    assert _payloads(st)[-1]["invalid_symbol"] == ["not a ticker!"]


@pytest.mark.parametrize(
    "junk",
    ["ſpy", " SPY", "SPY ", "TOOLONGSYMBOL", "", "NV DA", 123, None, ["SPY"]],
    ids=["long-s", "leading-ws", "trailing-ws", "too-long", "empty", "inner-space",
         "int", "none", "list"],
)
def test_an_invalid_symbol_is_refused_and_counted_never_promoted(st, policy, clock, junk):
    """A promotion is an ALLOWLIST WRITE, so the canonicalizer is a validation boundary, not a
    convenience normalizer. The long-s case is the load-bearing one: ``'ſpy'.upper()`` is
    ``'SPY'``, so case-folding BEFORE the ASCII check would let a filing impersonate a real
    ticker into the allowlist."""
    eligible = promote(st, [junk, "NVDA"], reason="mixed batch", clock=clock, policy=policy)
    assert eligible == ["NVDA"]
    payload = _payloads(st)[-1]
    assert len(payload["invalid_symbol"]) == 1
    assert current_universe(st, policy, clock=clock) == frozenset({"NVDA"})


def test_lowercase_is_canonicalized_rather_than_refused(st, policy, clock):
    """The counterpart to the test above: a plain lowercase ticker is a formatting difference,
    not an impersonation, so it canonicalizes. Pinned so that tightening the impersonation
    check never silently starts dropping ordinary parser output."""
    assert promote(st, ["nvda"], reason="lowercase", clock=clock, policy=policy) == ["NVDA"]
    assert current_universe(st, policy, clock=clock) == frozenset({"NVDA"})


# ---------------------------------------------------------------------------------------------
# the cap: bounded, counted, and NEVER an eviction
# ---------------------------------------------------------------------------------------------
def test_the_universe_is_bounded_and_the_overflow_is_refused_never_evicted(st, policy, clock):
    """AT CAPACITY THE NEW SYMBOL IS REFUSED; AN INCUMBENT IS NEVER DISPLACED.

    The direction matters: eviction-to-make-room means a burst of filings could push a symbol
    the account is actually HOLDING out of the allowlist, and a control that removes protection
    under load is worse than no control. The incumbent set here deliberately includes the held
    name so the regression would be visible."""
    assert promote(st, [HELD, "BBB", "CCC"], reason="first three", clock=clock,
                   policy=policy) == [HELD, "BBB", "CCC"]
    assert current_universe(st, policy, clock=clock) == frozenset({HELD, "BBB", "CCC"})

    assert promote(st, ["DDD", "EEE"], reason="overflow", clock=clock, policy=policy) == []
    payload = _payloads(st)[-1]
    assert payload["refused_over_cap"] == ["DDD", "EEE"]
    assert payload["new"] == [] and payload["eligible"] == []

    assert current_universe(st, policy, clock=clock) == frozenset({HELD, "BBB", "CCC"}), \
        "an incumbent must never be evicted to make room for a newcomer"
    assert "symbol_allowlist" in _verdict(st, policy, "DDD", clock=clock)["failed_checks"]
    assert st.audit.verify_chain().ok


def test_an_oversized_batch_is_bounded_and_the_remainder_is_counted(st, clock):
    """One ``promote`` call describes one cycle's disclosures. The per-call bound is not a
    policy knob — it stops a malformed upstream batch writing an unbounded blob into a log
    that is append-only and therefore cannot be cleaned up afterwards."""
    roomy = CopyPolicy(follow=frozenset({"x-y"}), max_universe_symbols=300)
    symbols = [f"S{i:03d}" for i in range(MAX_SYMBOLS_PER_CALL + 5)]
    eligible = promote(st, symbols, reason="huge batch", clock=clock, policy=roomy)

    assert len(eligible) == MAX_SYMBOLS_PER_CALL
    payload = _payloads(st)[-1]
    assert payload["refused_batch_bound"] == sorted(symbols)[MAX_SYMBOLS_PER_CALL:]
    assert len(payload["refused_batch_bound"]) == 5
    assert len(current_universe(st, roomy, clock=clock)) == MAX_SYMBOLS_PER_CALL


def test_lowering_the_cap_takes_effect_immediately_and_keeps_the_newest(st, policy, clock):
    """A tightening that does not tighten is not a control. The cap is enforced on the READ
    side too, so an operator who lowers it does not have to wait for 40 symbols to age out.
    The survivors are the NEWEST promotions — the ones with live evidence behind them."""
    promote(st, ["AAA"], reason="day 0", clock=clock, policy=policy)
    clock.advance(days=1)
    promote(st, ["BBB"], reason="day 1", clock=clock, policy=policy)
    clock.advance(days=1)
    promote(st, ["CCC"], reason="day 2", clock=clock, policy=policy)
    assert current_universe(st, policy, clock=clock) == frozenset({"AAA", "BBB", "CCC"})

    tightened = CopyPolicy(follow=policy.follow, max_universe_symbols=2,
                           universe_ttl_days=policy.universe_ttl_days)
    assert current_universe(st, tightened, clock=clock) == frozenset({"BBB", "CCC"})
    assert "symbol_allowlist" in _verdict(st, tightened, "AAA", clock=clock)["failed_checks"]


# ---------------------------------------------------------------------------------------------
# the TTL: expiry can stop a NEW BUY and can never strand a POSITION
# ---------------------------------------------------------------------------------------------
def test_ttl_expiry_is_inclusive_at_the_boundary(st, policy, clock):
    """A symbol promoted exactly ``universe_ttl_days`` ago is still in; the day after, it is
    out. Pinned because an off-by-one here silently changes how long a stale congressional
    signal keeps authorising new buys."""
    promote(st, ["NVDA"], reason="disclosure", clock=clock, policy=policy)

    assert "NVDA" in current_universe(st, policy, clock=clock.at(days=120))
    assert "NVDA" not in current_universe(st, policy, clock=clock.at(days=121))


def test_expiry_stops_a_new_buy_but_can_never_strand_a_position(st, policy, clock):
    """THE SAFETY PROPERTY THAT MAKES BOTH BOUNDS NON-DANGEROUS.

    After the TTL drops a held name out of the universe, a BUY of it is refused — but the
    mirrored EXIT is still adjudicable, because ``symbol_allowlist`` is ``holdings OR
    watchlist`` and the holding carries it. If this ever fails, the cap and the TTL have
    become controls that can trap the account in a position."""
    promote(st, [HELD, "NVDA"], reason="disclosure", clock=clock, policy=policy)
    expired = clock.at(days=200)
    assert current_universe(st, policy, clock=expired) == frozenset()

    new_buy = _verdict(st, policy, "NVDA", clock=expired)
    assert "symbol_allowlist" in new_buy["failed_checks"]

    exit_order = _verdict(st, policy, HELD, side="sell", notional=1000.0, clock=expired)
    assert "symbol_allowlist" not in exit_order["failed_checks"], \
        "an exit must never be gated on the copy universe"
    assert "sell_backed" not in exit_order["failed_checks"]
    assert exit_order["decision"] == "approved"


def test_a_re_promotion_refreshes_the_ttl_instead_of_double_counting(st, policy, clock):
    """The filer traded the name again — that is real new evidence, so the clock restarts and
    the symbol is reported as ``refreshed``, not ``new``. It must not consume a second slot."""
    promote(st, ["NVDA"], reason="first disclosure", clock=clock, policy=policy)
    clock.advance(days=100)
    assert promote(st, ["NVDA"], reason="second disclosure", clock=clock,
                   policy=policy) == ["NVDA"]

    payload = _payloads(st)[-1]
    assert payload["refreshed"] == ["NVDA"] and payload["new"] == []
    assert payload["eligible"] == ["NVDA"]

    # 130 days after the FIRST promotion (TTL 120) but only 30 after the second.
    assert "NVDA" in current_universe(st, policy, clock=clock.at(days=30))
    assert len(current_universe(st, policy, clock=clock)) == 1, "no second slot was spent"


# ---------------------------------------------------------------------------------------------
# the chain is the store: tamper can only ever shrink the universe
# ---------------------------------------------------------------------------------------------
def test_a_tampered_promotion_can_only_shrink_the_universe(st, policy, clock):
    """A mutable ``copy_universe`` table would let any local DB writer — no HMAC key, no audit
    entry, no trace — make an arbitrary symbol tradable. Deriving the set from SIGNED entries
    means a keyless writer who edits one can only cost themselves the universe.

    Here the attacker appends ``TSLA`` to a genuine promotion's payload. The result is not
    'NVDA and TSLA' and not even 'NVDA' — it is the EMPTY universe, the tightest possible
    value, and both symbols are refused by the risk engine."""
    promote(st, ["NVDA"], reason="disclosure", clock=clock, policy=policy)
    assert current_universe(st, policy, clock=clock) == frozenset({"NVDA"})

    with st.session_factory() as s, s.begin():
        row = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_COPY_UNIVERSE)
        ).scalars().one()
        row.payload = dict(row.payload, eligible=["NVDA", "TSLA"])

    assert st.audit.verify_chain().ok is False
    assert current_universe(st, policy, clock=clock) == frozenset(), \
        "an unverifiable chain must yield the EMPTY universe (fail closed)"
    for symbol in ("NVDA", "TSLA"):
        assert "symbol_allowlist" in _verdict(st, policy, symbol, clock=clock)["failed_checks"]


def test_a_truncated_chain_yields_the_empty_universe(st, policy, clock):
    """The layer the payload-tamper test above CANNOT reach.

    A keyless writer who deletes the tail of the chain leaves every surviving entry
    individually valid — including the promotion — so per-entry ``verify_entry`` says yes. Only
    the WHOLE-CHAIN check (seq contiguity + the SEC-11 signed high-water) sees the truncation,
    and it must fail closed, because a rollback is exactly how an EXPIRED promotion would
    otherwise be resurrected into the allowlist."""
    promote(st, ["NVDA"], reason="disclosure", clock=clock, policy=policy)
    st.audit.append("copy_universe_test_tail", actor="test", payload={"note": "tail"})

    with st.session_factory() as s, s.begin():
        tail = s.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)
        ).scalars().one()
        s.delete(tail)

    with st.session_factory() as s:
        promotion = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_COPY_UNIVERSE)
        ).scalars().one()
        assert st.audit.verify_entry(promotion), \
            "the promotion entry itself is untouched — only the CHAIN check can catch this"

    assert st.audit.verify_chain().ok is False
    assert current_universe(st, policy, clock=clock) == frozenset()
    assert "symbol_allowlist" in _verdict(st, policy, "NVDA", clock=clock)["failed_checks"]


def test_a_promotion_dated_in_the_future_does_not_count(st, policy, clock):
    """A promotion timestamped ahead of ``now`` is dropped by the ``ts <= now`` guard. Without
    it, a clock skew (or a writer with the key and a bad clock) could park a symbol in the
    allowlist for longer than the TTL allows."""
    promote(st, ["NVDA"], reason="disclosure", clock=clock, policy=policy)
    assert current_universe(st, policy, clock=clock.at(days=-1)) == frozenset()
    assert current_universe(st, policy, clock=clock) == frozenset({"NVDA"})
