"""Inverse Cramer — draft_proposal's deterministic forced_side override.

Enforces: the side that reaches the risk engine / order tuple is ALWAYS ``forced_side``
when supplied, regardless of what the deep model output — a model that disagrees is
recorded in ``reasons`` for visibility, never trusted over the deterministic value. This
mirrors SEC-21's "the headline's self-description never counts" posture, applied to side
instead of urgency. Hermetic: FrozenClock, tmp SQLite, FakeTransport, tmp parquet root.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from config.settings import Mode
from ingest.normalize import to_market_event
from orchestrator.mode_ladder import ModeLadder
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.events import persist_events
from workflows.packets import build_packet
from workflows.proposals import draft_proposal

HOLDINGS = frozenset({"AAPL"})


def _persist_event(st, clock, tmp_path, headline="Jim Cramer says buy AAPL on Mad Money"):
    ev = to_market_event(source="alpaca:benzinga", article_id="c-1", symbols=["AAPL"],
                         headline=headline, summary="Cramer recommends buying AAPL",
                         ts=(NOW - timedelta(minutes=10)).isoformat(),
                         event_type="cramer_call:buy")
    res = persist_events(st, [ev], parquet_root=tmp_path / "raw", clock=clock)
    return res.stored[0]


def _proposal_body(side="buy", pid="prop-1", symbol="AAPL", refs=()):
    return {
        "proposal_id": pid, "symbol": symbol, "side": side, "asset_type": "equity",
        "order_type": "limit", "quantity": 5, "notional": None, "time_horizon": "short",
        "thesis": "contrarian thesis", "invalidation": "cramer is right this time",
        "sizing_basis": "fixed-fraction of book", "source_refs": list(refs),
    }


def _grounding_body(pid, refs, grounded=True):
    return {"proposal_id": pid, "grounded": grounded, "injection_suspected": False,
            "unsupported_claims": [], "source_refs_checked": list(refs)}


def _seed_snapshot(st):
    snap = PortfolioSnapshot(id="snap-1", account="paper", cash=Decimal("100000"),
                             buying_power=Decimal("200000"), exposure=None,
                             holdings=[{"symbol": "AAPL", "market_value": "5000"}],
                             open_orders=[], ts=NOW, created_at=NOW)
    st.audit.record_state_change(snap, "portfolio_snapshot", actor="test")


def _quote_fn(symbol):
    return {"price": "100", "age_s": 1.0, "spread_pct": "0.01"}


def test_forced_side_overrides_a_disagreeing_model(st, clock, tmp_path):
    """Cramer said BUY -> forced_side is SELL. The model (mis-)drafts side='buy' anyway;
    the deterministic override must win: risk gate, order tuple, and the persisted record
    all see side='sell', and the override is recorded in reasons for visibility."""
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    row = _persist_event(st, clock, tmp_path)
    assert row.event_type == "cramer_call:buy"
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(side="buy", refs=[row.raw_ref])),   # model disagrees
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                             holdings_symbols=HOLDINGS, watchlist=frozenset(),
                             prompt_name="inverse_cramer_proposal", forced_side="sell")
    assert outcome.status == "drafted"
    assert outcome.proposal["side"] == "sell"
    assert outcome.record.side == "sell"
    assert outcome.order is not None
    assert outcome.order.side == "sell"
    assert any("forced_side override" in r for r in outcome.reasons)


def test_forced_side_matches_a_compliant_model_no_override_note(st, clock, tmp_path):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(side="sell", refs=[row.raw_ref])),   # model already agrees
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                             holdings_symbols=HOLDINGS, watchlist=frozenset(),
                             prompt_name="inverse_cramer_proposal", forced_side="sell")
    assert outcome.status == "drafted"
    assert outcome.order.side == "sell"
    assert not any("forced_side override" in r for r in outcome.reasons)


def test_inverse_cramer_prompt_template_is_rendered_with_the_fixed_side(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(side="sell", refs=[row.raw_ref])),
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    _seed_snapshot(st)
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                  parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                  holdings_symbols=HOLDINGS, watchlist=frozenset(),
                  prompt_name="inverse_cramer_proposal", forced_side="sell")
    sent_prompt = transport.calls[0]["input"]
    assert "sell" in sent_prompt
    assert "buy" in sent_prompt          # cramer_side is surfaced too
    assert "NOT yours to decide" in sent_prompt


def test_default_trade_proposal_prompt_unaffected_by_forced_side_kwargs(st, clock, tmp_path):
    """Regression: an ordinary (non-Cramer) draft_proposal call with no forced_side must
    render the original trade_proposal template exactly as before (no missing-placeholder
    crash, no override note)."""
    row = to_market_event(source="finnhub", article_id="p-1", symbols=["AAPL"],
                          headline="AAPL beats expectations", summary="strong demand",
                          ts=(NOW - timedelta(minutes=10)).isoformat())
    res = persist_events(st, [row], parquet_root=tmp_path / "raw", clock=clock)
    row = res.stored[0]
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(side="buy", refs=[row.raw_ref])),
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    _seed_snapshot(st)
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                             holdings_symbols=HOLDINGS, watchlist=frozenset())
    assert outcome.status == "drafted"
    assert outcome.order.side == "buy"
    assert outcome.reasons == ()
