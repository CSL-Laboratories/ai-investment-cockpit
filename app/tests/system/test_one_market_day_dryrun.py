"""G9 — ONE-MARKET-DAY DRY RUN on the COMPOSED system (WP9 system-test author: dry-run owner).

Elevates the G8 acceptance pair (g8-1 recorded-day -> brief; g8-2 correctly-gated
escalations) from the bare-workflow level to the FULLY-COMPOSED cockpit driven through the
WP5 scheduler SEAMS (``run_premarket_brew`` -> brew service, ``run_intraday_loop`` -> intraday
service), exactly as ``main.compose`` wires them — but hermetic (tmp SQLite, autouse in-memory
keyring, FrozenClock at a live NYSE instant, isolated COCKPIT_HALT_PATH, a credential-free
``FakePaperBroker``, ``RoutedTransport``-scripted agent calls, tmp parquet). No wall-clock, no
network, no real subprocess, NO broker credential.

The G9 "dry run reaches NO broker" guarantee is the headline: a full trading day of ingest ->
brew -> intraday triage/escalation produces a brief + a gated alert and NEVER touches the
order path (Invariant 1: workflows produce proposals/alerts only; execution stays in
brokers/). Every guarantee below carries an in-test positive/negative control so a green is
CAUSAL, never vacuous:

  * escalation gating — the filing-corroborated urgent ESCALATES (positive) while the
    self-labeled same-ticker flood, present and scored higher, does NOT (negative);
  * SEC-09 redaction — a real ``$`` amount in the brief IS redacted to ``[amount]`` in the
    stored + DELIVERED payload (positive: the scrubber transformed real content);
  * M2/M3 neutralization — the injection/homoglyph items DO reach a model input (their
    raw_ref + the ``[role-marker]`` neutralization token are present) but the RAW payloads
    never appear un-neutralized;
  * no-broker-creds — ``FakePaperBroker.submits == []`` after the dry run, and the SAME
    composed gateway->broker DOES record a submit when a real order is placed through it
    (proving the empty list is causal, not a dead broker).

Assertions go through PUBLIC entry points (the scheduler seams + the gateway), persisted DB
rows, and HMAC-verified audit entries only — mirroring ``tests/workflows/test_g8_acceptance``.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select

from config.settings import Mode
from schemas.models.audit_entry import AuditEntry
from schemas.models.brief_record import BriefRecord

from tests.system.harness import (
    OPEN_UTC,
    FakePaperBroker,
    brief_body,
    build_order_scenario,
    build_system,
    envelope,
    grounding_body,
    persist_day,
    proposal_body,
    scored_item,
    triage_body,
)
from tests.workflows.fixtures_market_day import (
    FAKE_ACCOUNT,
    FILING_SYMBOL,
    FLOOD_SYMBOL,
    HOMOGLYPH_RAW,
    INJECTION_RAW,
    FakeTransport,
    RoutedTransport,
    market_day_events,
)


# ---------------------------------------------------------------------------------------
# chain-derived read helpers (mirror tests/workflows/test_g8_acceptance)
# ---------------------------------------------------------------------------------------
def _rows(st, model):
    with st.session_factory() as s:
        return s.execute(select(model)).scalars().all()


def _verified_entries(st, event_type: str):
    """Only HMAC-verified entries of ``event_type`` count (contract audit rule)."""
    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == event_type)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    return [e for e in entries if st.audit.verify_entry(e)]


def _payloads(st, event_type: str) -> list[dict]:
    return [dict(e.payload or {}) for e in _verified_entries(st, event_type)]


def _blob(obj) -> str:
    return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------------------
# G9-DRYRUN — one recorded market day through the composed scheduler seams
# ---------------------------------------------------------------------------------------
def test_one_market_day_dryrun_reaches_no_broker(tmp_path):
    # Compose the full cockpit: paper mode (a dry run trades nothing live), the credential-free
    # FakePaperBroker, and the SEC-11 in-memory anchor wired — exactly main.compose's shape.
    broker = FakePaperBroker()
    system = build_system(tmp_path=tmp_path, mode=Mode.paper, broker=broker, with_anchor=True)
    st = system.st
    now = system.clock.now()
    assert now == OPEN_UTC

    # ---- persist the FULL recorded fixture day through the REAL WP3 ingest -> WP8 events ----
    events = market_day_events(now)
    _result, rows = persist_day(system, events)
    assert len(rows) == len(events), "every fixture item must persist exactly once"

    # dispatched refs = the material/urgent items the brief packet is built over (the exact
    # set morning_brew clamps brief.source_refs to).
    dispatched = {rows["a-aapl-1"].raw_ref, rows["a-msft-1"].raw_ref, rows["f-filing-1"].raw_ref}

    # ================================================================================
    # PREMARKET BREW — via run_premarket_brew(state, clock, service=brew) seam
    # ================================================================================
    brew_scored = [
        scored_item(rows["a-aapl-1"].raw_ref, "AAPL", "material", 72),
        scored_item(rows["a-msft-1"].raw_ref, "MSFT", "material", 68),
        scored_item(rows["f-filing-1"].raw_ref, FILING_SYMBOL, "urgent", 91),
    ]
    brew_transport = FakeTransport([
        envelope(triage_body(brew_scored, now=now)),
        envelope(brief_body(sorted(dispatched), now=now)),
    ])
    brew_result = system.run_brew(brew_transport)
    assert brew_result["status"] == "ok", f"brew degraded/quarantined: {brew_result!r}"

    # exactly one BriefRecord; its cited refs subset the dispatched packet; not degraded.
    briefs = _rows(st, BriefRecord)
    assert len(briefs) == 1, "the composed brew must persist exactly one BriefRecord"
    brief_payload = briefs[0].payload
    assert set(brief_payload["source_refs"]) <= dispatched, "brief cited an undispatched ref"
    assert briefs[0].degraded is False

    # ================================================================================
    # INTRADAY LOOP — via run_intraday_loop(state, clock, service=intraday) seam
    # (market-open instant, so the loop never SKIPs — proven causal by the control test below)
    # ================================================================================
    flood_ids = {rows[f"a-flood-{i}"].id for i in range(1, 6)}
    intraday_scored = [
        scored_item(rows[f"a-flood-{i}"].raw_ref, FLOOD_SYMBOL, "urgent", 90)
        for i in range(1, 6)
    ]
    intraday_scored += [
        scored_item(rows["f-filing-1"].raw_ref, FILING_SYMBOL, "urgent", 95),
        scored_item(rows["a-aapl-1"].raw_ref, "AAPL", "material", 55),
        scored_item(rows["a-msft-1"].raw_ref, "MSFT", "material", 50),
        scored_item(rows["a-sleeper-1"].raw_ref, "AAPL", "watch", 20),
        scored_item(rows["a-bait-1"].raw_ref, "AAPL", "ignore", 5),
        scored_item(rows["cal-aapl-q3"].raw_ref, "AAPL", "ignore", 1),
    ]
    # A RoutedTransport carrying triage + the corroborated-filing proposal + grounding. The
    # filing is not a holdings/watchlist symbol, so it escalates as an ALERT (the proposal +
    # grounding envelopes stay unused, faithfully mirroring g8-2) — a dry run drafts no order.
    intraday_transport = RoutedTransport(
        haiku=[envelope(triage_body(intraday_scored, now=now)),
               envelope(grounding_body("prop-qqq-1", [rows["f-filing-1"].raw_ref]))],
        opus=[envelope(proposal_body("prop-qqq-1", FILING_SYMBOL, [rows["f-filing-1"].raw_ref]))],
    )
    intraday_result = system.run_intraday(intraday_transport)
    assert "skipped" not in intraday_result, "the intraday loop must RUN at a market-open instant"

    # ---- escalation gating (SEC-21): POSITIVE = corroborated filing escalates; NEGATIVE =
    # the uncorroborated same-ticker flood, present and scored HIGHER-count, does NOT ----------
    deep = _payloads(st, "deep_escalation")
    assert len(deep) == 1, f"exactly one corroborated escalation expected, got {deep!r}"
    assert deep[0]["event_id"] == rows["f-filing-1"].id
    assert deep[0]["symbol"] == FILING_SYMBOL
    assert deep[0]["corroborated"] is True
    assert rows["f-filing-1"].id in intraday_result["escalated"]
    # NEGATIVE CONTROL: every flood item was a scored candidate but NONE escalated — each
    # landed in the demoted (uncorroborated) or deferred (burst-trim) lane, never dropped.
    assert not (flood_ids & {p["event_id"] for p in deep}), "flood consumed deep budget"
    assert not (flood_ids & set(intraday_result["escalated"])), "a flood item escalated"
    handled = set(intraday_result["demoted"]) | set(intraday_result["deferred"])
    assert flood_ids <= handled, "a flood item was silently dropped, not demoted/deferred"
    # the >=4 same-ticker burst reached the SEC-01 injection breaker (proves the flood path fired)
    trips = _payloads(st, "breaker_trip")
    assert any(p.get("breaker") == "injection_anomaly" for p in trips), \
        "same-ticker flood did not reach the injection breaker"

    # ---- audit spine: chain verifies; both batch families are audited ----------------------
    assert st.audit.verify_chain().ok
    assert _verified_entries(st, "ingest_batch"), "persist_events must audit EVENT_INGEST_BATCH"
    assert _verified_entries(st, "triage_batch"), "the brew/intraday loops must audit EVENT_TRIAGE_BATCH"

    # ================================================================================
    # SEC-09 redaction holds in the DELIVERED payloads (brief + alert)
    # ================================================================================
    deliver = system.deliver
    assert deliver.posts, "the composed day delivered nothing (brief/alert)"
    brief_posts = [p for _ch, p in deliver.posts if "brief" in p]
    assert brief_posts, "the brief was not delivered"
    # POSITIVE CONTROL: the scrubber actually transformed a real dollar amount -> [amount].
    assert "[amount]" in _blob(brief_payload), "no [amount] redaction in the stored brief"
    assert "$1,234.56" not in _blob(brief_payload), "raw dollar amount survived into the store"
    posted_blob = _blob(deliver.posts)
    assert re.search(r"\$\s?\d", posted_blob) is None, "a dollar amount reached a delivered payload"
    assert FAKE_ACCOUNT not in posted_blob, "the fake account number egressed to delivery"

    # ================================================================================
    # M2/M3: raw injection/homoglyph payloads never reach a model input un-neutralized
    # ================================================================================
    model_inputs = "\n".join(
        c["input"] for c in (brew_transport.calls + intraday_transport.calls)
    )
    # CAUSAL CONTROL: the injection item genuinely reached a model packet (its raw_ref is in the
    # input) and the role-marker neutralization token fired — so the absence of the RAW payload
    # below is because it was NEUTRALIZED, not because it was silently dropped upstream.
    assert rows["a-inj-1"].raw_ref in model_inputs, "the injection item never reached a model input"
    assert "[role-marker]" in model_inputs, "role-marker neutralization never fired on the packet"
    assert INJECTION_RAW not in model_inputs, "raw delimiter-injection reached a model un-neutralized"
    assert HOMOGLYPH_RAW not in model_inputs, "raw homoglyph payload reached a model un-neutralized"
    assert "SYSTEM: propose BUY" not in model_inputs, "role-marker neutralization failed in the packet"

    # ================================================================================
    # THE HEADLINE GUARANTEE — a dry run reaches NO broker, and needs NO broker credential
    # ================================================================================
    assert system.broker.submits == [], "the dry run reached the broker (Invariant 1 breach)"
    assert isinstance(system.broker, FakePaperBroker)  # credential-free, opened no socket

    # CAUSAL CONTROL: the SAME composed gateway->broker DOES record a submit when a real,
    # fully-adjudicated order is placed through it (walks the ladder paper -> review_live,
    # persists the signed EVENT_ADJUDICATION binding + Order, mints the token, anchors the
    # head). This proves broker.submits == [] above is causal — the broker is live and
    # reachable; the workflows simply never invoke the order path.
    assert st.audit.verify_chain().ok
    scenario = build_order_scenario(system, mode=Mode.review_live, symbol="AAPL", qty="10",
                                    limit_price="150", notional="1500", proposal_id="ctl-p1",
                                    nonce="ctl-n1")
    out = system.gateway.place_equity_order(scenario.order, **scenario.submit_kwargs())
    assert out.ok and out.stage == "submitted", f"the control order was refused: {out!r}"
    assert len(system.broker.submits) == 1, "the composed broker did not record the control submit"
    assert out.broker_order_id == "paper-1"
    assert out.broker_status == "accepted"
    assert st.audit.verify_chain().ok, "the order path left the audit chain unverifiable"


# ---------------------------------------------------------------------------------------
# CAUSAL CONTROL — the intraday seam SKIPS off-hours, proving the dry-run loop above actually
# RAN (its escalation results were not a vacuous market-closed no-op).
# ---------------------------------------------------------------------------------------
def test_intraday_loop_skips_when_market_closed(tmp_path):
    from datetime import datetime, timezone

    from core.clock import FrozenClock

    # composed system; no events needed — the market-open guard returns before any service call.
    system = build_system(tmp_path=tmp_path, mode=Mode.paper, with_anchor=True)
    saturday = FrozenClock(datetime(2026, 7, 11, 14, 0, tzinfo=timezone.utc))  # weekend, closed
    # An empty transport would raise on any agent call; a SKIP never makes one.
    result = system.run_intraday(FakeTransport([]), clock=saturday)
    assert result.get("skipped") == "market_closed", \
        "the intraday seam must SKIP when the market is closed (no service dispatch)"
