"""WP6 — the ``auto_veto`` veto-window logic (Invariant 1/2/6; SEC-01/03/23).

In ``auto_veto`` a proposal executes AFTER a veto window (default 5 min) UNLESS the operator
vetoes inside the window. This module owns that timer and the atomic race at its edge:

  * RACE — a veto landing at T-1s WINS. Both ``veto()`` and ``resolve()`` take ``st.lock``, so
    they serialize; execution at T+0 re-reads the ticket under the lock and sees the veto.
  * SEC-23 (fail-closed on delivery) — the window MUST NOT fire on SILENCE. If delivery to the
    operator was never CONFIRMED (the bridge/Discord was down), ``resolve()`` refuses to execute
    — a proposal the operator never saw is not "un-vetoed", it is UNDELIVERED. The window timer
    is measured from the confirmed-delivery instant, on a MONOTONIC clock; a backward clock jump
    (a manipulated/rewound time source trying to make the window look elapsed early) trips the
    synchronous injection breaker and refuses.
  * SEC-01 / mode re-check — at T+0, before minting, ``resolve()`` re-checks the live mode, the
    HALT sentinel, and the synchronous injection breaker ATOMICALLY under the lock, so a drop to
    ``review_live`` / a HALT / an anomaly between schedule and fire cancels the auto-exec.

``resolve()`` mints the single-use submit token ONLY when the window elapses cleanly with
CONFIRMED delivery and NO veto — the ``auto_veto`` equivalent of a human approval. This module
and ``bridge/approval.py`` are the ONLY token minters (SEC-03).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from brokers.order_tuple import order_tuple_hash
from config.settings import Mode
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.state import EVENT_ADJUDICATION

DEFAULT_VETO_WINDOW_SECONDS = 300.0


def _halt_active() -> bool:
    """BUG-138: fail-closed HALT probe for the veto path. The gateway wraps every probe
    so a probe fault reads as SET; resolve() called halt_is_set directly, so a probe
    fault (a relative halt-path pin, a monkeypatched seam) propagated instead of
    failing closed. Any fault here refuses the mint."""
    try:
        return halt_is_set(default_halt_path())
    except Exception:   # noqa: BLE001 - fail closed: any probe fault => HALT treated as SET
        return True


@dataclass
class VetoTicket:
    proposal_id: str
    risk_decision_id: str
    order_tuple_hash: str
    window_seconds: float
    delivered: bool = False
    delivered_at_monotonic: float | None = None
    vetoed: bool = False
    vetoed_by: str | None = None
    resolved: bool = False          # terminal: executed or cancelled
    executed: bool = False


@dataclass(frozen=True)
class VetoResolution:
    """Outcome of a ``resolve()`` at/after the window. ``executed`` True means a submit token was
    minted; otherwise ``reason`` says why not (undelivered / window_open / vetoed / mode_changed
    / halt / breaker / clock_backwards)."""

    executed: bool
    reason: str
    proposal_id: str
    token: object = None            # SubmitToken when executed


@dataclass(frozen=True)
class VetoOutcome:
    vetoed: bool
    reason: str
    proposal_id: str


def _default_monotonic() -> float:
    return time.monotonic()


class AutoVetoCoordinator:
    """Holds the in-flight ``auto_veto`` tickets and resolves them at the window edge. One per
    orchestrator process. Injectable clocks make the race + SEC-23 fully unit-testable."""

    def __init__(self, st, token_registry, *, window_seconds: float = DEFAULT_VETO_WINDOW_SECONDS,
                 monotonic: Callable[[], float] | None = None, breaker_engine=None,
                 mode_ladder=None) -> None:
        self.st = st
        self.token_registry = token_registry
        self.window_seconds = window_seconds
        self._monotonic = monotonic or _default_monotonic
        self._breaker_engine = breaker_engine
        self._mode_ladder = mode_ladder
        self._tickets: dict[str, VetoTicket] = {}
        self._last_monotonic: float | None = None      # SEC-23 backward-jump detector

    # ---- collaborators (lazily built so tests can inject) -----------------------------
    def _breakers(self):
        if self._breaker_engine is not None:
            return self._breaker_engine
        from orchestrator.breakers import BreakerEngine
        self._breaker_engine = BreakerEngine(self.st)
        return self._breaker_engine

    def _ladder(self):
        if self._mode_ladder is not None:
            return self._mode_ladder
        from orchestrator.mode_ladder import ModeLadder
        self._mode_ladder = ModeLadder(self.st)
        return self._mode_ladder

    def _read_monotonic(self) -> tuple[float, bool]:
        """Read the monotonic clock; report whether it went BACKWARD since the last read (a
        rewound/forged time source — SEC-23). Never lets the stored watermark rewind."""
        now = self._monotonic()
        backward = self._last_monotonic is not None and now < self._last_monotonic
        if not backward:
            self._last_monotonic = now
        return now, backward

    # ---- schedule / deliver / veto ----------------------------------------------------
    def schedule(self, *, proposal_id: str, risk_decision, resolved_order: dict,
                 window_seconds: float | None = None) -> VetoTicket:
        """Register a proposal for auto-execution after the veto window. It is NOT yet armed:
        the window clock only starts once delivery is confirmed (SEC-23)."""
        oth = order_tuple_hash(resolved_order)
        with self.st.lock:
            self._prune_settled()
            ticket = VetoTicket(
                proposal_id=proposal_id,
                risk_decision_id=risk_decision.id,
                order_tuple_hash=oth,
                window_seconds=self.window_seconds if window_seconds is None else window_seconds,
            )
            self._tickets[proposal_id] = ticket
            return ticket

    _MAX_SETTLED_TICKETS = 1024

    def _prune_settled(self) -> None:
        """BUG-080: ``_tickets`` grew by one per adjudicated proposal for the life of the
        process — append-only, never pruned. Settled tickets (resolved or vetoed) are kept
        only as a bounded recent-history window, oldest dropped first (dict preserves
        insertion order). LIVE tickets are never dropped. Caller holds ``st.lock``."""
        settled = [pid for pid, t in self._tickets.items() if t.resolved or t.vetoed]
        excess = len(settled) - self._MAX_SETTLED_TICKETS
        if excess > 0:
            for pid in settled[:excess]:
                del self._tickets[pid]

    def confirm_delivery(self, proposal_id: str) -> VetoTicket:
        """Mark that the operator ACTUALLY received the proposal (the Discord post/DM was
        acknowledged as delivered). Only now does the window clock start (SEC-23)."""
        with self.st.lock:
            ticket = self._tickets[proposal_id]
            if not ticket.delivered:
                now, backward = self._read_monotonic()
                # SEC-23: a backward monotonic reading at confirm is a clock discontinuity
                # (a forged/rewound time source). It must NOT stamp a stale-low delivery
                # instant that starts the veto countdown — that would let a later forward
                # clock see a full/elapsed window and mint an auto-exec token. Fail closed:
                # leave the ticket UNDELIVERED so resolve() refuses ('undelivered'),
                # consistent with how resolve() rejects a backward jump.
                if not backward:
                    ticket.delivered = True
                    ticket.delivered_at_monotonic = now
            return ticket

    def veto(self, proposal_id: str, *, actor_user_id, clock=None) -> VetoOutcome:
        """Operator veto. Verifies the actor is the allowlisted operator, then flags the ticket
        under ``st.lock`` (so a veto racing an about-to-fire ``resolve()`` is seen atomically),
        and records the veto as an ``EVENT_ADJUDICATION`` (decision='veto') so the gate's
        veto-rate metric counts it."""
        from bridge.operators import is_authorized
        clock = clock or self.st.clock
        with self.st.lock:
            if not is_authorized(actor_user_id, self.st.settings):
                self.st.audit.append(
                    "unauthorized_actor", actor=str(actor_user_id),
                    refs={"proposal_id": proposal_id},
                    payload={"proposal_id": proposal_id, "attempted_decision": "veto"},
                )
                return VetoOutcome(False, "unauthorized", proposal_id)

            ticket = self._tickets.get(proposal_id)
            if ticket is None:
                return VetoOutcome(False, "unknown_proposal", proposal_id)
            if ticket.resolved and ticket.executed:
                return VetoOutcome(False, "already_executed", proposal_id)
            if ticket.vetoed:
                return VetoOutcome(True, "already_vetoed", proposal_id)   # idempotent

            ticket.vetoed = True
            ticket.vetoed_by = str(actor_user_id)
            ticket.resolved = True
            self.st.audit.append(
                EVENT_ADJUDICATION, actor=str(actor_user_id),
                refs={"proposal_id": proposal_id},
                payload={"decision": "veto", "proposal_id": proposal_id,
                         "risk_decision_id": ticket.risk_decision_id,
                         "order_tuple_hash": ticket.order_tuple_hash,
                         "actor": str(actor_user_id), "auto": True},
            )
            return VetoOutcome(True, "vetoed", proposal_id)

    # ---- resolve at the window edge ---------------------------------------------------
    def resolve(self, proposal_id: str, *, clock=None, injection_context=None,
                injection_predicate=None) -> VetoResolution:
        """Called at/after the window to decide execution. ALL checks are under ``st.lock`` so a
        concurrent ``veto()`` is seen atomically. Mints the submit token ONLY on a clean
        elapsed+delivered+un-vetoed window in a still-live ``auto_veto`` mode."""
        clock = clock or self.st.clock
        with self.st.lock:
            ticket = self._tickets.get(proposal_id)
            if ticket is None:
                return VetoResolution(False, "unknown_proposal", proposal_id)
            if ticket.resolved:
                # Terminal already (vetoed or executed) — idempotent.
                return VetoResolution(ticket.executed,
                                      "vetoed" if ticket.vetoed else "already_resolved",
                                      proposal_id)

            # SEC-23: silence is not consent. No confirmed delivery => never auto-execute.
            if not ticket.delivered or ticket.delivered_at_monotonic is None:
                return VetoResolution(False, "undelivered", proposal_id)

            # SEC-23: a backward monotonic jump is a forged/rewound clock trying to make the
            # window look elapsed — trip the synchronous injection breaker and refuse.
            now, backward = self._read_monotonic()
            if backward:
                self._breakers().check_order_path(
                    context=_TrueCtx(), predicate=lambda c: True,
                    reason="monotonic clock regressed during veto window (SEC-23)",
                )
                return VetoResolution(False, "clock_backwards", proposal_id)

            # Window not yet elapsed.
            if now < ticket.delivered_at_monotonic + ticket.window_seconds:
                return VetoResolution(False, "window_open", proposal_id)

            # Veto beat us to the lock.
            if ticket.vetoed:
                ticket.resolved = True
                return VetoResolution(False, "vetoed", proposal_id)

            # SEC-01 / mode re-check, atomic: HALT, live mode, injection breaker.
            # BUG-138: the HALT file never takes st.lock, so an out-of-band --set landing
            # between this probe and the mint below minted an auto-exec token DURING halt
            # (the token then survived to execute after the halt cleared with no fresh
            # consent). Re-probe immediately before minting so no token is born under HALT.
            if _halt_active():
                ticket.resolved = True
                return VetoResolution(False, "halt", proposal_id)

            if self._ladder().current() is not Mode.auto_veto:
                ticket.resolved = True
                return VetoResolution(False, "mode_changed", proposal_id)

            event = self._breakers().check_order_path(
                context=injection_context, predicate=injection_predicate,
                reason="injection/anomaly detected at veto-window fire (SEC-01)",
            )
            if event is not None:
                ticket.resolved = True
                return VetoResolution(False, "breaker", proposal_id)

            # Final fail-closed re-probe: HALT set during the mode/breaker checks above
            # must still cancel the mint (same TOCTOU discipline as the gateway's
            # pre-broker re-probes).
            if _halt_active():
                ticket.resolved = True
                return VetoResolution(False, "halt", proposal_id)

            # Clean: mint the single-use submit token (the auto_veto equivalent of an approval)
            # and audit the adjudication so the gate counts it (decision='approve', auto=True).
            # BUG-138b: full-precision delivery instant in the nonce (int() truncation made
            # two tickets delivered in the same second collide), and the mint is guarded:
            # a duplicate-nonce raise previously escaped out of the lock with the ticket
            # left unresolved and no audit row. A failed mint now fails closed with cover.
            nonce = f"vetotok_{proposal_id}_{ticket.delivered_at_monotonic!r}"
            try:
                token = self.token_registry.mint(
                    nonce=nonce, proposal_id=proposal_id,
                    risk_decision_id=ticket.risk_decision_id,
                    order_tuple_hash=ticket.order_tuple_hash,
                )
            except Exception as exc:   # noqa: BLE001 - a failed mint must not execute
                self.st.audit.append(
                    EVENT_ADJUDICATION, actor="auto_veto",
                    refs={"proposal_id": proposal_id},
                    payload={"decision": "veto", "proposal_id": proposal_id,
                             "risk_decision_id": ticket.risk_decision_id,
                             "order_tuple_hash": ticket.order_tuple_hash,
                             "actor": "auto_veto", "auto": True,
                             "reason": f"veto-token mint failed: {exc}"},
                )
                ticket.resolved = True
                return VetoResolution(False, "token_mint_failed", proposal_id)
            self.st.audit.append(
                EVENT_ADJUDICATION, actor="auto_veto",
                refs={"proposal_id": proposal_id},
                payload={"decision": "approve", "proposal_id": proposal_id,
                         "risk_decision_id": ticket.risk_decision_id,
                         "order_tuple_hash": ticket.order_tuple_hash,
                         "actor": "auto_veto", "auto": True},
            )
            ticket.resolved = True
            ticket.executed = True
            return VetoResolution(True, "elapsed_clean", proposal_id, token)


@dataclass
class _TrueCtx:
    """A context whose default injection predicate reads True — used to force the injection
    breaker to trip on a clock regression (the mechanism, not the WP8 detector)."""

    injection_detected: bool = True
