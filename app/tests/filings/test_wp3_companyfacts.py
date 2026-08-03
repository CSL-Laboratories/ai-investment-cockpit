"""WP3 tests — ``edgar/companyfacts.py``: fetch + deterministic flatten of XBRL facts.

Replays the recorded ``companyfacts.json`` fixtures (zero live calls). WP3 only fetches and
flattens; delta computation is WP4's LLM-free core, not exercised here.
"""
from __future__ import annotations

from filings.edgar.companyfacts import (
    XbrlFactRecord,
    facts_for_accession,
    fetch_companyfacts,
    iter_facts,
)
from filings.edgar.fetch import SecFetcher
from tests.filings._sec_replay import FixtureTransport, VirtualTime

UA = "Lucas Salvador lucassalvador987@gmail.com (personal research)"
NVDA = "0001045810"


def make_fetcher(transport):
    vt = VirtualTime()
    return SecFetcher(
        user_agent=UA, rate_per_sec=2.0, transport=transport,
        monotonic=vt.monotonic, sleep=vt.sleep,
    )


def test_fetch_and_flatten_companyfacts_from_fixture():
    transport = FixtureTransport()
    fetcher = make_fetcher(transport)
    facts = fetch_companyfacts(fetcher, NVDA)
    assert facts["entityName"] == "NVIDIA CORP"
    rows = list(iter_facts(facts))
    assert rows, "expected flattened XBRL facts"
    r = rows[0]
    assert isinstance(r, XbrlFactRecord)
    # every flattened row carries the taxonomy/concept/unit + the reporting accession.
    assert r.taxonomy and r.concept and r.unit
    # the UA rode on the companyfacts request
    assert all(h["User-Agent"] == UA for _m, _u, h in transport.requests)


def test_iter_facts_is_deterministic_across_repeat_walks():
    transport = FixtureTransport()
    facts = fetch_companyfacts(make_fetcher(transport), NVDA)
    a = list(iter_facts(facts))
    b = list(iter_facts(facts))
    assert a == b  # frozen dataclasses, insertion-order walk -> identical sequences


def test_iter_facts_tolerates_malformed_structure():
    # a malformed concept/unit/row is skipped, never crashing the walk
    facts = {
        "facts": {
            "us-gaap": {
                "Good": {"label": "L", "units": {"USD": [{"val": 1, "accn": "a-1"}]}},
                "BadUnits": {"label": "x", "units": "not-a-dict"},
                "BadBody": "not-a-dict",
            },
            "bad-tax": "not-a-dict",
        }
    }
    rows = list(iter_facts(facts))
    assert [(r.concept, r.val, r.accession) for r in rows] == [("Good", 1, "a-1")]


def test_facts_for_accession_filters_by_reporting_filing():
    transport = FixtureTransport()
    facts = fetch_companyfacts(make_fetcher(transport), NVDA)
    all_rows = list(iter_facts(facts))
    some_acc = next(r.accession for r in all_rows if r.accession)
    subset = facts_for_accession(facts, some_acc)
    assert subset, "expected at least one fact for a real accession"
    assert all(r.accession == some_acc for r in subset)


def test_iter_facts_empty_when_no_facts_block():
    assert list(iter_facts({})) == []
    assert list(iter_facts({"facts": "nope"})) == []
