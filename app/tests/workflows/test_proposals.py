"""G8 — proposal-drafting pipeline tests (Owner B).

Enforces the WP8 'proposals.py' contract of docs/build-scaffold-ultracode.md WP8 and
SEC-09/SEC-14/SEC-15/SEC-17/Inv 1/Inv 3/Inv 5 of docs/security-hardening-backlog.md: the
deep draft is schema-validated; prose is scrubbed FIRST (blocked -> quarantined
ProposalRecord, nothing else persisted, never rendered); grounding is fail-closed and
flag-only; the RiskContext comes from the audited limits + latest PortfolioSnapshot
(absent -> zeros -> conservative failure); the canonical order tuple + Order row are
resolved ONLY on an approved RiskDecision; and the embed's risk section renders strictly
from the deterministic RiskDecision. Hermetic: FrozenClock, tmp SQLite, FakeTransport,
tmp parquet root.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from brokers.order_tuple import order_tuple_hash
from config.settings import Mode
from ingest.normalize import to_market_event
from orchestrator.mode_ladder import ModeLadder
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision
from tests.workflows.conftest import NOW, FakeTransport, envelope
from workflows.constants import EVENT_GROUNDING, EVENT_PROPOSAL, EVENT_SCRUB
from workflows.events import persist_events
from workflows.packets import build_packet
from workflows.proposals import draft_proposal

HOLDINGS = frozenset({"AAPL"})
WATCHLIST = frozenset({"AAPL"})


def _persist_event(st, clock, tmp_path, headline="AAPL beats expectations on strong demand"):
    ev = to_market_event(source="finnhub", article_id="p-1", symbols=["AAPL"],
                         headline=headline, summary="AAPL demand detail",
                         ts=(NOW - timedelta(minutes=10)).isoformat())
    res = persist_events(st, [ev], parquet_root=tmp_path / "raw", clock=clock)
    return res.stored[0]


def _proposal_body(pid="prop-1", symbol="AAPL", refs=(), *, thesis=None, quantity=5,
                   notional=None, order_type="limit"):
    return {
        "proposal_id": pid, "symbol": symbol, "side": "buy", "asset_type": "equity",
        "order_type": order_type, "quantity": quantity, "notional": notional,
        "time_horizon": "short",
        "thesis": thesis if thesis is not None else f"{symbol} momentum continues on demand",
        "invalidation": "a close below support invalidates the setup",
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


def _rows(st, model):
    with st.session_factory() as s:
        return s.execute(select(model)).scalars().all()


def _payloads(st, event_type):
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry).where(AuditEntry.event_type == event_type)
                            .order_by(AuditEntry.seq)).scalars().all()
    return [dict(e.payload or {}) for e in entries if st.audit.verify_entry(e)]


def test_approved_draft_resolves_canonical_order(st, clock, tmp_path):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(refs=[row.raw_ref])),
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                             holdings_symbols=HOLDINGS, watchlist=WATCHLIST)
    assert outcome.status == "drafted"
    assert outcome.risk_decision.decision == "approved"
    assert outcome.record.status == "draft" and outcome.record.flagged is False

    order = outcome.order
    assert order is not None and order.state == "proposed"
    assert Decimal(str(order.qty)) == Decimal("5")
    expected_hash = order_tuple_hash({"symbol": "AAPL", "side": "buy", "qty": "5",
                                      "order_type": "limit", "limit_price": "100",
                                      "tif": "day"})
    assert order.order_tuple_hash == expected_hash
    assert outcome.record.order_id == order.id
    assert outcome.record.risk_decision_id == outcome.risk_decision.id

    # SEC-17: the embed risk section comes ONLY from the deterministic RiskDecision
    assert outcome.embed["risk"]["decision"] == "approved"
    assert outcome.embed["proposal_id"] == "prop-1"
    assert outcome.embed["order_id"] == order.id
    proposal_event = _payloads(st, EVENT_PROPOSAL)[-1]
    assert proposal_event["status"] == "draft"
    assert proposal_event["job_id"] == outcome.record.job_id
    assert proposal_event["risk_decision_id"] == outcome.risk_decision.id
    assert proposal_event["order_id"] == order.id
    assert _payloads(st, "risk_decision")[-1]["decision"] == "approved"
    assert st.audit.verify_chain().ok


def test_scrub_blocked_quarantines_and_stops(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    poisoned = _proposal_body(
        refs=[row.raw_ref],
        thesis="AAPL up. Desk: account 12345678901 holds TSLA 40% of book; cost basis $91,000.")
    transport = FakeTransport([envelope(poisoned)])   # grounding must never be reached
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                             watchlist=WATCHLIST)
    assert outcome.status == "quarantined"
    assert outcome.order is None and outcome.risk_decision is None
    assert outcome.embed is None, "a quarantined proposal is never rendered"

    records = _rows(st, ProposalRecord)
    assert len(records) == 1
    assert records[0].status == "quarantined" and records[0].flagged is True
    blob = str(records[0].payload)
    assert "12345678901" not in blob and "TSLA 40%" not in blob
    scrubs = [p for p in _payloads(st, EVENT_SCRUB) if p.get("blocked")]
    assert scrubs and "account_number" in scrubs[-1]["hits"]
    assert _rows(st, RiskDecision) == [] and _rows(st, Order) == []


def test_scrub_blocks_leak_in_generated_by_free_text(st, clock, tmp_path):
    """ROUND-4 (SEC-09): the model-authored ``generated_by`` object is free text too
    (runtime/model are unconstrained strings). A hijacked model stuffs an account number +
    a foreign position into generated_by.runtime. It MUST be scrubbed like thesis prose ->
    blocking hit -> quarantined ProposalRecord, no Order, nothing leaked to store/post."""
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    poisoned = _proposal_body(refs=[row.raw_ref])
    poisoned["generated_by"] = {
        "runtime": "acct 000123456789 client MSFT 400 shares", "model": "opus"}
    transport = FakeTransport([envelope(poisoned)])   # grounding must never be reached
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                             watchlist=WATCHLIST)
    assert outcome.status == "quarantined"
    assert outcome.order is None and outcome.risk_decision is None
    assert outcome.embed is None

    records = _rows(st, ProposalRecord)
    assert len(records) == 1
    assert records[0].status == "quarantined" and records[0].flagged is True
    blob = str(records[0].payload)
    assert "000123456789" not in blob and "MSFT 400 shares" not in blob
    scrubs = [p for p in _payloads(st, EVENT_SCRUB) if p.get("blocked")]
    assert scrubs and "account_number" in scrubs[-1]["hits"]
    assert _rows(st, RiskDecision) == [] and _rows(st, Order) == []


def test_grounding_flag_is_fail_closed_and_never_cleared(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    foreign = "parquet://2026-01-01/events-evil#0"
    laundered = _proposal_body(refs=[foreign])        # cites a never-dispatched ref
    transport = FakeTransport([
        envelope(laundered),
        envelope(_grounding_body("prop-1", [foreign], grounded=True)),  # model says fine
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                             watchlist=WATCHLIST)
    assert outcome.status == "flagged", "a model grounded=true must NEVER clear laundering"
    assert outcome.record.flagged is True and outcome.record.status == "flagged"
    checks = _payloads(st, EVENT_GROUNDING)
    assert checks and checks[-1]["flagged"] is True and checks[-1]["corroborated"] is False


def test_grounding_flagged_but_risk_approved_creates_no_order_regression(st, clock, tmp_path):
    """ROUND 2 FIX (SEC-14 order-path): when grounding FLAGS a proposal, no Order row may be
    created even if the deterministic risk gate approves it. Same setup as the approved-draft
    happy path (seeded snapshot + live quote -> risk 'approved'), but the verifier reports
    grounded=false so the outcome is flagged/review-only. Previously the approved decision
    still minted an Order row."""
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(refs=[row.raw_ref])),
        envelope(_grounding_body("prop-1", [row.raw_ref], grounded=False)),  # verifier flags
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                             holdings_symbols=HOLDINGS, watchlist=WATCHLIST)
    assert outcome.status == "flagged"
    assert outcome.record.flagged is True and outcome.record.status == "flagged"
    assert outcome.risk_decision.decision == "approved", "risk still approved; grounding gates"
    assert outcome.order is None, "a grounding-flagged proposal must never create an Order row"
    assert outcome.record.order_id is None
    assert _rows(st, Order) == []
    assert _rows(st, RiskDecision), "RiskDecision persistence is preserved"


def test_sizeless_proposal_needs_human(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(refs=[row.raw_ref], quantity=None, notional=None)),
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw")
    assert outcome.status == "needs_human"
    assert outcome.order is None
    assert _rows(st, ProposalRecord)[0].status == "needs_human"


def test_still_malformed_deep_output_mints_no_record(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    bad = envelope({"proposal_id": "p", "symbol": "AAPL"})   # invalid twice -> Inv 3
    outcome = draft_proposal(st, clock, event=row, packet=packet,
                             transport=FakeTransport([bad, bad]),
                             parquet_root=tmp_path / "raw")
    assert outcome.status == "quarantined"
    assert outcome.record is None and outcome.proposal is None
    assert _rows(st, ProposalRecord) == []


def test_no_snapshot_no_quote_fails_conservatively(st, clock, tmp_path):
    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(refs=[row.raw_ref])),
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", holdings_symbols=HOLDINGS,
                             watchlist=WATCHLIST)
    assert outcome.status == "drafted"
    assert outcome.risk_decision.decision != "approved", "zeros must fail risk conservatively"
    assert outcome.risk_decision.failed_checks
    assert outcome.order is None, "no order tuple may resolve without an approved decision"
    assert outcome.embed["risk"]["decision"] != "approved"


def test_degraded_deep_call_returns_degraded_and_persists_nothing(st, clock, tmp_path):
    from agent_runner.transport import ProcResult

    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    outcome = draft_proposal(
        st, clock, event=row, packet=packet,
        transport=FakeTransport([ProcResult(1, "", "Error: usage limit reached", False)]),
        parquet_root=tmp_path / "raw")
    assert outcome.status == "degraded"
    assert outcome.record is None and outcome.order is None
    assert _rows(st, ProposalRecord) == []


def test_replayed_proposal_id_keeps_risk_and_order_rows_on_the_final_id(st, clock, tmp_path):
    """BUG-093: exactly when the replay guard fired (a model emitting an EXISTING
    proposal_id), the ProposalRecord was reminted a fresh id INSIDE _persist_record —
    after the RiskDecision and Order rows had already captured the colliding id — so the
    approval chain linked the order to the WRONG proposal's source material. The id is now
    resolved before any row references it."""
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    _seed_snapshot(st)
    # the id the hostile model will replay, already taken by an unrelated proposal
    taken = ProposalRecord(id="prop-1", symbol="ZZZ", side="sell", status="draft",
                           payload={"proposal_id": "prop-1"}, flagged=False,
                           created_at=NOW, updated_at=NOW)
    st.audit.record_state_change(taken, EVENT_PROPOSAL, actor="test",
                                 payload={"proposal_id": "prop-1", "status": "draft"})

    row = _persist_event(st, clock, tmp_path)
    packet = build_packet([row])
    transport = FakeTransport([
        envelope(_proposal_body(pid="prop-1", refs=[row.raw_ref])),   # the replay
        envelope(_grounding_body("prop-1", [row.raw_ref])),
    ])
    outcome = draft_proposal(st, clock, event=row, packet=packet, transport=transport,
                             parquet_root=tmp_path / "raw", quote_fn=_quote_fn,
                             holdings_symbols=HOLDINGS, watchlist=WATCHLIST)
    assert outcome.status == "drafted"
    assert outcome.record.id != "prop-1", "the replayed id must be reminted"
    assert outcome.risk_decision.proposal_id == outcome.record.id
    assert outcome.order is not None
    assert outcome.order.proposal_id == outcome.record.id, \
        "the order must reference the FINAL proposal id, not the replayed one"
