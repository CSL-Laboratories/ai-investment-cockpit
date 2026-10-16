"""Ingest poller that publishes sidecar research through the existing persist path.

CSL-192 forbids workflow edits. The composition root registers this poller on
the ingest runner when ``COCKPIT_VIBE_TRADING_ENABLED`` is on, so the next
brew window sees parquet-backed ``vibe-trading`` events without touching
morning_brew, triage, or OrderGateway.
"""
from __future__ import annotations


class VibeTradingPoller:
    """One research pass per ingest tick. ``on_event`` is the runner persist sink."""

    def __init__(self, adapter, *, state):
        self._adapter = adapter
        self._state = state
        self.counters = {"emit_error": 0}

    def poll(self, now, on_event) -> int:
        del now
        batch = self._adapter.collect(audit=self._state.audit, clock=self._state.clock)
        if batch.status != "ok" and not batch.events:
            raise RuntimeError(f"vibe-trading collect status={batch.status}")
        emitted = 0
        for event in batch.events:
            try:
                on_event(event)
            except Exception:
                self.counters["emit_error"] += 1
                continue
            emitted += 1
        if batch.status != "ok":
            error = RuntimeError(f"vibe-trading collect status={batch.status}")
            error.emitted = emitted
            raise error
        return emitted
