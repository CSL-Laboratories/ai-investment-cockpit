"""RW-17 §4/§5 — the WIRING of the zero-touch PAPER auto-execution lane.

The lane's own correctness (the adjudication, the budget rails, the notifications) lives in
``tests/copytrade/``. This file pins the four properties that are facts about how the pieces
are JOINED, and that no unit test of ``copytrade/`` can see:

  1. **OPT-IN, AND ITS REFUSALS ARE LOUD.** ``COCKPIT_CONGRESS_AUTOEXEC`` defaults false. Turned
     on in a configuration that can never trade — congress feed off, empty follow list, a daily
     rail below the minimum trade size — startup FAILS. Each of those boots clean and copies
     nobody, and the operator, who has stopped watching an approval queue on purpose, would be
     waiting on notifications that were never coming. Silence is the failure mode this whole
     package is built against, so it must not be reachable through configuration.

  2. **THE LANE IS ALL-OR-NOTHING AND FAILS CLOSED TO ABSENT.** No paper transport, no
     notification seam, or a gateway whose ``execution_scope`` is not ``"paper"`` -> no
     executor, a NAMED reason, and no scheduler job at all. A lane that trades but cannot tell
     you is the one thing the operator did not ask for; a lane in front of a LIVE transport is
     the one thing this package may never build.

  3. **THE SCHEDULE.** The copy cycle only inside a real session (an off-hours quote rejection
     spends one of a filing's three copy attempts), the resolve tick well under the cancel
     window, and the tick RESILIENT — a raising ``resolve_due`` must not wedge the job.

  4. **/cancel IS A VETO, NEVER AN APPROVAL.** It reuses the existing operator allowlist, it is
     answerable when nothing is wired, and nothing about it is a precondition for a trade.

Hermetic: no network, no keyring, no discord.py requirement, tmp SQLite, FrozenClock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import main
from bridge.bot import SLASH_COMMANDS, _WIRED_COMMANDS, CancelCommands
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator import scheduler as sch
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

OPEN = FrozenClock(datetime(2026, 7, 7, 14, 40, tzinfo=timezone.utc))    # Tue 10:40 ET
CLOSED = FrozenClock(datetime(2026, 7, 5, 14, 40, tzinfo=timezone.utc))  # Sunday
UA = "Cockpit ops@example.com"
FOLLOW = "Pelosi, Nancy"

BASE_JOB_IDS = {"premarket_brew", "intraday_loop", "eod_review", "daily_disarm", "anchor_head"}


def armed(**kw) -> Settings:
    """A Settings whose autoexec lane is legitimately switched on."""
    return Settings(congress_enabled=True, congress_user_agent=UA, congress_follow=FOLLOW,
                    congress_autoexec=True, **kw)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'autoexec-wiring.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(make_session_factory(engine), hmac_key=b"rw17-wiring-key",
                                   settings=Settings(), clock=OPEN)


def state_with(st, settings) -> OrchestratorState:
    st.settings = settings
    return st


class _Gateway:
    def __init__(self, scope: str = "paper") -> None:
        self.execution_scope = scope


class _Paper:
    """Stands in for ``PaperExecutionService``: what matters here is the gateway it was
    composed with, which the lane must REUSE rather than rebuild (Invariant 1).

    It also carries the two gate-(d) seams, because they are REQUIRED and not decorative. The
    executor cannot compute the gateway's pre-broker risk re-check itself (no broker, no quote
    seam), so an unwired ``autoexec_risk_recheck`` leaves it defaulting to
    ``fail_closed_risk_recheck`` — a lane that arms, notifies, counts down and then refuses
    every ticket, which reads to an operator exactly like a week in which Congress filed
    nothing. A double that omits them is not standing in for the real service; see
    ``_Bare`` below, which omits them ON PURPOSE and must be refused.
    """

    def __init__(self, gateway=None) -> None:
        self.gateway = gateway

    @staticmethod
    def quote(symbol):                                  # pragma: no cover - never called here
        return None

    @staticmethod
    def positions_snapshot():                           # pragma: no cover - never called here
        return []

    @staticmethod
    def autoexec_risk_recheck(order):                   # pragma: no cover - never called here
        return lambda _positions: {"decision": "approved", "failed_checks": []}


class _Bare:
    """A paper service with a valid gateway and NO risk-re-check seam."""

    def __init__(self, gateway=None) -> None:
        self.gateway = gateway


def deliver_ok(channel, payload) -> bool:
    return True


# =======================================================================================
# 1. the switch, and the LOUD refusals
# =======================================================================================
def test_autoexec_is_OFF_by_default():
    """Opt-in is the point: this replaces the operator's approval tap. A deployment must never
    acquire zero-touch execution by upgrading."""
    settings = Settings()
    assert settings.congress_autoexec is False
    assert settings.congress_autoexec_window_seconds == 300


@pytest.mark.parametrize("kw, expected", [
    ({"congress_enabled": False, "congress_follow": FOLLOW}, "COCKPIT_CONGRESS_ENABLED"),
    ({"congress_enabled": True, "congress_follow": ""}, "COCKPIT_CONGRESS_FOLLOW"),
    ({"congress_enabled": True, "congress_follow": FOLLOW,
      "congress_autoexec_max_daily_notional": Decimal("5")},
     "COCKPIT_CONGRESS_AUTOEXEC_MAX_DAILY_NOTIONAL"),
    # The same defect one rail over: a per-symbol cap under the sizing FLOOR means no symbol
    # can ever take its first trade, so the lane refuses everything while looking healthy.
    ({"congress_enabled": True, "congress_follow": FOLLOW,
      "congress_autoexec_max_notional_per_symbol_per_day": Decimal("5")},
     "COCKPIT_CONGRESS_AUTOEXEC_MAX_NOTIONAL_PER_SYMBOL_PER_DAY"),
])
def test_an_unreachable_autoexec_lane_REFUSES_AT_STARTUP(kw, expected):
    """Each of these boots clean and can never trade. A quiet no-op here is worse than a crash:
    the operator's only interface is now notifications, so a lane that will never notify is
    indistinguishable from a month in which nobody filed anything."""
    with pytest.raises(ValueError) as exc:
        Settings(congress_autoexec=True, congress_user_agent=UA, **kw)

    message = str(exc.value)
    assert expected in message
    assert "COCKPIT_CONGRESS_AUTOEXEC" in message, "the refusal must name the switch to turn off"


def test_a_legitimate_autoexec_configuration_boots():
    """The control for the test above: the refusal must be about unreachable lanes, not about
    autoexec itself."""
    settings = armed()
    assert settings.congress_autoexec is True
    assert settings.congress_autoexec_max_trades_per_day == 3


def test_the_budget_rails_have_conservative_defaults():
    """The rails REPLACE the human, so their defaults have to be tighter than what an attentive
    operator would have approved by hand — and none of them may default to a value that is
    effectively no rail at all."""
    s = Settings()
    assert s.congress_autoexec_max_daily_notional == s.congress_max_notional_per_trade == 500
    assert s.congress_autoexec_max_trades_per_day == 3
    assert s.congress_autoexec_max_open_positions == 10
    # one name cannot consume the whole day's budget
    assert s.congress_autoexec_max_notional_per_symbol_per_day < s.congress_autoexec_max_daily_notional


@pytest.mark.parametrize("bad", [0, 30, 59])
def test_a_cancel_window_below_the_RESOLVE_TICK_is_refused(bad):
    """A window shorter than the tick that resolves it is not a shorter window — it is one
    whose real length is scheduler jitter, and an unpredictable cancel window is not one."""
    with pytest.raises(ValueError):
        Settings(congress_autoexec_window_seconds=bad)


def test_the_window_floor_IS_the_resolve_cadence():
    """Pinned against the constant itself so the two cannot drift apart in a later edit."""
    ge = [m for m in Settings.model_fields["congress_autoexec_window_seconds"].metadata
          if hasattr(m, "ge")]
    assert ge and ge[0].ge == sch.AUTOEXEC_RESOLVE_SECONDS


# =======================================================================================
# 2. composing the lane: all-or-nothing, absent-with-a-reason, PAPER-scoped or nothing
# =======================================================================================
def test_the_lane_is_absent_and_explained_when_the_switch_is_off(st):
    wiring = main.build_paper_autoexec(state_with(st, Settings()),
                                       paper=_Paper(_Gateway()), deliver=deliver_ok)

    assert wiring.enabled is False and wiring.executor is None
    assert "COCKPIT_CONGRESS_AUTOEXEC" in wiring.reason
    assert wiring.status()["kind"] == "not_provisioned"


def test_the_lane_REFUSES_TO_EXIST_without_a_notification_seam(st):
    """An auto-executing lane that cannot tell the operator what it did is precisely what they
    did not ask for ("all i ask is for notifications"), so it is not composed at all. Stricter
    than the morning brief, which is still computed when it reaches nobody — a brief that
    reaches nobody loses a push; a TRADE that reaches nobody is an unannounced position."""
    wiring = main.build_paper_autoexec(state_with(st, armed()),
                                       paper=_Paper(_Gateway()), deliver=None)

    assert wiring.enabled is False
    assert "notification" in wiring.reason.lower() or "auto-execution" in wiring.reason.lower()


def test_the_lane_is_absent_without_a_paper_transport(st):
    wiring = main.build_paper_autoexec(state_with(st, armed()), paper=None, deliver=deliver_ok)

    assert wiring.enabled is False
    assert "alpaca" in wiring.reason.lower()


@pytest.mark.parametrize("scope", ["live", None, "paper_live", ""])
def test_a_NON_PAPER_gateway_makes_the_lane_REFUSE_TO_EXIST(st, scope):
    """THE RULE THAT OVERRIDES EVERYTHING, AS A TEST. ``_ORDER_AUTHORITY_BY_SCOPE`` maps
    ``live`` to the three live/auto rungs, so a lane composed in front of a non-paper gateway
    would be zero-touch adjudication ahead of a LIVE transport. An unreadable scope is refused
    for the same reason: an attribute we cannot read is not one we may assume."""
    gateway = _Gateway(scope) if scope is not None else SimpleNamespace()
    wiring = main.build_paper_autoexec(state_with(st, armed()),
                                       paper=_Paper(gateway), deliver=deliver_ok)

    assert wiring.enabled is False, "a non-paper gateway must never get an auto-exec lane"
    assert "paper" in wiring.reason.lower()


def test_the_lane_REUSES_the_composed_gateway_rather_than_building_a_second_one(st, monkeypatch):
    """Invariant 1 is ONE order path. Two gateway instances over one broker are two paths that
    merely happen to agree today, so the executor must receive the very object the human gate
    already submits through."""
    seen = {}

    class _Executor:
        def __init__(self, state, gateway, **kw):
            seen["state"], seen["gateway"], seen["kw"] = state, gateway, kw

    monkeypatch.setattr(main, "build_autoexec_notify", lambda *a, **k: (object(), None))
    gateway = _Gateway()
    fake_module = SimpleNamespace(PaperAutoExecutor=_Executor)
    monkeypatch.setitem(__import__("sys").modules, "copytrade.autoexec", fake_module)

    wiring = main.build_paper_autoexec(state_with(st, armed()),
                                       paper=_Paper(gateway), deliver=deliver_ok)

    assert wiring.enabled is True
    assert seen["gateway"] is gateway
    assert seen["kw"]["window_seconds"] == 300
    assert wiring.status()["execution_scope"] == "paper"
    assert wiring.status()["budget"]["max_trades_per_day"] == 3


def test_a_lane_that_cannot_be_constructed_never_kills_compose(st, monkeypatch):
    """One optional lane's constructor must not take the order path, the risk engine and the
    operator surface down with it — the per-feature fail-closed posture of this whole module."""

    def _boom(*a, **k):
        raise RuntimeError("interface drift")

    monkeypatch.setattr(main, "build_autoexec_notify", lambda *a, **k: (object(), None))
    monkeypatch.setitem(__import__("sys").modules, "copytrade.autoexec",
                        SimpleNamespace(PaperAutoExecutor=_boom))

    wiring = main.build_paper_autoexec(state_with(st, armed()),
                                       paper=_Paper(_Gateway()), deliver=deliver_ok)

    assert wiring.enabled is False and "interface drift" in wiring.reason


def test_the_notify_seam_ACTUALLY_JOINS_the_real_module(st):
    """THE SEAM IS JOINED, not merely tolerated.

    This test used to accept either answer ("exactly one of notify/reason, never both"), which
    pins nothing: ``copytrade.notify`` exposed no factory under any of the names the composer
    looks for, so the real answer was always ``(None, reason)`` — the lane could never be
    composed in production and every wiring test that mattered monkeypatched the seam away. A
    tolerant assertion is how that survived. So the join is asserted: a real notifier, callable
    as the executor's ``notify(event, payload) -> bool`` seam.
    """
    notify, reason = main.build_autoexec_notify(armed(), deliver_ok, st=st)

    assert reason is None, reason
    assert callable(notify), "the executor's seam is a callable (event, payload) -> bool"


def test_a_lane_whose_ALERTS_CHANNEL_IS_UNMAPPED_refuses_to_exist(st):
    """"A ``deliver`` exists" and "this lane can deliver" are different questions.

    ``build_deliver`` needs only ONE channel id to be a working callable, and it fails closed
    PER CHANNEL. ``copytrade.notify.CHANNEL_BY_EVENT`` routes ``queued`` to ``#proposals`` and
    ``submitted``/``refused``/``cancelled`` to ``#alerts``, so a proposals-only configuration
    produced a lane that armed cleanly, confirmed its queued notice, EXECUTED the trade — and
    then could never deliver a submitted, refused or cancelled message again. Every named
    refusal, which is this lane's only health signal, reached a log line and nothing else.
    """
    from bridge.deliver import build_deliver

    posts = []
    settings = armed(proposals_channel_id=111)          # #alerts and #brief unset
    deliver = build_deliver({"#proposals": 111}, lambda cid, _text: posts.append(cid))
    # The seam itself is the proof of the hazard: queued lands, everything after it does not.
    assert deliver("#proposals", "x") is True
    assert deliver("#alerts", "x") is False

    notify, reason = main.build_autoexec_notify(settings, deliver, st=st)
    assert notify is None, "a lane that cannot report an outcome is not composed"
    assert "#alerts" in reason

    wiring = main.build_paper_autoexec(state_with(st, settings),
                                       paper=_Paper(_Gateway()), deliver=deliver)
    assert wiring.enabled is False
    assert "channel" in wiring.reason.lower()


def test_a_fully_mapped_channel_configuration_arms(st):
    """The other half of the check: every channel the lane posts to is configured, so it arms."""
    from copytrade.notify import CHANNEL_BY_EVENT

    settings = armed(brief_channel_id=1, alerts_channel_id=2, proposals_channel_id=3)
    notify, reason = main.build_autoexec_notify(settings, deliver_ok, st=st)

    assert reason is None and callable(notify)
    # and the required set really is derived from the routing table, not hardcoded here
    assert set(CHANNEL_BY_EVENT.values()) == {"#proposals", "#alerts", "#brief"}


def test_a_lane_with_no_gate_d_seam_REFUSES_TO_EXIST(st):
    """A lane that cannot re-run risk before the broker call would refuse EVERY ticket at
    gateway gate (d) while reporting itself armed — an operator who stopped watching an
    approval queue reads that as "nothing was disclosed". Absent and explained beats armed and
    mute, so this is a refusal to compose."""
    wiring = main.build_paper_autoexec(state_with(st, armed()),
                                       paper=_Bare(_Gateway()), deliver=deliver_ok)

    assert wiring.enabled is False
    assert "risk re-check" in wiring.reason


def test_the_lane_REFUSES_when_the_executor_would_silently_drop_a_seam(st, monkeypatch):
    """``_supported_kwargs`` drops any kwarg the callee does not declare. For ``notify`` and
    ``risk_recheck_factory`` a silent drop is a lane that never notifies or never trades, so
    the composer must check the load-bearing ones SURVIVED the filter rather than trust it."""

    class _Narrow:
        def __init__(self, state, gateway, *, window_seconds=0, clock=None, policy=None):
            pass                                        # accepts neither notify nor the recheck

    monkeypatch.setitem(__import__("sys").modules, "copytrade.autoexec",
                        SimpleNamespace(PaperAutoExecutor=_Narrow))

    wiring = main.build_paper_autoexec(state_with(st, armed()),
                                       paper=_Paper(_Gateway()), deliver=deliver_ok)

    assert wiring.enabled is False
    assert "notify" in wiring.reason and "risk_recheck_factory" in wiring.reason


# =======================================================================================
# 3. the schedule
# =======================================================================================
def test_no_lane_means_no_jobs(st):
    """The RW-16 posture, preserved for every deployment that did not opt in."""
    pytest.importorskip("apscheduler")
    ids = {j.id for j in sch.build_scheduler(st, OPEN).get_jobs()}

    assert ids == BASE_JOB_IDS


def test_the_two_lane_jobs_register_on_their_justified_cadences(st):
    pytest.importorskip("apscheduler")
    services = {"copy_cycle": lambda state, clock: {}, "autoexec_resolve": lambda s, c: []}

    jobs = {j.id: j for j in sch.build_scheduler(st, OPEN, services=services).get_jobs()}

    assert set(jobs) == BASE_JOB_IDS | {"congress_copy_cycle", "autoexec_resolve"}
    assert jobs["congress_copy_cycle"].name == "congress_copy_cycle"
    # inside the REGULAR SESSION on weekdays: an off-hours quote fails the risk gate's hard
    # quote_sane check, and each rejection spends one of a filing's three copy attempts.
    assert sch.CONGRESS_COPY_CYCLE_CRON["day_of_week"] == "mon-fri"
    assert sch.CONGRESS_COPY_CYCLE_CRON["hour"] == "10,12,14"
    # and well under the cancel window, all week (a Friday-evening ticket still gets resolved)
    assert sch.AUTOEXEC_RESOLVE_CRON == {"minute": "*"}
    assert sch.AUTOEXEC_RESOLVE_SECONDS * 2 <= Settings().congress_autoexec_window_seconds


def test_the_copy_cycle_job_SKIPS_when_the_market_is_closed(st):
    """A cron cannot know about a market holiday or an early close. Running anyway would spend
    a filing's retry budget against a stale quote and a closed market."""
    called = []

    out = sch.run_congress_copy_cycle(st, CLOSED,
                                      service=lambda s, c: called.append(1) or {})

    assert out["skipped"] == "market_closed"
    assert called == []


def test_the_copy_cycle_job_runs_the_service_in_session(st):
    out = sch.run_congress_copy_cycle(st, OPEN, service=lambda s, c: {"proposed": 2})

    assert out == {"proposed": 2}


def test_a_failing_copy_cycle_never_raises_into_the_scheduler(st):
    """One sick cycle must not take down the resolve tick or any feed sharing the thread."""

    def _boom(state, clock):
        raise RuntimeError("bad batch")

    out = sch.run_congress_copy_cycle(st, OPEN, service=_boom)

    assert "bad batch" in out["error"]


def test_the_resolve_tick_reports_every_resolution(st):
    out = sch.run_autoexec_resolve(st, OPEN, service=lambda s, c: ["r1", "r2"])

    assert out["resolved"] == 2 and out["resolutions"] == ["r1", "r2"]


def test_one_bad_tick_does_not_stop_the_NEXT_one(st):
    """PER-TICK isolation (per-TICKET isolation is ``resolve_due``'s own contract). A raising
    tick is logged and swallowed so the following minute resolves the same tickets; a ticket
    that fails to resolve does not trade, so the cost is a delayed outcome, never an
    unadjudicated order."""
    ticks = iter([RuntimeError("poisoned ticket"), ["r1"]])

    def _service(state, clock):
        nxt = next(ticks)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    first = sch.run_autoexec_resolve(st, OPEN, service=_service)
    second = sch.run_autoexec_resolve(st, OPEN, service=_service)

    assert "poisoned ticket" in first["error"]
    assert second["resolved"] == 1, "the next tick must still resolve the healthy tickets"


def test_the_resolve_tick_is_a_no_op_without_a_service(st):
    assert sch.run_autoexec_resolve(st, OPEN)["skipped"] == "no_autoexec_service"


# ---- the DAILY DIGEST: the one message that arrives whether or not anything happened -----
def test_the_digest_job_registers_after_the_close_on_weekdays(st):
    """It was fully implemented in ``copytrade/notify.py`` and PRODUCED BY NOTHING — no
    scheduler job, no call site — so the lane's only end-of-day statement was never sent and
    "the lane did nothing today" stayed unanswerable, which is the exact property AUTOEXEC.md
    §2/§3 say must be a positive statement."""
    pytest.importorskip("apscheduler")
    services = {"copy_cycle": lambda s, c: {}, "autoexec_resolve": lambda s, c: [],
                "autoexec_digest": lambda s, c: {}}

    jobs = {j.id for j in sch.build_scheduler(st, OPEN, services=services).get_jobs()}

    assert "autoexec_digest" in jobs
    # after eod_review (16:15) and daily_disarm (16:00), so it reports a settled day
    assert sch.AUTOEXEC_DIGEST_CRON["hour"] == 16
    assert sch.AUTOEXEC_DIGEST_CRON["minute"] > sch.EOD_MINUTE
    assert sch.AUTOEXEC_DIGEST_CRON["day_of_week"] == "mon-fri"


def test_a_failing_digest_never_raises_into_the_scheduler(st):
    def boom(_state, _clock):
        raise RuntimeError("the surface is down")

    out = sch.run_autoexec_digest(st, OPEN, service=boom)

    assert "the surface is down" in out["error"]


def test_the_digest_is_a_no_op_without_a_service(st):
    assert sch.run_autoexec_digest(st, OPEN)["skipped"] == "no_autoexec_digest_service"


def test_the_digest_service_reports_the_CHAIN_and_the_live_budget(st):
    """Its rows come from the signed ticket ledger and its rails from ``budget_state`` — not
    from ``PaperAutoExecutor.counts``, which is an in-memory dict that a restart empties and
    which would therefore under-report on exactly the days the digest is worth reading."""
    sent = {}

    class _Notifier:
        def daily_digest(self, **kw):
            sent.update(kw)
            return SimpleNamespace(confirmed=True, status="confirmed")

    service = main.build_autoexec_digest_service(SimpleNamespace(notify=_Notifier()))
    out = service(st, OPEN)

    assert out["day"] == OPEN.now().date().isoformat()
    assert out["readable"] is True and out["delivered"] is True
    assert sent["executed"] == () and sent["refused"] == ()
    # the budget block is the SAME arithmetic that will refuse tomorrow's first trade
    assert sent["budget_remaining"]["trades"] == Settings().congress_autoexec_max_trades_per_day


def test_no_typed_notifier_means_NO_digest_job_rather_than_a_silent_one(st):
    assert main.build_autoexec_digest_service(SimpleNamespace(notify=None)) is None
    assert main.build_autoexec_digest_service(SimpleNamespace(notify=lambda *_a: True)) is None


def test_the_resolve_service_passes_the_WALL_CLOCK_instant_to_the_executor():
    """The executor measures the cancel window; it must be told the same instant every other
    scheduled job reads, never a monotonic reading that parses as 1970."""
    seen = {}

    executor = SimpleNamespace(
        resolve_due=lambda *, now=None: seen.setdefault("now", now) or [])
    main.build_autoexec_resolve_service(executor)(None, OPEN)

    assert seen["now"] == OPEN.now()


# =======================================================================================
# 4. /cancel — a VETO on the existing auth path, never an approval
# =======================================================================================
def _interaction(user_id="123456789012345678"):
    return SimpleNamespace(user=SimpleNamespace(id=user_id), id="iid")


def _bot_state(operator="123456789012345678"):
    return SimpleNamespace(settings=SimpleNamespace(operator_ids=operator))


def test_cancel_is_registered_as_a_REAL_body_not_a_stub():
    assert "cancel" in SLASH_COMMANDS and "cancel" in _WIRED_COMMANDS


def test_cancel_reuses_the_existing_operator_allowlist():
    """One auth path, not two. A second one is a second thing to get wrong, and the failure
    mode of getting it wrong here is a stranger cancelling the operator's trades."""
    executor = SimpleNamespace(cancel=lambda *a, **k: pytest.fail("must not reach the executor"))
    core = CancelCommands(_bot_state(), executor)

    assert core.handle(_interaction("999999999999999999")) == CancelCommands.UNAUTHORIZED


def test_cancel_forwards_the_actor_and_renders_the_outcome():
    seen = {}

    def _cancel(proposal_id, *, actor_user_id):
        seen["proposal_id"], seen["actor"] = proposal_id, actor_user_id
        return SimpleNamespace(cancelled=True, reason="cancelled", proposal_id=proposal_id)

    core = CancelCommands(_bot_state(), SimpleNamespace(cancel=_cancel))
    message = core.handle(_interaction(), proposal_id="cp_abc")

    assert seen == {"proposal_id": "cp_abc", "actor": "123456789012345678"}
    assert "CANCELLED" in message and "cp_abc" in message


def test_a_refused_cancel_says_so_rather_than_implying_success():
    core = CancelCommands(_bot_state(), SimpleNamespace(
        cancel=lambda pid, *, actor_user_id: SimpleNamespace(
            cancelled=False, reason="already_executed")))

    message = core.handle(_interaction(), proposal_id="cp_abc")

    assert "NOT cancelled" in message and "already_executed" in message


def test_an_unreadable_outcome_is_reported_as_STILL_QUEUED():
    """The fallback must never be "cancelled": an outcome this adapter cannot read must not be
    reported to the operator as a trade that was stopped."""
    core = CancelCommands(_bot_state(), SimpleNamespace(
        cancel=lambda pid, *, actor_user_id: object()))

    assert "STILL QUEUED" in core.handle(_interaction(), proposal_id="cp_abc")


def test_a_raising_executor_never_becomes_silence():
    """An exception inside a Discord handler surfaces as an interaction timeout, and an operator
    who typed /cancel and saw nothing cannot tell "cancelled" from "still coming"."""
    def _raise(pid, *, actor_user_id):
        raise RuntimeError("lock timeout")

    core = CancelCommands(_bot_state(), SimpleNamespace(cancel=_raise))
    message = core.handle(_interaction(), proposal_id="cp_abc")

    assert "FAILED" in message and "lock timeout" in message


def test_cancel_answers_plainly_when_no_lane_is_wired():
    """A command that silently disappears is indistinguishable from a bot that is not
    responding — the worst possible property for the one control that stops a trade."""
    assert CancelCommands(_bot_state(), None).handle(
        _interaction(), proposal_id="cp_abc") == CancelCommands.NOT_WIRED


# =======================================================================================
# RW-17 adversarial round, C1 — the channel requirement is DERIVED, and its refusal is READABLE
#
# ``build_autoexec_notify`` already refuses a lane whose channels are not all mapped. These two
# tests pin the parts of that refusal an operator's life actually depends on, and that the
# existing pair above cannot see: that the required set is read off the ROUTING TABLE at call
# time (so a sixth event added to ``CHANNEL_BY_EVENT`` tomorrow cannot arm a lane that has
# nowhere to post it), and that the reason reaches ``status()`` — the only place an operator or
# ``GET /status`` can ask "why has this lane never said anything?".
# =======================================================================================
def test_the_required_channels_are_READ_OFF_THE_ROUTING_TABLE_not_hardcoded(st, monkeypatch):
    """A hardcoded {#proposals, #alerts, #brief} would pass every test in this file today and
    silently arm a mute lane the day a new event routes somewhere new."""
    from bridge.deliver import CHANNEL_SYSTEM
    from copytrade import notify as _notify

    # every channel the lane routes to TODAY is configured...
    settings = armed(brief_channel_id=1, alerts_channel_id=2, proposals_channel_id=3)
    assert main.build_autoexec_notify(settings, deliver_ok, st=st)[1] is None
    # ... and now one more event routes to a channel that is not.
    monkeypatch.setitem(_notify.CHANNEL_BY_EVENT, "rw17_future_event", CHANNEL_SYSTEM)

    notify, reason = main.build_autoexec_notify(settings, deliver_ok, st=st)

    assert notify is None, "a lane with an unroutable event may not be composed"
    assert CHANNEL_SYSTEM in reason


def test_the_unmapped_channel_refusal_is_visible_in_STATUS(st):
    """``status()`` is what ``GET /status`` serves. A lane that is absent for a fixable config
    reason has to SAY the reason there, or the operator's only evidence is silence — which is
    the one thing this package exists to make unreadable-as-healthy."""
    settings = armed(proposals_channel_id=111)          # #alerts and #brief unset

    status = main.build_paper_autoexec(state_with(st, settings), paper=_Paper(_Gateway()),
                                       deliver=deliver_ok).status()

    assert status["kind"] == "not_provisioned"
    assert "#alerts" in status["reason"] and "channel id" in status["reason"]


def test_cancel_without_an_id_explains_itself_and_lists_what_is_queued():
    core = CancelCommands(_bot_state(), SimpleNamespace(
        cancel=lambda *a, **k: None,
        pending=lambda: [SimpleNamespace(proposal_id="cp_1")]))

    message = core.handle(_interaction())

    assert "Usage" in message and "cp_1" in message
