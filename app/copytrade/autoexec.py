"""RW-17 §1 — the PAPER auto-execution lane for congressional copy-trading.

THE OPERATOR'S REQUEST, AND WHAT THIS IS NOT
--------------------------------------------
*"i do not want to do any approvals when this tool is running, i want it to make trades for
me. all i ask is for notifications."* — they chose PAPER capital and a notify-plus-cancel
window rather than instant execution.

So this module **replaces the human approver with an audited auto-adjudication. It does not
remove the approval step, and it does not add a second order path.** Invariant 1 is intact:
every order still reaches the broker only via risk-pass -> mode -> approval -> ``OrderGateway``,
and the approval is a REAL, persisted, HMAC-covered ``ApprovalRecord``.

**NOTHING HERE MAKES LIVE CAPITAL REACHABLE.** Every resolve re-checks ``Mode.paper`` and the
gateway is expected to be the PAPER-scoped one (``_ORDER_AUTHORITY_BY_SCOPE['paper']``, which
admits ``paper`` and nothing else). ``auto_veto``/``auto_full`` are LIVE rungs here; they are
untouched, still gated by SEC-20/SEC-11, and this lane never steps onto them. RW-16's SEC-08
live block stays exactly where it is.

WHY THE APPROVAL HAS TO BE A REAL ROW (the one thing that is easy to get wrong)
------------------------------------------------------------------------------
``OrderGateway.submit`` gate (e) does **not** trust a caller-supplied approval object. It
re-loads the ``ApprovalRecord`` **by id from the DB** and then requires an HMAC-signed
``EVENT_ADJUDICATION`` whose ``refs['row_id'] == approval.id`` and whose payload binds
``decision='approve'`` + ``proposal_id`` + ``risk_decision_id`` + ``order_tuple_hash``
(``_adjudication_binds``). An appended audit event alone — the shape ``bridge/veto.py`` uses to
mint a capability token — would sail past nothing: gate (e) would refuse every single order.
So :meth:`PaperAutoExecutor._mint_approval` persists the row and its covering signed entry in
ONE transaction through ``st.audit.record_state_change``, exactly as
``bridge/approval.record_decision`` does for a human tap. ``tests/copytrade/test_autoexec.py``
drives a disclosure-shaped order all the way to a SUBMITTED paper order with zero human calls,
so this claim is executed, not asserted.

The rest of the file is ``bridge/veto.py``'s shape: a delivery-gated, monotonic-clock window
with an atomic re-check of every environmental control at the firing edge.

REFUSAL IS THE DEFAULT. A ticket resolves into an order only if ALL of these hold at T+0:

  * the operator did not cancel it (their ONLY lever — a VETO, never an approval);
  * the queued notification was CONFIRMED delivered (SEC-23: silence is not consent, and an
    unnotified auto-trade is the single thing the operator explicitly did not ask for);
  * the monotonic clock did not go backwards (a rewound time source trying to make the window
    look elapsed trips the injection breaker);
  * the HALT sentinel is clear (SEC-04);
  * ``ModeLadder.current() is Mode.paper`` — a mode change mid-window VOIDS the ticket;
  * no vendor window is watch-only, no breaker is tripped, and the synchronous injection
    breaker does not fire (SEC-01);
  * ``st.audit.verify_chain()`` is ok;
  * every budget rail in ``copytrade/budget.py`` still has room.

Each of those is a NAMED, COUNTED outcome in :data:`ALL_RESOLUTION_REASONS` and each is
notified. "The lane did nothing today" has to be a positive statement.

WHAT THE ADVERSARIAL ROUND CHANGED (RW-17 closure)
--------------------------------------------------
The first cut of this file got the *authority* right and the *liveness* wrong. Six defects, all
of the same family — an outcome that reaches nobody — are closed here, and each has a named
control you can grep for:

  1. **Per-ticket isolation.** ``resolve_due`` now guards EVERY ticket, and every fallible
     collaborator (``positions_fn``, ``risk_recheck_factory``, the runtime/ladder/breaker
     lookups) is evaluated BEFORE the approval is minted. A broker fault used to escape the
     tick, abandon every ticket behind it un-notified, and leave a minted approval on the chain
     that permanently refused the filing as an ``approval_replay`` while still consuming budget.
  2. **Nothing is delivered while ``st.lock`` is held.** Refusals collect into an OUTBOX and are
     pushed after the lock is released. The lock guards the order path, the mode ladder and the
     breaker engine; a hung webhook must never hold it.
  3. **Terminal means FINAL, not "transient".** HALT, watch-only, a tripped breaker, an
     unverifiable chain and every per-DAY budget rail are TRANSIENT (:data:`TRANSIENT_REASONS`)
     and keep the ticket live, because the ``Order`` row already exists and
     ``workflows/copytrade_orders._consuming`` will never re-propose the disclosure — a terminal
     refusal on a five-minute HALT stranded the filing forever.
  4. **Tickets expire.** A ticket that can never resolve used to live (and re-notify, and retry
     delivery) for the life of the process. :data:`REASON_TICKET_EXPIRED` bounds it, the live
     queue is capped, and the queued-delivery retry backs off.
  5. **Every terminal notification is retried and counted.** Only ``queued`` gated execution;
     ``submitted``/``refused``/``cancelled`` were fire-and-forget, so a surface outage produced a
     trade the operator was never told about. They now retry on later ticks and every
     non-delivery is counted under :data:`REASON_NOTIFY_UNDELIVERED`.
  6. **The ticket lifecycle is on the SIGNED chain** (:data:`EVENT_AUTOEXEC_TICKET`). Tickets
     used to live only in ``self._tickets``, so a restart between the queued notice and the
     window edge vaporised the trade silently. :meth:`PaperAutoExecutor.recover_orphans` reads
     the chain at boot and settles — named, notified, counted — every ticket the operator was
     promised and the process never resolved. It is also what makes the daily digest
     (``copytrade/digest.py``) survive a restart, since ``self.counts`` does not.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bridge.approval import token_nonce_for_approval
from bridge.operators import is_authorized
from brokers.auto_paper_submit import PAPER_SCOPE, auto_paper_place
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode
from copytrade.budget import (
    ALL_RAILS,
    AUTOEXEC_LANE,
    AutoExecBudget,
    budget_state,
)
from copytrade.budget import check as budget_check
from core.ids import new_id
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.state import (
    EVENT_ADJUDICATION,
    EVENT_BREAKER_CLEAR,
    EVENT_BREAKER_TRIP,
)
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order

log = logging.getLogger(__name__)

DEFAULT_AUTOEXEC_WINDOW_SECONDS = 300.0

DEFAULT_AUTOEXEC_TICKET_MAX_AGE_SECONDS = 21_600.0
"""How long a ticket may stay LIVE before it is refused as :data:`REASON_TICKET_EXPIRED`.

Six hours — comfortably one regular session. It exists because most refusals here are now
TRANSIENT (a HALT clears, a breaker is reset, tomorrow's budget is fresh), and a transient
refusal keeps the ticket alive. Without an upper bound, a lane whose HALT was set on Monday
would still be counting down a Monday-priced order on Friday, re-attempting a notification every
minute. The trade this lane copies is 30-45 days old by statute, so nothing is lost by refusing
a ticket that could not resolve inside a session — and an expiry is a NAMED, NOTIFIED, COUNTED
outcome, which is the only kind this module is allowed to have.
"""

EVENT_AUTOEXEC_TICKET = "autoexec_ticket"
"""The signed lifecycle event for ONE ticket: ``state='queued'`` when the operator was told a
trade is coming, ``state='settled'`` when it executed or was refused.

Two things depend on it, and neither can be served by ``self._tickets`` or ``self.counts``:

  * :meth:`PaperAutoExecutor.recover_orphans` — a queued-with-no-settled pair on the chain is a
    trade the operator was PROMISED and this process never delivered an outcome for (a restart
    inside the cancel window). In-memory state cannot see across a restart; the chain can.
  * ``copytrade/digest.py`` — the end-of-day statement is derived from these entries rather than
    from the in-memory histogram, so a lane that refused forty trades and a lane that saw no
    filings stay distinguishable across a restart.

It is an APPEND-ONLY, HMAC-covered record like every other entry (Invariant 6). It is NOT an
authority: the gateway's gate (e) reads ``EVENT_ADJUDICATION`` and nothing else, and no code
path grants an order anything on the strength of one of these.
"""

# The actor recorded on every row and entry this lane writes. Deliberately NOT the operator's
# id: the audit log must say a ROBOT decided this. ``bridge/operators.is_authorized`` will
# never return True for it, which is the point — it can adjudicate on the paper lane, and it
# can never impersonate the human anywhere the human's authority is what is being checked.
AUTOEXEC_ACTOR = "copytrade_autoexec"
AUTOEXEC_SURFACE = "autoexec"

# ---- the closed outcome vocabulary (every non-emitting outcome is named + counted) -----
REASON_EXECUTED = "executed"
REASON_UNKNOWN_TICKET = "unknown_ticket"
REASON_ALREADY_RESOLVED = "already_resolved"
REASON_CANCELLED = "cancelled"
REASON_UNDELIVERED = "undelivered"
REASON_WINDOW_OPEN = "window_open"
REASON_CLOCK_BACKWARDS = "clock_backwards"
REASON_HALT = "halt"
REASON_MODE_NOT_PAPER = "mode_not_paper"
REASON_NOT_PAPER_TRANSPORT = "not_paper_transport"
REASON_WATCH_ONLY = "watch_only"
REASON_BREAKER_TRIPPED = "breaker_tripped"
REASON_BREAKER = "breaker"
REASON_CHAIN_UNVERIFIED = "audit_chain_unverified"
REASON_ORDER_MISSING = "order_missing"
REASON_APPROVAL_REPLAY = "approval_replay"
REASON_APPROVAL_FAILED = "approval_write_failed"
REASON_GATEWAY_REFUSED = "gateway_refused"
REASON_PENDING_EXPOSURE = "pending_exposure"
REASON_POSITIONS_UNAVAILABLE = "positions_unavailable"
REASON_RESOLVE_FAULT = "resolve_fault"
REASON_TICKET_EXPIRED = "ticket_expired"
REASON_SUPERSEDED = "superseded"
REASON_QUEUE_FULL = "queue_full"
REASON_ABANDONED_RESTART = "abandoned_restart"
REASON_NOTIFY_UNDELIVERED = "notification_undelivered"

ALL_RESOLUTION_REASONS: tuple[str, ...] = (
    REASON_EXECUTED,
    REASON_UNKNOWN_TICKET,
    REASON_ALREADY_RESOLVED,
    REASON_CANCELLED,
    REASON_UNDELIVERED,
    REASON_WINDOW_OPEN,
    REASON_CLOCK_BACKWARDS,
    REASON_HALT,
    REASON_MODE_NOT_PAPER,
    REASON_NOT_PAPER_TRANSPORT,
    REASON_WATCH_ONLY,
    REASON_BREAKER_TRIPPED,
    REASON_BREAKER,
    REASON_CHAIN_UNVERIFIED,
    REASON_ORDER_MISSING,
    REASON_APPROVAL_REPLAY,
    REASON_APPROVAL_FAILED,
    REASON_GATEWAY_REFUSED,
    REASON_PENDING_EXPOSURE,
    REASON_POSITIONS_UNAVAILABLE,
    REASON_RESOLVE_FAULT,
    REASON_TICKET_EXPIRED,
    REASON_SUPERSEDED,
    REASON_QUEUE_FULL,
    REASON_ABANDONED_RESTART,
    REASON_NOTIFY_UNDELIVERED,
) + ALL_RAILS

REFUSABLE_REASONS: frozenset[str] = frozenset({
    REASON_CLOCK_BACKWARDS, REASON_HALT, REASON_MODE_NOT_PAPER, REASON_NOT_PAPER_TRANSPORT,
    REASON_WATCH_ONLY, REASON_BREAKER_TRIPPED, REASON_BREAKER, REASON_CHAIN_UNVERIFIED,
    REASON_ORDER_MISSING, REASON_APPROVAL_REPLAY, REASON_APPROVAL_FAILED,
    REASON_GATEWAY_REFUSED, REASON_PENDING_EXPOSURE, REASON_POSITIONS_UNAVAILABLE,
    REASON_RESOLVE_FAULT, REASON_TICKET_EXPIRED, REASON_SUPERSEDED, REASON_QUEUE_FULL,
    REASON_ABANDONED_RESTART, REASON_UNDELIVERED,
}) | frozenset(ALL_RAILS)
"""Every reason that can reach :meth:`PaperAutoExecutor._refuse` and therefore the operator's
screen. ``copytrade/notify.py`` must be able to name a GATE for each of them — a refusal that
renders as an unrecognised code tells the operator about our internal drift instead of about
their lane, and ``tests/copytrade/test_notify.py`` fails if this set grows a member the gate
table does not cover."""

TRANSIENT_REASONS: frozenset[str] = frozenset({
    REASON_HALT,
    REASON_WATCH_ONLY,
    REASON_BREAKER_TRIPPED,
    REASON_BREAKER,
    REASON_CHAIN_UNVERIFIED,
    REASON_POSITIONS_UNAVAILABLE,
    REASON_RESOLVE_FAULT,
    REASON_PENDING_EXPOSURE,
}) | frozenset(ALL_RAILS)
"""Refusals that DO NOT kill the ticket, because the condition is expected to clear.

This is a correctness control, not a convenience. ``workflows/copytrade_orders._consuming``
treats "an ``Order`` row exists" as "this disclosure was copied", so a filing whose only order
is a dead ticket is NEVER re-proposed. Marking a five-minute HALT, a reset-able breaker, or a
per-DAY budget cap terminal therefore stranded the filing permanently — the exact failure that
function's docstring was written to prevent ("A HALT window is transient by design; a retry is
the correct response to it"). A transient refusal keeps the ticket live, re-evaluates it on the
next tick, and is bounded by :data:`DEFAULT_AUTOEXEC_TICKET_MAX_AGE_SECONDS` so it cannot become
an immortal one.

Deliberately NOT here: ``cancelled`` (the operator decided), ``mode_not_paper`` /
``not_paper_transport`` (the rung is what permits a robot to adjudicate at all — a ticket queued
in paper is VOID off it), ``clock_backwards`` (an attack signal, not a condition), and every
outcome that already touched the chain (``approval_*``, ``gateway_refused``, ``order_missing``)
where retrying would mint or replay authority."""

# Notification events this module emits. ``filled`` and ``daily_digest`` belong to the
# notifier/scheduler, not to the execution lane.
NOTIFY_QUEUED = "queued"
NOTIFY_SUBMITTED = "submitted"
NOTIFY_CANCELLED = "cancelled"
NOTIFY_REFUSED = "refused"


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _naive_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)


def _default_monotonic() -> float:
    return time.monotonic()


def _ticket_notional(order) -> Decimal:
    """The dollar amount the budget rails measure this order against.

    Explicit notional, else ``qty * limit_price``. An UN-PRICED order (a market order with no
    client-side price) yields ``NaN``, NOT zero — an unmeasurable commitment must fail the
    rails rather than sail through them as free. RW-16 only ever emits limit orders (there is
    nothing in a six-week-old filing that could justify paying for immediacy), so this branch
    is a guard against a future caller, not a live case."""
    if order.notional is not None:
        value = _dec(order.notional)
        if value.is_finite():
            return value
    if order.limit_price is not None:
        value = _dec(order.qty) * _dec(order.limit_price)
        if value.is_finite():
            return value
    return Decimal("NaN")


def fail_closed_risk_recheck(_order):
    """The DEFAULT risk re-check factory: refuses everything.

    ``OrderGateway`` gate (d) re-runs risk immediately before the broker call, and this lane
    cannot compute that verdict itself (it needs a fresh quote + a positions snapshot, both of
    which live in the composition layer). An un-wired lane must therefore refuse rather than
    invent an approval, so the default returns a REJECTED verdict with a named failed check.
    The composer passes the real factory — see ``PaperAutoExecutor.__init__``."""

    def _recheck(_positions):
        return {"decision": "rejected",
                "failed_checks": ["autoexec_risk_recheck_not_wired"]}

    return _recheck


@dataclass
class AutoTicket:
    """One queued auto-trade, live between ``schedule`` and its terminal resolution.

    ``delivered_at_monotonic`` is stamped only when the queued notification is CONFIRMED, and
    the cancel window is measured from THAT instant on a monotonic clock — never from wall
    time, and never from the moment we merely tried to notify."""

    proposal_id: str
    order_id: str
    risk_decision_id: str
    order_tuple_hash: str
    symbol: str
    side: str
    notional: Decimal
    window_seconds: float
    queued_at: datetime | None = None
    delivered: bool = False
    delivered_at_monotonic: float | None = None
    cancelled: bool = False
    cancelled_by: str | None = None
    resolved: bool = False          # terminal: executed, cancelled, or refused
    executed: bool = False
    approval_id: str | None = None
    context: dict = field(default_factory=dict)   # rendering facts for the notifier

    # ---- liveness bookkeeping (all added by the RW-17 adversarial round) ----------------
    max_age_seconds: float = DEFAULT_AUTOEXEC_TICKET_MAX_AGE_SECONDS
    scheduled_at_monotonic: float | None = None
    """Stamped at :meth:`PaperAutoExecutor.schedule`, INDEPENDENTLY of delivery. The expiry
    clock has to start when the ticket was created, not when the operator was told: a ticket
    whose notification never lands would otherwise never age and never expire."""
    delivery_attempts: int = 0
    next_delivery_monotonic: float | None = None
    """Backoff watermark for the QUEUED-notification retry. Without it, N undelivered tickets
    produce N delivery attempts on EVERY 60s tick, forever — a self-sustaining storm against
    precisely the surface that is already failing."""
    last_refusal_reason: str | None = None
    """The last refusal the operator was TOLD about. A transient refusal is re-evaluated every
    tick; re-sending the same message every minute for the duration of a HALT would train the
    operator to ignore the one channel they have left. The reason CHANGING is news; the reason
    persisting is not."""
    pending_notices: list = field(default_factory=list)
    """Terminal notifications (``submitted``/``refused``/``cancelled``) whose delivery was NOT
    confirmed, retried on later ticks. Each item is a mutable dict
    ``{event, payload, attempts, next_monotonic}``."""

    def seconds_remaining(self, now_monotonic: float) -> float | None:
        if self.delivered_at_monotonic is None:
            return None
        return max(self.delivered_at_monotonic + self.window_seconds - now_monotonic, 0.0)

    def expired(self, now_monotonic: float) -> bool:
        if self.scheduled_at_monotonic is None or self.max_age_seconds <= 0:
            return False
        return now_monotonic - self.scheduled_at_monotonic >= self.max_age_seconds


@dataclass(frozen=True)
class AutoResolution:
    """Outcome of one resolve attempt. ``executed`` True means a paper order reached the
    broker through the gateway; otherwise ``reason`` names — from
    :data:`ALL_RESOLUTION_REASONS` — exactly which control refused, and ``detail`` says by how
    much, so the refusal notification is never just "no"."""

    executed: bool
    reason: str
    proposal_id: str
    order_id: str | None = None
    detail: str | None = None
    approval_id: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None


@dataclass(frozen=True)
class CancelOutcome:
    cancelled: bool
    reason: str
    proposal_id: str


@dataclass
class _TrueCtx:
    """Forces the injection breaker to trip (the mechanism, not a detector) — used when the
    monotonic clock regresses mid-window. Copied from ``bridge/veto.py`` deliberately: a
    rewound time source on an auto-execution path is the same attack there and here."""

    injection_detected: bool = True


class PaperAutoExecutor:
    """The zero-touch PAPER lane. One per orchestrator process.

    Collaborators are injected so the whole thing is unit-testable against a ``FrozenClock``
    and a tmp SQLite chain, with no network and no human:

      * ``notify`` — the operator surface seam. A callable ``notify(event, payload) -> bool``
        (or an object exposing ``send``/``deliver``/``notify``). The RETURN VALUE IS THE
        DELIVERY CONFIRMATION: truthy means the surface confirmed receipt. Falsy, or a raised
        exception, means UNCONFIRMED — and an unconfirmed queued notification blocks execution
        forever, by design. Do NOT hardwire Discord here; ``copytrade/notify.py`` adapts the
        abstract surface (CLAUDE.md).
      * ``risk_recheck_factory(order) -> callable(positions) -> verdict`` — gate (d)'s fresh
        deterministic re-run. Defaults to :func:`fail_closed_risk_recheck`.
      * ``positions_fn() -> positions`` — the fresh snapshot handed to that re-check.
      * ``budget`` — the :class:`~copytrade.budget.AutoExecBudget` rails; defaults to reading
        them off ``st.settings`` with conservative fallbacks.
    """

    _MAX_SETTLED_TICKETS = 1024

    _MAX_LIVE_TICKETS = 64
    """Hard cap on tickets that may be LIVE at once, across all cycles.

    Nothing upstream bounds how many orders one cycle may produce (``run_copy_cycle`` iterates
    the whole batch; the disclosure scan limit is 20_000), so a parser bug that turns one filing
    into three hundred produces three hundred tickets, three hundred notifications, and — on a
    degraded surface — three hundred delivery retries per minute forever. The budget rails bound
    the DOLLARS but not the QUEUE. This bounds the queue: past the cap, ``schedule`` refuses by
    name (:data:`REASON_QUEUE_FULL`) instead of accepting work it cannot honestly service."""

    _MAX_NOTICE_ATTEMPTS = 5
    """How many times a TERMINAL notification is retried before it is abandoned (counted, and
    logged at error). Unlike the queued notice — which gates execution and so may retry until
    the ticket expires — a submitted/refused/cancelled notice describes something that has
    already happened, so retrying it forever only grows the storm."""

    _NOTICE_BACKOFF_SECONDS = (0.0, 60.0, 300.0, 900.0, 1800.0)

    def __init__(self, st, gateway, *, window_seconds: float = DEFAULT_AUTOEXEC_WINDOW_SECONDS,
                 notify=None, clock=None, policy=None, budget: AutoExecBudget | None = None,
                 risk_recheck_factory: Callable | None = None,
                 positions_fn: Callable | None = None,
                 monotonic: Callable[[], float] | None = None,
                 breaker_engine=None, mode_ladder=None,
                 producing_runtime: str | None = None,
                 ticket_max_age_seconds: float | None = None) -> None:
        self.st = st
        self.gateway = gateway
        self.window_seconds = float(window_seconds)
        self.ticket_max_age_seconds = float(
            ticket_max_age_seconds if ticket_max_age_seconds is not None
            else getattr(st.settings, "congress_autoexec_ticket_max_age_seconds",
                         DEFAULT_AUTOEXEC_TICKET_MAX_AGE_SECONDS))
        self._notify = notify
        self._clock = clock or st.clock
        self.policy = policy
        self.budget = budget if budget is not None else AutoExecBudget.from_settings(st.settings)
        self._risk_recheck_factory = risk_recheck_factory or fail_closed_risk_recheck
        self._positions_fn = positions_fn or (lambda: [])
        if risk_recheck_factory is None:
            # SAY IT AT BOOT, NOT AT THE FIRST REFUSAL. Without this seam the lane is armed,
            # notifies, counts down, and then refuses EVERY ticket at gate (d) — an operator
            # who has stopped watching an approval queue would read that as "nothing to copy
            # today" rather than "the feature is not wired". Fail closed AND loud.
            log.warning(
                "RW-17: the PAPER auto-execution lane has NO risk_recheck_factory wired, so it "
                "will refuse every ticket at gate (d) ('autoexec_risk_recheck_not_wired'). "
                "Compose it with risk_recheck_factory= (and positions_fn=) from the paper "
                "execution service, or the lane can never trade."
            )
        self._monotonic = monotonic or _default_monotonic
        self._breaker_engine = breaker_engine
        self._mode_ladder = mode_ladder
        self._producing_runtime = producing_runtime
        self._tickets: dict[str, AutoTicket] = {}
        self._last_monotonic: float | None = None
        # Every non-emitting outcome is counted, by name, for the daily digest. A lane that
        # refuses everything and a lane that saw nothing must not look alike.
        self.counts: dict[str, int] = {reason: 0 for reason in ALL_RESOLUTION_REASONS}

    # ---- collaborators (lazily built so tests can inject) ------------------------------
    def _breakers(self):
        if self._breaker_engine is None:
            from orchestrator.breakers import BreakerEngine
            self._breaker_engine = BreakerEngine(self.st)
        return self._breaker_engine

    def _ladder(self):
        if self._mode_ladder is None:
            from orchestrator.mode_ladder import ModeLadder
            self._mode_ladder = ModeLadder(self.st)
        return self._mode_ladder

    def _runtime(self) -> str:
        """The copy path's fixed provenance string. Imported lazily: ``workflows`` imports
        ``copytrade``, so a module-level import here would close a cycle."""
        if self._producing_runtime is None:
            from workflows.copytrade_orders import COPYTRADE_RUNTIME
            self._producing_runtime = COPYTRADE_RUNTIME
        return self._producing_runtime

    # ---- clock ------------------------------------------------------------------------
    def _read_monotonic(self) -> tuple[float, bool]:
        """Read the monotonic clock and report whether it went BACKWARD since the last read.
        The stored watermark never rewinds, so a forged time source cannot walk the window
        forward one refusal at a time (SEC-23, ``bridge/veto.py``)."""
        now = self._monotonic()
        backward = self._last_monotonic is not None and now < self._last_monotonic
        if not backward:
            self._last_monotonic = now
        return now, backward

    def _today(self, now: datetime | None = None) -> date:
        return _naive_utc(now or self._clock.now()).date()

    # ---- notification seam -------------------------------------------------------------
    def _deliver(self, event: str, payload: dict) -> bool:
        """Push one event at the operator surface and report whether delivery was CONFIRMED.

        Fail closed in BOTH directions that matter: a seam that is not wired at all, one that
        returns falsy, and one that raises are all UNCONFIRMED. This is the only interface the
        operator has left, so a silent execution is the failure mode being designed against —
        never the other way round."""
        target = self._notify
        if target is None:
            return False
        fn = target if callable(target) else (
            getattr(target, "send", None)
            or getattr(target, "deliver", None)
            or getattr(target, "notify", None)
        )
        if fn is None:
            log.error("autoexec: notify seam %r exposes no send/deliver/notify", type(target))
            return False
        try:
            result = fn(event, payload)
        except Exception:                    # noqa: BLE001 - a failed post is an UNDELIVERED post
            log.exception("autoexec: delivery of %r failed; the trade will NOT proceed", event)
            return False
        for attr in ("delivered", "confirmed", "ok"):
            if hasattr(result, attr):
                return bool(getattr(result, attr))
        return bool(result)

    def _notify_payload(self, ticket: AutoTicket, **extra) -> dict:
        """Deterministic, JSON-native facts for the notifier. Rendered from STORED values only
        — never model text (Invariant: execution stays model-free, and so does its narration)."""
        payload = {
            "lane": AUTOEXEC_LANE,
            "proposal_id": ticket.proposal_id,
            "order_id": ticket.order_id,
            # The ID, never the values. SEC-17 requires the operator's risk block to be
            # rendered from the stored ``RiskDecision`` ROW, so the notifier is handed the
            # pointer this ticket is bound to and reads the record back itself; a verdict or a
            # ceiling copied into this dict would be a risk number sourced from a payload.
            "risk_decision_id": ticket.risk_decision_id,
            "symbol": ticket.symbol,
            "side": ticket.side,
            "notional": str(ticket.notional),
            "window_seconds": ticket.window_seconds,
            "queued_at": None if ticket.queued_at is None else ticket.queued_at.isoformat(),
            "cancel_command": f"/cancel {ticket.proposal_id}",
        }
        payload.update(ticket.context or {})
        payload.update(extra)
        return payload

    # ---- the OUTBOX: nothing is ever delivered while ``st.lock`` is held ----------------
    def _flush(self, outbox: list) -> None:
        """Deliver everything the locked section queued, AFTER the lock was released.

        ``st.lock`` is the single orchestrator lock: it guards ``OrderGateway.review/submit``,
        ``ModeLadder.set_mode`` and the breaker engine. The production delivery seam is an HTTP
        POST with its own retry budget. Holding the lock across it stalls the human order path,
        the mode ladder and the killswitch's mode drop for as long as the surface hangs — and
        because the resolve tick is every minute with ``max_instances=1``, every following tick
        is dropped too. The SUBMITTED notice always did this correctly; the refusal paths did
        not, and now every path goes through here.

        An unconfirmed delivery is not dropped: it is parked on the ticket and retried on later
        ticks by :meth:`_retry_notices`, and counted under
        :data:`REASON_NOTIFY_UNDELIVERED` either way."""
        for ticket, event, payload in outbox:
            if self._deliver(event, payload):
                continue
            self._count(REASON_NOTIFY_UNDELIVERED)
            log.warning("autoexec: %s notice for %s was NOT confirmed delivered; queued for "
                        "retry", event, ticket.proposal_id)
            with self.st.lock:
                ticket.pending_notices.append(
                    {"event": event, "payload": payload, "attempts": 1,
                     "next_monotonic": self._monotonic() + self._NOTICE_BACKOFF_SECONDS[1]}
                )

    def _retry_notices(self, ticket: AutoTicket) -> None:
        """Re-attempt this ticket's undelivered TERMINAL notices, with backoff.

        AUTOEXEC.md §3 makes delivery a precondition of execution. That is honoured for
        ``queued`` by refusing to fire — but ``submitted`` describes a trade that has ALREADY
        reached the broker, so the only honest remedy left is to keep trying and to count the
        failure. An executed paper trade the operator was never told about is the failure this
        package exists to prevent; it must at minimum be visible in the counters and the digest.
        """
        with self.st.lock:
            due = [n for n in ticket.pending_notices
                   if n["next_monotonic"] is None or self._monotonic() >= n["next_monotonic"]]
        for notice in due:
            if self._deliver(notice["event"], notice["payload"]):
                with self.st.lock:
                    if notice in ticket.pending_notices:
                        ticket.pending_notices.remove(notice)
                continue
            self._count(REASON_NOTIFY_UNDELIVERED)
            notice["attempts"] += 1
            if notice["attempts"] >= self._MAX_NOTICE_ATTEMPTS:
                log.error(
                    "autoexec: ABANDONING the %r notice for %s after %d attempts — the operator "
                    "has NOT been told about this outcome; it survives only in the signed "
                    "%s chain and in the daily digest",
                    notice["event"], ticket.proposal_id, notice["attempts"],
                    EVENT_AUTOEXEC_TICKET,
                )
                with self.st.lock:
                    if notice in ticket.pending_notices:
                        ticket.pending_notices.remove(notice)
                continue
            idx = min(notice["attempts"], len(self._NOTICE_BACKOFF_SECONDS) - 1)
            notice["next_monotonic"] = self._monotonic() + self._NOTICE_BACKOFF_SECONDS[idx]

    # ---- the signed ticket lifecycle ---------------------------------------------------
    def _append_ticket_event(self, ticket: AutoTicket, state: str, **extra) -> None:
        """Append one :data:`EVENT_AUTOEXEC_TICKET` entry. NEVER raises.

        A chain write that fails must not undo a trade that already happened or turn a refusal
        into an exception, so the failure is logged and counted rather than propagated. The
        caller holds ``st.lock``."""
        payload = {
            "lane": AUTOEXEC_LANE,
            "state": state,
            "proposal_id": ticket.proposal_id,
            "order_id": ticket.order_id,
            "risk_decision_id": ticket.risk_decision_id,
            "symbol": ticket.symbol,
            "side": ticket.side,
            "notional": str(ticket.notional),
            "window_seconds": ticket.window_seconds,
        }
        payload.update(extra)
        try:
            self.st.audit.append(
                EVENT_AUTOEXEC_TICKET, actor=AUTOEXEC_ACTOR,
                refs={"proposal_id": ticket.proposal_id, "order_id": ticket.order_id},
                payload=payload,
            )
        except Exception:                    # noqa: BLE001 - a ledger fault is not an outcome
            log.exception("autoexec: could not append the %r ticket event for %s",
                          state, ticket.proposal_id)

    # ---- schedule ----------------------------------------------------------------------
    def schedule(self, *, order, risk_decision, proposal_id: str,
                 window_seconds: float | None = None, context: dict | None = None) -> AutoTicket:
        """Queue an already-drafted, risk-approved ``Order`` row for auto-execution.

        This does NOT arm anything by itself. The cancel window only starts once the queued
        notification is CONFIRMED delivered, which is attempted here and retried (with backoff)
        on subsequent :meth:`resolve_due` ticks until it succeeds.

        TWO REFUSALS LIVE HERE, and both exist because the alternative is a SILENT outcome:

          * **a live ticket is never overwritten.** ``schedule`` used to assign
            ``self._tickets[proposal_id]`` unconditionally, so re-scheduling a proposal that
            already had a notified, counting-down ticket dropped the first one on the floor: it
            was never resolved, never counted, never notified, and the ``/cancel <proposal_id>``
            the operator was holding silently re-targeted a DIFFERENT order. The displaced
            ticket is now settled as :data:`REASON_SUPERSEDED` first.
          * **the queue is capped.** Past :attr:`_MAX_LIVE_TICKETS` live tickets the new one is
            refused as :data:`REASON_QUEUE_FULL` rather than accepted — see that constant.

        Both return a ticket that is already ``resolved``; the caller does not have to branch.
        """
        canon = canonical_order_tuple(
            symbol=order.symbol, side=order.side, qty=order.qty,
            order_type=order.order_type, limit_price=order.limit_price, tif=order.tif,
        )
        ticket = AutoTicket(
            proposal_id=proposal_id,
            order_id=order.id,
            risk_decision_id=risk_decision.id,
            order_tuple_hash=order_tuple_hash(canon),
            symbol=str(order.symbol).upper(),
            side=str(order.side).lower(),
            notional=_ticket_notional(order),
            window_seconds=self.window_seconds if window_seconds is None else float(window_seconds),
            queued_at=self._clock.now(),
            context=dict(context or {}),
            max_age_seconds=self.ticket_max_age_seconds,
        )
        outbox: list = []
        accepted = True
        with self.st.lock:
            self._prune_settled()
            ticket.scheduled_at_monotonic = self._monotonic()
            displaced = self._tickets.get(proposal_id)
            if displaced is not None and not displaced.resolved:
                self._refuse(
                    displaced, REASON_SUPERSEDED,
                    f"proposal {proposal_id!r} was re-queued with a different order before this "
                    "ticket resolved; the earlier trade is cancelled and nothing was sent",
                    outbox=outbox, terminal=True,
                )
            live = sum(1 for t in self._tickets.values() if not t.resolved)
            if live >= self._MAX_LIVE_TICKETS:
                accepted = False
                self._refuse(
                    ticket, REASON_QUEUE_FULL,
                    f"{live} auto-trade ticket(s) are already queued (cap "
                    f"{self._MAX_LIVE_TICKETS}); refusing to queue another rather than promising "
                    "an outcome this lane cannot deliver",
                    outbox=outbox, terminal=True,
                )
            self._tickets[proposal_id] = ticket
        self._flush(outbox)
        if accepted:
            self._try_confirm_delivery(ticket)
        return ticket

    def _prune_settled(self) -> None:
        """Bound the ticket map. Settled tickets are a recent-history window (oldest dropped
        first — dict preserves insertion order); LIVE tickets are never dropped. Same fix as
        BUG-080 on ``AutoVetoCoordinator``. Caller holds ``st.lock``.

        A settled ticket that still owes the operator a notification is NOT dropped: forgetting
        it would turn a retryable non-delivery into the silent stop this module refuses to have.
        """
        settled = [pid for pid, t in self._tickets.items()
                   if t.resolved and not t.pending_notices]
        excess = len(settled) - self._MAX_SETTLED_TICKETS
        for pid in settled[:excess] if excess > 0 else []:
            del self._tickets[pid]

    def _try_confirm_delivery(self, ticket: AutoTicket) -> bool:
        """Attempt the ``queued`` notification and, ONLY on a confirmed delivery, start the
        window clock. Delivery is attempted outside ``st.lock`` (it may touch a network
        surface); the stamp is taken under it.

        THE RETRY BACKS OFF. Every live ticket used to re-attempt its queued notice on every
        60s tick; three hundred undelivered tickets was six hundred POSTs a minute, forever,
        against a rate limit the storm itself trips — so "delivery failed" became a
        self-sustaining state and the serial resolve loop could overrun its own cadence. Now the
        Nth failure waits :attr:`_NOTICE_BACKOFF_SECONDS`\\ [N] before the next attempt."""
        if ticket.delivered:
            return True
        with self.st.lock:
            due = (ticket.next_delivery_monotonic is None
                   or self._monotonic() >= ticket.next_delivery_monotonic)
        if not due:
            return False
        delivered = self._deliver(NOTIFY_QUEUED, self._notify_payload(ticket))
        with self.st.lock:
            ticket.delivery_attempts += 1
            if not delivered:
                idx = min(ticket.delivery_attempts, len(self._NOTICE_BACKOFF_SECONDS) - 1)
                ticket.next_delivery_monotonic = (
                    self._monotonic() + self._NOTICE_BACKOFF_SECONDS[idx])
                return False
            if ticket.delivered:
                return True
            now, backward = self._read_monotonic()
            if backward:
                # A backward reading at confirm must not stamp a stale-low delivery instant: a
                # later forward clock would then see an already-elapsed window and fire without
                # any real cancel opportunity. Leave the ticket UNDELIVERED (SEC-23).
                return False
            ticket.delivered = True
            ticket.delivered_at_monotonic = now
            # THE OPERATOR HAS NOW BEEN PROMISED A TRADE. Record that promise on the signed
            # chain: it is the only artefact that survives a restart, and without it a process
            # that dies inside the cancel window leaves a message saying "this WILL execute"
            # with nothing anywhere able to say it did not. See :meth:`recover_orphans`.
            self._append_ticket_event(ticket, "queued")
        return True

    def confirm_delivery(self, proposal_id: str) -> AutoTicket | None:
        """Public delivery-confirmation seam, for a surface that acknowledges asynchronously."""
        with self.st.lock:
            ticket = self._tickets.get(proposal_id)
        if ticket is None:
            return None
        with self.st.lock:
            if not ticket.delivered:
                now, backward = self._read_monotonic()
                if not backward:
                    ticket.delivered = True
                    ticket.delivered_at_monotonic = now
        return ticket

    # ---- the operator's ONLY lever -----------------------------------------------------
    def cancel(self, proposal_id: str, *, actor_user_id) -> CancelOutcome:
        """Cancel a queued auto-trade. This is a VETO, not an approval: the operator never has
        to act for a trade to PROCEED, only to stop one.

        Idempotent (a double tap returns ``cancelled=True`` and has no second effect) and
        atomic against a concurrent :meth:`resolve_due` — both take ``st.lock``, so a cancel
        landing at T-1s WINS. An unauthorized actor is refused and audited and can never
        cancel; a foreign user must not be able to suppress the operator's trades either.
        """
        notify_payload = None
        with self.st.lock:
            if not is_authorized(actor_user_id, self.st.settings):
                self.st.audit.append(
                    "unauthorized_actor", actor=str(actor_user_id),
                    refs={"proposal_id": proposal_id},
                    payload={"proposal_id": proposal_id, "attempted_decision": "cancel",
                             "lane": AUTOEXEC_LANE},
                )
                return CancelOutcome(False, "unauthorized", proposal_id)

            ticket = self._tickets.get(proposal_id)
            if ticket is None:
                return CancelOutcome(False, REASON_UNKNOWN_TICKET, proposal_id)
            if ticket.executed:
                return CancelOutcome(False, "already_executed", proposal_id)
            if ticket.cancelled:
                return CancelOutcome(True, "already_cancelled", proposal_id)   # idempotent
            if ticket.resolved:
                return CancelOutcome(False, REASON_ALREADY_RESOLVED, proposal_id)

            ticket.cancelled = True
            ticket.cancelled_by = str(actor_user_id)
            ticket.resolved = True
            self.st.audit.append(
                EVENT_ADJUDICATION, actor=str(actor_user_id),
                refs={"proposal_id": proposal_id},
                payload={"decision": "veto", "proposal_id": proposal_id,
                         "risk_decision_id": ticket.risk_decision_id,
                         "order_tuple_hash": ticket.order_tuple_hash,
                         "actor": str(actor_user_id), "auto": False,
                         "lane": AUTOEXEC_LANE, "kind": "autoexec_cancel",
                         "order_id": ticket.order_id, "symbol": ticket.symbol,
                         "side": ticket.side},
            )
            self.counts[REASON_CANCELLED] = self.counts.get(REASON_CANCELLED, 0) + 1
            self._append_ticket_event(ticket, "settled", executed=False,
                                      reason=REASON_CANCELLED,
                                      detail=f"cancelled by {actor_user_id}")
            notify_payload = self._notify_payload(ticket, cancelled_by=str(actor_user_id))
        self._flush([(ticket, NOTIFY_CANCELLED, notify_payload)])
        return CancelOutcome(True, REASON_CANCELLED, proposal_id)

    # ---- the scheduler tick ------------------------------------------------------------
    def pending(self) -> list[AutoTicket]:
        with self.st.lock:
            return [t for t in self._tickets.values() if not t.resolved]

    def resolve_due(self, *, now: datetime | None = None, injection_context=None,
                    injection_predicate=None) -> list[AutoResolution]:
        """One scheduler tick. Resolves every live ticket whose window has elapsed.

        Returns a resolution for every ticket it ACTED on — executions and named refusals
        alike. A ticket whose window is still open is skipped silently (it is not news);
        an UNDELIVERED one is retried and reported, because a notification seam that has been
        down for an hour is very much news.

        **PER-TICKET ISOLATION IS THE CONTRACT OF THIS METHOD** — ``orchestrator/scheduler.py``
        says so in ``run_autoexec_resolve``'s docstring ("one ticket that cannot be adjudicated
        must be settled and counted without abandoning the tickets behind it"), and until the
        adversarial round the control did not exist. A single ``positions_snapshot`` 503 escaped
        the whole tick: the first ticket kept a minted, budget-consuming approval on the chain
        that permanently refused its filing as an ``approval_replay``, and every ticket behind it
        was skipped with no notification and no counter. Each ticket is now guarded, and a raise
        becomes a named, counted, notified :data:`REASON_RESOLVE_FAULT`.
        """
        with self.st.lock:
            live = [t for t in self._tickets.values() if not t.resolved]
            owed = [t for t in self._tickets.values() if t.resolved and t.pending_notices]
        out: list[AutoResolution] = []
        for ticket in live:
            try:
                resolution = self._resolve_one(
                    ticket, now=now, injection_context=injection_context,
                    injection_predicate=injection_predicate,
                )
            except Exception as exc:         # noqa: BLE001 - one bad ticket never costs the rest
                resolution = self._fault(ticket, exc)
            if resolution is not None:
                out.append(resolution)
        for ticket in owed:
            try:
                self._retry_notices(ticket)
            except Exception:                # noqa: BLE001 - a retry never wedges the tick
                log.exception("autoexec: retrying notices for %s failed", ticket.proposal_id)
        with self.st.lock:
            self._prune_settled()
        return out

    def _fault(self, ticket: AutoTicket, exc: BaseException) -> AutoResolution:
        """Settle ONE ticket that raised, without abandoning the tick.

        Non-terminal on purpose: an unclassifiable fault is most likely environmental (a vendor
        blip, a momentarily locked DB), and a terminal refusal here would strand the disclosure
        forever via ``_consuming``. The ticket stays live, is re-tried next tick, and is bounded
        by the ticket expiry. NEVER raises — it is the last line of defence for the tick."""
        log.exception("autoexec: ticket %s raised during resolve: %r", ticket.proposal_id, exc)
        outbox: list = []
        try:
            with self.st.lock:
                resolution = self._refuse(
                    ticket, REASON_RESOLVE_FAULT,
                    f"{type(exc).__name__}: {exc}", outbox=outbox, terminal=False,
                )
            self._flush(outbox)
            return resolution
        except Exception:                    # noqa: BLE001 - refusing to refuse is still a count
            log.exception("autoexec: could not even record the fault for %s",
                          ticket.proposal_id)
            self._count(REASON_RESOLVE_FAULT)
            return AutoResolution(False, REASON_RESOLVE_FAULT, ticket.proposal_id,
                                  order_id=ticket.order_id, detail=f"{type(exc).__name__}")

    def _count(self, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def _refuse(self, ticket: AutoTicket, reason: str, detail: str | None = None, *,
                outbox: list, terminal: bool | None = None) -> AutoResolution:
        """Record a named refusal, mark the ticket terminal unless the condition is TRANSIENT,
        and queue the notification on ``outbox``.

        A refusal the operator never hears about is a silent stop, which is the exact failure
        this package exists to avoid — so the notification is not optional. But it is also not
        delivered here: the caller holds ``st.lock`` at every call site, and delivery is a
        network POST. See :meth:`_flush`.

        ``terminal`` defaults to "is this reason NOT in :data:`TRANSIENT_REASONS`". Passing it
        explicitly is for the two cases that are decided by context rather than by the reason.

        A TRANSIENT refusal notifies only when the reason CHANGED. The ticket is re-evaluated
        every 60s; a HALT held for an hour would otherwise send sixty identical messages to the
        one surface the operator has left, which is how a channel becomes noise and a real
        refusal goes unread."""
        if terminal is None:
            terminal = reason not in TRANSIENT_REASONS
        if terminal:
            ticket.resolved = True
        self._count(reason)
        resolution = AutoResolution(False, reason, ticket.proposal_id,
                                    order_id=ticket.order_id, detail=detail)
        if terminal:
            self._append_ticket_event(ticket, "settled", executed=False, reason=reason,
                                      detail=None if detail is None else str(detail)[:500])
        if terminal or ticket.last_refusal_reason != reason:
            payload = self._notify_payload(ticket, refused_by=reason, detail=detail,
                                           terminal=terminal)
            outbox.append((ticket, NOTIFY_REFUSED, payload))
        ticket.last_refusal_reason = reason
        return resolution

    def _resolve_one(self, ticket: AutoTicket, *, now, injection_context,
                     injection_predicate) -> AutoResolution | None:
        """One ticket, one tick. The locked half is :meth:`_resolve_locked`; every notification
        it produced is delivered here, after the lock has been released."""
        # Delivery is retried OUTSIDE the lock (it may touch a network surface) before the
        # atomic gate block below re-reads everything under st.lock.
        if not ticket.delivered:
            self._try_confirm_delivery(ticket)

        outbox: list = []
        with self.st.lock:
            resolution = self._resolve_locked(
                ticket, now=now, injection_context=injection_context,
                injection_predicate=injection_predicate, outbox=outbox,
            )
        self._flush(outbox)
        if ticket.pending_notices:
            self._retry_notices(ticket)
        return resolution

    def _resolve_locked(self, ticket: AutoTicket, *, now, injection_context,
                        injection_predicate, outbox: list) -> AutoResolution | None:
        """The atomic gate block. CALLER HOLDS ``st.lock``; this method never does I/O at the
        operator surface — every message it produces goes on ``outbox``, and :meth:`_flush`
        pushes them once the lock is gone.

        THE ORDER OF THE GATES IS LOAD-BEARING and is documented at each step. The one addition
        the adversarial round made is at the END: every fallible collaborator is resolved BEFORE
        the approval is minted, so a vendor fault can no longer spend budget on a trade that
        never happens.
        """
        if ticket.resolved:
            self._count(REASON_ALREADY_RESOLVED)
            return AutoResolution(ticket.executed, REASON_ALREADY_RESOLVED,
                                  ticket.proposal_id, order_id=ticket.order_id)

        mono, backward = self._read_monotonic()
        if backward:
            self._breakers().check_order_path(
                context=_TrueCtx(), predicate=lambda _c: True,
                reason="monotonic clock regressed during the autoexec cancel window (SEC-23)",
            )
            return self._refuse(ticket, REASON_CLOCK_BACKWARDS,
                                "the monotonic clock went backwards mid-window", outbox=outbox)

        # THE UPPER BOUND ON A TICKET'S LIFE. Checked before delivery, because a ticket whose
        # notification never landed is exactly the one that would otherwise never age out: it
        # never fires, so it never settles, so it retries forever. See
        # ``DEFAULT_AUTOEXEC_TICKET_MAX_AGE_SECONDS``.
        if ticket.expired(mono):
            age = mono - (ticket.scheduled_at_monotonic or mono)
            return self._refuse(
                ticket, REASON_TICKET_EXPIRED,
                f"queued {age:.0f}s ago and still unresolved (limit {ticket.max_age_seconds:.0f}s); "
                "the quote it was sized against is stale, so the trade is abandoned rather than "
                "executed late",
                outbox=outbox, terminal=True,
            )

        # SEC-23 — silence is not consent. An undelivered ticket NEVER fires; it stays live
        # and keeps retrying, so a surface that recovers still trades (late, notified).
        if not ticket.delivered or ticket.delivered_at_monotonic is None:
            self._count(REASON_UNDELIVERED)
            return AutoResolution(False, REASON_UNDELIVERED, ticket.proposal_id,
                                  order_id=ticket.order_id,
                                  detail="the queued notification was never confirmed "
                                         "delivered; the trade will not proceed")

        # STRICTLY AFTER the advertised deadline, never AT it. The queued message prints
        # ``window_seconds`` as the operator's allowance, so firing at exactly T+window means an
        # operator acting on the stated deadline is told the trade already went through. The
        # error now runs the safe way: the real window is the advertised one plus, at most, one
        # 60s resolve tick, and extra cancel time can only ever PREVENT a trade.
        if mono <= ticket.delivered_at_monotonic + ticket.window_seconds:
            return None                      # window still open — not news, not a refusal

        # A cancel that beat us to the lock.
        if ticket.cancelled:
            ticket.resolved = True
            self._count(REASON_CANCELLED)
            return AutoResolution(False, REASON_CANCELLED, ticket.proposal_id,
                                  order_id=ticket.order_id)

        # ---- the atomic environmental re-check at the firing edge -----------------------
        if halt_is_set(default_halt_path()):
            return self._refuse(ticket, REASON_HALT,
                                "the HALT sentinel is set (SEC-04)", outbox=outbox)

        # The transport check is FIRST among the structural ones, and it is here rather
        # than only inside ``auto_paper_place`` so that a misconfigured lane produces a
        # named refusal BEFORE any approval or capability token is minted. Discovering the
        # wrong transport after minting would leave an unconsumed approval on the chain for
        # an order that can never be placed.
        scope = getattr(self.gateway, "_execution_scope", None)
        if scope != PAPER_SCOPE:
            return self._refuse(
                ticket, REASON_NOT_PAPER_TRANSPORT,
                f"the composed gateway's execution_scope is {scope!r}, not 'paper'; a lane "
                "that adjudicates without a human may exist only in front of the paper "
                "transport",
                outbox=outbox,
            )

        # The chain is checked BEFORE the mode, deliberately. ``ModeLadder.current()``
        # fails closed to ``read_only`` on an unverifiable chain, so checking the mode
        # first would refuse a tampered chain under the reason ``mode_not_paper`` — a
        # true refusal with a misleading name, which is how a tamper gets investigated as
        # a config problem. Name the real cause.
        try:
            chain_ok = self.st.audit.verify_chain().ok
        except Exception as exc:              # noqa: BLE001 - any probe fault fails closed
            chain_ok = False
            log.exception("autoexec: verify_chain raised (%s)", type(exc).__name__)
        if not chain_ok:
            # No signed chain means no trustworthy budget, no trustworthy mode, and no way
            # to write an approval anybody could later verify. Refuse before touching it.
            return self._refuse(ticket, REASON_CHAIN_UNVERIFIED,
                                "the audit chain does not verify; nothing may be executed "
                                "or spent against an untrusted ledger", outbox=outbox)

        mode = self._ladder().current()
        if mode is not Mode.paper:
            # PAPER ONLY, BY CONSTRUCTION. A ticket queued in paper and resolved after any
            # move off it is VOID — it is not re-queued at the new rung, because the rung
            # is what decides whether a robot may adjudicate at all.
            return self._refuse(
                ticket, REASON_MODE_NOT_PAPER,
                f"the ladder moved to {getattr(mode, 'value', mode)!r}; the zero-touch lane "
                "is paper-only and this ticket is void",
                outbox=outbox,
            )

        if self._breakers().is_watch_only():
            return self._refuse(ticket, REASON_WATCH_ONLY,
                                "a vendor usage window is exhausted; the order path is "
                                "disabled (SEC-01/L8)", outbox=outbox)

        tripped = _tripped_breakers(self.st)
        if tripped:
            # In ``paper`` a breaker trip does NOT move the ladder (``drop_to_review_live``
            # is a no-op below review_live), so the mode check above cannot see it. Reading
            # the signed trip/clear ledger is what makes a tripped breaker actually stop
            # this lane instead of quietly doing nothing.
            return self._refuse(ticket, REASON_BREAKER_TRIPPED,
                                f"breaker(s) tripped and not cleared: {sorted(tripped)}",
                                outbox=outbox)

        if self._breakers().check_order_path(
                context=injection_context if injection_context is not None else ticket.context,
                predicate=injection_predicate,
                reason="injection/anomaly detected at autoexec fire (SEC-01)") is not None:
            return self._refuse(ticket, REASON_BREAKER,
                                "the SEC-01 injection/anomaly breaker tripped", outbox=outbox)

        # ---- the budget rails (what replaces the human as the bound on damage) ----------
        state = budget_state(self.st, day=self._today(now))
        allowed, rail, detail = budget_check(
            state, self.budget, symbol=ticket.symbol, side=ticket.side,
            notional=ticket.notional,
        )
        if not allowed:
            return self._refuse(ticket, rail, detail, outbox=outbox)

        # ---- the order row, re-loaded from the DB --------------------------------------
        order = self._load_order(ticket.order_id)
        if order is None:
            return self._refuse(ticket, REASON_ORDER_MISSING,
                                f"order {ticket.order_id!r} is no longer in the database",
                                outbox=outbox)

        # ---- EVERY FALLIBLE COLLABORATOR, RESOLVED BEFORE ANYTHING IS MINTED ------------
        # THIS ORDERING IS A SAFETY CONTROL, NOT A STYLE CHOICE. These four used to be
        # evaluated as call ARGUMENTS to ``auto_paper_place``, i.e. after the approval was on
        # the signed chain and the capability token was minted. ``positions_fn`` is
        # ``PaperExecutionService.positions_snapshot``, whose own docstring says it RAISES on a
        # broker fault. One 503 therefore: killed the tick, abandoned every ticket behind this
        # one un-notified, left an unconsumed approval on the append-only chain that made the
        # NEXT tick refuse this filing forever as an ``approval_replay``, and — because
        # ``copytrade/budget.py`` counts spend at adjudication — burned a slot of
        # ``max_trades_per_day`` and its dollars for a trade that never reached a broker. Three
        # such faults exhausted the shipped daily budget with zero trades and zero messages,
        # which is indistinguishable from "Congress filed nothing".
        #
        # Resolved here, a fault is a NAMED, COUNTED, NOTIFIED, TRANSIENT refusal that has spent
        # nothing and can simply be retried on the next tick.
        try:
            positions = self._positions_fn()
            risk_recheck = self._risk_recheck_factory(order)
            ladder = self._ladder()
            breaker_engine = self._breakers()
            producing_runtime = self._runtime()
        except Exception as exc:              # noqa: BLE001 - a data fault must cost nothing
            log.exception("autoexec: a pre-submit collaborator failed for %s",
                          ticket.proposal_id)
            return self._refuse(
                ticket, REASON_POSITIONS_UNAVAILABLE,
                f"{type(exc).__name__}: {exc} — the positions snapshot / risk re-check seam "
                "could not be read, so nothing was adjudicated and no budget was spent",
                outbox=outbox,
            )

        # ---- SINGLE USE: one ticket, at most one approval, ever ------------------------
        if self._existing_autoexec_approval(ticket.proposal_id) is not None:
            # Belt to the in-memory ticket's braces: survives a process restart, because
            # it reads the persisted row rather than this object's memory.
            return self._refuse(ticket, REASON_APPROVAL_REPLAY,
                                "this proposal already carries an auto-approval; refusing "
                                "to mint a second one", outbox=outbox)

        try:
            approval = self._mint_approval(ticket, order)
        except IntegrityError:
            # The UNIQUE interaction_id lost a cross-process race: somebody else already
            # adjudicated this proposal. That is single-use working, not an error.
            return self._refuse(ticket, REASON_APPROVAL_REPLAY,
                                "an auto-approval for this proposal already exists "
                                "(unique-constraint race)", outbox=outbox)
        except Exception as exc:              # noqa: BLE001 - no approval => no order
            log.exception("autoexec: minting the auto-approval failed")
            return self._refuse(ticket, REASON_APPROVAL_FAILED,
                                f"{type(exc).__name__}: {exc}", outbox=outbox)

        ticket.approval_id = approval.id

        # ---- the ONE order path (Invariant 1) ------------------------------------------
        # FROM HERE THE APPROVAL IS ON THE CHAIN, so every exit below is TERMINAL: retrying
        # would either replay a minted authority or mint a second one. The whole block is
        # guarded so that a raise cannot leave the ticket live-but-adjudicated, which is the
        # state that produced the phantom ``approval_replay`` refusals.
        try:
            self.gateway.mint_submit_token(
                nonce=token_nonce_for_approval(approval.id),
                proposal_id=ticket.proposal_id,
                risk_decision_id=ticket.risk_decision_id,
                order_tuple_hash=ticket.order_tuple_hash,
            )
            risk_decision = _RiskDecisionRef(ticket.risk_decision_id)
            # THE ONE ORDER PATH. Routed through ``brokers/auto_paper_submit.py`` rather than
            # calling the gateway from here: Invariant 1's static call-site gate requires every
            # order-placement site to live under ``brokers/``, so that the complete list of ways
            # an order can be placed is greppable in one package. A lane that adjudicates
            # without a human is exactly the caller that must not be able to hide from that
            # list. The seam also fails closed on a non-paper-scoped gateway.
            outcome = auto_paper_place(
                self.gateway,
                order,
                approval=approval,
                risk_decision=risk_decision,
                ladder=ladder,
                breaker_engine=breaker_engine,
                risk_recheck=risk_recheck,
                positions=positions,
                token_nonce=token_nonce_for_approval(approval.id),
                producing_runtime=producing_runtime,
                clock=self._clock,
            )
        except Exception as exc:              # noqa: BLE001 - the approval already exists
            log.exception("autoexec: the order path raised for %s", ticket.proposal_id)
            return self._refuse(
                ticket, REASON_GATEWAY_REFUSED,
                f"{type(exc).__name__}: {exc} (the auto-approval was already written, so this "
                "ticket is closed rather than retried)",
                outbox=outbox, terminal=True,
            )

        ticket.resolved = True
        if not outcome.ok:
            # SEC-07's pending-exposure cap gets its OWN name. It is the one gateway refusal
            # that is not about this order at all — it means the ledger of approved-but-unfilled
            # orders has never drained (a non-crossing paper LIMIT order acks as ``accepted``,
            # which no state-machine edge consumes until RW-15's order-status poll ships). An
            # operator reading a generic ``gateway_refused`` would go looking at the filing;
            # they need to be told to look at the exposure ledger.
            reason = (REASON_PENDING_EXPOSURE if outcome.stage == "pending_exposure"
                      else REASON_GATEWAY_REFUSED)
            resolution = self._refuse(
                ticket, reason, f"{outcome.stage}: {outcome.reason}",
                outbox=outbox, terminal=True,
            )
            return AutoResolution(
                False, reason, ticket.proposal_id, order_id=ticket.order_id,
                approval_id=approval.id, detail=resolution.detail,
            )

        ticket.executed = True
        self._count(REASON_EXECUTED)
        self._append_ticket_event(
            ticket, "settled", executed=True, reason=REASON_EXECUTED,
            broker_order_id=outcome.broker_order_id, broker_status=outcome.broker_status,
            approval_id=approval.id,
        )
        resolution = AutoResolution(
            True, REASON_EXECUTED, ticket.proposal_id, order_id=ticket.order_id,
            approval_id=approval.id, broker_order_id=outcome.broker_order_id,
            broker_status=outcome.broker_status,
        )
        # The SUBMITTED notice goes on the outbox like every other message: the trade already
        # happened, and a slow surface must not hold the orchestrator lock. Unlike before, its
        # delivery is now TRACKED — an unconfirmed post is retried on later ticks and counted
        # under ``notification_undelivered``, because "the trade executed and nobody was told"
        # is the single worst outcome this lane can produce.
        outbox.append((ticket, NOTIFY_SUBMITTED, self._notify_payload(
            ticket, approval_id=resolution.approval_id,
            broker_order_id=resolution.broker_order_id,
            broker_status=resolution.broker_status)))
        return resolution

    # ---- restart recovery ---------------------------------------------------------------
    def recover_orphans(self) -> list[AutoResolution]:
        """Settle every ticket a PREVIOUS process promised the operator and never resolved.

        THE HOLE THIS CLOSES. ``self._tickets`` is process memory. A restart between the queued
        notification and the window edge used to vaporise the ticket completely: the operator
        held a message reading *"this WILL execute unless you cancel it"* and — by the design's
        own rule that no action is needed for a trade to proceed — had no reason to act. Nothing
        executed, nothing was refused, no outcome was ever delivered, ``/cancel`` answered
        ``unknown_ticket``, and the ``Order`` row stayed ``proposed`` forever because
        ``workflows/copytrade_orders._consuming`` treats an existing order row as "already
        copied" and never re-proposes the disclosure. With a 300s window and a one-minute tick
        that is a ~5-minute exposure on EVERY deploy, on every ticket in the cycle.

        THE FIX IS THE SIGNED CHAIN, NOT A NEW TABLE. :data:`EVENT_AUTOEXEC_TICKET` records the
        promise (``state='queued'``) and the outcome (``state='settled'``). A verified queued
        entry with no verified settled entry IS an abandoned promise, and it is readable by any
        process. Each one is refused as :data:`REASON_ABANDONED_RESTART` — notified, counted, and
        written back to the chain so the recovery is itself idempotent.

        DELIBERATELY NOT RESUMED. The window could be resumed instead, but the facts the queued
        message was rendered from (the filer, the disclosure lag, the sizing sentence) live on
        the in-memory ``AutoTicket.context`` and are gone; and the limit price was struck against
        a quote that is now old. Refusing is the fail-closed answer and it keeps "the trade did
        not happen" a positive statement. The disclosure stays consumed — that is a real cost,
        and it is named in ``docs/runbooks/congress-autoexec.md``.

        NEVER RAISES: it runs at composition time and a lane that cannot recover must still be a
        lane that runs.
        """
        try:
            orphans = self._orphan_tickets()
        except Exception:                    # noqa: BLE001 - recovery never blocks the boot
            log.exception("autoexec: could not scan for orphaned tickets after a restart")
            return []
        out: list[AutoResolution] = []
        for ticket in orphans:
            outbox: list = []
            try:
                with self.st.lock:
                    self._tickets.setdefault(ticket.proposal_id, ticket)
                    resolution = self._refuse(
                        ticket, REASON_ABANDONED_RESTART,
                        "this trade was announced to you and the process restarted before its "
                        "cancel window closed; NOTHING was sent to the broker and the ticket is "
                        "abandoned rather than executed against a stale price",
                        outbox=outbox, terminal=True,
                    )
                self._flush(outbox)
                out.append(resolution)
            except Exception:                # noqa: BLE001 - one orphan never costs the rest
                log.exception("autoexec: could not settle orphaned ticket %s",
                              ticket.proposal_id)
        if out:
            log.warning(
                "RW-17: settled %d auto-trade ticket(s) that a previous process announced and "
                "never resolved (restart inside the cancel window). None of them traded.",
                len(out),
            )
        return out

    def _orphan_tickets(self) -> list[AutoTicket]:
        """Announced-but-unsettled tickets, reconstructed from the SIGNED chain.

        Entries that fail ``verify_entry`` are skipped in BOTH directions, matching
        ``_tripped_breakers``: a forged ``settled`` must not be able to hide an abandoned
        promise, and a forged ``queued`` must not be able to manufacture a notification."""
        with self.st.session_factory() as s:
            rows = s.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type == EVENT_AUTOEXEC_TICKET)
                .order_by(AuditEntry.seq)
            ).scalars().all()
        queued: dict[str, dict] = {}
        for row in rows:
            try:
                if not self.st.audit.verify_entry(row):
                    continue
            except Exception:                # noqa: BLE001 - an unverifiable row is ignored
                continue
            payload = row.payload or {}
            pid = str(payload.get("proposal_id") or "")
            if not pid:
                continue
            if payload.get("state") == "queued":
                queued[pid] = payload
            elif payload.get("state") == "settled":
                queued.pop(pid, None)
        out: list[AutoTicket] = []
        for pid, payload in queued.items():
            if pid in self._tickets:
                continue                     # this process already owns it
            out.append(AutoTicket(
                proposal_id=pid,
                order_id=str(payload.get("order_id") or ""),
                risk_decision_id=str(payload.get("risk_decision_id") or ""),
                order_tuple_hash="",
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or ""),
                notional=_dec(payload.get("notional")),
                window_seconds=float(payload.get("window_seconds") or self.window_seconds),
                delivered=True,
                max_age_seconds=self.ticket_max_age_seconds,
            ))
        return out

    # ---- persistence helpers -----------------------------------------------------------
    def _load_order(self, order_id: str) -> Order | None:
        with self.st.session_factory() as s:
            row = s.get(Order, order_id)
            if row is not None:
                s.expunge(row)
            return row

    def _autoexec_interaction_id(self, proposal_id: str) -> str:
        """One auto-adjudication per proposal, enforced by the DB.

        ``ApprovalRecord.interaction_id`` carries a UNIQUE constraint, which
        ``bridge/approval.record_decision`` uses as its cross-process idempotency backstop.
        Deriving it deterministically from the proposal id borrows that same guarantee: a
        second auto-approval for one proposal cannot be inserted, in this process or any
        other, however the in-memory ticket state is manipulated."""
        return f"autoexec:{proposal_id}"

    def _existing_autoexec_approval(self, proposal_id: str) -> ApprovalRecord | None:
        with self.st.session_factory() as s:
            row = s.execute(
                select(ApprovalRecord).where(
                    ApprovalRecord.interaction_id == self._autoexec_interaction_id(proposal_id)
                )
            ).scalars().first()
            if row is not None:
                s.expunge(row)
            return row

    def _mint_approval(self, ticket: AutoTicket, order: Order) -> ApprovalRecord:
        """Write the auto-adjudication the way a human tap is written.

        ``record_state_change`` persists the ``ApprovalRecord`` row AND its covering
        ``EVENT_ADJUDICATION`` entry in ONE transaction (Invariant 6), so the gateway's gate
        (e) finds both: the row it re-loads by id, and the HMAC-signed payload whose
        ``refs['row_id']`` is that id and whose fields bind decision + proposal +
        risk-decision + order tuple. This is the whole mechanism by which a robot may approve
        without any gate being weakened — the gate is satisfied, not bypassed.

        The payload additionally carries ``lane``/``symbol``/``side``/``notional``/``order_id``,
        which is what makes ``copytrade/budget.py`` able to re-derive today's spend from the
        signed chain instead of a counter. The gateway ignores the extra keys.
        """
        now = self._clock.now()
        row = ApprovalRecord(
            id=new_id("appr"),
            proposal_id=ticket.proposal_id,
            risk_decision_id=ticket.risk_decision_id,
            actor_user_id=AUTOEXEC_ACTOR,
            user_decision="approve",
            surface=AUTOEXEC_SURFACE,
            interaction_id=self._autoexec_interaction_id(ticket.proposal_id),
            message_id=None,
            device="scheduler",
            order_tuple_hash=ticket.order_tuple_hash,
            single_use_consumed=False,
            notes=None,
            decided_at=now,
            created_at=now,
        )
        payload = {
            # ---- the four fields gate (e) binds on (``_adjudication_binds``) ----
            "decision": "approve",
            "proposal_id": ticket.proposal_id,
            "risk_decision_id": ticket.risk_decision_id,
            "order_tuple_hash": ticket.order_tuple_hash,
            # ---- provenance: this was a ROBOT, on the paper rung ----
            "actor": AUTOEXEC_ACTOR,
            "auto": True,
            "lane": AUTOEXEC_LANE,
            "surface": AUTOEXEC_SURFACE,
            "mode": Mode.paper.value,
            # ---- what the budget rails re-derive their spend from ----
            "order_id": order.id,
            "symbol": ticket.symbol,
            "side": ticket.side,
            "notional": str(ticket.notional),
            "window_seconds": ticket.window_seconds,
        }
        persisted, _entry = self.st.audit.record_state_change(
            row, EVENT_ADJUDICATION, actor=AUTOEXEC_ACTOR, payload=payload
        )
        return persisted


@dataclass(frozen=True)
class _RiskDecisionRef:
    """The gateway reads exactly one field off the ``risk_decision`` argument — ``.id`` — and
    then re-loads the real row from the DB itself (it deliberately trusts no caller-supplied
    object). Passing this two-line reference rather than a detached ORM row makes that
    contract explicit and keeps a stale in-memory copy from ever being mistaken for authority.
    """

    id: str


def _tripped_breakers(st) -> set[str]:
    """Breakers currently TRIPPED, derived from the HMAC-verified trip/clear ledger.

    Latest verified event per breaker name wins (seq-ascending). Rows that fail
    ``verify_entry`` are skipped in BOTH directions, which means a forged CLEAR cannot un-trip
    a breaker; and if the chain read fails outright, every breaker reads as tripped, because
    "I could not tell" and "everything is fine" must not be the same answer.
    """
    try:
        with st.session_factory() as s:
            rows = s.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type.in_([EVENT_BREAKER_TRIP, EVENT_BREAKER_CLEAR]))
                .order_by(AuditEntry.seq)
            ).scalars().all()
    except Exception:                            # noqa: BLE001 - unreadable => assume tripped
        return {"unreadable_breaker_ledger"}
    latest: dict[str, bool] = {}
    for row in rows:
        try:
            if not st.audit.verify_entry(row):
                continue
        except Exception:                        # noqa: BLE001 - unverifiable entry is ignored
            continue
        name = (row.payload or {}).get("breaker")
        if not name:
            continue
        latest[str(name)] = row.event_type == EVENT_BREAKER_TRIP
    return {name for name, tripped in latest.items() if tripped}
