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
from workflows.morning_brew import morning_brew
from workflows.proposals import draft_proposal
from workflows.triage_loop import triage_loop


def build_services(state, *, transport=None, deliver=None, parquet_root=None,
                   quote_fn=None, book_fn=None, verify_params=None,
                   holdings_fn=None, watchlist_fn=None) -> dict:
    """Build the injected scheduler services: ``{"brew", "intraday", "eod"}``, each a
    plain ``(state, clock) -> dict`` closing over the seams. ``holdings_fn`` /
    ``watchlist_fn`` are re-evaluated per tick (the reconciled sets move intraday).

    ``book_fn(symbol) -> BookSnapshot | None`` is the QT-05 execution-verification seam,
    threaded through to ``draft_proposal`` alongside ``quote_fn``. Absent (the state of the
    tree today, since no book feed exists) the verification tiers stay dormant and the risk
    gate behaves exactly as it did before QT — the check is additive, not a new requirement."""

    def _holdings() -> frozenset:
        return frozenset(holdings_fn()) if holdings_fn is not None else frozenset()

    def _watchlist() -> frozenset:
        return frozenset(watchlist_fn()) if watchlist_fn is not None else frozenset()

    def _propose(st, clock, **kw):
        # draft_proposal with the composed quote + book seams (proposals never fetch either)
        return draft_proposal(st, clock, quote_fn=quote_fn, book_fn=book_fn,
                              verify_params=verify_params, **kw)

    def brew(st, clock) -> dict:
        return morning_brew(st, clock, transport=transport, deliver=deliver,
                            parquet_root=parquet_root, holdings_symbols=_holdings())

    def intraday(st, clock) -> dict:
        return triage_loop(st, clock, transport=transport, deliver=deliver,
                           parquet_root=parquet_root, holdings_symbols=_holdings(),
                           watchlist=_watchlist(), propose_fn=_propose)

    def eod(st, clock) -> dict:
        return eod_review(st, clock)

    return {"brew": brew, "intraday": intraday, "eod": eod}


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
