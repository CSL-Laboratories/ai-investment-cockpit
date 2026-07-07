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
from typing import Callable

from agent_runner.usage import UsageTracker
from config.settings import Settings, get_settings
from core.clock import Clock, default_clock
from storage.audit import AuditLog

# ---- audit event types (single source of truth; Inv 6) --------------------------------
EVENT_MODE_CHANGE = "mode_change"          # EVERY ladder transition (arm/drop/disarm/recover)
EVENT_BREAKER_TRIP = "breaker_trip"
EVENT_BREAKER_CLEAR = "breaker_clear"
EVENT_WATCH_ONLY = "watch_only"            # L8 usage-window degradation (NOT a mode change)
EVENT_KILLSWITCH = "killswitch"
EVENT_DRAWDOWN_MARK = "drawdown_mark"      # EOD-recorded realized drawdown the gate reads
EVENT_ADJUDICATION = "proposal_decision"   # one per approve/reject/modify/veto (WP6 writes)

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


@dataclass
class OrchestratorState:
    session_factory: Callable
    audit: AuditLog
    usage: UsageTracker
    settings: Settings
    clock: Clock = default_clock
    # Serializes the mode ladder's read-modify-write against a concurrent killswitch/arm so
    # the (read current -> check gate/HALT -> persist) sequence is atomic (SEC-04 race fix).
    # Reentrant: killswitch/daily_disarm hold it while calling drop_to_review_live.
    lock: "threading.RLock" = field(default_factory=threading.RLock, repr=False, compare=False)

    @classmethod
    def build(
        cls,
        session_factory: Callable,
        *,
        hmac_key,
        settings: Settings | None = None,
        usage: UsageTracker | None = None,
        clock: Clock = default_clock,
    ) -> "OrchestratorState":
        """Construct a state from a session factory, wiring an ``AuditLog`` on the same
        clock. ``hmac_key`` is the audit-chain key (from keyring in production; a literal
        in tests)."""
        return cls(
            session_factory=session_factory,
            audit=AuditLog(session_factory, hmac_key=hmac_key, clock=clock),
            usage=usage if usage is not None else UsageTracker(),
            settings=settings if settings is not None else get_settings(),
            clock=clock,
        )
