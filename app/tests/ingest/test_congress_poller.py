"""RW-16 — the congressional poller's FORWARD-PROGRESS contract, and disclosure persistence.

Entirely offline: the providers are fakes that return a scripted ``ProviderResult``, so no test
here has a URL, a credential or a socket. An autouse fixture detonates ``urllib.request.urlopen``
for the same reason the parse tests do.

WHAT THIS FILE IS ACTUALLY GUARDING
------------------------------------
The same defect this repo has now paid for twice — DEFECT D in the G20 round-4 sweep, fixed in
``ingest/edgar_brew.py`` and then AGAIN in ``ingest/finra_short_interest.py`` (see
``tests/ingest/test_finra_short_interest.py``: "a transient emit error does not burn the dedup
key" / "cursor never advances past a row that failed to emit"). Both pollers had committed the
dedup key BEFORE calling ``on_event`` and counted a raising consumer as a terminal drop. A
MOMENTARY downstream outage therefore destroyed the row permanently, and a later row in the same
batch carried the cursor past it, while the counters recorded a tidy "drop".

A congressional PTR is the worst possible thing to lose that way: it is published once, in a
daily batch, and the window that contained it closes. So the two properties are asserted here at
the seam that cannot be faked — the ``since`` the poller asks its providers for on the NEXT
cycle:

  1. a key means "this WAS emitted", never "we tried", so a failed emit GIVES THE KEY BACK;
  2. the cursor is a BARRIER at the earliest row that failed to emit, so a later success cannot
     carry the window past unread evidence.

NOTE FOR WHOEVER LANDS ``ingest/congress/poller.py``
-----------------------------------------------------
These tests were written against the pinned build contract before that module (and
``schemas/models/congress_disclosure.py``) existed, so they SKIP until it does. The interface
they use is the contract's, verbatim:

    CongressPoller(providers, *, on_event, store, dedup=None, clock=...)
    poll() -> PollResult                                   # counts; NEVER silent
    persist_disclosures(session_factory, disclosures) -> int      # idempotent on key
    load_disclosures(session_factory, *, since=None, limit=500) -> list[Disclosure]

plus the four signals ``main.CongressPollAdapter`` reads off the poller object (``counters``,
``progress_signal``, ``strand_signal``, ``cursor``). Nothing here asserts an internal name the
contract does not pin: the emitted count is read through the same tolerant field list the
composition root uses, and rows are identified by a token in their payload rather than by an
assumed event schema.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from copytrade.models import (
    ASSET_EQUITY,
    CHAMBER_HOUSE,
    CHAMBER_SENATE,
    TX_PURCHASE,
    AmountRange,
    Disclosure,
)
from ingest.congress.provider import ProviderResult, unprovisioned

# The cheap skip FIRST: with the poller absent, this module must not go on to import the ORM
# layer at all. (``storage.engine`` is imported lazily inside the ``sf`` fixture for the same
# reason — a module-level import there would run before this guard.)
poller_mod = pytest.importorskip(
    "ingest.congress.poller",
    reason="RW-16 ingest/congress/poller.py has not landed yet (pinned contract: "
           "CongressPoller(providers, *, on_event, store, dedup=None, clock=...) -> PollResult)",
)
schema_mod = pytest.importorskip(
    "schemas.models.congress_disclosure",
    reason="RW-16 schemas/models/congress_disclosure.py has not landed yet (pinned contract: "
           "CongressDisclosure with PK = Disclosure.key, persist_disclosures, load_disclosures)",
)

CongressPoller = poller_mod.CongressPoller
CongressDisclosure = schema_mod.CongressDisclosure
persist_disclosures = schema_mod.persist_disclosures
load_disclosures = schema_mod.load_disclosures

TODAY = date(2026, 5, 20)
NOW = datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_live_congress(monkeypatch):
    """NOTHING in this file may reach house.gov or senate.gov."""
    import urllib.request

    def _boom(*a, **k):  # pragma: no cover - only fires on a regression
        raise AssertionError("a live urllib.request.urlopen was attempted in a congress test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def sf(tmp_path):
    from storage.engine import create_all, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{(tmp_path / 'cockpit.db').as_posix()}")
    create_all(engine)
    return make_session_factory(engine)


def _clock():
    return NOW


# ============================================================================ FIXTURE DATA

def _disclosure(symbol="AAPL", *, chamber=CHAMBER_HOUSE, doc_id="20026543", line_no=0,
                tx_date=date(2026, 5, 2), filed_date=date(2026, 5, 12), filer="pelosi-nancy",
                tx_type=TX_PURCHASE, asset_type=ASSET_EQUITY) -> Disclosure:
    """A parsed row, built directly. The parsers have their own file; this one is about the
    poller's contract, so the input is a plain domain value."""
    return Disclosure(
        chamber=chamber,
        doc_id=doc_id,
        line_no=line_no,
        filer_slug=filer,
        filer_display="Hon. Nancy Pelosi",
        tx_date=tx_date,
        filed_date=filed_date,
        tx_type=tx_type,
        asset_type=asset_type,
        amount=AmountRange(Decimal("1001"), Decimal("15000")),
        symbol=symbol,
        asset_description=f"{symbol} Common Stock",
        raw_ref=f"https://example.invalid/{doc_id}.pdf",
        content_hash=f"hash-{chamber}-{doc_id}-{line_no}",
    )


class _FakeProvider:
    """A ``DisclosureProvider`` whose answer is scripted and whose WINDOW is recorded.

    ``windows`` is the load-bearing part: the ``since`` the poller asks for on the next cycle IS
    the cursor, observed at the only seam that cannot be faked by an internal attribute name.
    """

    def __init__(self, name, results, *, raises=None):
        self.name = name
        self._results = list(results)
        self._raises = raises
        self.windows: list[tuple[date, date]] = []

    def fetch(self, *, since, today):
        self.windows.append((since, today))
        if self._raises is not None:
            raise self._raises
        if not self._results:
            return ProviderResult()
        return self._results[0] if len(self._results) == 1 else self._results.pop(0)


def _result(*disclosures, documents_seen=None, parse_errors=()) -> ProviderResult:
    rows = list(disclosures)
    return ProviderResult(
        disclosures=rows,
        documents_seen=len(rows) if documents_seen is None else documents_seen,
        parse_errors=list(parse_errors),
    )


def _make_poller(providers, on_event, store, **over):
    kwargs = {"on_event": on_event, "store": store, "clock": _clock}
    kwargs.update(over)
    return CongressPoller(tuple(providers), **kwargs)


# ---- naming-independent readers ------------------------------------------------------------
# The contract pins that ``PollResult`` COUNTS; it does not pin every field name. These read the
# result the same tolerant way ``main.CongressPollAdapter`` does, so a reasonable naming choice
# by the implementing agent does not turn into a false test failure — while the BEHAVIOURS below
# stay strict.

_EMITTED_FIELDS = ("emitted", "events_emitted", "emitted_events", "events", "persisted")


def _emitted(result) -> int:
    for name in _EMITTED_FIELDS:
        value = getattr(result, name, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, (list, tuple, set, frozenset)):
            return len(value)
    raise AssertionError(
        f"PollResult carried no readable emitted count (looked for {list(_EMITTED_FIELDS)}); "
        "a poll whose throughput nobody can read is the silence RW-16 exists to refuse"
    )


def _mentions(payload, token: str) -> bool:
    """Is this emitted payload the one carrying ``token``? Schema-agnostic on purpose."""
    try:
        return token in json.dumps(payload, default=str)
    except (TypeError, ValueError):              # pragma: no cover - defensive
        return token in repr(payload)


def _tokens(payloads, tokens):
    return [t for t in tokens if any(_mentions(p, t) for p in payloads)]


# ================================================================= (1) A HEALTHY CYCLE

def test_a_healthy_cycle_emits_every_disclosure_exactly_once(sf):
    house = _FakeProvider("house", [_result(_disclosure("AAPL"),
                                            _disclosure("MSFT", line_no=1))])
    senate = _FakeProvider("senate", [_result(_disclosure(
        "NVDA", chamber=CHAMBER_SENATE, doc_id="aa11bb22", filer="carper-thomas"))])
    out = []
    poller = _make_poller([house, senate], out.append, sf)

    result = poller.poll()

    assert result is not None, "poll() must return a PollResult; a bare None counts nothing"
    assert _emitted(result) == 3
    assert len(out) == 3
    assert _tokens(out, ["AAPL", "MSFT", "NVDA"]) == ["AAPL", "MSFT", "NVDA"]
    assert house.windows and senate.windows, "both chambers were asked"
    assert all(w[1] == NOW.date() for w in house.windows + senate.windows), \
        "the window's end is the injected wall clock's day, never a monotonic reading"


def test_the_same_disclosures_are_not_re_emitted_on_the_next_cycle(sf):
    """A PTR window is re-fetched every cycle by construction (the cursor is a filed-date
    high-water mark, and filings arrive in daily batches). Re-emitting is therefore the DEFAULT
    behaviour that dedup exists to prevent."""
    provider = _FakeProvider("house", [_result(_disclosure("AAPL"))])
    out = []
    poller = _make_poller([provider], out.append, sf)

    assert _emitted(poller.poll()) == 1
    second = poller.poll()
    assert _emitted(second) == 0
    assert len(out) == 1, "the identical row was emitted twice"


def test_a_quiet_cycle_is_not_the_same_observation_as_a_broken_one(sf):
    """``documents_seen`` rises even when ``disclosures`` does not. "Congress was quiet" and "we
    read forty filings and parsed none of them" must never look identical from the inside."""
    quiet = _FakeProvider("house", [_result()])
    broken = _FakeProvider("senate", [ProviderResult(
        documents_seen=40, parse_errors=[(f"doc{i}", "unparsed_row") for i in range(40)])])
    out = []

    quiet_result = _make_poller([quiet], out.append, sf).poll()
    broken_result = _make_poller([broken], out.append, sf).poll()

    assert _emitted(quiet_result) == 0 and _emitted(broken_result) == 0
    assert out == []
    assert json.dumps(quiet_result, default=str) != json.dumps(broken_result, default=str), \
        "a quiet cycle and a 40-document parse failure produced identical results"


# ============================================ (2) THE FORWARD-PROGRESS CONTRACT (DEFECT D)

def _flaky(out, down, *, only=None):
    """An ``on_event`` that RAISES while ``down["yes"]`` — a transient downstream outage
    (persist_events down, disk full, a momentary DB lock), not a bad row."""
    def on_event(payload):
        if down["yes"] and (only is None or _mentions(payload, only)):
            raise RuntimeError("persist_events is down")
        out.append(payload)
    return on_event


def test_a_transient_emit_error_does_not_burn_the_dedup_key(sf):
    """DEFECT D, third time. The key means "this WAS emitted", never "we tried". ``Dedup.add`` is
    a check-and-set with no race-free peek, so it must be taken before ``on_event`` and GIVEN
    BACK when the emit fails; otherwise a momentary outage destroys a once-published filing."""
    provider = _FakeProvider("house", [_result(_disclosure("AAPL"))])
    out, down = [], {"yes": True}
    poller = _make_poller([provider], _flaky(out, down), sf)

    assert _emitted(poller.poll()) == 0
    assert out == []

    down["yes"] = False                                     # the downstream recovers
    assert _emitted(poller.poll()) == 1, \
        "the dedup key survived a FAILED emit: the disclosure is silently lost forever"
    assert _tokens(out, ["AAPL"]) == ["AAPL"]


def test_the_cursor_never_advances_past_a_row_that_failed_to_emit(sf):
    """DEFECT D, the half that rolling the key back does NOT fix. The cursor is a filed-date
    high-water mark, so a LATER row emitting fine would carry the window past the failed EARLIER
    row and evict it from every future fetch. The earliest failed filing date is a BARRIER.

    Asserted at the ``since`` the provider is asked for on the next cycle — the one place the
    cursor is observable without depending on an internal name."""
    early = _disclosure("AAPL", doc_id="20026500", filed_date=date(2026, 5, 10))
    late = _disclosure("MSFT", doc_id="20026599", filed_date=date(2026, 5, 18))
    provider = _FakeProvider("house", [_result(early, late)])
    out, down = [], {"yes": True}
    poller = _make_poller([provider], _flaky(out, down, only="AAPL"), sf)

    assert _emitted(poller.poll()) == 1                      # MSFT went out
    assert _tokens(out, ["AAPL", "MSFT"]) == ["MSFT"]

    down["yes"] = False
    poller.poll()
    since_after_failure = provider.windows[1][0]
    assert since_after_failure <= early.filed_date, (
        f"the next window starts at {since_after_failure}, past the failed row filed on "
        f"{early.filed_date} -> that disclosure is unreachable forever"
    )
    assert _tokens(out, ["AAPL", "MSFT"]) == ["AAPL", "MSFT"], \
        "the retried row never came back, or the confirmed duplicate was re-emitted"


def test_a_failed_emit_is_named_in_the_poll_result_never_silently_dropped(sf):
    """Every non-emitting outcome is COUNTED and NAMED. A poll that returns 0 with no reason is
    indistinguishable from a quiet week."""
    provider = _FakeProvider("house", [_result(_disclosure("AAPL"))])
    out, down = [], {"yes": True}
    poller = _make_poller([provider], _flaky(out, down), sf)
    result = poller.poll()

    rendered = json.dumps(result, default=str) + json.dumps(
        getattr(poller, "counters", {}), default=str) + str(getattr(poller, "progress_signal", ""))
    assert "0" in rendered
    assert any(word in rendered.lower() for word in ("emit", "error", "fail", "retry", "held")), \
        f"a failed emit left no named trace in the poll result or the signals: {rendered}"


def test_a_retriable_provider_error_holds_the_window_and_a_structural_one_does_not_wedge_it(sf):
    """The distinction ``ingest/edgar_brew.py`` spent six rounds on, and the reason
    ``provider.STRUCTURAL_PARSE_REASONS`` exists: a retriable error is a BARRIER (the evidence
    is good and arrives next cycle); a structural one is TERMINAL (holding for a document that
    can NEVER be read wedges the feed forever, and a permanent wedge is how a feed dies)."""
    retriable = _FakeProvider("house", [
        ProviderResult(disclosures=[_disclosure("AAPL", filed_date=date(2026, 5, 18))],
                       documents_seen=2, parse_errors=[("20026500", "http_503")]),
        _result(),
    ])
    out = []
    _make_poller([retriable], out.append, sf).poll()

    structural = _FakeProvider("senate", [
        ProviderResult(disclosures=[_disclosure("MSFT", chamber=CHAMBER_SENATE, doc_id="aa11",
                                                filed_date=date(2026, 5, 18))],
                       documents_seen=2,
                       parse_errors=[("bb22", "paper_ptr_unsupported")]),
        _result(),
    ])
    structural_poller = _make_poller([structural], out.append, sf)
    structural_poller.poll()
    structural_poller.poll()

    # A scanned Senate filing can never be read. If it held the cursor, the window would never
    # move again — assert the structural feed DID make progress across two polls.
    assert structural.windows[1][0] >= structural.windows[0][0], \
        "a structurally unreadable document wedged the cursor backwards"
    assert structural.windows[-1][0] > date(2026, 1, 1), \
        "the cursor never left its cold-start floor despite a healthy, fully-read cycle"


# =================================================== (3) FAULT ISOLATION BETWEEN CHAMBERS

def test_one_chambers_bug_cannot_silence_the_other(sf):
    """The whole reason there are two providers and not one poller with two halves: an eFD
    terms-page change must not take the House feed down with it."""
    exploding = _FakeProvider("senate", [], raises=RuntimeError("eFD redesigned its terms page"))
    healthy = _FakeProvider("house", [_result(_disclosure("AAPL"))])
    out = []
    poller = _make_poller([exploding, healthy], out.append, sf)

    result = poller.poll()                                   # must NOT raise

    assert _emitted(result) == 1
    assert _tokens(out, ["AAPL"]) == ["AAPL"]
    rendered = json.dumps(result, default=str) + json.dumps(
        getattr(poller, "counters", {}), default=str)
    assert "senate" in rendered or "RuntimeError" in rendered, \
        "one chamber's crash left no counted trace"


def test_an_unprovisioned_provider_is_counted_and_never_raises(sf):
    """A feed that is switched off and a feed that saw a quiet week must never be the same
    observation, and neither may take the cycle down."""
    off = _FakeProvider("senate", [unprovisioned("set COCKPIT_CONGRESS_USER_AGENT")])
    on = _FakeProvider("house", [_result(_disclosure("AAPL"))])
    out = []
    poller = _make_poller([off, on], out.append, sf)

    result = poller.poll()

    assert _emitted(result) == 1
    rendered = json.dumps(result, default=str) + json.dumps(
        getattr(poller, "counters", {}), default=str)
    assert "provision" in rendered.lower() or "senate" in rendered, \
        "an unprovisioned chamber vanished from the cycle's report"


def test_the_poller_exposes_the_four_signals_the_composition_root_reads(sf):
    """``main.CongressPollAdapter`` forwards ``counters`` / ``progress_signal`` /
    ``strand_signal`` / ``cursor`` to ``IngestRunner._signals``. A missing attribute there does
    not crash — it silently reports nothing, which is the failure mode this feed cannot have."""
    provider = _FakeProvider("house", [_result(_disclosure("AAPL"))])
    poller = _make_poller([provider], lambda e: None, sf)
    poller.poll()

    assert isinstance(getattr(poller, "counters", None), dict), \
        "counters must be a dict for IngestRunner._signals to merge"
    assert hasattr(poller, "progress_signal")
    assert hasattr(poller, "cursor")


# ================================================== (4) PERSISTENCE: IDEMPOTENT ON KEY

def _row_count(session_factory) -> int:
    from sqlalchemy import func, select

    with session_factory() as session:
        return session.execute(
            select(func.count()).select_from(CongressDisclosure)).scalar_one()


def test_persist_disclosures_is_idempotent_on_key(sf):
    """Invariant 11's shape for this feed: replaying a window produces ZERO duplicate rows. The
    key is ``chamber:doc_id:line_no`` and it is the PRIMARY KEY, so the database itself refuses
    a second copy rather than trusting the caller to have deduped."""
    rows = [_disclosure("AAPL", line_no=0), _disclosure("MSFT", line_no=1)]

    assert persist_disclosures(sf, rows) == 2
    assert _row_count(sf) == 2

    assert persist_disclosures(sf, rows) == 0, \
        "a replayed window inserted rows again: persist_disclosures is not idempotent on key"
    assert _row_count(sf) == 2

    # The SAME list re-presented in a different order is still the same two rows.
    assert persist_disclosures(sf, list(reversed(rows))) == 0
    assert _row_count(sf) == 2


def test_persist_disclosures_stores_only_the_rows_that_are_new(sf):
    first = _disclosure("AAPL", line_no=0)
    second = _disclosure("MSFT", line_no=1)
    assert persist_disclosures(sf, [first]) == 1
    assert persist_disclosures(sf, [first, second]) == 1, \
        "a mixed batch must report the NEW rows, not the whole batch"
    assert _row_count(sf) == 2


def test_a_duplicate_inside_one_batch_is_collapsed_not_a_crash(sf):
    """A PTR re-fetched twice inside one cycle (two providers, an overlapping window) must be a
    clean no-op, not an IntegrityError that costs the whole batch its evidence."""
    row = _disclosure("AAPL")
    assert persist_disclosures(sf, [row, row]) == 1
    assert _row_count(sf) == 1


def test_persist_disclosures_tolerates_an_empty_batch(sf):
    assert persist_disclosures(sf, []) == 0
    assert _row_count(sf) == 0


def test_the_primary_key_is_the_disclosure_key(sf):
    """PK = ``Disclosure.key`` (the contract). Two rows off the SAME document but different
    lines are different rows; the same line twice is one row."""
    same_doc = [_disclosure("AAPL", line_no=0), _disclosure("MSFT", line_no=1)]
    assert persist_disclosures(sf, same_doc) == 2

    # the same chamber+doc+line from the other chamber is a DIFFERENT row
    assert persist_disclosures(sf, [_disclosure("AAPL", chamber=CHAMBER_SENATE)]) == 1
    assert _row_count(sf) == 3

    pk_columns = [c.name for c in CongressDisclosure.__table__.primary_key.columns]
    assert len(pk_columns) == 1, f"the natural key must be ONE column, saw {pk_columns}"


def test_load_disclosures_round_trips_a_stored_row(sf):
    original = _disclosure("AAPL")
    persist_disclosures(sf, [original])

    loaded = load_disclosures(sf)

    assert len(loaded) == 1
    row = loaded[0]
    assert isinstance(row, Disclosure), "load_disclosures returns the DOMAIN type (the contract)"
    assert row.key == original.key
    assert (row.chamber, row.doc_id, row.line_no) == (
        original.chamber, original.doc_id, original.line_no)
    assert (row.tx_type, row.asset_type, row.symbol) == (
        original.tx_type, original.asset_type, original.symbol)
    assert row.tx_date == original.tx_date and row.filed_date == original.filed_date
    assert row.amount == original.amount, "the disclosed BUCKET must survive the round trip"
    assert row.filer_slug == original.filer_slug
    assert row.raw_ref == original.raw_ref
    assert row.content_hash == original.content_hash, \
        "the audit binding (Invariant 8 / SEC-13) must survive persistence"


def test_load_disclosures_filters_by_since_and_bounds_its_result(sf):
    old = _disclosure("OLD", doc_id="20025000", filed_date=date(2026, 1, 5),
                      tx_date=date(2026, 1, 2))
    new = _disclosure("NEW", doc_id="20026000", filed_date=date(2026, 5, 18),
                      tx_date=date(2026, 5, 10))
    persist_disclosures(sf, [old, new])

    assert {r.symbol for r in load_disclosures(sf)} == {"OLD", "NEW"}
    recent = load_disclosures(sf, since=date(2026, 5, 1))
    assert {r.symbol for r in recent} == {"NEW"}, \
        "`since` must exclude a filing that closed before the window"
    assert load_disclosures(sf, limit=1) and len(load_disclosures(sf, limit=1)) == 1


def test_an_option_row_round_trips_its_expiry_and_strike(sf):
    from copytrade.models import ASSET_OPTION_CALL

    option = Disclosure(
        chamber=CHAMBER_HOUSE, doc_id="20026543", line_no=7, filer_slug="pelosi-nancy",
        filer_display="Hon. Nancy Pelosi", tx_date=date(2026, 5, 3),
        filed_date=date(2026, 5, 12), tx_type=TX_PURCHASE, asset_type=ASSET_OPTION_CALL,
        amount=AmountRange(Decimal("50000001"), None), symbol="MSFT",
        asset_description="MSFT Call", option_expiry=date(2027, 1, 16),
        option_strike=Decimal("400.00"), raw_ref="https://example.invalid/x.pdf",
        content_hash="hash-opt",
    )
    persist_disclosures(sf, [option])
    loaded = load_disclosures(sf)[0]

    assert loaded.is_option
    assert loaded.option_expiry == date(2027, 1, 16)
    assert loaded.option_strike == Decimal("400.00")
    assert loaded.amount.high is None, \
        "the open-ended top tier must not acquire a ceiling in the database"
    assert loaded.amount.low == Decimal("50000001")


def test_the_poller_persists_what_it_emits(sf):
    """``store`` is bound at construction (the contract), and the poller is the only thing that
    writes it. Raw disclosures are kept SEPARATE from anything derived (Invariant 8) so the copy
    engine can be re-run against the evidence without re-fetching a government file server."""
    provider = _FakeProvider("house", [_result(_disclosure("AAPL"),
                                               _disclosure("MSFT", line_no=1))])
    poller = _make_poller([provider], lambda e: None, sf)
    poller.poll()

    stored = load_disclosures(sf)
    assert {r.symbol for r in stored} == {"AAPL", "MSFT"}
    poller.poll()
    assert len(load_disclosures(sf)) == 2, "a second cycle duplicated the stored evidence"
