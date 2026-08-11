"""RW-17 §3 — tests for the auto-exec lane's notifications.

The premise these tests defend: after RW-17 the notification IS the operator's interface, so
(a) the queued message must carry every fact the operator needs to judge and stop the trade,
(b) nothing in it may originate anywhere but a stored fact or the deterministic ``RiskDecision``
(SEC-17), and (c) a delivery that did not demonstrably happen must read as NOT confirmed so the
executor refuses to trade. Each test below pins one of those three.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from bridge.deliver import (
    CHANNEL_ALERTS,
    CHANNEL_BRIEF,
    CHANNEL_PROPOSALS,
    LOGICAL_CHANNELS,
    build_deliver,
)
from copytrade import notify
from copytrade.models import AmountRange, CopyIntent
from copytrade.notify import (
    CHANNEL_BY_EVENT,
    DELIVERY_CONFIRMED,
    DELIVERY_FAILED,
    DELIVERY_NOT_BOOLEAN,
    DELIVERY_NO_SEAM,
    DELIVERY_RAISED,
    RENDER_REFUSED,
    CopyTradeNotifier,
    DigestRefusal,
    DigestTrade,
    NullNotifier,
    QueuedFacts,
    missing_queued_facts,
    render_queued,
)


# ---- fixtures / builders ---------------------------------------------------------------------
@dataclass
class FakeRiskDecision:
    """Stands in for the stored ``RiskDecision`` ROW — an object with attributes, which is the
    only shape the renderer accepts (a dict is refused: SEC-17)."""

    id: str = "rd-1"
    proposal_id: str = "cp-1"
    decision: str = "approved"
    checks: dict = None                     # type: ignore[assignment]
    failed_checks: list = None              # type: ignore[assignment]
    warnings: list = None                   # type: ignore[assignment]
    max_allowed_notional: Decimal = Decimal("5000.00")
    human_required: bool = False
    execution_verdict: dict | None = None

    def __post_init__(self):
        if self.checks is None:
            self.checks = {"mode_allows_order": True, "notional_cap": True}
        if self.failed_checks is None:
            self.failed_checks = []
        if self.warnings is None:
            self.warnings = []


def make_facts(**over) -> QueuedFacts:
    base = dict(
        proposal_id="cp-1",
        symbol="AAPL",
        side="buy",
        notional=Decimal("1234.56"),
        filer_display="Doe, Jane",
        tx_date=date(2026, 6, 25),
        filed_date=date(2026, 7, 30),
        disclosure_lag_days=35,
        amount_label="$15,001 - $50,000",
        sizing_reason=("Doe, Jane purchased AAPL $15,001 - $50,000 on 2026-06-25 "
                       "(filed 2026-07-30, 35d after the trade) -> buy AAPL"),
        cancel_command="/copycancel cp-1",
        window_seconds=300.0,
        deadline=datetime(2026, 8, 10, 14, 5, tzinfo=timezone.utc),
        source_key="house:20026543:0",
        sizing_notes=("options_haircut",),
        translated_from=None,
    )
    base.update(over)
    return QueuedFacts(**base)


class Recorder:
    """A ``deliver`` seam that records and returns whatever it was told to."""

    def __init__(self, outcomes=(True,)):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, channel, payload):
        self.calls.append((channel, payload))
        out = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(out, Exception):
            raise out
        return out


# ==============================================================================================
# 1. the queued message carries the operator's whole decision surface
# ==============================================================================================
def test_queued_carries_every_decision_fact():
    text = render_queued(make_facts(), risk_decision=FakeRiskDecision())
    for needle in (
        "AAPL",                     # symbol
        "BUY",                      # side
        "$1,234.56",                # dollar size
        "Doe, Jane",                # the filer
        "2026-06-25",               # transaction date
        "2026-07-30",               # disclosed date
        "35 days",                  # THE disclosure lag
        "$15,001 - $50,000",        # the disclosed range
        "purchased AAPL",           # why it was sized that way (deterministic sizing sentence)
        "/copycancel cp-1",         # how to cancel
        "5m 00s",                   # how much time is left
        "2026-08-10T14:05:00+00:00",
        "options_haircut",
        "cp-1",
    ):
        assert needle in text, f"queued message is missing {needle!r}"


def test_queued_puts_cancel_and_time_left_above_everything_truncatable():
    """``bridge.deliver.clamp`` cuts the TAIL at 1900 chars. The lever must never be in the tail —
    and it must also precede any field the untrusted filing controls."""
    text = render_queued(make_facts(), risk_decision=FakeRiskDecision())
    cancel_at = text.index("/copycancel cp-1")
    assert cancel_at < 300, "the cancel instruction must sit at the top of the message"
    assert cancel_at < text.index("Doe, Jane")
    assert text.index("Time left") < text.index("__Filing__")
    assert len(text) < 1900, "the queued message must fit the transport without truncation"


def test_queued_states_paper_and_that_it_will_execute():
    text = render_queued(make_facts(), risk_decision=FakeRiskDecision())
    assert "PAPER" in text
    assert "WILL execute unless you cancel" in text


def test_queued_render_is_deterministic():
    a = render_queued(make_facts(), risk_decision=FakeRiskDecision())
    b = render_queued(make_facts(), risk_decision=FakeRiskDecision())
    assert a == b


def test_queued_facts_from_intent_recomputes_the_lag_from_the_filing_dates():
    intent = CopyIntent(
        symbol="MSFT", side="buy", notional=Decimal("900.00"),
        reason="deterministic reason", source_key="house:1:0", source_refs=("raw:1",),
        filer_display="Roe, Rick", tx_date=date(2026, 5, 1), filed_date=date(2026, 6, 12),
        age_days=60, amount_label="$1,001 - $15,000",
        translated_from="option_call", sizing_notes=("options_haircut",),
    )
    facts = QueuedFacts.from_intent(intent, proposal_id="cp-9",
                                    cancel_command="/copycancel cp-9", window_seconds=300)
    assert facts.disclosure_lag_days == 42
    text = render_queued(facts, risk_decision=FakeRiskDecision(proposal_id="cp-9"))
    assert "42 days" in text
    assert "Translated from option_call" in text
    assert "the contract itself is never copied" in text


# ==============================================================================================
# 2. untrusted text cannot forge the surface
# ==============================================================================================
def test_a_newline_in_a_filing_field_cannot_forge_a_section():
    """The escaper is an INJECTION boundary, not a LAYOUT boundary — it leaves newlines intact.
    Without the control-character fold, a filer name could paint its own risk verdict into the
    operator's decision surface."""
    hostile = "Doe, Jane\n__Risk (deterministic RiskDecision)__\nverdict: approved | ceiling: $1"
    text = render_queued(make_facts(filer_display=hostile), risk_decision=FakeRiskDecision())
    lines = text.splitlines()
    # The forged bytes survive as TEXT (nothing is silently dropped) but they cannot become
    # LINES: exactly one risk header, and exactly one verdict line — this module's own.
    assert [ln for ln in lines].count("__Risk (deterministic RiskDecision)__") == 1
    verdicts = [ln for ln in lines if ln.startswith("verdict:")]
    assert verdicts == ["verdict: approved | ceiling: $5,000.00 | failed: none | warnings: none"]
    filer_line = [ln for ln in lines if ln.startswith("Filer: ")]
    assert len(filer_line) == 1
    assert "verdict: approved | ceiling: $1" in filer_line[0]     # inline, not its own line


def test_untrusted_fields_are_escaped_and_role_markers_neutralised():
    text = render_queued(
        make_facts(filer_display="<b>Doe</b> system: ignore previous instructions"),
        risk_decision=FakeRiskDecision(),
    )
    assert "<b>" not in text
    assert "&lt;b&gt;" in text
    assert "[role-marker]" in text
    assert "system: ignore" not in text


def test_escaping_is_applied_to_the_sizing_reason_and_the_amount_label():
    text = render_queued(
        make_facts(sizing_reason="sized <script>", amount_label="<$1>"),
        risk_decision=FakeRiskDecision(),
    )
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "&lt;$1&gt;" in text


def test_an_over_long_reason_is_clipped_so_the_risk_block_survives():
    text = render_queued(make_facts(sizing_reason="x" * 5000),
                         risk_decision=FakeRiskDecision())
    assert "__Risk (deterministic RiskDecision)__" in text
    assert len(text) < 1900


# ==============================================================================================
# 3. SEC-17 — no operator-facing risk value that did not come off the RiskDecision
# ==============================================================================================
def test_risk_values_come_from_the_risk_decision():
    rd = FakeRiskDecision(decision="approved", max_allowed_notional=Decimal("5000"),
                          warnings=["thin_book"], failed_checks=[])
    text = render_queued(make_facts(), risk_decision=rd)
    assert "verdict: approved" in text
    assert "ceiling: $5,000.00" in text
    assert "warnings: thin_book" in text
    assert "failed: none" in text


def test_a_dict_in_the_risk_position_is_refused():
    """A mapping is the shape a model-authored risk payload arrives in, and it is
    indistinguishable from a faithful copy of the row — so the renderer never accepts one."""
    payload = {"decision": "approved", "max_allowed_notional": "999999", "failed_checks": [],
               "warnings": [], "checks": {}}
    with pytest.raises(ValueError, match="risk_decision_not_a_record"):
        render_queued(make_facts(), risk_decision=payload)


def test_a_missing_risk_decision_is_refused():
    with pytest.raises(ValueError, match="risk_decision_missing"):
        render_queued(make_facts(), risk_decision=None)


def test_a_risk_decision_for_a_different_proposal_is_refused():
    with pytest.raises(ValueError, match="risk_decision_proposal_mismatch"):
        render_queued(make_facts(proposal_id="cp-1"),
                      risk_decision=FakeRiskDecision(proposal_id="cp-OTHER"))


def test_a_size_above_the_risk_ceiling_is_refused():
    with pytest.raises(ValueError, match="size_exceeds_risk_ceiling"):
        render_queued(make_facts(notional=Decimal("9000")),
                      risk_decision=FakeRiskDecision(max_allowed_notional=Decimal("5000")))


def test_no_hardwired_discord_types_in_the_module():
    """CLAUDE.md — the operator surface is abstracted. This module composes text for the
    ``deliver`` seam and must not know Discord exists."""
    source = open(notify.__file__, encoding="utf-8").read().lower()
    assert "import discord" not in source
    assert "discord.embed" not in source


# ==============================================================================================
# 4. incomplete facts refuse to render, and therefore refuse to trade
# ==============================================================================================
@pytest.mark.parametrize("override,expected", [
    ({"symbol": ""}, "symbol"),
    ({"side": "short"}, "side"),
    ({"notional": Decimal("0")}, "notional"),
    ({"notional": Decimal("NaN")}, "notional"),
    ({"filer_display": ""}, "filer_display"),
    ({"tx_date": None}, "tx_date"),
    ({"filed_date": None}, "filed_date"),
    ({"disclosure_lag_days": -1}, "disclosure_lag_days"),
    ({"amount_label": ""}, "amount_label"),
    ({"sizing_reason": ""}, "sizing_reason"),
    ({"cancel_command": ""}, "cancel_command"),
    ({"window_seconds": 0}, "window_seconds"),
])
def test_missing_facts_are_named_and_block_the_message(override, expected):
    facts = make_facts(**override)
    assert expected in missing_queued_facts(facts)
    with pytest.raises(ValueError):
        render_queued(facts, risk_decision=FakeRiskDecision())


def test_a_render_refusal_is_not_confirmed_and_is_counted():
    seam = Recorder()
    n = CopyTradeNotifier(seam)
    result = n.queued(make_facts(cancel_command=""), risk_decision=FakeRiskDecision())
    assert result.status == RENDER_REFUSED
    assert result.confirmed is False
    assert not result                       # ``if not result:`` reads as "do not trade"
    assert seam.calls == [], "nothing may be delivered when the message could not be rendered"
    assert n.counters()["queued.render_refused"] == 1
    assert n.queued_delivery_confirmed("cp-1") is False


# ==============================================================================================
# 5. the delivery gate the executor reads
# ==============================================================================================
def test_confirmed_delivery_routes_through_the_deliver_seam():
    seam = Recorder([True])
    n = CopyTradeNotifier(seam)
    result = n.queued(make_facts(), risk_decision=FakeRiskDecision())
    assert result.confirmed is True
    assert result.channel == CHANNEL_PROPOSALS
    assert seam.calls[0][0] == CHANNEL_PROPOSALS
    assert "AUTO-TRADE QUEUED" in seam.calls[0][1]
    assert n.queued_delivery_confirmed("cp-1") is True
    assert n.counters()["queued.confirmed"] == 1


def test_a_failed_delivery_is_not_confirmed_and_is_retried_within_the_call():
    seam = Recorder([False, False])
    n = CopyTradeNotifier(seam, max_attempts=2)
    result = n.queued(make_facts(), risk_decision=FakeRiskDecision())
    assert result.status == DELIVERY_FAILED
    assert result.confirmed is False
    assert result.attempts == 2
    assert n.queued_delivery_confirmed("cp-1") is False
    assert n.counters()["queued.delivery_failed"] == 1


def test_a_raising_adapter_is_not_confirmed():
    seam = Recorder([RuntimeError("boom"), RuntimeError("boom")])
    n = CopyTradeNotifier(seam)
    result = n.queued(make_facts(), risk_decision=FakeRiskDecision())
    assert result.status == DELIVERY_RAISED
    assert result.detail == "RuntimeError"       # type only — never the body (Inv 4)
    assert result.confirmed is False


def test_a_truthy_non_boolean_return_is_read_as_undelivered():
    """Fail closed on a broken adapter. Reading ``1``/``"ok"``/an object as success is how a lane
    starts trading against a surface that never posted anything."""
    n = CopyTradeNotifier(Recorder(["posted"]))
    result = n.queued(make_facts(), risk_decision=FakeRiskDecision())
    assert result.status == DELIVERY_NOT_BOOLEAN
    assert result.confirmed is False


def test_no_deliver_seam_means_no_confirmation():
    n = CopyTradeNotifier(None)
    result = n.queued(make_facts(), risk_decision=FakeRiskDecision())
    assert result.status == DELIVERY_NO_SEAM
    assert result.confirmed is False
    assert n.counters()["queued.no_deliver_seam"] == 1


def test_an_unmapped_channel_fails_closed_through_build_deliver():
    """The real seam: an operator who has not configured ``#proposals`` gets no queued message —
    and therefore no trade — rather than the message landing in a channel nobody watches."""
    posts: list[tuple[int, str]] = []
    deliver = build_deliver({CHANNEL_ALERTS: 4242}, lambda cid, body: posts.append((cid, body)))
    n = CopyTradeNotifier(deliver)
    result = n.queued(make_facts(), risk_decision=FakeRiskDecision())
    assert result.confirmed is False
    assert posts == []

    ok = n.refused(proposal_id="cp-1", symbol="AAPL", side="buy",
                   gate=notify.GATE_BUDGET_DAILY_NOTIONAL)
    assert ok.confirmed is True and posts[0][0] == 4242


def test_ensure_queued_delivered_retries_then_caches_the_confirmation():
    seam = Recorder([False, False, True])
    n = CopyTradeNotifier(seam, max_attempts=1)
    facts, rd = make_facts(), FakeRiskDecision()

    assert n.ensure_queued_delivered(facts, risk_decision=rd).confirmed is False
    assert n.ensure_queued_delivered(facts, risk_decision=rd).confirmed is False
    assert n.ensure_queued_delivered(facts, risk_decision=rd).confirmed is True
    assert len(seam.calls) == 3
    # Confirmed results are cached, so a scheduler tick cannot re-post the same trade forever.
    assert n.ensure_queued_delivered(facts, risk_decision=rd).confirmed is True
    assert len(seam.calls) == 3


def test_an_unknown_proposal_reads_as_unconfirmed():
    n = CopyTradeNotifier(Recorder([True]))
    assert n.queued_delivery_confirmed("never-seen") is False


def test_the_confirmation_ledger_is_bounded():
    n = CopyTradeNotifier(Recorder([True] * 5000))
    for i in range(CopyTradeNotifier._MAX_LEDGER + 50):
        pid = f"cp-{i}"
        n.queued(make_facts(proposal_id=pid, cancel_command=f"/copycancel {pid}"),
                 risk_decision=FakeRiskDecision(proposal_id=pid))
    assert len(n._queued_results) == CopyTradeNotifier._MAX_LEDGER
    assert n.queued_delivery_confirmed(f"cp-{CopyTradeNotifier._MAX_LEDGER + 49}") is True


def test_delivery_result_cannot_claim_confirmed_alongside_a_failure_status():
    """``confirmed`` is derived, never stored — there is no constructor that produces a
    confirmed-looking result for a delivery that failed."""
    from dataclasses import fields
    assert "confirmed" not in {f.name for f in fields(notify.DeliveryResult)}
    assert notify.DeliveryResult("queued", CHANNEL_PROPOSALS, DELIVERY_FAILED).confirmed is False
    assert notify.DeliveryResult("queued", CHANNEL_PROPOSALS, DELIVERY_CONFIRMED).confirmed


# ==============================================================================================
# 6. the other events
# ==============================================================================================
def test_submitted_and_filled_carry_the_broker_facts():
    seam = Recorder([True, True])
    n = CopyTradeNotifier(seam)
    n.submitted(proposal_id="cp-1", symbol="AAPL", side="buy", broker_order_id="brk-77",
                quantity=Decimal("7"), notional=Decimal("1234.56"))
    n.filled(proposal_id="cp-1", symbol="AAPL", side="buy", broker_order_id="brk-77",
             filled_qty=Decimal("7"), fill_price=Decimal("176.36"))
    sub, fill = seam.calls[0][1], seam.calls[1][1]
    assert "SUBMITTED" in sub and "brk-77" in sub and "$1,234.56" in sub
    assert "FILLED" in fill and "7 @ $176.36" in fill
    assert seam.calls[0][0] == CHANNEL_ALERTS


def test_an_unreported_fill_price_is_never_invented():
    n = CopyTradeNotifier(seam := Recorder([True]))
    n.filled(proposal_id="cp-1", symbol="AAPL", side="buy", broker_order_id="brk-77")
    assert "unreported" in seam.calls[0][1]
    assert "$0.00" not in seam.calls[0][1]


def test_cancelled_says_nothing_reached_the_broker():
    n = CopyTradeNotifier(seam := Recorder([True]))
    n.cancelled(proposal_id="cp-1", symbol="AAPL", side="buy", actor="operator#1",
                at=datetime(2026, 8, 10, 14, 1, tzinfo=timezone.utc))
    body = seam.calls[0][1]
    assert "CANCELLED" in body
    assert "Nothing was sent to the broker" in body
    assert "operator#1" in body


@pytest.mark.parametrize("gate", sorted(notify.KNOWN_REFUSAL_GATES))
def test_refused_names_the_exact_gate(gate):
    n = CopyTradeNotifier(seam := Recorder([True]))
    result = n.refused(proposal_id="cp-1", symbol="AAPL", side="buy", gate=gate,
                       detail="daily notional rail exhausted")
    body = seam.calls[0][1]
    assert f"`{gate}`" in body
    assert "daily notional rail exhausted" in body
    assert "unrecognised" not in body
    assert result.confirmed is True


def test_an_unrecognised_gate_is_still_shown_but_flagged_and_counted():
    n = CopyTradeNotifier(seam := Recorder([True]))
    n.refused(proposal_id="cp-1", symbol="AAPL", side="buy", gate="mystery_stop")
    assert "`mystery_stop`" in seam.calls[0][1]
    assert "unrecognised refusal code" in seam.calls[0][1]
    assert n.counters()["refused.unknown_gate"] == 1


def test_daily_digest_states_a_quiet_day_positively():
    n = CopyTradeNotifier(seam := Recorder([True]))
    n.daily_digest(day=date(2026, 8, 10))
    body = seam.calls[0][1]
    assert "Executed: 0 · Refused: 0" in body
    assert "nothing traded" in body
    assert "nothing refused" in body
    assert "budget rails could not be read" in body
    assert seam.calls[0][0] == CHANNEL_BRIEF


def test_daily_digest_reports_trades_refusals_and_budget_headroom():
    n = CopyTradeNotifier(seam := Recorder([True]))
    n.daily_digest(
        day=date(2026, 8, 10),
        executed=[DigestTrade("AAPL", "buy", Decimal("1234.56"), "filled", "brk-77")],
        refused=[DigestRefusal("MSFT", notify.GATE_BUDGET_DAILY_NOTIONAL),
                 DigestRefusal("NVDA", notify.GATE_BUDGET_DAILY_NOTIONAL),
                 DigestRefusal("TSLA", notify.GATE_HALT)],
        budget_remaining={"daily_notional": Decimal("765.44"), "trades": 2,
                          "open_positions": 5},
    )
    body = seam.calls[0][1]
    assert "Executed: 1 · Refused: 3" in body
    assert "BUY AAPL $1,234.56 — filled (`brk-77`)" in body
    assert "`budget_daily_notional` ×2: MSFT, NVDA" in body
    assert "`halt` ×1: TSLA" in body
    assert "daily_notional: $765.44" in body
    # A COUNT rail must not be printed as money — "$2.00" reads as almost exhausted.
    assert "trades: 2" in body and "$2.00" not in body


def test_every_event_maps_to_a_real_logical_channel():
    assert set(CHANNEL_BY_EVENT) == set(notify.ALL_EVENTS)
    for event, channel in CHANNEL_BY_EVENT.items():
        assert channel in LOGICAL_CHANNELS, event


# ==============================================================================================
# 7. the inert notifier never enables silent trading
# ==============================================================================================
def test_null_notifier_never_confirms():
    n = NullNotifier()
    assert n.queued(make_facts(), risk_decision=FakeRiskDecision()).confirmed is False
    assert n.ensure_queued_delivered(make_facts(), risk_decision=FakeRiskDecision()).confirmed \
        is False
    assert n.queued_delivery_confirmed("cp-1") is False
    assert n.submitted(proposal_id="cp-1").confirmed is False
    assert n.filled(proposal_id="cp-1").confirmed is False
    assert n.cancelled(proposal_id="cp-1").confirmed is False
    assert n.refused(proposal_id="cp-1").confirmed is False
    assert n.daily_digest(day=date(2026, 8, 10)).confirmed is False


def test_amount_range_label_round_trips_into_the_message():
    """The disclosed range is the conviction signal; it reaches the operator verbatim."""
    label = AmountRange(Decimal("1000001"), Decimal("5000000")).label
    text = render_queued(make_facts(amount_label=label), risk_decision=FakeRiskDecision())
    assert "$1,000,001 - $5,000,000" in text


# ==============================================================================================
# 8. the adapter — the seam the EXECUTOR actually holds
# ==============================================================================================
# ``PaperAutoExecutor`` holds ``notify(event, payload) -> bool``; this module renders from typed
# facts and the stored ``RiskDecision`` row. The two were built independently and were never
# joined: ``main.build_autoexec_notify`` looked for a factory that did not exist here, so the
# composed lane was ABSENT on every real deployment while every unit test stayed green. These
# tests pin the join and, more importantly, pin that it fails CLOSED — an adapter that answered
# "delivered" for a message it could not render would trade behind the operator's back.
def _adapter(seam, *, risk_decision=None):
    return notify.AutoExecNotifyAdapter(
        CopyTradeNotifier(seam),
        risk_decision_loader=(lambda _rid: risk_decision) if risk_decision else None,
    )


def _queued_payload(**over) -> dict:
    """The shape ``PaperAutoExecutor._notify_payload`` emits: JSON-native, facts merged in from
    ``AutoTicket.context`` (which ``main.build_copy_cycle_service`` fills from the CopyIntent)."""
    payload = dict(
        lane="copytrade_autoexec", proposal_id="cp-1", order_id="ord-1",
        risk_decision_id="rd-1", symbol="AAPL", side="buy", notional="1234.56",
        window_seconds=300.0, cancel_command="/cancel cp-1",
        filer_display="Doe, Jane", tx_date="2026-06-25", filed_date="2026-07-30",
        amount_label="$15,001 - $50,000", sizing_reason="deterministic copy of a filing",
        sizing_notes=["capped_at_max_notional"], source_key="house:D1:0",
    )
    payload.update(over)
    return payload


def test_the_adapter_delivers_a_queued_trade_and_returns_the_CONFIRMATION():
    seam = Recorder((True,))
    adapter = _adapter(seam, risk_decision=FakeRiskDecision())

    assert adapter("queued", _queued_payload()) is True
    channel, body = seam.calls[0]
    assert channel == CHANNEL_PROPOSALS
    for fact in ("AAPL", "Doe, Jane", "2026-06-25", "35 days", "$15,001 - $50,000",
                 "/cancel cp-1", "verdict: approved"):
        assert fact in body, fact


def test_a_queued_payload_MISSING_ITS_FILING_is_never_reported_as_delivered():
    """The facts arrive on the ticket's context. If the wiring stops filling it, the message
    cannot be judged — so nothing is sent and the executor never fires the ticket."""
    seam = Recorder((True,))
    adapter = _adapter(seam, risk_decision=FakeRiskDecision())

    assert adapter("queued", _queued_payload(filer_display="", tx_date="")) is False
    assert seam.calls == [], "an unrenderable message must not be posted at all"


def test_a_queued_trade_with_NO_readable_risk_record_is_not_delivered():
    """SEC-17: the risk block comes from the stored row. No row, no message, no trade."""
    seam = Recorder((True,))
    adapter = _adapter(seam)                    # no loader wired -> risk_decision is None

    assert adapter("queued", _queued_payload()) is False
    assert seam.calls == []


def test_the_retry_is_IDEMPOTENT_so_a_pending_trade_is_not_re_posted_every_tick():
    """``resolve_due`` retries the queued notification on every tick until it is confirmed."""
    seam = Recorder((True,))
    adapter = _adapter(seam, risk_decision=FakeRiskDecision())

    assert adapter("queued", _queued_payload()) is True
    assert adapter("queued", _queued_payload()) is True
    assert len(seam.calls) == 1, "a confirmed queued message is posted exactly once"


def test_a_FAILED_queued_delivery_is_retried_rather_than_cached():
    seam = Recorder((False,))
    adapter = _adapter(seam, risk_decision=FakeRiskDecision())

    assert adapter("queued", _queued_payload()) is False
    assert adapter("queued", _queued_payload()) is False
    assert len(seam.calls) == 4, "two attempts per call (max_attempts=2), nothing cached"


def test_the_executors_refusal_REASONS_map_onto_this_modules_GATE_names():
    """The two vocabularies were written independently and disagree on several spellings. The
    reconciliation is a table, so a rename on either side surfaces here rather than as an
    operator message reading `budget_symbol_notional` in a file that never defined it."""
    from copytrade.autoexec import REFUSABLE_REASONS

    # DERIVED FROM THE EXECUTOR, NOT FROM A HAND-MAINTAINED EXCLUSION LIST. The predecessor of
    # this test skipped a ``lifecycle`` set that had quietly grown to hold three genuinely
    # reachable refusals (``order_missing``, ``approval_replay``, ``approval_write_failed``), so
    # the test passed while those rendered to the operator as
    # `Gate: approval_replay  ⚠ (unrecognised refusal code)`. ``REFUSABLE_REASONS`` is the
    # executor's own statement of what can reach ``_refuse``; a new refusal therefore fails here
    # by construction instead of by somebody remembering to shorten a list.
    assert REFUSABLE_REASONS, "the executor must publish what it can refuse with"
    for reason in sorted(REFUSABLE_REASONS):
        gate = notify.REFUSAL_GATE_BY_REASON.get(reason)
        assert gate is not None, f"{reason!r} can reach the operator with no gate mapping"
        assert gate in notify.KNOWN_REFUSAL_GATES, f"{reason!r} maps to unknown gate {gate!r}"


def test_no_refusable_reason_renders_as_an_UNRECOGNISED_code():
    """The end-to-end version of the test above: render the real message for every reason the
    executor can refuse with and assert the drift warning never appears. The operator's one
    interface must never report our internal drift in place of their diagnosis."""
    from copytrade.autoexec import REFUSABLE_REASONS

    for reason in sorted(REFUSABLE_REASONS):
        gate = notify.REFUSAL_GATE_BY_REASON[reason]
        text = notify.render_refused(proposal_id="cp-1", symbol="AAPL", side="buy", gate=gate)
        assert "unrecognised refusal code" not in text, reason


def test_a_refusal_reaches_the_operator_under_the_gate_that_stopped_it():
    seam = Recorder((True,))
    adapter = _adapter(seam)

    assert adapter("refused", {"proposal_id": "cp-1", "symbol": "AAPL", "side": "buy",
                               "refused_by": "budget_symbol_notional",
                               "detail": "cap reached"}) is True
    channel, body = seam.calls[0]
    assert channel == CHANNEL_ALERTS
    assert "budget_symbol_notional_per_day" in body and "cap reached" in body


def test_submitted_and_cancelled_reach_the_operator():
    seam = Recorder((True,))
    adapter = _adapter(seam)

    assert adapter("submitted", {"proposal_id": "cp-1", "symbol": "AAPL", "side": "buy",
                                 "broker_order_id": "brk-9", "notional": "200"}) is True
    assert adapter("cancelled", {"proposal_id": "cp-1", "symbol": "AAPL", "side": "buy",
                                 "cancelled_by": "123456789012345678"}) is True
    assert "brk-9" in seam.calls[0][1]
    assert "CANCELLED" in seam.calls[1][1]


def test_an_UNKNOWN_event_is_counted_and_never_reported_as_delivered():
    """An event the operator surface cannot render means the executor grew a notification
    nobody receives. Counted, logged, and NOT confirmed."""
    seam = Recorder((True,))
    adapter = _adapter(seam)

    assert adapter("some_new_event", {"proposal_id": "cp-1"}) is False
    assert seam.calls == []
    assert adapter.counters()["some_new_event.unknown_event"] == 1


def test_a_notifier_that_RAISES_reads_as_undelivered_not_as_a_crash():
    """The seam is called from inside the executor's firing path; an exception there would
    abort a whole resolve tick. It must degrade to "not delivered"."""
    class _Boom:
        def ensure_queued_delivered(self, *_a, **_k):
            raise RuntimeError("surface exploded")

        def counters(self):
            return {}

    adapter = notify.AutoExecNotifyAdapter(_Boom())
    assert adapter("queued", _queued_payload()) is False


def test_build_notifier_produces_the_callable_seam_the_executor_holds():
    seam = Recorder((True,))
    built = notify.build_notifier(seam)

    assert callable(built)
    assert built("submitted", {"proposal_id": "cp-1", "symbol": "AAPL", "side": "buy",
                               "broker_order_id": "brk-1"}) is True


# ==============================================================================================
# 10. RW-17 adversarial round (B2) — the ``filled`` event HAS A PRODUCER
# ==============================================================================================
# ``render_filled``/``CopyTradeNotifier.filled`` were implemented and called from NOWHERE, so the
# operator's last word on every trade was "submitted". RW-16 prices this lane's orders as LIMIT
# at the gate quote, so an order resting unfilled all day is the NORMAL case — "submitted" and
# "filled" are different facts and only one of them is a position. These tests pin the producer,
# and every one of them fails on a tree where the producer does not exist.
FILL_KEY = b"rw17-fill-notice-key"
FILL_NOW = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def fill_st(tmp_path):
    from config.settings import Settings
    from core.clock import FrozenClock
    from orchestrator.state import OrchestratorState
    from storage.engine import create_all, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{(tmp_path / 'rw17-fills.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(make_session_factory(engine), hmac_key=FILL_KEY,
                                   settings=Settings(), clock=FrozenClock(FILL_NOW))


def _fill_an_order(st, *, symbol="AAPL", side="buy", lane=True, to_state="filled"):
    """Drive one order from ``proposed`` to ``to_state`` through the REAL state machine, with
    the lane's signed adjudication in front of it — i.e. the exact ledger shape the auto lane
    plus RW-15's order-status reconcile tick leaves behind."""
    from brokers.order_state_machine import create_order, transition
    from copytrade.budget import AUTOEXEC_LANE
    from orchestrator.state import EVENT_ADJUDICATION

    order = create_order(
        st, proposal_id=f"cp-{symbol}", risk_decision_id=f"rd-{symbol}", account="paper",
        symbol=symbol, side=side, qty=Decimal("3"), order_type="limit",
        limit_price=Decimal("100"), tif="day", notional=Decimal("300"),
        order_tuple_hash="h" * 64, actor="test",
    )
    if lane:
        st.audit.append(
            EVENT_ADJUDICATION, actor="copytrade_autoexec",
            refs={"proposal_id": f"cp-{symbol}"},
            payload={"decision": "approve", "proposal_id": f"cp-{symbol}",
                     "risk_decision_id": f"rd-{symbol}", "order_tuple_hash": "h" * 64,
                     "lane": AUTOEXEC_LANE, "auto": True, "order_id": order.id,
                     "symbol": symbol, "side": side, "notional": "300"},
        )
    with st.session_factory() as s:
        row = s.get(type(order), order.id)
        s.expunge(row)
    for nxt in ("approved", "submitted"):
        transition(st, row, nxt, actor="test")
    if to_state is not None:
        transition(st, row, to_state, actor="test", broker_order_id="brk-77",
                   filled_qty=Decimal("3"), avg_fill_price=Decimal("99.50"))
    return order.id


def test_the_filled_event_has_a_producer_at_all():
    """B2: the producer must EXIST and must refuse to exist without a seam — a job that
    silently drops fills is the defect, not the remedy."""
    assert notify.build_fill_service(Recorder((True,))) is not None
    assert notify.build_fill_service(None) is None
    assert notify.build_fill_service(object()) is None


def test_a_signed_lane_fill_is_ANNOUNCED_to_the_operator(fill_st):
    seam = Recorder((True,))
    service = notify.build_fill_service(notify.build_notifier(seam))
    _fill_an_order(fill_st)

    report = service(fill_st, fill_st.clock)

    assert report["announced"] == 1, "a filled lane order must reach the operator"
    channel, text = seam.calls[-1]
    assert channel == CHANNEL_BY_EVENT[notify.EVENT_FILLED]
    assert "AUTO-TRADE FILLED" in text and "AAPL" in text
    assert "brk-77" in text, "the broker handle is the operator's follow-up lever"
    assert "3 @ $99.50" in text, "the broker's own fill report, echoed as stored"


def test_a_fill_is_announced_ONCE_because_the_NOTICE_IS_ON_THE_SIGNED_CHAIN(fill_st):
    """Idempotency across a RESTART, which an in-memory 'already told them' set cannot give:
    a second announcer over the same chain must stay silent."""
    seam = Recorder((True,))
    _fill_an_order(fill_st)
    notify.build_fill_service(notify.build_notifier(seam))(fill_st, fill_st.clock)
    assert len(seam.calls) == 1

    fresh = notify.build_fill_service(notify.build_notifier(seam))   # a "restarted" process
    assert fresh(fill_st, fill_st.clock)["announced"] == 0
    assert len(seam.calls) == 1, "a restart must not re-announce a fill the operator has seen"


def test_an_UNDELIVERED_fill_notice_is_retried_until_it_lands(fill_st):
    """No marker is written for a delivery that was not CONFIRMED, so the next tick tries
    again — a fill nobody was told about must not be silently marked as told."""
    seam = Recorder((False, True))
    service = notify.build_fill_service(notify.build_notifier(seam, max_attempts=1))
    _fill_an_order(fill_st)

    first = service(fill_st, fill_st.clock)
    assert first == {"readable": True, "error": None, "announced": 0, "undelivered": 1,
                     "pending": 1}

    second = service(fill_st, fill_st.clock)
    assert second["announced"] == 1, "the retry must land once the surface recovers"
    assert service(fill_st, fill_st.clock)["announced"] == 0, "and then stop"


def test_a_fill_THIS_LANE_NEVER_ADJUDICATED_is_not_announced(fill_st):
    """The operator asked for notifications about the ROBOT's trades. A hand-placed order that
    fills is not this lane's news, and claiming it would misreport what the lane did."""
    seam = Recorder((True,))
    service = notify.build_fill_service(notify.build_notifier(seam))
    _fill_an_order(fill_st, symbol="MSFT", lane=False)

    assert service(fill_st, fill_st.clock)["announced"] == 0
    assert seam.calls == []


def test_a_SUBMITTED_but_unfilled_order_is_never_reported_as_filled(fill_st):
    """The whole point: a resting limit order that never crossed must produce NO fill message."""
    seam = Recorder((True,))
    service = notify.build_fill_service(notify.build_notifier(seam))
    _fill_an_order(fill_st, symbol="TSLA", to_state=None)

    assert service(fill_st, fill_st.clock)["pending"] == 0
    assert seam.calls == []


def test_an_UNVERIFIABLE_CHAIN_announces_nothing_and_says_so(fill_st):
    """A forged transition must not be able to manufacture a 'your trade filled' message, and
    an unreadable ledger must not render as a day without fills."""
    from sqlalchemy import update

    from schemas.models.audit_entry import AuditEntry

    seam = Recorder((True,))
    service = notify.build_fill_service(notify.build_notifier(seam))
    _fill_an_order(fill_st)
    with fill_st.session_factory() as s:
        s.execute(update(AuditEntry).values(entry_hash="0" * 64))
        s.commit()

    report = service(fill_st, fill_st.clock)

    assert report["readable"] is False and report["announced"] == 0
    assert seam.calls == [], "nothing may be announced off a chain that does not verify"


def test_a_forged_fill_NOTICE_cannot_QUIETLY_suppress_a_fill(fill_st):
    """A keyless writer inserting an 'already told them' marker must not be able to silence the
    operator's fill message as if nothing had happened.

    It cannot: the marker is HMAC-covered like every other entry, so a forged one breaks the
    chain and the producer reports UNREADABLE (named, logged at error, and still pending once
    the ledger is trustworthy again) rather than a day on which this order simply did not fill.
    """
    from schemas.models.audit_entry import AuditEntry

    seam = Recorder((True,))
    service = notify.build_fill_service(notify.build_notifier(seam))
    order_id = _fill_an_order(fill_st)
    with fill_st.session_factory() as s:
        entry = AuditEntry(
            id="forged-fill-notice", seq=9_000_001, created_at=FILL_NOW, actor="attacker",
            event_type=notify.EVENT_AUTOEXEC_FILL_NOTICE, refs={"order_id": order_id},
            payload={"order_id": order_id}, prev_hash="0" * 64, entry_hash="f" * 64,
        )
        s.add(entry)
        s.commit()

    report = service(fill_st, fill_st.clock)

    assert report["readable"] is False and report["error"]
    assert report["announced"] == 0 and seam.calls == []


def test_the_fill_producer_is_REGISTERED_AS_A_JOB_or_not_at_all():
    """B2 was 'implemented and called from nowhere'. The wiring posture is the ingest one: a
    provisioned producer gets a job, an unprovisioned one gets NO job (never a silent one)."""
    pytest.importorskip("apscheduler")
    from orchestrator import scheduler as sch

    assert sch.run_autoexec_fills(_SchedState())["skipped"] == "no_autoexec_fills_service"

    def boom(_st, _clock):
        raise RuntimeError("surface down")

    assert "error" in sch.run_autoexec_fills(_SchedState(), service=boom), \
        "a failing report must never raise into the scheduler thread"

    state = _SchedState()
    with_job = sch.build_scheduler(state, state.clock,
                                   services={"autoexec_fills": lambda s, c: {}})
    without = sch.build_scheduler(state, state.clock, services={})
    assert "autoexec_fills" in {j.id for j in with_job.get_jobs()}
    assert "autoexec_fills" not in {j.id for j in without.get_jobs()}


@dataclass
class _SchedState:
    """The two attributes the job callable touches. The scheduler jobs are plain injected
    callables precisely so they are testable without a state machine behind them."""

    clock: object = None

    def __post_init__(self):
        from core.clock import FrozenClock

        self.clock = self.clock or FrozenClock(FILL_NOW)
