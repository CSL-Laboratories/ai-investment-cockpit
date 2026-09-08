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

from sqlalchemy import select

from agent_runner.usage import UsageTracker
from config.settings import Settings, get_settings
from core.clock import Clock, default_clock
from core.ids import new_id
from schemas.models.audit_entry import AuditEntry
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
EVENT_SAFETY_SESSION_OPEN = "safety_session_open"
EVENT_SAFETY_SESSION_CLOSE = "safety_session_close"
SAFETY_SESSION_VERSION = 1

# SEC-20 bypass-attempt detector: any of these in the chain blocks a gate unlock.
BYPASS_EVENTS = frozenset({
    "hard_check_failed",
    "resubmit_after_denied",
    "approval_against_failed_risk",
})


def _verified_snapshot(audit, session, anchor_store):
    anchor = anchor_store.latest() if anchor_store is not None else None
    return (
        audit.verify_session_against_anchor(session, anchor[0], anchor[1])
        if anchor is not None
        else audit.verify_session(session)
    )


def _restore_usage_rows(rows) -> UsageTracker:
    tracker = UsageTracker()
    transition_ids: set[str] = set()
    for row in rows:
        if row.event_type != EVENT_WATCH_ONLY:
            continue
        payload = row.payload or {}
        refs = row.refs or {}
        vendor = payload.get("vendor")
        if refs.get("vendor") != vendor:
            raise ValueError("watch-only vendor differs between signed refs and payload")
        transition_id = payload.get("transition_id")
        if transition_id is not None:
            kind = payload.get("transition_kind")
            state = payload.get("state")
            if (
                not isinstance(transition_id, str)
                or not transition_id
                or refs.get("transition_id") != transition_id
            ):
                raise ValueError("watch-only transition identity differs between refs and payload")
            if transition_id in transition_ids:
                raise ValueError("duplicate watch-only transition identity")
            if (
                kind not in {"trip", "recovery"}
                or (kind == "recovery") != (state == "ok")
                or payload.get("watch_only") is not (state != "ok")
            ):
                raise ValueError("malformed watch-only transition protocol")
            transition_ids.add(transition_id)
        tracker.restore(
            vendor,
            state=payload.get("state"),
            generation=payload.get("generation"),
        )
    return tracker


def _restore_usage_tracker(
    session_factory: Callable,
    audit: AuditLog,
    *,
    anchor_store: "AnchorStore | None" = None,
) -> UsageTracker:
    """Rebuild L8 state from one verified, snapshot-pinned signed-ledger read.

    ``UsageWindow`` domain rows are mutable and therefore cannot authorize an order.  The
    append-only HMAC ledger is already the canonical state-change authority (Inv 6).  A bad
    chain, anchor mismatch, malformed payload, or contradictory vendor/ref binding installs a
    synthetic exhausted window: awareness can boot, but ``BreakerEngine.is_watch_only()``
    disables every order path until the integrity problem is repaired.
    """
    tracker = UsageTracker()
    try:
        with session_factory() as session:
            verified = _verified_snapshot(audit, session, anchor_store)
            if not verified.ok:
                tracker.fail_closed(
                    f"watch-only ledger verification failed: {verified.error or 'unknown error'}"
                )
                return tracker
            rows = session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars().all()
            tracker = _restore_usage_rows(rows)
    except Exception as exc:  # noqa: BLE001 - unreadable safety authority must fail closed
        tracker.fail_closed(
            f"watch-only ledger restoration failed: {type(exc).__name__}: {exc}"
        )
        return tracker
    return tracker


@dataclass
class SafetySession:
    """Process ownership envelope for restart-conservative order authority."""

    required: bool = False
    session_id: str | None = None
    opened: bool = False
    accepting: bool = False
    startup_hold: str | None = None
    open_seq: int | None = None
    open_entry_id: str | None = None
    close_error: str | None = None

    def status(self) -> dict:
        return {
            "required": self.required,
            "session_id": self.session_id,
            "opened": self.opened,
            "accepting": self.accepting,
            "startup_hold": self.startup_hold,
            "open_seq": self.open_seq,
            "close_error": self.close_error,
        }


def _closed_session_predecessor(rows, tracker: UsageTracker):
    """Validate the complete v1 session history and return its latest close row."""
    session_rows = [
        row for row in rows
        if row.event_type in {EVENT_SAFETY_SESSION_OPEN, EVENT_SAFETY_SESSION_CLOSE}
    ]
    if not session_rows:
        if rows:
            raise ValueError("legacy non-empty ledger has no safety-session authority")
        return None

    opened = None
    latest_close = None
    replay = UsageTracker()
    for row in rows:
        if row.event_type == EVENT_WATCH_ONLY:
            payload, refs = row.payload or {}, row.refs or {}
            vendor = payload.get("vendor")
            if refs.get("vendor") != vendor:
                raise ValueError("watch-only vendor differs between signed refs and payload")
            replay.restore(
                vendor,
                state=payload.get("state"),
                generation=payload.get("generation"),
            )
            continue
        if row.event_type not in {EVENT_SAFETY_SESSION_OPEN, EVENT_SAFETY_SESSION_CLOSE}:
            continue
        payload, refs = row.payload or {}, row.refs or {}
        session_id = payload.get("session_id")
        if (
            payload.get("version") != SAFETY_SESSION_VERSION
            or not isinstance(session_id, str)
            or not session_id
            or refs.get("session_id") != session_id
        ):
            raise ValueError("malformed safety-session event")
        if row.event_type == EVENT_SAFETY_SESSION_OPEN:
            if opened is not None:
                raise ValueError("a safety session was opened while its predecessor remained open")
            if latest_close is None:
                if row.seq != 1 or payload.get("predecessor_close_seq") is not None:
                    raise ValueError("first safety session is not genuine empty-store genesis")
                if payload.get("predecessor_head_seq") != 0:
                    raise ValueError("genesis session has a non-empty predecessor")
            else:
                if (
                    row.seq != latest_close.seq + 1
                    or row.prev_hash != latest_close.entry_hash
                    or payload.get("predecessor_close_seq") != latest_close.seq
                    or payload.get("predecessor_head_seq") != latest_close.seq
                ):
                    raise ValueError("safety-session predecessor/head binding is invalid")
            if payload.get("predecessor_vendor_state") != replay.committed_snapshot():
                raise ValueError("safety-session predecessor vendor view is inconsistent")
            opened = row
        else:
            if (
                opened is None
                or session_id != (opened.payload or {}).get("session_id")
                or payload.get("open_seq") != opened.seq
                or payload.get("committed_vendor_state") != replay.committed_snapshot()
            ):
                raise ValueError("safety-session close does not match its open/vendor authority")
            opened, latest_close = None, row

    if opened is not None:
        raise ValueError("previous safety session is unclosed")
    if latest_close is None or latest_close is not rows[-1]:
        raise ValueError("signed activity exists after the latest safety-session close")
    if tracker.committed_snapshot() != replay.committed_snapshot():
        raise ValueError("restored vendor state differs from verified session replay")
    return latest_close


def _restore_and_open_safety_session(
    session_factory, audit, *, anchor_store, session_id
) -> tuple[UsageTracker, SafetySession]:
    """Restore predecessor authority and claim this process in one transaction."""
    session_state = SafetySession(required=True, session_id=session_id)
    try:
        with session_factory() as session:
            with session.begin():
                verified = _verified_snapshot(audit, session, anchor_store)
                if not verified.ok:
                    raise ValueError(
                        f"safety-session ledger verification failed: "
                        f"{verified.error or 'unknown error'}"
                    )
                rows = session.execute(
                    select(AuditEntry).order_by(AuditEntry.seq)
                ).scalars().all()
                tracker = _restore_usage_rows(rows)
                predecessor = _closed_session_predecessor(rows, tracker)
                entry = audit.append_in(
                    session,
                    EVENT_SAFETY_SESSION_OPEN,
                    actor="orchestrator",
                    refs={"session_id": session_id},
                    payload={
                        "version": SAFETY_SESSION_VERSION,
                        "session_id": session_id,
                        "predecessor_close_seq": (
                            predecessor.seq if predecessor is not None else None
                        ),
                        "predecessor_head_seq": verified.head_seq,
                        "predecessor_head_hash": verified.head_hash,
                        "predecessor_vendor_state": tracker.committed_snapshot(),
                    },
                )
                if entry.seq != verified.head_seq + 1 or entry.prev_hash != verified.head_hash:
                    raise ValueError("safety-session ownership lost an exact-head race")
            audit.observe_committed(entry)
        session_state.opened = True
        session_state.accepting = True
        session_state.open_seq = entry.seq
        session_state.open_entry_id = entry.id
        return tracker, session_state
    except BaseException as exc:  # noqa: BLE001 - any uncertain ownership is a startup hold
        tracker = _restore_usage_tracker(
            session_factory, audit, anchor_store=anchor_store
        )
        session_state.startup_hold = (
            f"{type(exc).__name__}: {exc}"
        )
        return tracker, session_state


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
    # BUG-159 lock order: denial admission takes this short lock and releases it before
    # transition persistence can wait for ``lock``. Gateway final authorization takes
    # ``lock`` first and this lock last, releases it, then immediately enters the broker.
    # No path holds this lock across SQLite, risk work, callbacks, or broker I/O.
    safety_admission_lock: "threading.Lock" = field(
        default_factory=threading.Lock, repr=False, compare=False
    )
    safety_transition_lock: "threading.RLock" = field(
        default_factory=threading.RLock, repr=False, compare=False
    )
    safety_session: SafetySession = field(
        default_factory=SafetySession, repr=False, compare=False
    )
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
        require_safety_session: bool = False,
    ) -> "OrchestratorState":
        """Construct a state from a session factory, wiring an ``AuditLog`` on the same
        clock. ``hmac_key`` is the audit-chain key (from keyring in production; a literal
        in tests). ``anchor_store`` is the SEC-11 external witness (production wires the
        git/#audit-log store; tests may inject a file/in-memory stand-in)."""
        audit = AuditLog(session_factory, hmac_key=hmac_key, clock=clock)
        process_epoch = new_id("process")
        if require_safety_session:
            if usage is not None:
                raise ValueError("a required safety session cannot use injected usage authority")
            restored_usage, safety_session = _restore_and_open_safety_session(
                session_factory,
                audit,
                anchor_store=anchor_store,
                session_id=process_epoch,
            )
        else:
            restored_usage = (
                usage
                if usage is not None
                else _restore_usage_tracker(
                    session_factory,
                    audit,
                    anchor_store=anchor_store,
                )
            )
            safety_session = SafetySession()
        return cls(
            session_factory=session_factory,
            audit=audit,
            usage=restored_usage,
            settings=settings if settings is not None else get_settings(),
            clock=clock,
            process_epoch=process_epoch,
            anchor_store=anchor_store,
            safety_session=safety_session,
        )

    def effective_watch_only_reason(self, vendor: str | None = None) -> str | None:
        """Stable reason for the effective (committed + admitted + session) denial."""
        safety = self.safety_session
        if safety.required:
            if safety.startup_hold or not safety.opened:
                return "watch_only_startup_hold"
            if not safety.accepting:
                return "watch_only_pending"
        return self.usage.denial_reason(vendor)

    def admit_integrity_failure(self, reason: str) -> None:
        """Synchronously publish a sticky global denial without attempting unsafe repair."""
        with self.safety_admission_lock:
            self.usage.fail_closed(reason)

    def begin_safety_shutdown(self) -> None:
        """Fence new order/recovery authority before producers are quiesced."""
        with self.safety_admission_lock:
            if self.safety_session.required:
                self.safety_session.accepting = False
                self.usage.note_admission_change()

    def close_safety_session(self, *, producers_quiesced: bool) -> bool:
        """Sign a clean close only after every caller-owned quiescence proof succeeds.

        This method never guesses that daemon workers stopped. The production lifecycle
        passes ``False`` for a configured producer it cannot join, leaving the signed open
        marker intact so the next start holds.
        """
        safety = self.safety_session
        if not safety.required:
            return True
        self.begin_safety_shutdown()
        if not safety.opened or safety.startup_hold:
            safety.close_error = "safety_session_not_owned"
            return False
        if not producers_quiesced:
            safety.close_error = "producers_not_quiescent"
            return False
        usage_status = self.usage.status_snapshot()
        if usage_status["pending"] or usage_status["integrity_error"]:
            safety.close_error = "watch_only_transition_unresolved"
            return False

        try:
            with self.lock:
                with self.session_factory() as session:
                    with session.begin():
                        verified = _verified_snapshot(self.audit, session, self.anchor_store)
                        if not verified.ok:
                            raise ValueError(verified.error or "audit chain is invalid")
                        rows = session.execute(
                            select(AuditEntry).order_by(AuditEntry.seq)
                        ).scalars().all()
                        session_events = [
                            row for row in rows
                            if row.event_type in {
                                EVENT_SAFETY_SESSION_OPEN,
                                EVENT_SAFETY_SESSION_CLOSE,
                            }
                        ]
                        latest = session_events[-1] if session_events else None
                        if (
                            latest is None
                            or latest.event_type != EVENT_SAFETY_SESSION_OPEN
                            or (latest.payload or {}).get("session_id") != safety.session_id
                            or latest.seq != safety.open_seq
                        ):
                            raise ValueError("current process no longer owns the open session")
                        intents = {
                            row.seq for row in rows
                            if (row.payload or {}).get("_phase") == "intent"
                        }
                        results = {
                            (row.refs or {}).get("intent_seq") for row in rows
                            if (row.payload or {}).get("_phase") == "result"
                        }
                        if intents - results:
                            raise ValueError("unresolved submit intent prevents clean close")
                        replay = _restore_usage_rows(rows)
                        committed = self.usage.committed_snapshot()
                        if replay.committed_snapshot() != committed:
                            raise ValueError("committed vendor view changed before clean close")
                        entry = self.audit.append_in(
                            session,
                            EVENT_SAFETY_SESSION_CLOSE,
                            actor="orchestrator",
                            refs={"session_id": safety.session_id},
                            payload={
                                "version": SAFETY_SESSION_VERSION,
                                "session_id": safety.session_id,
                                "open_seq": safety.open_seq,
                                "committed_vendor_state": committed,
                            },
                        )
                        if (
                            entry.seq != verified.head_seq + 1
                            or entry.prev_hash != verified.head_hash
                        ):
                            raise ValueError("clean close lost the verified audit head")
                    self.audit.observe_committed(entry)
        except BaseException as exc:  # noqa: BLE001 - uncertain close must remain open
            safety.close_error = f"{type(exc).__name__}: {exc}"
            return False
        safety.opened = False
        safety.close_error = None
        return True
