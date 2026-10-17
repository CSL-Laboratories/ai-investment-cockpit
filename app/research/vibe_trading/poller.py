"""Ingest poller that publishes sidecar research through the existing persist path.

CSL-192 forbids workflow edits. The composition root registers this poller on
the ingest runner when ``COCKPIT_VIBE_TRADING_ENABLED`` is on, so the next
brew window sees parquet-backed ``vibe-trading`` events without touching
morning_brew, triage, or OrderGateway.
"""
from __future__ import annotations

from ingest.normalize import content_hash


def _raw_ref_for_hash(state, digest: str) -> str:
    from sqlalchemy import select

    from schemas.models.market_event import MarketEvent

    with state.session_factory() as session:
        refs = session.execute(
            select(MarketEvent.raw_ref).where(MarketEvent.content_hash == digest)
        ).scalars().all()
    parquet = [ref for ref in refs if type(ref) is str and ref.startswith("parquet://")]
    if not parquet:
        raise RuntimeError("sidecar envelope is not stored")
    # Overlapping polls can each store the same content-addressed envelope; any
    # copy holds the identical payload, so pick one deterministically.
    return min(parquet)


def stamp_source_refs(state, events) -> list[dict]:
    """Attach each row to the already-persisted envelope parquet ref."""
    envelopes = [event for event in events if event.get("event_type") == "signal:envelope"]
    rows = [event for event in events if event.get("event_type") != "signal:envelope"]
    refs = {
        event["raw_input"]["sha256"]: _raw_ref_for_hash(state, event["content_hash"])
        for event in envelopes
    }
    stamped = []
    for event in rows:
        raw = dict(event["raw_input"])
        sha = raw.get("source_payload_sha256")
        if type(sha) is str:
            ref = refs.get(sha)
            if ref is None:
                raise RuntimeError("research row has no stored source envelope")
            raw["source_ref"] = ref
            event = {**event, "raw_input": raw, "content_hash": content_hash(raw)}
        stamped.append(event)
    return stamped


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
        envelopes = [event for event in batch.events if event.get("event_type") == "signal:envelope"]
        emitted = 0
        for event in envelopes:
            self._emit(on_event, event, emitted)
            emitted += 1
        for event in stamp_source_refs(self._state, batch.events):
            self._emit(on_event, event, emitted)
            emitted += 1
        if batch.status != "ok":
            error = RuntimeError(f"vibe-trading collect status={batch.status}")
            error.emitted = emitted
            raise error
        return emitted

    def _emit(self, on_event, event, emitted: int) -> None:
        try:
            on_event(event)
        except Exception as exc:
            self.counters["emit_error"] += 1
            # PersistSink records the missed identity and re-raises. Continuing
            # would report a healthy tick, and a later sidecar image may never
            # retry this one, so the delivery gap would stay open.
            try:
                exc.emitted = emitted
            except (AttributeError, TypeError):
                wrapped = RuntimeError("vibe-trading persist failed")
                wrapped.emitted = emitted
                raise wrapped from exc
            raise
