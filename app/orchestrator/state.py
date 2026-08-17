"""WP5 — the orchestrator's dependency-injection container + shared constants.

``OrchestratorState`` bundles the load-bearing collaborators (SQLAlchemy session factory,
the tamper-evident ``AuditLog``, the per-vendor ``UsageTracker``, ``Settings``, and an
injectable ``Clock``) so every job/endpoint/state-machine takes them as one argument and is
fully testable against a ``FrozenClock`` + an in-memory/tmp SQLite DB (no wall-clock, no
network).

Audit ``event_type`` constants live here as the single source of truth shared by the mode
ladder, gate, breakers, and killswitch — so a state-change writes, and the gate reads, the
exact same event strings (SEC-20: the gate is computed from these audited events, never a
mutable domain table).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol, runtime_checkable

from agent_runner.usage import UsageTracker
from config.settings import Settings, get_settings
from core.clock import Clock, default_clock
from core.ids import new_id
from storage.audit import AuditLog

# ---- audit event types (single source of truth; Inv 6) --------------------------------
EVENT_MODE_CHANGE = "mode_change"          # EVERY ladder transition (arm/drop/disarm/recover)
EVENT_BREAKER_TRIP = "breaker_trip"
EVENT_BREAKER_CLEAR = "breaker_clear"
EVENT_WATCH_ONLY = "watch_only"            # L8 usage-window degradation (NOT a mode change)
EVENT_MARKET_DATA_RAW = "market_data_raw_snapshot"
EVENT_FILING_INGESTED = "filing_ingested"  # SEC metadata + raw hash, atomic with Filing row
EVENT_EDGAR_POLL_COMPLETED = "edgar_poll_completed"  # per-CIK trustworthy SEC poll watermark
EVENT_KILLSWITCH = "killswitch"
EVENT_DRAWDOWN_MARK = "drawdown_mark"      # EOD-recorded realized drawdown the gate reads
EVENT_ADJUDICATION = "proposal_decision"   # one per approve/reject/modify/veto (WP6 writes)
# WP7 order path (SEC-13 two-phase submit + G7 per-transition audit):
EVENT_ORDER_INTENT = "order_intent"        # BEFORE the broker submit (paired with a result)
EVENT_ORDER_RESULT = "order_result"        # AFTER the submit (ack/fill/reject), same txn
EVENT_ORDER_TRANSITION = "order_transition"  # order-state-machine step (proposed->approved->...)
EVENT_ORDER_FILL_PROGRESS = "order_fill_progress"  # cumulative partial-fill update, state stable
EVENT_PAPER_NOTIFICATION = "paper_fill_notification"  # post-commit Discord delivery result
# SEC-11 external witness: a head anchor posted to the out-of-DB witness (git / #audit-log).
EVENT_ANCHOR = "anchor"

# SEC-20 bypass-attempt detector: any of these in the chain blocks a gate unlock.
BYPASS_EVENTS = frozenset({
    "hard_check_failed",
    "resubmit_after_denied",
    "approval_against_failed_risk",
})


@dataclass(frozen=True)
class SystemEvent:
    """A ``#system``-style event returned by breakers/killswitch — deliberately NOT
    Discord-dependent (the bridge renders it; the core never imports discord)."""

    channel: str          # conventionally "#system"
    kind: str             # breaker_trip | injection_breaker | watch_only | killswitch | recover
    breaker: str | None
    reason: str
    mode_after: str
    at: datetime


@runtime_checkable
class AnchorStore(Protocol):
    """SEC-11 external-witness seam. The out-of-DB, append-only record of ``(seq, head_hash)``
    the LOCAL process cannot silently rewrite (git-committed head + ``#audit-log`` post). The
    performance gate and the mode oracle consult ``latest()`` and fail closed for an auto
    unlock / auto mode-read whenever the verified in-DB head is BELOW the last external
    anchor — the only closure for the replayable-watermark rollback (a keyless writer who
    ``DELETE``s the tail and replays a captured ``audit_meta`` still cannot rewrite the
    external witness). ``bridge/anchor.py`` provides the concrete implementations; a plain
    local file is only a mirror and does NOT provide the guarantee on its own."""

    def latest(self) -> "tuple[int, str] | None":
        """The highest anchored ``(seq, head_hash)``, or ``None`` if nothing anchored yet."""
        ...

    def append(self, seq: int, head_hash: str) -> None:
        """Record a new head anchor on the external witness (monotonic; never rewinds)."""
        ...


@dataclass
class OrchestratorState:
    session_factory: Callable
    audit: AuditLog
    usage: UsageTracker
    settings: Settings
    clock: Clock = default_clock
    # Per-process freshness epoch. Durable feed watermarks from an earlier process cannot
    # authorize orders after a restart that failed to compose the feed.
    process_epoch: str = field(default_factory=lambda: new_id("process"), compare=False)
    # Serializes the mode ladder's read-modify-write against a concurrent killswitch/arm so
    # the (read current -> check gate/HALT -> persist) sequence is atomic (SEC-04 race fix).
    # Reentrant: killswitch/daily_disarm hold it while calling drop_to_review_live.
    lock: "threading.RLock" = field(default_factory=threading.RLock, repr=False, compare=False)
    # SEC-11 external witness (optional). When wired (production + the auto-mode paths), the
    # gate + mode oracle require the in-DB head to be >= the last external anchor; when None
    # (pre-WP6 tests) behavior is exactly the in-DB verify_chain() posture.
    anchor_store: "AnchorStore | None" = field(default=None, compare=False)
    # BUG-058: the BRK_BROKER_ERRORS production signal. The gateway increments this (under
    # ``lock``) when a broker submit raises; the intraday triage tick samples-and-resets it
    # into ``BreakerSignals.broker_errors``. In-memory on purpose — a breaker measures a
    # SPIKE in the current session, and every increment already has its own audit record
    # (the order_submit result + refusal), so no separate durable counter is warranted.
    broker_errors_since_sample: int = field(default=0, compare=False)
    # RW-14: optional composed market-data authority. Absent means the paper transport is not
    # provisioned; when present the gateway synchronously refuses missing/stale observations.
    market_data: object | None = field(default=None, repr=False, compare=False)
    # W-23: one broker-free deterministic edge provider shared by draft and approval-time
    # verification. It returns evidence or None; it has no order/broker capability.
    edge_provider: object | None = field(default=None, repr=False, compare=False)
    # W-22: last verified paper-performance observation. Production composition requires it;
    # uncomposed tests keep the legacy additive posture until a paper service is attached.
    paper_performance: object | None = field(default=None, repr=False, compare=False)
    paper_performance_required: bool = field(default=False, repr=False, compare=False)

    @classmethod
    def build(
        cls,
        session_factory: Callable,
        *,
        hmac_key,
        settings: Settings | None = None,
        usage: UsageTracker | None = None,
        clock: Clock = default_clock,
        anchor_store: "AnchorStore | None" = None,
    ) -> "OrchestratorState":
        """Construct a state from a session factory, wiring an ``AuditLog`` on the same
        clock. ``hmac_key`` is the audit-chain key (from keyring in production; a literal
        in tests). ``anchor_store`` is the SEC-11 external witness (production wires the
        git/#audit-log store; tests may inject a file/in-memory stand-in)."""
        return cls(
            session_factory=session_factory,
            audit=AuditLog(session_factory, hmac_key=hmac_key, clock=clock),
            usage=usage if usage is not None else UsageTracker(),
            settings=settings if settings is not None else get_settings(),
            clock=clock,
            anchor_store=anchor_store,
        )
