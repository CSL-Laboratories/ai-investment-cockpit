"""WP6 — Discord-outage fallback (SEC-23; Inv 1).

When the Discord bridge is unreachable, the operator must still be reachable AND the system must
stay fail-closed. This module:

  * DESKTOP-NOTIFIES the operator and QUEUES the proposal (an injected ``notifier`` callable —
    no hard OS/desktop dependency; a no-op notifier is fine in tests/headless);
  * keeps approvals IDEMPOTENT against Discord interaction retries (the same interaction id
    enqueues once) — the durable, tamper-evident idempotency still lives in
    ``bridge/approval.record_decision`` (keyed on the same interaction id);
  * EXPIRES queued proposals by quote-staleness — a queued proposal is dropped once its
    RiskDecision/quote is older than ``max_age_seconds``. It NEVER lingers and NEVER
    auto-executes: silence during an outage is not consent (SEC-23). Execution only ever happens
    through the normal approval/veto token-mint path once the operator acts.

There is NO discord import here.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

DEFAULT_MAX_AGE_SECONDS = 600.0


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass
class QueuedProposal:
    proposal_id: str
    risk_decision_id: str
    resolved_order: dict
    quote_evaluated_at: datetime          # freshness basis (RiskDecision.evaluated_at)
    interaction_id: str | None = None
    queued_at: datetime | None = None
    notified: bool = False

    def is_expired(self, now: datetime, max_age_seconds: float) -> bool:
        age = (_aware(now) - _aware(self.quote_evaluated_at)).total_seconds()
        return age > max_age_seconds


class DiscordOutageFallback:
    """A small, thread-safe queue with desktop notification + quote-staleness expiry. It holds
    NO order authority: it only surfaces + expires proposals; approval still flows through
    ``bridge/approval.record_decision`` (which mints the token)."""

    def __init__(self, st, *, notifier: Callable[[QueuedProposal], None] | None = None,
                 max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS, clock=None) -> None:
        self.st = st
        self._notifier = notifier
        self.max_age_seconds = max_age_seconds
        self._clock = clock or st.clock
        self._queue: dict[str, QueuedProposal] = {}          # keyed by proposal_id
        self._by_interaction: dict[str, str] = {}            # interaction_id -> proposal_id
        self._lock = threading.Lock()

    def enqueue(self, *, proposal_id: str, risk_decision, resolved_order: dict,
                interaction_id: str | None = None) -> QueuedProposal:
        """Queue a proposal for out-of-band operator attention and desktop-notify. Idempotent
        against a retried interaction id AND a re-enqueued proposal id."""
        now = self._clock.now()
        with self._lock:
            if interaction_id and interaction_id in self._by_interaction:
                return self._queue[self._by_interaction[interaction_id]]
            if proposal_id in self._queue:
                return self._queue[proposal_id]
            qp = QueuedProposal(
                proposal_id=proposal_id,
                risk_decision_id=risk_decision.id,
                resolved_order=dict(resolved_order),
                quote_evaluated_at=getattr(risk_decision, "evaluated_at", now),
                interaction_id=interaction_id,
                queued_at=now,
            )
            self._queue[proposal_id] = qp
            if interaction_id:
                self._by_interaction[interaction_id] = proposal_id
        self._notify(qp)
        return qp

    def _notify(self, qp: QueuedProposal) -> None:
        if self._notifier is None:
            qp.notified = False
            return
        try:
            self._notifier(qp)
            qp.notified = True
        except Exception:                                    # noqa: BLE001 - notify is best-effort
            qp.notified = False

    def expire_stale(self, now: datetime | None = None) -> list[QueuedProposal]:
        """Drop every queued proposal whose quote has gone stale. Returns the dropped ones (for
        auditing/telemetry). They are DISCARDED, never executed (SEC-23)."""
        now = now or self._clock.now()
        dropped: list[QueuedProposal] = []
        with self._lock:
            for pid in list(self._queue):
                qp = self._queue[pid]
                if qp.is_expired(now, self.max_age_seconds):
                    dropped.append(qp)
                    del self._queue[pid]
                    if qp.interaction_id:
                        self._by_interaction.pop(qp.interaction_id, None)
        return dropped

    def pending(self, now: datetime | None = None) -> list[QueuedProposal]:
        """Live (non-expired) queued proposals. Expiry is applied first so a stale proposal is
        never surfaced as actionable."""
        self.expire_stale(now)
        with self._lock:
            return list(self._queue.values())

    def get(self, proposal_id: str) -> QueuedProposal | None:
        with self._lock:
            return self._queue.get(proposal_id)


@dataclass
class _NullNotifier:
    """A no-op notifier record (for headless/test use)."""

    calls: list = field(default_factory=list)

    def __call__(self, qp: QueuedProposal) -> None:
        self.calls.append(qp.proposal_id)
