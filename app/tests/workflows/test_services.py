"""G8 — service-registry + EOD review tests (Owner B).

Enforces the WP8 'services.py' contract of docs/build-scaffold-ultracode.md WP8 and SEC-20
(EVENT_DRAWDOWN_MARK feeds the performance gate): build_services returns injected
``(state, clock) -> dict`` callables for the WP5 scheduler seams ({"brew","intraday",
"eod"}) plus the fresh ``ask`` seam, the scheduler stubs stay NotImplementedError, and
eod_review records the drawdown deterministically (no LLM). Hermetic: FrozenClock, tmp
SQLite; the service closures run against empty windows so no transport is needed.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from orchestrator.scheduler import run_eod_review, run_intraday_loop, run_premarket_brew
from orchestrator.state import EVENT_DRAWDOWN_MARK
from schemas.models.audit_entry import AuditEntry
from workflows.services import build_services, eod_review


def _payloads(st, event_type):
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry)
                            .where(AuditEntry.event_type == event_type)).scalars().all()
    return [dict(e.payload or {}) for e in entries if st.audit.verify_entry(e)]


def test_build_services_returns_scheduler_and_fresh_question_seams(st):
    services = build_services(st)
    assert set(services) == {"brew", "intraday", "eod", "ask"}
    assert all(callable(fn) for fn in services.values())


def test_w23_service_registry_threads_the_edge_provider_to_drafting():
    import inspect

    source = inspect.getsource(build_services)
    assert "edge_provider=edge_provider" in source


def test_question_service_reaches_fresh_interrogation(st, monkeypatch):
    seen = {}

    def fake_fresh(state, **kwargs):
        seen.update(kwargs)
        return {"pattern": "fresh", "answer": "checked"}

    monkeypatch.setattr("workflows.services.fresh_interrogate", fake_fresh)
    answer = build_services(st, transport="transport", parquet_root="raw")["ask"](
        st, question="Why?", proposal_id="prop-1"
    )
    assert answer["pattern"] == "fresh"
    assert seen["question"] == "Why?" and seen["proposal_id"] == "prop-1"
    assert seen["transport"] == "transport" and seen["parquet_root"] == "raw"


def test_services_run_via_the_scheduler_hooks(st, clock):
    """The WP5 scheduler dispatches injected services; defaults keep raising (contract:
    scheduler.py is NOT modified — test_not_wired_workflows_raise stays green)."""
    services = build_services(st)
    assert run_premarket_brew(st, clock, service=services["brew"])["status"] == "no_events"
    # clock = 2026-07-07 14:00 UTC == 10:00 ET Tuesday -> market open, service invoked
    assert run_intraday_loop(st, clock, service=services["intraday"])["status"] == "no_events"
    assert run_eod_review(st, clock, service=services["eod"])["status"] == "ok"
    with pytest.raises(NotImplementedError):
        run_premarket_brew(st, clock)
    with pytest.raises(NotImplementedError):
        run_eod_review(st, clock)


def test_eod_review_records_drawdown_mark_for_the_gate(st, clock):
    result = eod_review(st, clock, drawdown_fn=lambda: "123.45")
    assert result["status"] == "ok" and result["drawdown_recorded"] is True
    assert result["at"] == clock.now().isoformat()
    marks = _payloads(st, EVENT_DRAWDOWN_MARK)
    assert marks == [{"drawdown": "123.45", "_ts": marks[0]["_ts"]}]  # str payload (SEC-20)


def test_eod_review_without_drawdown_fn_records_nothing(st, clock):
    result = eod_review(st, clock)
    assert result["drawdown_recorded"] is False
    assert _payloads(st, EVENT_DRAWDOWN_MARK) == []


def test_holdings_and_watchlist_fns_are_reevaluated_per_tick(st, clock):
    """The reconciled sets move intraday; the closures must consult the fns each run."""
    calls = {"holdings": 0, "watchlist": 0}

    def holdings_fn():
        calls["holdings"] += 1
        return {"AAPL"}

    def watchlist_fn():
        calls["watchlist"] += 1
        return {"MSFT"}

    services = build_services(st, holdings_fn=holdings_fn, watchlist_fn=watchlist_fn)
    services["intraday"](st, clock)
    services["intraday"](st, clock)
    assert calls["holdings"] == 2
    assert calls["watchlist"] == 2
