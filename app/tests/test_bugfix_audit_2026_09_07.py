"""Regression pins for the 2026-09-07 bug-hunt fixes (re-audit of cf2bcec).

Each test names the BUG id from the fix commit message and fails on the old code:
tampered limits fall back safely, NaN gate inputs reject instead of crashing, dead
clocks are armed, replay/auth ordering holds, vendor integrity blocks approvals,
drawdown garbage locks the gate, forged embed rows refuse to render, id-less news
stays distinct, veto minting is HALT-atomic, sim races count instead of crash, cash
semantics accept real mergers, and the P2 hardening holds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bridge.approval import Rejection, record_decision
from bridge.embeds import (
    render_execution_section,
    render_proposal_embed,
    render_risk_section,
    verified_risk_facts,
)
from bridge import veto as veto_mod
from bridge.veto import AutoVetoCoordinator
from brokers.alpaca_paper import AlpacaPaperBroker
from brokers.capability import TokenRegistry
from brokers.gateway import OrderGateway
from brokers.order_tuple import order_tuple_hash
from config.settings import EnvFileSecretError, Mode, Settings
from core.clock import FrozenClock
from ingest.alpaca_news import AlpacaNewsConsumer
from ingest.market_data import MarketDataError, MarketDataService
from orchestrator.breakers import (
    BreakerEngine,
    StaleAnalysisError,
    breach_daily_equity_drawdown,
    breach_stale_data,
)
from orchestrator.gate import compute_gate
from orchestrator.limits import EVENT_LIMITS_CHANGE, current_limits
from orchestrator.state import EVENT_DRAWDOWN_MARK, EVENT_MODE_CHANGE, OrchestratorState
from quant.book.lob import BookSnapshot, LevelSnapshot
from quant.events import Fill, Liquidity, PriceScale, Side
from quant.execution.autoloop import AutoLoop
from quant.execution.sim_venue import LatencyModel, SimVenue
from quant.oms import CancelOrder, NewOrder
from quant.verify import MarketImpactInputs, Verdict, VerifyParams, verify_proposal
from risk.engine import RiskContext, evaluate
from schemas.models.audit_entry import AuditEntry
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory
from storage.parquet_store import verify_snapshot, write_snapshot
from strategies.merger_semantics import _CASH, _assertion_matches
from tests.brokers.conftest import build_scenario
from tests.workflows.conftest import FakeTransport, envelope
from workflows.interrogation import fresh_interrogate

D = Decimal
NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
OPERATOR = "123456789012345678"
FOREIGN = "999999999999999999"
PENNY = PriceScale(tick_size=D("0.01"))
NOW_NS = 1_000_000_000


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)
    yield


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'bugfix.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=b"bugfix-audit-key",
                                   settings=Settings(), clock=clock)


def _ctx(**over) -> RiskContext:
    base = dict(
        max_notional=D("10000"), max_positions=10, concentration_pct=D("0.5"),
        daily_loss_limit=D("1000"), quote_staleness_s=30.0, max_spread_pct=D("0.02"),
        pending_exposure_cap=D("50000"), cash=D("50000"), buying_power=D("50000"),
        holdings={"AAPL": {"market_value": D("5000")}}, open_positions=1,
        realized_loss_today=D("0"), pending_notional=D("0"), watchlist={"MSFT"},
        quote_price=D("190"), quote_age_s=2.0, quote_spread_pct=D("0.001"), mode="paper",
    )
    base.update(over)
    return RiskContext(**base)


def _proposal(**over) -> dict:
    p = dict(proposal_id="p1", symbol="AAPL", side="buy", asset_type="equity",
             order_type="limit", time_horizon="short", thesis="t", invalidation="i",
             sizing_basis="thesis", source_refs=[], notional="1000")
    p.update(over)
    return p


def _book(*, seq=10, ts=0):
    return BookSnapshot(
        symbol="S", seq=seq, ts=ts,
        bids=(LevelSnapshot(price_ticks=10_000, qty=800, order_count=1),),
        asks=(LevelSnapshot(price_ticks=10_002, qty=800, order_count=1),),
    )


def _vproposal(**over):
    p = {"symbol": "S", "side": "buy", "quantity": 1,
         "order_type": "limit", "time_horizon": "short"}
    p.update(over)
    return p


METRICS = MarketImpactInputs(
    symbol="S", as_of=NOW.date(),
    median_daily_dollar_volume=D("100000000"),
    annualized_realized_volatility=D("0.20"), observations=60, age_days=1,
)
PARAMS = VerifyParams(scale=PENNY)


def _resolved(symbol="AAPL"):
    return {"symbol": symbol, "side": "buy", "qty": "1", "order_type": "limit",
            "limit_price": "150", "tif": "day"}


class _FakeRD:
    def __init__(self, rid, evaluated_at=None):
        self.id = rid
        self.evaluated_at = evaluated_at


# --- BUG-126: deleted tightening must fail closed, forged append still ignored ----
def test_limits_deleted_newest_tightening_fails_closed_not_looser(st):
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 4}})
    st.audit.append(EVENT_LIMITS_CHANGE, actor="operator",
                    payload={"limits": {"deep_model_daily_budget": 2}})
    assert current_limits(st)["deep_model_daily_budget"] == 2
    with st.session_factory() as s, s.begin():
        rows = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_LIMITS_CHANGE).order_by(AuditEntry.seq)).scalars().all()
        assert len(rows) == 2
        s.delete(rows[-1])   # keyless writer deletes the genuine tightening
    got = current_limits(st)["deep_model_daily_budget"]
    assert got == 0, f"deleted tightening must fail closed to 0, got {got}"
    assert got != 4


# --- BUG-127: NaN gate inputs reject, never crash ----------------------------------
@pytest.mark.parametrize("field", [
    "max_notional", "buying_power", "pending_exposure_cap", "concentration_pct",
    "quote_price", "max_spread_pct",
])
def test_risk_nan_inputs_reject_without_raising(field):
    d = evaluate(_proposal(), _ctx(**{field: D("NaN")}))
    assert d["decision"] == "rejected", f"NaN {field} must reject, got {d['decision']}"


@pytest.mark.parametrize("mode", [None, "garbage", "READ_ONLY", "live"])
def test_risk_unknown_mode_rejects(mode):
    d = evaluate(_proposal(), _ctx(mode=mode))
    assert d["decision"] == "rejected"
    assert "mode_allows_order" in d["failed_checks"]


def test_risk_known_modes_still_pass_the_mode_gate():
    assert evaluate(_proposal(), _ctx(mode="paper"))["decision"] == "approved"
    assert "mode_allows_order" not in evaluate(
        _proposal(), _ctx(mode=Mode.review_live))["failed_checks"]


# --- BUG-128: verdict-age ceiling is enforced when a clock is supplied -------------
class _OldVerdict:
    verdict = "ok"
    book_seq = 5
    computed_at_ns = NOW_NS - 60_000_000_000  # 60s old vs a 5s ceiling
    expected_cost_bps = D("5")
    net_edge_state = "not_applicable"


def test_stale_verdict_by_age_reads_stale_with_clock():
    ctx = _ctx(quant_verdict=_OldVerdict(), quant_book_seq=5,
               quant_verdict_now_ns=NOW_NS, require_quant_verdict=True)
    d = evaluate(_proposal(), ctx)
    assert d["decision"] in ("rejected", "needs_human")
    quality = next(c for c in d["checks"] if c["name"] == "execution_quality")
    assert quality["passed"] is False


def test_fresh_verdict_by_age_still_prices():
    fresh = SimpleNamespace(verdict="ok", book_seq=5, computed_at_ns=NOW_NS,
                            expected_cost_bps=D("5"), net_edge_state="not_applicable")
    ctx = _ctx(quant_verdict=fresh, quant_book_seq=5,
               quant_verdict_now_ns=NOW_NS, require_quant_verdict=True)
    assert evaluate(_proposal(), ctx)["decision"] == "approved"


# --- BUG-135: verify_proposal requires its clock ------------------------------------
def test_verify_without_clock_is_insufficient_data():
    v = verify_proposal(_vproposal(), _book(), PARAMS, metrics=METRICS, now_ns=None)
    assert v.verdict is Verdict.INSUFFICIENT_DATA
    assert "now_ns" in v.reasons[0]


# --- BUG-134: NaN / inverted thresholds fail closed ----------------------------------
def test_verify_nan_thresholds_fail_closed():
    from dataclasses import replace
    bad = replace(PARAMS, max_adv_participation=D("NaN"))
    v = verify_proposal(_vproposal(), _book(), bad, metrics=METRICS, now_ns=NOW_NS)
    assert v.verdict is Verdict.INSUFFICIENT_DATA


def test_verify_inverted_cost_thresholds_fail_closed():
    from dataclasses import replace
    bad = replace(PARAMS, marginal_cost_bps=D("60"), uneconomic_cost_bps=D("50"))
    v = verify_proposal(_vproposal(), _book(), bad, metrics=METRICS, now_ns=NOW_NS)
    assert v.verdict is Verdict.INSUFFICIENT_DATA


# --- BUG-133: auth before idempotency; replays must bind ------------------------------
def test_foreign_replay_of_known_interaction_is_unauthorized(st, clock):
    first = record_decision(
        st, proposal_id="p1", risk_decision=_FakeRD("rd-1"), resolved_order=_resolved(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-known", clock=clock,
    )
    assert not isinstance(first, Rejection)
    replay = record_decision(
        st, proposal_id="p1", risk_decision=_FakeRD("rd-1"), resolved_order=_resolved(),
        actor_user_id=FOREIGN, user_decision="approve", surface="discord",
        interaction_id="ix-known", clock=clock,
    )
    assert isinstance(replay, Rejection) and replay.code == "unauthorized"


def test_mismatched_replay_is_interaction_conflict(st, clock):
    record_decision(
        st, proposal_id="p1", risk_decision=_FakeRD("rd-1"), resolved_order=_resolved(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-1", clock=clock,
    )
    conflict = record_decision(
        st, proposal_id="p1", risk_decision=_FakeRD("rd-1"), resolved_order=_resolved(),
        actor_user_id=OPERATOR, user_decision="veto", surface="discord",
        interaction_id="ix-1", clock=clock,
    )
    assert isinstance(conflict, Rejection) and conflict.code == "interaction_conflict"


def test_same_binding_replay_stays_idempotent(st, clock):
    a = record_decision(
        st, proposal_id="p1", risk_decision=_FakeRD("rd-1"), resolved_order=_resolved(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-same", clock=clock,
    )
    b = record_decision(
        st, proposal_id="p1", risk_decision=_FakeRD("rd-1"), resolved_order=_resolved(),
        actor_user_id=OPERATOR, user_decision="approve", surface="discord",
        interaction_id="ix-same", clock=clock,
    )
    assert not isinstance(b, Rejection) and b.id == a.id


# --- BUG-132: TTL anchored at the risk eval -------------------------------------------
def test_gateway_refuses_fresh_approval_over_stale_eval(st, clock):
    gw = OrderGateway(AlpacaPaperBroker(), st=st, approval_ttl_seconds=10)
    sc = build_scenario(st, gateway=gw)
    with st.session_factory() as s, s.begin():
        row = s.get(RiskDecision, sc.risk_decision.id)
        row.evaluated_at = clock.now() - timedelta(seconds=100)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "approval" and "expired" in (out.reason or "")


# --- BUG-131: vendor integrity failure blocks per-vendor authority ----------------------
def test_integrity_failure_blocks_vendor_views_and_freshness(st):
    st.usage.fail_closed("test contradiction")
    engine = BreakerEngine(st)
    assert engine.is_watch_only("claude") is True
    assert engine.is_watch_only() is True
    with pytest.raises(StaleAnalysisError):
        engine.assert_fresh_analysis("claude", 0)


# --- BUG-129: garbage drawdown / timestamps lock the gate ------------------------------
class _Anchor:
    def __init__(self):
        self.point = None

    def latest(self):
        return self.point


def _anchored(st):
    anchor = _Anchor()
    head = st.audit.head()
    anchor.point = head
    st.anchor_store = anchor
    return st


def test_gate_malformed_drawdown_locks(st):
    st.audit.append(EVENT_DRAWDOWN_MARK, actor="t", payload={"drawdown": "garbage"})
    _anchored(st)
    res = compute_gate(st, Mode.auto_veto)
    assert res.unlockable is False
    assert any("drawdown" in r for r in res.reasons)


def test_gate_malformed_mode_timestamp_locks_not_crashes(st):
    st.audit.append(EVENT_MODE_CHANGE, actor="t",
                    payload={"to": "paper", "_ts": "not-a-timestamp"})
    _anchored(st)
    res = compute_gate(st, Mode.auto_veto)
    assert res.unlockable is False


# --- BUG-130: breaker predicates fail closed on unreadable signals ----------------------
def test_breaker_predicates_trip_on_nan():
    assert breach_daily_equity_drawdown(D("NaN"), D("500")) is True
    assert breach_stale_data(float("nan"), 120.0) is True
    assert breach_stale_data(120.0, 120.0) is False  # boundary unchanged


# --- BUG-136: embed renders reconcile against the signed copy -----------------------------
def _risk_row(**over):
    base = dict(id="rd-1", proposal_id="p1", decision="approved",
                checks=[{"name": "buying_power", "passed": True, "hard": True, "detail": ""}],
                failed_checks=[], warnings=[], max_allowed_notional=D("2500"),
                human_required=False, execution_verdict=None)
    base.update(over)
    return SimpleNamespace(**base)


def _signed_facts():
    return {"proposal_id": "p1", "decision": "approved",
            "checks": [{"name": "buying_power", "passed": True, "hard": True, "detail": ""}],
            "failed_checks": [], "warnings": [], "max_allowed_notional": "2500",
            "human_required": False}


def test_embed_matching_row_renders_verified():
    sec = render_risk_section(_risk_row(), audit_facts=_signed_facts())
    assert sec["decision"] == "approved" and sec["verified"] is True


def test_embed_forged_row_refuses():
    forged = _risk_row(decision="approved", failed_checks=[],
                       max_allowed_notional=D("999999"))
    sec = render_risk_section(forged, audit_facts=_signed_facts())
    assert sec.get("unverified") is True
    assert "999999" not in str(sec)


def test_embed_execution_section_reconciles_verdict():
    verdict = {"verdict": "ok", "expected_cost_bps": "5"}
    row = _risk_row(execution_verdict=dict(verdict))
    good = dict(_signed_facts(), execution_verdict=dict(verdict))
    assert render_execution_section(row, audit_facts=good)["verdict"] == "ok"
    tampered = _risk_row(execution_verdict={"verdict": "ok", "expected_cost_bps": "1"})
    bad = render_execution_section(tampered, audit_facts=good)
    assert bad.get("unverified") is True


def test_embed_end_to_end_marks_verified():
    verdict = {"verdict": "ok", "expected_cost_bps": "5"}
    row = _risk_row(execution_verdict=dict(verdict))
    facts = dict(_signed_facts(), execution_verdict=dict(verdict))
    embed = render_proposal_embed({"proposal_id": "p1", "symbol": "A", "side": "buy",
                                   "order_type": "limit", "source_refs": []},
                                  row, raw_excerpt_fn=None, audit_facts=facts)
    assert embed["risk"]["verified"] is True
    assert embed["execution"]["verdict"] == "ok"


def test_embed_without_coverage_refuses():
    sec = render_risk_section(_risk_row(), audit_facts={})
    assert sec.get("unverified") is True


def test_verified_risk_facts_round_trip(st):
    rd = RiskDecision(id="rd-9", proposal_id="p9", decision="approved", checks=[],
                      failed_checks=[], warnings=[], max_allowed_notional=D("100"),
                      human_required=False, evaluated_at=NOW, created_at=NOW)
    st.audit.record_state_change(rd, "risk_decision", actor="risk",
                                 payload=dict(_signed_facts(), proposal_id="p9"))
    facts = verified_risk_facts(st, "rd-9")
    assert facts is not None and facts["decision"] == "approved"
    assert verified_risk_facts(st, "rd-missing") is None


# --- BUG-137: distinct id-less news stays distinct -------------------------------------------
def test_distinct_id_less_frames_keep_distinct_ids():
    frames = [
        {"symbols": ["AAPL"], "headline": "Alert: halt", "summary": "first story",
         "created_at": "2026-07-06T09:30:00Z"},
        {"symbols": ["AAPL"], "headline": "Alert: halt", "summary": "second story",
         "created_at": "2026-07-06T09:30:00Z"},
    ]
    out = []
    c = AlpacaNewsConsumer(connect=lambda: iter(frames))
    assert c.run(out.append) == 2
    assert out[0]["article_id"] != out[1]["article_id"]
    assert all(a.startswith("synthetic:") for a in (e["article_id"] for e in out))
def test_identical_id_less_redelivery_still_dedupes():
    frame = {"symbols": ["AAPL"], "headline": "Alert: halt", "summary": "same",
             "created_at": "2026-07-06T09:30:00Z"}
    out = []
    c = AlpacaNewsConsumer(connect=lambda: iter([frame, frame]))
    assert c.run(out.append) == 1


# --- BUG-138: veto mint is HALT-atomic; mint failures are covered ------------------------------
class _Mono:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class _FakeLadder:
    def __init__(self, mode):
        self._mode = mode

    def current(self):
        return self._mode


def _veto_risk():
    return SimpleNamespace(id="rd-v")


def _veto_order():
    return dict(_resolved(), proposal_id="prop-v")


def _coord(st, mono):
    reg = TokenRegistry()
    coord = AutoVetoCoordinator(st, reg, window_seconds=300.0, monotonic=mono,
                                mode_ladder=_FakeLadder(Mode.auto_veto))
    return coord, reg


def test_veto_reprobe_before_mint_blocks_halt_race(st, monkeypatch):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono)
    coord.schedule(proposal_id="prop-v", risk_decision=_veto_risk(),
                   resolved_order=_veto_order())
    coord.confirm_delivery("prop-v")
    mono.t = 301.0
    calls = {"n": 0}

    def flapping():
        calls["n"] += 1
        return calls["n"] > 1  # clean at first probe, HALT set before the mint

    monkeypatch.setattr(veto_mod, "_halt_active", flapping)
    res = coord.resolve("prop-v")
    assert res.executed is False and res.reason == "halt"
    assert reg.live_count() == 0


def test_veto_halt_probe_fault_fails_closed(st, monkeypatch):
    mono = _Mono(0.0)
    coord, reg = _coord(st, mono)
    coord.schedule(proposal_id="prop-v", risk_decision=_veto_risk(),
                   resolved_order=_veto_order())
    coord.confirm_delivery("prop-v")
    mono.t = 301.0

    def broken(_path):
        raise RuntimeError("probe exploded")

    # Fault the UNDERLYING probe: the _halt_active wrapper must convert it to SET.
    monkeypatch.setattr(veto_mod, "halt_is_set", broken)
    res = coord.resolve("prop-v")
    assert res.executed is False and res.reason == "halt"
    assert reg.live_count() == 0


def test_veto_mint_collision_is_covered_not_a_crash(st):
    mono = _Mono(1000.0)
    coord, reg = _coord(st, mono)
    coord.schedule(proposal_id="prop-v", risk_decision=_veto_risk(),
                   resolved_order=_veto_order())
    coord.confirm_delivery("prop-v")
    reg.mint(nonce="vetotok_prop-v_1000.0", proposal_id="prop-v",
             risk_decision_id="rd-v", order_tuple_hash=order_tuple_hash(_veto_order()))
    mono.t = 1301.0
    res = coord.resolve("prop-v")
    assert res.executed is False and res.reason == "token_mint_failed"


# --- BUG-139: sim withdraw-vs-fill race counts, never crashes --------------------------
def test_sim_fill_before_traveling_cancel_does_not_crash():
    venue = SimVenue(symbol="S")
    loop = AutoLoop(venue=venue, strategies=[], scale=PENNY)
    ts = 10_000_000
    intent = NewOrder(client_id="m", symbol="S", side=Side.BUY, qty=10,
                      price_ticks=10_000)
    loop.oms.register(intent, strategy="t", ts=ts)
    venue.accept_order(intent, owner="t", ts=ts)
    for outcome in venue.advance(ts + 1_000_000):
        order = loop.oms.get(outcome.client_id)
        if order is not None and order.is_live:
            loop.oms.on_acknowledged(outcome.client_id, ts=outcome.ts)
    assert loop.oms.get("m").is_live
    # The strategy cancels through the loop: the cancel is still traveling, so the
    # OMS order must stay live (pre-fix _route retired it here, and the fill below
    # raised IllegalTransition out of _absorb, killing the run).
    loop._route(None, [CancelOrder(client_id="m")], ts + 1_000_000)
    assert loop.oms.get("m").is_live
    # A fill lands before the cancel arrives: legitimate fill of a live order.
    fill = Fill(order_id="m", symbol="S", seq=1, side=Side.BUY, price_ticks=10_000,
                qty=10, liquidity=Liquidity.TAKER, exchange_ts=ts + 1_000_001,
                recv_ts=ts + 1_000_001, ingest_ts=ts + 1_000_001)
    loop._absorb([fill])  # must not raise
    assert loop.oms.get("m").is_terminal
    assert loop.result.desync_fills == 0
    # The cancel then arrives at the venue (which still holds the resting order,
    # the fill above being loop-level): confirmation must not clobber the terminal
    # OMS state.
    venue.advance(ts + 2_000_000)
    assert venue.drain_confirmed_cancels() == ["m"]
    for client_id in ["m"]:
        order = loop.oms.get(client_id)
        if order is not None and order.is_live:
            loop.oms.on_cancelled(client_id, ts=ts + 2_000_000,
                                  reason="venue_confirmed_cancel")
    assert loop.oms.get("m").state.value == "filled"


def test_sim_pending_replace_with_bad_qty_is_refused():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=1_000,
                                                      market_data_ns=0,
                                                      cancel_ns=0))
    intent = NewOrder(client_id="x", symbol="S", side=Side.BUY, qty=100,
                      price_ticks=100)
    venue.accept_order(intent, owner="t", ts=0)
    assert venue.replace_order("x", new_qty=0, ts=10) is False
    venue.advance(2_000)  # must not raise
    assert venue.book.get("x") is not None


# --- BUG-140: cash semantics accept real mergers ------------------------------------------
CASH_SENTENCE = (
    "each share of common stock will be converted into the right to receive "
    "$150 in cash per share, with no fractional shares issued"
)


def test_cash_with_fractional_boilerplate_is_accepted():
    assert list(_assertion_matches(_CASH, CASH_SENTENCE.lower())) != []


def test_negated_cash_still_refused():
    assert list(_assertion_matches(
        _CASH, "it is not true that each share of common stock will receive "
               "$150 in cash per share")) == []


# --- BUG-141/142: market-data universe + size coercion --------------------------------------
class _QuoteClient:
    def __init__(self, quote):
        self._quote = quote

    def latest_quote(self, symbol):
        return dict(self._quote)


def _quote_frame(**over):
    q = {"ts": "2026-07-07T14:00:00Z", "bid": "100.00", "ask": "100.02",
         "bid_size": 10, "ask_size": 12, "feed": "iex"}
    q.update(over)
    return q


def _md_service(tmp_path, clock, symbols, quote):
    return MarketDataService(
        _QuoteClient(quote), symbols=symbols, clock=clock,
        raw_root=tmp_path / "mdraw", snapshot_source=SimpleNamespace(publish=lambda *a: None),
    )


def test_market_data_rejects_path_traversal_symbol(tmp_path, clock):
    with pytest.raises(MarketDataError):
        _md_service(tmp_path, clock, ["../evil"], _quote_frame())


def test_market_data_accepts_decimal_string_sizes(tmp_path, clock):
    svc = _md_service(tmp_path, clock, ["AAPL"], _quote_frame(bid_size="10.0", ask_size="12.0"))
    res = svc.poll_quotes()
    assert res["updated"] == ["AAPL"] and res["failed"] == []


def test_market_data_discovery_faults_closed_on_bad_symbol(tmp_path, clock):
    svc = _md_service(tmp_path, clock, ["AAPL"], _quote_frame())
    svc._additional_symbols_fn = lambda: ["../../evil"]
    assert svc._refresh_symbols() == ("AAPL",)


# --- BUG-143: direct Settings() still polices the .env file ----------------------------------
def test_direct_settings_construction_polices_env_file(tmp_path, monkeypatch):
    import config.settings as settings_mod
    env = tmp_path / ".env"
    env.write_text("COCKPIT_MODE=paper\nCOCKPIT_ALPACA_KEY_ID=shh\n", encoding="utf-8")
    monkeypatch.setattr(settings_mod, "ENV_FILE", env)
    with pytest.raises(EnvFileSecretError):
        Settings()


# --- BUG-144: interrogation wraps unresolvable refs --------------------------------------------
def test_interrogation_wraps_unresolvable_refs(st, clock, tmp_path):
    from schemas.models.proposal_record import ProposalRecord
    payload = {"proposal_id": "prop-u", "symbol": "AAPL", "side": "buy",
               "asset_type": "equity", "order_type": "limit", "quantity": 5,
               "time_horizon": "short", "thesis": "t", "invalidation": "i",
               "sizing_basis": "fixed",
               "source_refs": ['FOREIGN-REF"><inject x="1']}
    record = ProposalRecord(id="prop-u", symbol="AAPL", side="buy", status="draft",
                            payload=payload, job_id=None, risk_decision_id="rd-u",
                            order_id=None, flagged=False, created_at=NOW, updated_at=NOW)
    st.audit.record_state_change(record, "trade_proposal", actor="test")
    body = {"question": "q?", "answer": "a.", "pattern": "fresh",
            "session_id": None, "generated_at": NOW.isoformat()}
    transport = FakeTransport([envelope(body)])
    fresh_interrogate(st, question="q?", proposal_id="prop-u", transport=transport,
                      parquet_root=tmp_path / "raw", clock=clock)
    prompt = transport.calls[0]["input"]
    assert "unresolvable_refs=" in prompt
    assert "\n\nunresolvable_refs=" not in prompt  # never a bare prompt line
    assert "<untrusted-content" in prompt


# --- BUG-145: missing snapshots verify as False --------------------------------------------------
def test_verify_snapshot_missing_file_is_false(tmp_path):
    assert verify_snapshot(tmp_path / "raw", "2026-07-07", "NOPE", "deadbeef") is False


def test_verify_snapshot_round_trip_and_tamper(tmp_path):
    root, day, sym = tmp_path / "raw", "2026-07-07", "AAPL"
    _, digest = write_snapshot(root, day, sym, [{"a": 1}])
    assert verify_snapshot(root, day, sym, digest) is True
    assert verify_snapshot(root, day, sym, "0" * 64) is False
