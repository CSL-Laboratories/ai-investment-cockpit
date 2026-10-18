"""WP8 — workflow service registry + EOD review (contract Owner B 'services.py').

Implements docs/build-scaffold-ultracode.md WP8 (scheduler wiring: the WP5 scheduler jobs
take injected ``(state, clock) -> dict`` callables — ``scheduler.py`` itself is untouched
and its not-wired stubs keep raising) and SEC-20 (the EOD review feeds the performance
gate's drawdown metric via ``EVENT_DRAWDOWN_MARK``). ``build_services`` closes each
pipeline over its seams (transport / deliver / parquet_root / quote / holdings /
watchlist) so ``main.compose`` and the tests inject everything; nothing here touches
wall-clock, Discord, or brokers execution (Invariant 1).
"""
from __future__ import annotations

from orchestrator.state import EVENT_DRAWDOWN_MARK
from workflows.interrogation import fresh_interrogate
from workflows.morning_brew import morning_brew
from workflows.proposals import draft_proposal
from workflows.research_update import empty_run_update
from workflows.triage_loop import triage_loop


def build_services(state, *, transport=None, deliver=None, parquet_root=None,
                   quote_fn=None, book_fn=None, observation_fn=None, metrics_fn=None,
                   verify_params=None,
                   holdings_fn=None, watchlist_fn=None, edge_provider=None) -> dict:
    """Build scheduler services plus W-22's fresh Discord interrogation seam.

    The scheduled ``brew``/``intraday``/``eod`` callables retain their ``(state, clock)``
    shape. ``ask`` takes an explicit question and proposal/job id and always invokes the
    existing SEC-18 fresh-context workflow. ``research_update`` is the RW-35 empty-run
    research increment: retained slice A evidence becomes a concise delivered update.
    ``holdings_fn`` /
    ``watchlist_fn`` are re-evaluated per tick (the reconciled sets move intraday).

    ``book_fn`` supplies L1 prices and ``metrics_fn`` supplies RW-14 ADV/volatility. They are
    threaded together so the production verifier cannot silently fall back to displayed IEX
    sizes. A composed metrics source also arms W-21's deliberately unavailable-edge gate."""

    def _holdings() -> frozenset:
        return frozenset(holdings_fn()) if holdings_fn is not None else frozenset()

    def _watchlist() -> frozenset:
        return frozenset(watchlist_fn()) if watchlist_fn is not None else frozenset()

    def _propose(st, clock, **kw):
        # draft_proposal with the composed quote + book seams (proposals never fetch either)
        return draft_proposal(st, clock, quote_fn=quote_fn, book_fn=book_fn,
                              observation_fn=observation_fn, metrics_fn=metrics_fn,
                              verify_params=verify_params,
                              edge_provider=edge_provider, **kw)

    _propose.uses_draft_proposal_template = True

    def brew(st, clock) -> dict:
        # The watchlist is what keeps the brew's calendar pre-filter from being holdings-only.
        # ``intraday`` has always received it; ``brew`` was the one caller that did not, which
        # is why its filter could not distinguish "a company you are watching" from "any of the
        # ~500 companies reporting this week".
        return morning_brew(st, clock, transport=transport, deliver=deliver,
                            parquet_root=parquet_root, holdings_symbols=_holdings(),
                            watchlist=_watchlist())

    def intraday(st, clock) -> dict:
        return triage_loop(st, clock, transport=transport, deliver=deliver,
                           parquet_root=parquet_root, holdings_symbols=_holdings(),
                           watchlist=_watchlist(), propose_fn=_propose)

    def eod(st, clock) -> dict:
        return eod_review(st, clock)

    def ask(st, *, question: str, proposal_id: str | None = None,
            job_id: str | None = None) -> dict:
        return fresh_interrogate(
            st,
            question=question,
            proposal_id=proposal_id,
            job_id=job_id,
            transport=transport,
            parquet_root=parquet_root,
            clock=st.clock,
        )

    def research_update(st, clock) -> dict:
        return empty_run_update(st, clock, deliver=deliver)

    return {
        "brew": brew,
        "intraday": intraday,
        "eod": eod,
        "ask": ask,
        "research_update": research_update,
    }


def eod_review(state, clock=None, *, drawdown_fn=None) -> dict:
    """Minimal EOD review: record the realized drawdown for the gate (SEC-20) when a
    ``drawdown_fn`` is injected. Deterministic — no LLM call."""
    clock = clock if clock is not None else state.clock
    now = clock.now()
    recorded = False
    if drawdown_fn is not None:
        state.audit.append(EVENT_DRAWDOWN_MARK, actor="eod_review",
                           payload={"drawdown": str(drawdown_fn())})
        recorded = True
    return {"status": "ok", "drawdown_recorded": recorded, "at": now.isoformat()}
