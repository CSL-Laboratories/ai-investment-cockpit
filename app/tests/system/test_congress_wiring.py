"""RW-16 — the congressional feed's WIRING: absent-and-explained, and NOT scheduled to trade.

This is the composition-root half of RW-16. It pins three boundaries that no unit test of
``copytrade/`` or ``ingest/congress/`` can see, because each of them is a property of how the
pieces are joined rather than of any one piece:

  1. **UNPROVISIONED IS A FIRST-CLASS STATE, AND IT NEVER RAISES.** Two independent switches
     (``COCKPIT_CONGRESS_ENABLED``, ``COCKPIT_CONGRESS_USER_AGENT``) each hold the feed off.
     Neither may take ``compose()`` down with it: one optional feed's constructor must not be
     able to remove the order path, the risk engine and the operator surface from the process.

  2. **ABSENT, NOT QUIET.** A feed that produces zero events forever looks exactly like a quiet
     market. So an unwired congress feed must be VISIBLY absent in ``IngestRunner.status()``,
     with the reason attached — and it must NOT get a feed row full of zeroes next to the live
     feeds, which would recreate the "present and silent" shape the ingest layer exists to make
     unreachable. Both halves are asserted, with a wired feed as the contrast control.

  3. **THE COPY CYCLE IS SCHEDULED ONLY WITH THE LANE THAT ADJUDICATES IT (RW-17).** RW-16 left
     the cron job out on purpose and this file pinned that absence, so that adding the job would
     be a conscious act with a failing test attached. The operator has since made that act: they
     asked for trades without approvals ("all i ask is for notifications"), and RW-17 wires the
     cycle plus a ``resolve_due`` tick behind ``COCKPIT_CONGRESS_AUTOEXEC``.

     THE PIN DID ITS JOB AND IS NOT DELETED — IT IS INVERTED. What it now asserts is the shape
     of the reversal rather than the reversal's absence: with no auto-exec lane composed the job
     set is EXACTLY what RW-16 left behind (a congress-awareness deployment gains nothing on a
     timer), and with the lane composed exactly two jobs appear, both named. What did NOT change
     is the boundary the original omission was protecting: ``producing_runtime`` is still
     ``"deterministic:copytrade"``, ``assert_execution_runtime`` still REFUSES it above paper,
     and the scheduled cycle therefore still emits nothing in any live rung. Scheduling it did
     not answer SEC-08 and did not need to.

Hermetic: no network (the providers are constructed, never fetched), no keyring dependence
(``core.secrets.get_secret`` is stubbed to None so only the congress switches vary), tmp
SQLite, FrozenClock.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

import main
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator import scheduler as sch
from orchestrator.ingest_runner import FEED_CONGRESS, IngestRunner
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

CLOCK = FrozenClock(datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc))   # Tue 10:00 ET
UA = "Cockpit ops@example.com"

BASE_JOB_IDS = {"premarket_brew", "intraday_loop", "eod_review", "daily_disarm", "anchor_head"}


@pytest.fixture(autouse=True)
def _no_ambient_vendor_secrets(monkeypatch):
    """Only the congress switches may decide anything here.

    The in-memory keyring is SESSION-scoped (root conftest), so a vendor secret another test
    stored would otherwise wire Alpaca/Finnhub into this runner and make the assertions below
    depend on test ORDER. Exactly the three VENDOR names are blanked — the audit HMAC key is
    deliberately left alone, because ``compose()`` mints and re-reads it and a blanket stub
    would break the composition test at the bottom of this file for an unrelated reason.
    """
    import core.secrets

    real = core.secrets.get_secret
    vendor = {main._ALPACA_KEY_NAME, main._ALPACA_SECRET_NAME, main._FINNHUB_KEY_NAME}
    monkeypatch.setattr(core.secrets, "get_secret",
                        lambda name: None if name in vendor else real(name))


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'congress-wiring.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(make_session_factory(engine), hmac_key=b"rw16-wiring-key",
                                   settings=Settings(), clock=CLOCK)


def runner_for(st, tmp_path, settings) -> IngestRunner:
    fetchers = main.build_ingest_fetchers(settings)
    return main.build_ingest_runner(st, parquet_root=tmp_path / "raw", fetchers=fetchers,
                                    settings=settings, clock=CLOCK, monotonic=lambda: 0.0)


def provisioned(**kw) -> Settings:
    return Settings(congress_enabled=True, congress_user_agent=UA, **kw)


# =======================================================================================
# 1. unprovisioned, per switch — a None value and a REASON, never an exception
# =======================================================================================
@pytest.mark.parametrize("settings,switch", [
    (Settings(), "COCKPIT_CONGRESS_ENABLED"),
    (Settings(congress_enabled=False, congress_user_agent=UA), "COCKPIT_CONGRESS_ENABLED"),
    (Settings(congress_enabled=True), "COCKPIT_CONGRESS_USER_AGENT"),
    (Settings(congress_enabled=True, congress_user_agent=""), "COCKPIT_CONGRESS_USER_AGENT"),
])
def test_an_unprovisioned_congress_feed_is_None_and_does_not_raise(settings, switch):
    """Either switch alone holds the feed off, and the KEY still exists carrying None.

    A missing key and a None value are not the same thing to a caller: ``fetchers["congress"]``
    must be answerable, and the answer must be "not provisioned" rather than ``KeyError``.
    """
    fetchers = main.build_ingest_fetchers(settings)       # must not raise

    assert FEED_CONGRESS in fetchers
    assert fetchers[FEED_CONGRESS] is None
    reason = fetchers.unprovisioned[FEED_CONGRESS]
    assert switch in reason, f"the reason must name the switch that turns it on: {reason!r}"


def test_the_default_settings_leave_congress_OFF():
    """Off by default is the documented posture, and it is a security property: turning this
    on starts fetching two .gov sites AND starts proposing orders."""
    assert Settings().congress_enabled is False
    assert main.build_ingest_fetchers(Settings())[FEED_CONGRESS] is None


def test_a_whitespace_only_user_agent_is_not_a_user_agent(tmp_path):
    """"   " is unset with extra steps. Fetching a .gov host with a blank UA is empirically a
    403, and an IP block then looks exactly like "Congress filed nothing"."""
    env = tmp_path / ".env"
    env.write_text("COCKPIT_CONGRESS_ENABLED=true\nCOCKPIT_CONGRESS_USER_AGENT=   \n",
                   encoding="utf-8")
    settings = Settings(_env_file=str(env))

    assert settings.congress_user_agent.strip() == ""
    fetchers = main.build_ingest_fetchers(settings)
    assert fetchers[FEED_CONGRESS] is None
    assert "COCKPIT_CONGRESS_USER_AGENT" in fetchers.unprovisioned[FEED_CONGRESS]


def test_the_reason_carried_into_status_is_the_one_that_was_LOGGED(caplog):
    """No second, independently-worded explanation of one absence.

    Two sentences for one fact is how they start disagreeing — and the startup WARNING has
    scrolled off the console by the time anyone reads /status, so /status's copy is the one
    that has to be right.
    """
    with caplog.at_level(logging.WARNING, logger="cockpit.main"):
        fetchers = main.build_ingest_fetchers(Settings(congress_enabled=True))

    reason = fetchers.unprovisioned[FEED_CONGRESS]
    remedy = reason.split("To enable: ", 1)[1]
    logged = [r.getMessage() for r in caplog.records
              if "NOT PROVISIONED" in r.getMessage() and "congress" in r.getMessage()]
    assert logged, "an unprovisioned feed must say so in the log, once, at WARNING"
    assert remedy in logged[0]


def test_a_broken_provider_constructor_never_takes_compose_down(monkeypatch):
    """The fail-closed-PER-FEED posture, attacked at its weakest joint.

    RW-16 pins the providers' ``fetch``, not their ``__init__``, so the composition root is
    coding against an interface it does not own. If that constructor changes under it, the
    correct outcome is one dead feed with a named reason — not a process that will not start.
    """
    import ingest.congress.house_clerk as house

    class Exploding:
        def __init__(self, **kwargs):
            raise RuntimeError("provider wiring moved")

    monkeypatch.setattr(house, "HouseClerkProvider", Exploding)

    fetchers = main.build_ingest_fetchers(provisioned())        # must not raise
    assert fetchers[FEED_CONGRESS] is None
    assert "provider wiring moved" in fetchers.unprovisioned[FEED_CONGRESS]


# =======================================================================================
# 2. status(): absent-and-explained, never present-and-silent
# =======================================================================================
def test_an_unprovisioned_congress_feed_is_ABSENT_from_status_feeds(st, tmp_path):
    """A feed row of zeroes next to the live feeds is the "present and silent" shape this
    package exists to make unreachable, so absence gets its OWN vocabulary."""
    runner = runner_for(st, tmp_path, Settings())
    status = runner.status()

    assert FEED_CONGRESS not in runner.polls
    assert FEED_CONGRESS not in runner.streams
    assert FEED_CONGRESS not in status["feeds"]
    entry = status["unprovisioned"][FEED_CONGRESS]
    assert entry["kind"] == "not_provisioned"
    assert "COCKPIT_CONGRESS_ENABLED" in entry["reason"]


def test_status_still_reports_exactly_the_wired_feeds_under_feeds(st, tmp_path):
    """``unprovisioned`` is a SIBLING of ``feeds``, not a member of it — the existing
    composition test (tests/workflows/test_main_compose.py) pins that identity and this feature
    must not have widened it."""
    runner = runner_for(st, tmp_path, provisioned())
    status = runner.status()

    assert status["feeds"].keys() == runner.polls.keys() | runner.streams.keys()
    assert set(status["unprovisioned"]) & set(status["feeds"]) == set()


def test_a_PROVISIONED_congress_feed_is_wired_and_no_longer_reported_absent(st, tmp_path):
    """The contrast control that makes every "absent" assertion above causal: with both
    switches set, the feed is a real poll feed with a real status row and no absence entry."""
    runner = runner_for(st, tmp_path, provisioned())
    status = runner.status()

    assert FEED_CONGRESS in runner.polls
    assert FEED_CONGRESS in status["feeds"]
    assert FEED_CONGRESS not in status["unprovisioned"]
    assert status["feeds"][FEED_CONGRESS]["polls"] == 0        # wired, not yet ticked


def test_a_registered_feed_can_never_also_be_reported_unprovisioned(st, tmp_path):
    """The two vocabularies must stay mutually exclusive. A feed that is both running and
    reported off is worse than either — it is a status page that cannot be believed."""
    runner = runner_for(st, tmp_path, provisioned())
    with pytest.raises(ValueError):
        runner.mark_unprovisioned(FEED_CONGRESS, "should be impossible")


def test_the_house_provider_is_wired_and_the_senate_one_is_separately_opt_in():
    """The Senate EFD is agreement-gated and far less machine-readable, so enabling the House
    feed must not silently acquire a second, flakier scraper nobody asked for."""
    house_only = main.build_ingest_fetchers(provisioned())[FEED_CONGRESS]
    assert [p.name for p in house_only] == ["house"]

    both = main.build_ingest_fetchers(provisioned(congress_senate_enabled=True))[FEED_CONGRESS]
    assert [p.name for p in both] == ["house", "senate"]


def test_every_wired_provider_carries_the_declared_contact_string():
    """The one kwarg that is mandatory. "Fetch house.gov anonymously" is not a degraded mode of
    this feed; it is a different and worse one, so a provider that cannot accept a contact
    string is REFUSED rather than constructed."""
    providers = main.build_ingest_fetchers(provisioned(congress_senate_enabled=True))
    for provider in providers[FEED_CONGRESS]:
        assert callable(getattr(provider, "fetch", None))
        assert UA in repr(vars(provider)), f"{provider.name} did not keep the declared UA"


def test_a_provider_that_refuses_the_contact_string_is_refused_in_turn(monkeypatch):
    """The check above, attacked: a constructor that quietly drops ``user_agent`` would leave
    the cockpit scraping a .gov host anonymously and believing it had declared itself."""
    import ingest.congress.house_clerk as house

    class NoContactString:
        name = "house"

        def __init__(self, *, rate_per_sec=None, http_get=None):
            pass

        def fetch(self, *, since, today):
            raise AssertionError("never reached")

    monkeypatch.setattr(house, "HouseClerkProvider", NoContactString)

    fetchers = main.build_ingest_fetchers(provisioned())
    assert fetchers[FEED_CONGRESS] is None
    assert "user_agent" in fetchers.unprovisioned[FEED_CONGRESS]


# =======================================================================================
# 3. the scheduler: ingest yes, the copy CYCLE no
# =======================================================================================
def test_the_congress_INGEST_feed_is_scheduled_on_its_own_cadence(st, tmp_path):
    """Reading disclosures is awareness — Engine 1. It gets a job like every other feed."""
    pytest.importorskip("apscheduler")
    runner = runner_for(st, tmp_path, provisioned())
    jobs = {j.id: j for j in sch.build_scheduler(st, CLOCK, ingest=runner).get_jobs()}

    assert "ingest_congress" in jobs
    trigger = str(jobs["ingest_congress"].trigger)
    # hourly at :20 on weekdays — PTRs publish in daily batches on business days, and the
    # signal is already 30-45 days old by law, so minutes buy nothing and every extra tick is
    # another request at a .gov host with no documented rate budget.
    assert "minute='20'" in trigger
    assert "day_of_week='mon-fri'" in trigger


def test_the_scheduler_registers_NO_copy_cycle_job_without_the_autoexec_lane(st, tmp_path):
    """RW-16'S OMISSION, STILL PINNED FOR EVERY DEPLOYMENT THAT DID NOT OPT IN.

    The copy cycle creates order rows in state ``proposed``. Under RW-16 a cron job would have
    quietly manufactured proposals off a 30-45 day-old signal into an approval queue nobody
    asked to fill. RW-17 changes that ONLY for the operator who set
    ``COCKPIT_CONGRESS_AUTOEXEC`` and whose lane fully composed — because the same switch is
    what puts an adjudicator behind the queue. Congress ingest alone must still add nothing.

    If you are here because this test failed: registering the cycle unconditionally re-creates
    exactly the unattended approval queue RW-16 refused, for operators who never asked for it.
    """
    pytest.importorskip("apscheduler")
    runner = runner_for(st, tmp_path, provisioned())
    jobs = sch.build_scheduler(st, CLOCK, ingest=runner).get_jobs()

    ids = {j.id for j in jobs}
    # EXACT, not a substring sweep: the base five plus one job per PROVISIONED poll feed, and
    # nothing else. Anything new — a copy cycle under any name — fails here.
    assert ids == BASE_JOB_IDS | {f"ingest_{feed}" for feed in runner.polls}
    assert not [j for j in jobs if "copy" in (j.id + j.name).lower()]


def test_the_copy_cycle_and_resolve_jobs_appear_WITH_the_autoexec_lane(st, tmp_path):
    """RW-17 — the reversal, pinned as deliberately as the omission was.

    The services are the switch: ``main.compose`` adds ``copy_cycle`` / ``autoexec_resolve`` to
    the registry only when the whole lane (opt-in flag + paper gateway + notification seam)
    composed. Given them, exactly two jobs appear, and their cadences are the ones justified in
    ``scheduler.py`` — the cycle inside the regular session (a rejection on an off-hours quote
    spends one of a filing's three copy attempts), the resolve tick every minute (well under the
    60s-floored cancel window, so the operator's window is never shorter than advertised).
    """
    pytest.importorskip("apscheduler")
    runner = runner_for(st, tmp_path, provisioned())
    services = {"copy_cycle": lambda state, clock: {}, "autoexec_resolve": lambda s, c: []}

    jobs = {j.id: j for j in sch.build_scheduler(st, CLOCK, services=services,
                                                 ingest=runner).get_jobs()}

    assert {"congress_copy_cycle", "autoexec_resolve"} <= set(jobs)
    assert set(jobs) == BASE_JOB_IDS | {f"ingest_{feed}" for feed in runner.polls} | {
        "congress_copy_cycle", "autoexec_resolve"}
    cycle = str(jobs["congress_copy_cycle"].trigger)
    assert "day_of_week='mon-fri'" in cycle and "hour='10,12,14'" in cycle
    assert "minute='*'" in str(jobs["autoexec_resolve"].trigger)


def test_the_only_congress_job_polls_the_INGEST_feed_and_nothing_else(st, tmp_path):
    """A job named for ingest must actually be ingest. Running it drives ``runner.poll`` for
    exactly the congress feed — it does not reach the copy engine."""
    pytest.importorskip("apscheduler")
    runner = runner_for(st, tmp_path, provisioned())
    polled: list[str] = []
    runner.poll = lambda feed: polled.append(feed)          # type: ignore[assignment]

    for job in sch.build_scheduler(st, CLOCK, ingest=runner).get_jobs():
        if "congress" in job.id:
            job.func()
    assert polled == ["congress"]


def test_the_scheduler_module_exposes_exactly_the_TWO_declared_copy_entrypoints():
    """The other way a cycle could arrive: a ``run_copy_*`` callable on the scheduler that
    something else then binds to a trigger.

    RW-16 asserted this list was EMPTY. RW-17 adds exactly two names and pins them by name, so
    the property survives the reversal: a third copy-shaped entrypoint appearing later is still
    a failing test, and the ingest cadence table still carries FEEDS only — a copy cycle must
    never be able to arrive dressed as a feed.
    """
    assert sorted(n for n in dir(sch) if "copy" in n.lower()) == [
        "CONGRESS_COPY_CYCLE_CRON", "run_congress_copy_cycle"]
    assert [k for k in sch.INGEST_CRON if "copy" in k.lower()] == []
    # the resolve tick must stay well under the cancel window Settings will accept (see the
    # constant's docstring): equal or longer, and the real window becomes scheduler jitter.
    assert sch.AUTOEXEC_RESOLVE_SECONDS <= 60


def test_no_registered_job_dispatches_to_anything_but_the_known_entrypoints(st, tmp_path):
    """Names, not ids. A copy cycle registered under an innocent id ("eod_extras") would clear
    every id-based check above, so this reads what each job's callable actually CALLS."""
    pytest.importorskip("apscheduler")
    runner = runner_for(st, tmp_path, provisioned())
    allowed = {"run_premarket_brew", "run_intraday_loop", "run_eod_review", "run_daily_disarm",
               "run_anchor_head", "run_ingest_poll", "get",      # "get" = services.get(...)
               "run_congress_copy_cycle", "run_autoexec_resolve"}      # RW-17, named on purpose

    for job in sch.build_scheduler(st, CLOCK, ingest=runner).get_jobs():
        referenced = set(job.func.__code__.co_names)
        assert referenced <= allowed, f"{job.id} dispatches to {sorted(referenced - allowed)}"


def test_the_copy_cycle_IS_WIRED_from_exactly_ONE_place():
    """RW-16 PINNED "THERE IS NO BUTTON". RW-17 REPLACES IT WITH "THERE IS EXACTLY ONE WIRE."

    This is the test the old one said would "have to be updated on purpose" once a real trigger
    was added, and this is that update. The property it protects is unchanged in kind: the copy
    cycle must have a KNOWN, ENUMERATED set of callers, so that a second trigger — an API route,
    a bridge command, a helper somebody adds — cannot appear without a reviewer seeing it. Two
    independent ways to start a cycle is two idempotency stories, and the composition root is
    the only place the wire belongs.

    ``load_disclosures`` gets the same treatment for the same reason: RW-16 recorded that the
    ingest side stored rows nothing read back, and ``main.build_copy_cycle_service`` is now the
    one reader.
    """
    from pathlib import Path

    from schemas.models.congress_disclosure import load_disclosures
    from workflows.copytrade_orders import run_copy_cycle

    assert callable(run_copy_cycle) and callable(load_disclosures)

    app_root = Path(main.__file__).resolve().parent

    def callers_of(symbol: str) -> list[str]:
        return sorted(
            rel
            for p in app_root.rglob("*.py")
            if not (rel := p.relative_to(app_root).as_posix()).startswith(("tests/", ".venv/"))
            and (f"{symbol}(" in (body := p.read_text(encoding="utf-8"))
                 or f"import {symbol}" in body)
        )

    assert callers_of("run_copy_cycle") == ["main.py", "workflows/copytrade_orders.py"], \
        "run_copy_cycle acquired a second trigger; the composition root is the only wire"
    assert callers_of("load_disclosures") == ["main.py",
                                              "schemas/models/congress_disclosure.py"]


# =======================================================================================
# end to end: the absence survives the real composition root
# =======================================================================================
def test_compose_reports_congress_absent_with_its_reason(tmp_path):
    """The whole path, not the pieces: ``get_settings`` -> ``build_ingest_fetchers`` ->
    ``build_ingest_runner`` -> ``app.state.ingest``, which is what /status reads.

    An unconfigured cockpit must answer "congress is OFF because you have not set
    COCKPIT_CONGRESS_ENABLED" rather than presenting a feed list that merely lacks a name the
    operator was looking for.
    """
    pytest.importorskip("fastapi")
    _, app, _ = main.compose(db_url=f"sqlite:///{(tmp_path / 'compose.db').as_posix()}",
                             parquet_root=str(tmp_path / "raw"))
    status = app.state.ingest.status()

    assert FEED_CONGRESS not in status["feeds"]
    assert "COCKPIT_CONGRESS_ENABLED" in status["unprovisioned"][FEED_CONGRESS]["reason"]
    # ``feeds`` still holds exactly the wired feeds (the identity test_main_compose pins)
    assert status["feeds"].keys() == app.state.ingest.polls.keys() | \
        app.state.ingest.streams.keys()


# =======================================================================================
# the load-bearing kwargs must survive _supported_kwargs (regression, RW-16)
# =======================================================================================
# ``main._supported_kwargs`` drops any kwarg the callee does not declare, and its docstring
# makes the CALLER responsible for checking that the load-bearing ones came through. The
# provider path does that for ``user_agent``; ``CongressPollAdapter._poller_for`` did not, so a
# poller signature that ever lost ``store=`` or ``on_event=`` would have been built without it
# — parsing every filing and then writing/emitting nothing, while the feed reported a clean
# poll and a tidy zero. "The congressional feed was quiet today" is the one answer this whole
# package exists to make impossible, so a missing seam must REFUSE, never degrade.
class _PollerMissingStore:
    """A poller whose signature has no ``store=`` — the drift being guarded against."""

    def __init__(self, providers, *, on_event, clock=None, dedup=None):
        self.providers = providers
        self.on_event = on_event

    def poll(self):                                     # pragma: no cover - never reached
        raise AssertionError("must not be constructed, let alone polled")


class _PollerMissingSink:
    """A poller whose signature has no ``on_event=``: it would parse and emit to nobody."""

    def __init__(self, providers, *, store=None, clock=None, dedup=None):
        self.providers = providers

    def poll(self):                                     # pragma: no cover - never reached
        raise AssertionError("must not be constructed, let alone polled")


@pytest.mark.parametrize(
    "factory, dropped",
    [(_PollerMissingStore, "store"), (_PollerMissingSink, "on_event")],
)
def test_congress_adapter_refuses_a_poller_that_would_silently_drop_evidence(factory, dropped):
    """A poller that cannot accept a load-bearing seam is REFUSED, and the refusal names it.

    Fails without the guard in ``CongressPollAdapter._poller_for``: ``_supported_kwargs``
    filters the missing kwarg out, the poller constructs happily, and the wiring bug becomes a
    silent evidence loss instead of a visible feed error.
    """
    adapter = main.CongressPollAdapter(
        (), store=object(), clock=FrozenClock(datetime(2026, 8, 10, tzinfo=timezone.utc)),
        poller_factory=factory,
    )
    with pytest.raises(TypeError) as exc:
        adapter.poll(0.0, lambda event: None)

    assert dropped in str(exc.value)
    # It must refuse at CONSTRUCTION - never build the object and discover the loss at poll time.
    assert adapter._poller is None


def test_congress_adapter_builds_when_every_load_bearing_kwarg_survives():
    """The guard must not fire on the real poller shape (it accepts both seams)."""

    built = {}

    class _GoodPoller:
        def __init__(self, providers, *, on_event, store=None, clock=None, dedup=None):
            built["on_event"], built["store"] = on_event, store

        def poll(self):
            return type("R", (), {"emitted": 0})()

    def sink(event):
        return None

    store = object()
    adapter = main.CongressPollAdapter(
        (), store=store, clock=FrozenClock(datetime(2026, 8, 10, tzinfo=timezone.utc)),
        poller_factory=_GoodPoller,
    )
    assert adapter.poll(0.0, sink) == 0
    assert built["on_event"] is sink and built["store"] is store
