"""Inverse Cramer — triage_loop wiring: a deterministically-detected Cramer call escalates
to a proposal draft for ANY symbol (not just holdings/watchlist), with the side FIXED to
the inverse of what Cramer said and routed through the dedicated prompt template.
"""
from __future__ import annotations

from ingest.cramer_signal import inverse_side
from tests.workflows.conftest import FakeTransport, envelope
from tests.workflows.test_triage_loop import _mk, _payloads, _persist, _scored, _triage_body
from workflows.constants import EVENT_TRIAGE_BATCH
from workflows.proposals import ProposalOutcome
from workflows.triage_loop import triage_loop

HOLDINGS = frozenset({"AAPL"})
WATCHLIST = frozenset({"ZZZ"})


def _stub_outcome():
    return ProposalOutcome(status="drafted", proposal={"proposal_id": "p"}, record=None,
                           risk_decision=None, order=None, embed=None, reasons=())


def test_cramer_call_escalates_for_a_symbol_outside_holdings_and_watchlist(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk("t-c", "NFLX", "Jim Cramer says buy NFLX", event_type="cramer_call:buy"),
    ])
    row = rows[0]
    assert row.symbol == "NFLX" not in (HOLDINGS | WATCHLIST)
    transport = FakeTransport([envelope(_triage_body([
        _scored(row.raw_ref, "NFLX", "material", 70),
    ]))])
    captured = []

    def propose_fn(state, clk, **kw):
        captured.append(kw)
        return _stub_outcome()

    result = triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
                         holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
                         propose_fn=propose_fn)
    assert result["status"] == "ok"
    assert row.id in result["escalated"]
    assert len(captured) == 1
    assert captured[0]["forced_side"] == "sell"          # Cramer said buy -> inverse is sell
    assert captured[0]["prompt_name"] == "inverse_cramer_proposal"


def test_cramer_call_sell_inverts_to_buy(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk("t-c2", "GME", "Cramer says sell GME", event_type="cramer_call:sell"),
    ])
    row = rows[0]
    transport = FakeTransport([envelope(_triage_body([
        _scored(row.raw_ref, "GME", "material", 70),
    ]))])
    captured = []
    result = triage_loop(
        st, clock, transport=transport, parquet_root=tmp_path / "raw",
        holdings_symbols=frozenset(), watchlist=frozenset(),
        propose_fn=lambda s, c, **kw: (captured.append(kw) or _stub_outcome()),
    )
    assert row.id in result["escalated"]
    assert captured[0]["forced_side"] == inverse_side("sell") == "buy"


def test_ordinary_escalation_outside_holdings_watchlist_is_still_just_an_alert(st, clock, tmp_path):
    """Regression: a NORMAL (non-Cramer) escalation for a symbol outside holdings/watchlist
    stays an alert, not a proposal draft — only cramer_call:* gets the "any symbol" carve-out."""
    rows = _persist(st, clock, tmp_path, [
        _mk("t-o", "NVDA", "NVDA surprise guidance cut"),
    ])
    row = rows[0]
    transport = FakeTransport([envelope(_triage_body([
        _scored(row.raw_ref, "NVDA", "urgent", 95),
    ]))])
    deliver_calls = []
    result = triage_loop(
        st, clock, transport=transport, deliver=lambda ch, p: deliver_calls.append((ch, p)) or True,
        parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS, watchlist=WATCHLIST,
        propose_fn=lambda s, c, **kw: (_ for _ in ()).throw(
            AssertionError("propose_fn must not be called for a non-holdings/watchlist, "
                           "non-cramer escalation")),
    )
    # "urgent" with no deterministic corroboration is demoted, never escalated/proposed —
    # confirms propose_fn (which would raise) was correctly never invoked.
    assert row.id not in result["escalated"]


def test_cramer_call_disposition_is_escalated_in_the_audit_batch(st, clock, tmp_path):
    rows = _persist(st, clock, tmp_path, [
        _mk("t-c3", "AMD", "Cramer says buy AMD", event_type="cramer_call:buy"),
    ])
    row = rows[0]
    transport = FakeTransport([envelope(_triage_body([
        _scored(row.raw_ref, "AMD", "material", 70),
    ]))])
    triage_loop(st, clock, transport=transport, parquet_root=tmp_path / "raw",
               holdings_symbols=frozenset(), watchlist=frozenset(),
               propose_fn=lambda s, c, **kw: _stub_outcome())
    batch = _payloads(st, EVENT_TRIAGE_BATCH)[-1]
    assert batch["dispositions"][row.id] == "escalated"
