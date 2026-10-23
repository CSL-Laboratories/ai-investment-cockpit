"""WP7 — the ``OrderGateway``: the ONE order path to a broker (Inv 1; SEC-01/03/04/07/08).

Invariant 1: an order reaches a broker ONLY through risk-pass AND mode-check AND
approval/veto — nothing else. This module is the choke point that enforces it.

SEC-03 — the broker connection and the single-use ``TokenRegistry`` are PRIVATE instance
attributes set in ``__init__``; there is NO reachable module-global broker client, and the
broker is never dispatched by string name. A caller holding the gateway object still cannot
submit: the ORDER AUTHORITY is the chain-anchored approval re-verified at gate (e) — an
HMAC-signed ``EVENT_ADJUDICATION`` whose payload binds THIS exact resolved order to a
persisted, non-consumed ``ApprovalRecord`` (``_adjudication_binds``). The gateway trusts the
DB rows + the signed chain, NEVER the caller-supplied ``approval``/``risk_decision`` objects
(a forged in-memory object cannot move the binding). The single-use capability token is
replay protection layered on top, not the authority — self-minting a token proves nothing
because gate (e) independently refuses any order lacking a signed approval that binds it.

The single LOCKED submit transaction re-runs EVERY gate immediately before the broker call
(TOCTOU: buying power, exposure, mode, HALT and the risk verdict can all change between
approval and submit), failing CLOSED on the first failure and recording an ``AuditEntry`` for
the refusal. A hard-risk failure or an approval made against a non-approved risk decision
records a ``BYPASS_EVENTS`` event so the performance gate (SEC-20) sees the attempt.

Only if ALL gates pass does it perform the two-phase submit (SEC-13): an intent AuditEntry
BEFORE the private broker call, the broker call, then a result AuditEntry AFTER — followed by
consuming the single-use approval and advancing the order to ``submitted`` (both audited).
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from brokers.capability import CapabilityError, SubmitToken, TokenRegistry
from brokers.order_state_machine import (
    TERMINAL_STATES,
    IllegalOrderTransition,
    legal_order_transition,
    transition,
)

from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from orchestrator.breakers import BreakerThresholds, StaleAnalysisError
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.market_calendar import (
    CalendarCoverageError,
    is_market_open,
    require_calendar_coverage,
)
from orchestrator.mode_ladder import ExecutionRuntimeError
from orchestrator.state import (
    BYPASS_EVENTS,
    EVENT_ADJUDICATION,
    EVENT_ORDER_TRANSITION,
    OrchestratorState,
)
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.risk_decision import RiskDecision

log = logging.getLogger(__name__)

# BUG-052: the broker statuses that are DEFINITIVE about an order's fate at submit time,
# mapped onto FSM states. Anything absent here ("new", "accepted", "pending_new", ...) is
# indeterminate and the order stays 'submitted' — never invent a fill. Alpaca spells
# cancellation "canceled"; both spellings map to the FSM's "cancelled".
_BROKER_STATUS_TO_STATE: dict[str, str] = {
    "filled": "filled",
    "partially_filled": "partially_filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "expired",
    "rejected": "rejected",
}


def _sanitize_broker_response(result) -> dict:
    """Adversarial-review hardening (BUG-052 round 2, F1): the broker RESPONSE is network
    data and must satisfy a shape contract BEFORE anything dereferences it — a None/str
    return, or a non-JSON-native field value, used to crash AFTER the try block, inside
    the audit-result construction, re-opening the exact dangling-intent hole BUG-062
    closed. Raises on a non-dict (caught by the caller's except into the covered abort
    path); normalizes every field this module consumes to JSON-native-or-None. A garbage
    ``filled_qty`` becomes None — it must never FABRICATE a fill."""
    if not isinstance(result, dict):
        raise TypeError(f"broker returned {type(result).__name__}, expected a dict response")
    boid = result.get("broker_order_id")
    fq = _dec(result.get("filled_qty")) if result.get("filled_qty") is not None else None
    afp = _dec(result.get("avg_fill_price")) if result.get("avg_fill_price") is not None else None
    return {
        "broker_order_id": None if boid is None else str(boid)[:128],
        "status": None if result.get("status") is None else str(result.get("status"))[:64],
        "filled_qty": fq if (fq is None or (fq.is_finite() and fq >= 0)) else None,
        "avg_fill_price": (
            afp if (afp is None or (afp.is_finite() and afp > 0)) else None
        ),
    }


def _ack_consistent(ack_state: str, result: dict, order) -> bool:
    """F3: a TERMINAL, exposure-draining transition needs fill data CONSISTENT with the
    claimed status — the status string alone must not drain the SEC-07 cap. 'filled'
    requires filled_qty == order.qty (> 0); 'partially_filled' requires 0 < filled_qty <
    qty. Cancel/expire/reject at submit time carry no fill data and stand as the response
    of record. Inconsistent fill data leaves the order 'submitted' (open, over-counted —
    the fail-safe direction) and is audited as ignored."""
    if ack_state in ("cancelled", "expired", "rejected"):
        return True
    fq = result.get("filled_qty")
    qty = _dec(order.qty)
    if fq is None or not fq.is_finite() or not qty.is_finite():
        return False
    if ack_state == "filled":
        return fq > 0 and fq == qty
    if ack_state == "partially_filled":
        return 0 < fq < qty
    return False


# ---- refusal / bypass audit event names -----------------------------------------------
EVENT_ORDER_REFUSED = "order_refused"                 # generic gate refusal (not a bypass)
# BYPASS_EVENTS the performance gate watches (SEC-20); assert we spell them exactly.
BYPASS_HARD_CHECK_FAILED = "hard_check_failed"
BYPASS_APPROVAL_AGAINST_FAILED_RISK = "approval_against_failed_risk"
BYPASS_RESUBMIT_AFTER_DENIED = "resubmit_after_denied"
assert {
    BYPASS_HARD_CHECK_FAILED,
    BYPASS_APPROVAL_AGAINST_FAILED_RISK,
    BYPASS_RESUBMIT_AFTER_DENIED,
} <= BYPASS_EVENTS

# Broker-specific authority sets. A paper transport must never be reachable from a live mode,
# and a live transport must never become reachable merely because the operator selected paper.
_ORDER_AUTHORITY_BY_SCOPE = {
    "paper": frozenset({"paper"}),
    "live": frozenset({"review_live", "auto_veto", "auto_full"}),
}
_AUTO_MODES = frozenset({"auto_veto", "auto_full"})
# Final admission may block. A wait longer than the fence's remaining lease (or this
# ceiling, when the fence reports no remainder) is re-fenced and admission re-read.
# The ceiling also bounds a stuck lock so it fails closed.
_ADMISSION_WAIT_BUDGET_S = 5.0
_MAX_FENCE_ROUNDS = 3
# Set of order ids with a submit inside this state. Shared by every gateway that
# holds the state, so a second gateway cannot submit the same order.
_INFLIGHT_ATTR = "_gateway_inflight_orders"

# Defaults for the SEC-07 pending-exposure cap and approval time-to-live (Settings/limits
# override via getattr; the gateway ctor overrides both for tests).
DEFAULT_PENDING_EXPOSURE_CAP = Decimal("10000")
DEFAULT_APPROVAL_TTL_SECONDS = 900                    # 15 minutes


def _dec(x) -> Decimal:
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _naive_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)


def _lease_remaining_s(fence_result: object) -> float | None:
    """Seconds of claim lease a fence reported, or None when it reported none.

    A real number, or an object with a numeric ``remaining_s``, is the remainder at
    the moment the fence returned. Anything else leaves the admission ceiling in charge.
    """
    if isinstance(fence_result, bool) or fence_result is None:
        return None
    if isinstance(fence_result, (int, float)):
        remaining = fence_result
    else:
        remaining = getattr(fence_result, "remaining_s", None)
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
            return None
    value = float(remaining)
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return 0.0
    return value


@dataclass
class GateOutcome:
    """The result of a review / submit. ``ok=False`` means REFUSED; ``stage`` names the gate
    that refused (or ``'submitted'`` on success). Nothing reached the broker unless
    ``ok and broker_order_id is not None``."""

    ok: bool
    stage: str
    reason: str | None = None
    order_tuple_hash: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    checks_passed: list[str] = field(default_factory=list)


def _halt_active(path) -> bool:
    """SEC-04 fail-closed HALT probe: ANY probe failure counts as SET. ``halt_is_set`` already
    fails closed internally (R3-S3: Windows winerror != 2, NUL-byte ValueError, unreadable OSError
    all read as SET); this belt-and-suspenders catch broadened from ``except OSError`` to
    ``except Exception`` so that ANY unexpected probe fault (e.g. a re-raised ValueError, a
    monkeypatched seam) still fails closed rather than propagating and skipping the gate."""
    try:
        return halt_is_set(path)
    except Exception:   # noqa: BLE001 - fail closed: any probe fault => HALT treated as SET
        return True


class OrderGateway:
    def __init__(
        self,
        broker,
        *,
        st=None,
        token_registry: TokenRegistry | None = None,
        pending_exposure_cap: Decimal | None = None,
        approval_ttl_seconds: int | None = None,
        execution_scope: str = "live",
        require_analysis_generation: bool = False,
        claim_fence: Callable[[], object] | None = None,
    ) -> None:
        # SEC-03: broker + registry are PRIVATE instance state — never module globals, never
        # dispatched by string name. ``st`` (the TRUST ROOT — the HMAC-signed audit chain + its DB)
        # is likewise pinned here: gate (e) verifies the signed adjudication against THIS chain, so
        # a caller holding only the gateway cannot substitute their own HMAC key / DB by threading a
        # forged ladder (see ``_resolve_state``). Production MUST construct with ``st=`` the composed
        # state; trusted test construction may omit it and the (genuine) ladder state is used.
        self._broker = broker
        self._st = st
        self._registry = token_registry or TokenRegistry()
        self._cap_override = pending_exposure_cap
        self._ttl_override = approval_ttl_seconds
        if execution_scope not in _ORDER_AUTHORITY_BY_SCOPE:
            raise ValueError(f"unknown broker execution_scope {execution_scope!r}")
        self._execution_scope = execution_scope
        self._allowed_modes = _ORDER_AUTHORITY_BY_SCOPE[execution_scope]
        # Production paper composition enables this.  Low-level gateway fixtures that test
        # unrelated gates may omit agent lineage, but the real proposal path may not.
        self._require_analysis_generation = bool(require_analysis_generation)
        # Profile composition installs the fence here. submit() will not accept a different one.
        if claim_fence is not None and not callable(claim_fence):
            raise TypeError("claim_fence must be callable")
        self._claim_fence = claim_fence
        # Same-thread re-entry (the state lock is an RLock). A fence must not submit again.
        self._submit_active = False
        # Captured once at construction. A later replacement of the class attribute is not
        # the implementation this gateway was trusted to call.
        from brokers.alpaca_paper import AlpacaPaperBroker
        self._admitted_paper_submit = AlpacaPaperBroker.submit_order

    @property
    def execution_scope(self) -> str:
        """Scope pinned at construction for this gateway's own mode check.

        Profile handoff does not treat this attribute as paper authority. A
        caller can pass ``execution_scope="paper"`` to ``OrderGateway``. Paper
        admission is the ``PaperOrderGateway`` type.
        """
        return self._execution_scope

    def _resolve_state(self, ladder):
        """Return the pinned TRUST ROOT for gate (e). The signed audit chain is the order authority;
        its HMAC key + DB must not be caller-supplied (SEC-03). When the gateway was built with a
        state, the caller's ladder MUST carry that exact state — else FAIL CLOSED (a caller cannot
        swap in an attacker-keyed chain). When built without one (trusted test construction), the
        ladder's state is used, since the test supplies the genuine chain."""
        cand = getattr(ladder, "st", None)
        if self._st is not None:
            if cand is not self._st:
                raise CapabilityError(
                    "SEC-03: the ladder's state is not the gateway's pinned trust root; refusing to "
                    "trust a caller-substituted audit chain / HMAC key"
                )
            return self._st
        if cand is None:
            raise CapabilityError("SEC-03: no trust state available for the order gateway")
        return cand

    # ---- capability mint seam (the approval/veto path calls this; the gateway NEVER mints
    # its own token in submit — it only consumes) --------------------------------------
    def mint_submit_token(
        self, *, nonce: str, proposal_id: str, risk_decision_id: str, order_tuple_hash: str
    ) -> SubmitToken:
        """Mint a single-use submit token. IMPORTANT (SEC-03, post-adversarial-review): the
        token is single-use REPLAY PROTECTION, NOT the order authority. The authority is the
        chain-anchored approval re-verified at submit gate (e) (``_adjudication_binds`` — an
        HMAC-signed adjudication that binds this exact order). So a caller holding the gateway
        who self-mints a token for an arbitrary binding still cannot submit: gate (e)
        independently refuses any order without a signed, persisted approval that binds it
        (regression: ``test_gateway_hardening``). The token merely guarantees a given
        authorization is consumed at most once."""
        return self._registry.mint(
            nonce=nonce, proposal_id=proposal_id, risk_decision_id=risk_decision_id,
            order_tuple_hash=order_tuple_hash,
        )

    def mint_live_submit_token(
        self, st, *, approval_id, nonce, proposal_id, risk_decision_id, order_tuple_hash
    ) -> SubmitToken:
        """SEC-03 (d) — the LIVE Robinhood-Agentic capability mint. Unlike ``mint_submit_token``
        (bare single-use replay protection for the in-process paper flow), the LIVE capability is
        bound to the COVERING signed ``EVENT_ADJUDICATION`` at MINT time: the token cannot even be
        CREATED for an order no HMAC-signed adjudication approves (``_adjudication_binds`` re-derives
        the binding from the chain, never a caller-supplied object). Defense-in-depth atop gate (e)
        (which independently refuses at submit): the live capability never exists for an unapproved
        order, so a stolen/replayed live token maps only to a genuinely-approved binding. Refuses
        with ``CapabilityError`` when no covering adjudication binds this exact order."""
        # SEC-03: a pinned gateway verifies against its OWN trust root, never a caller-passed chain.
        st = self._st if self._st is not None else st
        if not self._adjudication_binds(st, approval_id, proposal_id=proposal_id,
                                        risk_decision_id=risk_decision_id,
                                        order_tuple_hash=order_tuple_hash):
            raise CapabilityError(
                "SEC-03: refusing to mint a live-MCP capability token with no covering signed "
                "EVENT_ADJUDICATION binding this exact order (proposal/risk_decision/order_tuple)"
            )
        return self._registry.mint(
            nonce=nonce, proposal_id=proposal_id, risk_decision_id=risk_decision_id,
            order_tuple_hash=order_tuple_hash,
        )

    def live_token_count(self) -> int:
        return self._registry.live_count()

    # ---- helpers -----------------------------------------------------------------------
    @staticmethod
    def _resolve_tuple(order: Order) -> dict:
        """The canonical resolved-order tuple from the order row (fail-closed validation)."""
        return canonical_order_tuple(
            symbol=order.symbol, side=order.side, qty=order.qty,
            order_type=order.order_type, limit_price=order.limit_price, tif=order.tif,
        )

    @staticmethod
    def _order_notional(order: Order) -> Decimal:
        """Best-effort notional for the exposure cap: explicit notional, else qty*limit_price,
        else 0 (an un-priced market order contributes nothing to the sum but is still gated by
        the risk engine's own pending-exposure check)."""
        if order.notional is not None:
            n = _dec(order.notional)
            if n.is_finite():
                return n
        if order.limit_price is not None:
            n = _dec(order.qty) * _dec(order.limit_price)
            if n.is_finite():
                return n
        return Decimal("0")

    def _cap(self, st: OrchestratorState) -> Decimal:
        if self._cap_override is not None:
            return _dec(self._cap_override)
        return _dec(getattr(st.settings, "pending_exposure_cap", DEFAULT_PENDING_EXPOSURE_CAP))

    def _ttl_seconds(self, st: OrchestratorState) -> int | None:
        if self._ttl_override is not None:
            return self._ttl_override
        return getattr(st.settings, "approval_ttl_seconds", DEFAULT_APPROVAL_TTL_SECONDS)

    def _record_refusal(self, st, order, *, event, stage, reason, actor, extra=None):
        payload = {
            "stage": stage,
            "reason": reason,
            "order_id": getattr(order, "id", None),
            "proposal_id": getattr(order, "proposal_id", None),
            "risk_decision_id": getattr(order, "risk_decision_id", None),
            "order_tuple_hash": getattr(order, "order_tuple_hash", None),
        }
        if extra:
            payload.update(extra)
        st.audit.append(event, actor=actor, refs={"order_id": getattr(order, "id", None)},
                        payload=payload)

    @staticmethod
    def _final_audit_authority(st) -> tuple[bool, str | None]:
        """Fresh snapshot-pinned full verification after the real submit intent."""
        try:
            with st.session_factory() as session, session.begin():
                anchor_store = getattr(st, "anchor_store", None)
                anchor = anchor_store.latest() if anchor_store is not None else None
                if anchor_store is not None and anchor is None:
                    return False, "required external anchor is unavailable"
                verified = (
                    st.audit.verify_session_against_anchor(session, anchor[0], anchor[1])
                    if anchor is not None
                    else st.audit.verify_session(session)
                )
                if not verified.ok:
                    return False, verified.error or "audit chain is invalid"
        except BaseException as exc:  # noqa: BLE001 - unreadable authority is a veto
            return False, f"{type(exc).__name__}: {exc}"
        return True, None

    def _abort_after_intent(
        self, st, order, intent, *, stage, reason, actor, resolved_hash, event=None
    ) -> GateOutcome:
        """Conservatively cover a post-intent safety refusal when the ledger permits it."""
        try:
            st.audit.result(
                intent,
                "order_submit",
                refs={"order_id": order.id, "aborted": stage},
                payload={"aborted": reason, "safety_reason": stage},
            )
        except BaseException as exc:  # noqa: BLE001 - never trade to make logging succeed
            st.admit_integrity_failure(
                f"final safety abort result failed: {type(exc).__name__}: {exc}"
            )
            return GateOutcome(False, stage, reason, order_tuple_hash=resolved_hash)
        try:
            self._record_refusal(
                st,
                order,
                event=event or EVENT_ORDER_REFUSED,
                stage=stage,
                reason=reason,
                actor=actor,
                extra={"intent_seq": intent.seq},
            )
        except BaseException as exc:  # noqa: BLE001 - covering result remains authoritative
            st.admit_integrity_failure(
                f"final safety refusal log failed: {type(exc).__name__}: {exc}"
            )
        return GateOutcome(False, stage, reason, order_tuple_hash=resolved_hash)

    @contextmanager
    def _single_submit(self, st, order_id):
        """One in-flight submit per state and order.

        A nested call on this gateway, or on another gateway sharing ``st``, is refused
        before it can consume a token.
        """
        inflight = getattr(st, _INFLIGHT_ATTR, None)
        if inflight is None:
            inflight = set()
            setattr(st, _INFLIGHT_ATTR, inflight)
        if self._submit_active or order_id in inflight:
            yield False
            return
        self._submit_active = True
        inflight.add(order_id)
        try:
            yield True
        finally:
            self._submit_active = False
            inflight.discard(order_id)

    def _call_claim_fence(self, claim_fence, st, order, intent, actor, resolved_hash):
        """Run the fence. An exception covers the open intent; BaseException is re-raised.

        Returns ``(refusal, fence_value)``. ``refusal`` is a ``GateOutcome`` when the
        fence failed closed, and ``fence_value`` is what a successful fence returned.
        """
        try:
            value = claim_fence()
        except BaseException as exc:  # noqa: BLE001 - cover the intent, then refuse or re-raise
            outcome = self._abort_after_intent(
                st,
                order,
                intent,
                stage="claim_fence",
                reason=f"claim fence refused the broker call: {exc}",
                actor=actor,
                resolved_hash=resolved_hash,
            )
            if not isinstance(exc, Exception):
                raise
            return outcome, None
        return None, value

    def _cover_admission(self, st, order, intent, actor, resolved_hash, admit):
        """Run one admission attempt. A raise records the open intent, then control flow re-raises."""
        try:
            stage, reason = admit()
        except BaseException as exc:  # noqa: BLE001 - cover the intent, then refuse or re-raise
            outcome = self._abort_after_intent(
                st,
                order,
                intent,
                stage="admission_lock",
                reason=f"admission failed before transport: {exc}",
                actor=actor,
                resolved_hash=resolved_hash,
            )
            if not isinstance(exc, Exception):
                raise
            return outcome
        if stage is not None:
            return self._abort_after_intent(
                st,
                order,
                intent,
                stage=stage,
                reason=reason,
                actor=actor,
                resolved_hash=resolved_hash,
            )
        return None

    def _time_sensitive_refusal(
        self, st, order, *, approval, risk_decision, breaker_engine, risk_recheck,
        positions, clock,
    ):
        """Feed, risk re-run, approval/risk-eval TTL, consumed approval, and order state.

        These are the checks a blocking claim fence can outlast. The post-fence refresh
        uses the same rules as ``_precheck`` and does not record audit itself. The
        caller-supplied clock is not authority here: age and freshness use ``st.clock``.
        """
        clock = st.clock
        market_data = getattr(st, "market_data", None)
        if market_data is not None:
            now = clock.now()
            try:
                reason = market_data.stale_reason(
                    order.symbol,
                    now=now,
                    limit_s=getattr(
                        market_data, "max_age_s", BreakerThresholds().data_staleness_s
                    ),
                )
            except Exception:  # noqa: BLE001 - a broken guard cannot grant order authority
                reason = "market-data freshness guard unavailable"
            if reason is not None:
                try:
                    market_open = is_market_open(now)
                except Exception:  # noqa: BLE001 - broken calendar/clock must refuse cleanly
                    return ("market_data", None, "market-hours freshness guard unavailable")
                if market_open and breaker_engine.needs_watch_only_transition("market_data"):
                    breaker_engine.enter_watch_only(
                        "market_data", reason, clock=clock, actor="order_gateway"
                    )
                return ("market_data", None, reason)

        verdict = risk_recheck(positions)
        decision = _verdict_get(verdict, "decision")
        failed = _verdict_get(verdict, "failed_checks") or []
        if decision != "approved":
            if failed:
                return (
                    "risk",
                    BYPASS_HARD_CHECK_FAILED,
                    f"risk re-run failed hard checks {list(failed)}",
                )
            return ("risk", None, f"risk re-run decision {decision!r} != approved")

        db_appr = self._load_row(st, ApprovalRecord, getattr(approval, "id", None))
        if db_appr is None:
            return (
                "approval",
                None,
                "approval is not covered by a valid adjudication AuditEntry (not persisted)",
            )
        if db_appr.single_use_consumed or self._consumed_on_chain(st, db_appr.id):
            return (
                "approval",
                BYPASS_RESUBMIT_AFTER_DENIED,
                "approval already consumed (single-use replay)",
            )
        if db_appr.risk_decision_id != getattr(risk_decision, "id", None):
            return (
                "approval",
                None,
                "approval.risk_decision_id does not match the risk decision",
            )
        db_rd = self._load_row(st, RiskDecision, db_appr.risk_decision_id)
        if db_rd is None or db_rd.decision != "approved":
            return (
                "approval",
                BYPASS_APPROVAL_AGAINST_FAILED_RISK,
                "approval authorizes an order whose risk decision is not approved",
            )
        ttl = self._ttl_seconds(st)
        if ttl is not None:
            eval_at = getattr(db_rd, "evaluated_at", None) or db_appr.decided_at
            if eval_at is not None:
                age = (_naive_utc(clock.now()) - _naive_utc(eval_at)).total_seconds()
                if age > ttl:
                    return (
                        "approval",
                        None,
                        f"risk evaluation expired ({age:.0f}s > {ttl}s TTL)",
                    )
        db_order = self._load_row(st, Order, getattr(order, "id", None))
        if (
            db_order is None
            or db_order.state not in ("proposed", "approved")
            or self._order_submitted_on_chain(st, getattr(order, "id", None))
        ):
            return (
                "order_state",
                None,
                f"order state {getattr(db_order, 'state', None)!r} is not submittable",
            )
        return None

    def _refresh_time_sensitive(
        self, st, order, intent, *, approval, risk_decision, breaker_engine, risk_recheck,
        positions, clock, actor, resolved_hash,
    ):
        """Re-check feed, risk, and approval after a fence. Return a refusal, or None."""
        try:
            found = self._time_sensitive_refusal(
                st, order, approval=approval, risk_decision=risk_decision,
                breaker_engine=breaker_engine, risk_recheck=risk_recheck,
                positions=positions, clock=clock,
            )
        except BaseException as exc:  # noqa: BLE001 - cover the intent, then refuse or re-raise
            outcome = self._abort_after_intent(
                st, order, intent,
                stage="claim_fence",
                reason=f"post-fence safety check failed: {exc}",
                actor=actor,
                resolved_hash=resolved_hash,
            )
            if not isinstance(exc, Exception):
                raise
            return outcome
        if found is None:
            return None
        stage, event, reason = found
        return self._abort_after_intent(
            st, order, intent,
            stage=stage,
            reason=reason,
            actor=actor,
            resolved_hash=resolved_hash,
            event=event,
        )

    @staticmethod
    def _load_row(st, model, row_id):
        """Re-load a persisted row by id and detach it. The gateway trusts the DB row, NEVER a
        caller-supplied in-memory object (a caller holding the gateway could forge one)."""
        if not row_id:
            return None
        with st.session_factory() as s:
            row = s.get(model, row_id)
            if row is not None:
                s.expunge(row)
            return row

    def _adjudication_binds(self, st, approval_id, *, proposal_id, risk_decision_id,
                            order_tuple_hash) -> bool:
        """A covering ``EVENT_ADJUDICATION`` AuditEntry (refs.row_id == approval_id) whose
        HMAC-VERIFIED SIGNED payload binds EXACTLY (decision=approve, proposal_id,
        risk_decision_id, order_tuple_hash). The signed payload — not the mutable
        ``ApprovalRecord`` columns — is the root of trust: a keyless writer cannot forge it
        (SEC-12), and a caller holding the gateway cannot substitute the binding via a by-value
        object because we re-derive everything from the chain (the reproduced-bypass fix)."""
        with st.session_factory() as s:
            entries = s.execute(
                select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
            ).scalars().all()
        for e in entries:
            if (e.refs or {}).get("row_id") != approval_id:
                continue
            if not st.audit.verify_entry(e):
                continue
            p = e.payload or {}
            if (p.get("decision") == "approve"
                    and p.get("proposal_id") == proposal_id
                    and p.get("risk_decision_id") == risk_decision_id
                    and p.get("order_tuple_hash") == order_tuple_hash):
                return True
        return False

    def _consumed_on_chain(self, st, approval_id) -> bool:
        """SEC-12: 'already consumed' derived from the APPEND-ONLY SIGNED ledger, not the mutable
        ``approval_record.single_use_consumed`` column. A keyless DB writer can reset that column
        (it is outside the HMAC chain, so ``verify_chain`` stays green) but CANNOT forge the
        HMAC-signed ``approval_consumed`` adjudication the submit path appends — so single-use
        replay of a genuine approval is caught here even under a mutable-column rewind."""
        with st.session_factory() as s:
            entries = s.execute(
                select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
            ).scalars().all()
        for e in entries:
            if ((e.refs or {}).get("row_id") == approval_id
                    and (e.payload or {}).get("kind") == "approval_consumed"
                    and st.audit.verify_entry(e)):
                return True
        return False

    def _order_submitted_on_chain(self, st, order_id) -> bool:
        """SEC-12: whether the SIGNED ledger already shows this order submitted. Backs the
        pre-broker duplicate-execution guard so a keyless writer who rewinds the mutable
        ``order_row.state`` column back to 'approved' still cannot drive a second broker call —
        the HMAC-signed ``EVENT_ORDER_TRANSITION`` to 'submitted' cannot be forged."""
        if not order_id:
            return False
        with st.session_factory() as s:
            entries = s.execute(
                select(AuditEntry).where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
            ).scalars().all()
        for e in entries:
            if ((e.refs or {}).get("row_id") == order_id
                    and (e.payload or {}).get("to") == "submitted"
                    and st.audit.verify_entry(e)):
                return True
        return False

    # ---- the pre-trade gate (checks a-f, h) — shared by review AND submit --------------
    def _precheck(self, st, order, *, approval, risk_decision, ladder, breaker_engine,
                  risk_recheck, positions, producing_runtime, analysis_vendor,
                  analysis_generation, resolved_hash, clock):
        """Run gates (a) HALT, (b) mode, (c) SEC-01 breaker, (d) risk re-run, (e) approval
        validity, (f) runtime pin — in order. Returns ``(stage, event, reason)`` on the FIRST
        failure (``event`` is the audit event to record; ``None`` for the caller to default to
        ``EVENT_ORDER_REFUSED``), or ``None`` when all pass. Does NOT record audit itself and
        does NOT consume the token (that is gate (g), submit-only)."""
        # (a) HALT sentinel — an unreadable flag counts as SET (fail closed).
        if _halt_active(default_halt_path()):
            return ("halt", None, "HALT sentinel is set (SEC-04)")

        # RW-17: absent exchange-calendar data is an unknown operational state, never an
        # authorization to trade. This coverage-only check is shared by review and submit,
        # and intentionally runs before any approval token can be consumed. It does not add
        # a market-hours restriction to human-approved review_live orders.
        try:
            require_calendar_coverage(clock.now())
        except (CalendarCoverageError, TypeError, ValueError) as exc:
            return ("calendar", None, str(exc))

        # RW-14: the scheduled breaker is not a submit-time authority. Re-check the same
        # cached observation synchronously so a scheduler delay/restart cannot create a stale
        # window between approval and broker submit. A composed service starts faulted until
        # its first valid poll, which also makes restart fail closed.
        market_data = getattr(st, "market_data", None)
        if market_data is not None:
            now = clock.now()
            try:
                reason = market_data.stale_reason(
                    order.symbol,
                    now=now,
                    limit_s=getattr(
                        market_data, "max_age_s", BreakerThresholds().data_staleness_s
                    ),
                )
            except Exception:  # noqa: BLE001 - a broken guard cannot grant order authority
                reason = "market-data freshness guard unavailable"
            if reason is not None:
                try:
                    market_open = is_market_open(now)
                except Exception:  # noqa: BLE001 - broken calendar/clock must refuse cleanly
                    return ("market_data", None, "market-hours freshness guard unavailable")
                if market_open and breaker_engine.needs_watch_only_transition("market_data"):
                    breaker_engine.enter_watch_only(
                        "market_data", reason, clock=clock, actor="order_gateway"
                    )
                return ("market_data", None, reason)

        # (b) mode / watch-only.
        denial = breaker_engine.watch_only_reason()
        if denial is not None:
            return ("mode", None, denial)
        mode = ladder.current()
        mode_v = mode.value if hasattr(mode, "value") else str(mode)
        if mode_v not in self._allowed_modes:
            return (
                "mode",
                None,
                f"mode {mode_v!r} cannot use {self._execution_scope!r} broker transport",
            )
        # BUG-113: the composed paper service derives these values from the signed
        # proposal->AgentJob lineage.  Re-check inside both review and submit so a generation
        # change between operator approval and the broker boundary fails closed.
        if (
            self._require_analysis_generation
            or analysis_vendor is not None
            or analysis_generation is not None
        ):
            if analysis_vendor != producing_runtime:
                return (
                    "analysis_freshness",
                    None,
                    "analysis vendor does not match the producing runtime",
                )
            try:
                breaker_engine.assert_fresh_analysis(
                    analysis_vendor,
                    analysis_generation,
                )
            except (StaleAnalysisError, TypeError, ValueError):
                return (
                    "analysis_freshness",
                    None,
                    "analysis generation is missing, malformed, or stale (SEC-23)",
                )
        try:
            active_breakers = breaker_engine.active_trips()
        except Exception:  # noqa: BLE001 - unreadable breaker authority must refuse
            return ("breaker", None, "active-breaker state is unavailable")
        if active_breakers:
            return (
                "breaker",
                None,
                "active safety breaker(s): " + ", ".join(sorted(active_breakers)),
            )

        # (c) SEC-01 synchronous injection/anomaly breaker.
        if breaker_engine.check_order_path(context=order) is not None:
            return ("breaker", None, "SEC-01 injection/anomaly breaker tripped")

        # (d) risk RE-RUN (TOCTOU) — deterministic recheck against fresh positions.
        verdict = risk_recheck(positions)
        decision = _verdict_get(verdict, "decision")
        failed = _verdict_get(verdict, "failed_checks") or []
        if decision != "approved":
            if failed:
                return ("risk", BYPASS_HARD_CHECK_FAILED,
                        f"risk re-run failed hard checks {list(failed)}")
            return ("risk", None, f"risk re-run decision {decision!r} != approved")

        # (e) approval validity — the SEC-03 order authority. Trust the CHAIN + PERSISTED rows,
        # NEVER the caller-supplied approval/risk_decision objects: a caller holding the gateway
        # can forge an in-memory ApprovalRecord/RiskDecision (reproduced 4c bypass). Re-load both
        # by id, require an HMAC-signed adjudication that binds THIS resolved order, and read the
        # single-use / decision flags from the PERSISTED rows.
        db_appr = self._load_row(st, ApprovalRecord, getattr(approval, "id", None))
        if db_appr is None:
            return ("approval", None,
                    "approval is not covered by a valid adjudication AuditEntry (not persisted)")
        if db_appr.single_use_consumed or self._consumed_on_chain(st, db_appr.id):
            return ("approval", BYPASS_RESUBMIT_AFTER_DENIED,
                    "approval already consumed (single-use replay)")
        if db_appr.risk_decision_id != getattr(risk_decision, "id", None):
            return ("approval", None,
                    "approval.risk_decision_id does not match the risk decision")
        if db_appr.proposal_id != order.proposal_id:
            return ("approval", None, "approval does not bind this order's proposal")
        if db_appr.order_tuple_hash != resolved_hash:
            return ("approval", None,
                    "order_tuple_hash mismatch (modify/re-price needs a new approval)")
        db_rd = self._load_row(st, RiskDecision, db_appr.risk_decision_id)
        if db_rd is None or db_rd.decision != "approved":
            return ("approval", BYPASS_APPROVAL_AGAINST_FAILED_RISK,
                    "approval authorizes an order whose risk decision is not approved")
        if not self._adjudication_binds(st, db_appr.id, proposal_id=order.proposal_id,
                                        risk_decision_id=db_appr.risk_decision_id,
                                        order_tuple_hash=resolved_hash):
            return ("approval", None,
                    "no HMAC-signed adjudication binds this approval to this order")
        ttl = self._ttl_seconds(st)
        if ttl is not None:
            # BUG-132: the TTL is documented (Settings.approval_ttl_seconds) as measured
            # from the RiskDecision EVAL, and the read-side helper
            # (bridge.approval.approval_validity) enforces exactly that — but this gate
            # measured from the approval's decided_at, so a fresh approval minted over a
            # stale risk evaluation passed. Measure from evaluated_at, falling back to
            # decided_at only for legacy rows that predate the column.
            eval_at = getattr(db_rd, "evaluated_at", None) or db_appr.decided_at
            if eval_at is not None:
                age = (_naive_utc(clock.now()) - _naive_utc(eval_at)).total_seconds()
                if age > ttl:
                    return ("approval", None,
                            f"risk evaluation expired ({age:.0f}s > {ttl}s TTL)")

        # (f) runtime pin (SEC-08) — auto modes refuse a non-claude producing runtime.
        try:
            ladder.assert_execution_runtime(mode, producing_runtime)
        except ExecutionRuntimeError as exc:
            return ("runtime", None, str(exc))

        return None

    def _pending_ok(self, st, order) -> tuple[bool, Decimal, Decimal]:
        """(h) SEC-07 pending-exposure cap, COMPREHENSIVE completeness-reconcile (WP8; the SEC-07
        "Sharpened" closure + round-3/round-4 hardening, docs/security-hardening-backlog.md): sum
        approved-but-unfilled notional across OPEN siblings + THIS order's notional must be <= the
        cap. The open SET and every notional are anchored in HMAC-verified
        ``EVENT_ORDER_TRANSITION`` payloads — NEVER a mutable ``order_row`` column — because the
        threat model is a keyless writer with arbitrary UPDATE/DELETE on the domain tables.
        Understatement is the attack, so ANY reconcile discrepancy FAILS CLOSED.

        From ONE verify_entry-filtered, seq-ascending scan of the signed EVENT_ORDER_TRANSITION
        ledger we build ``chain_latest[rid]`` (last verified 'to' state), ``chain_notional[rid]``
        (the MAX verified payload notional across the row's WHOLE signed history — round-5 bypass 1:
        latest-wins let a laundered notional=0 signed on a later transition shrink the value; the
        birth signs the real notional atomically, so max-across-history preserves it),
        ``chain_to_states[rid]`` (the set of every verified 'to' state ever signed) and
        ``chain_ids`` (row_ids with >=1 verified transition). Against ``db_rows`` (every persisted
        Order except the current, with ``db_ids`` its id set) and
        ``open_states={approved,submitted,partially_filled}`` we FAIL CLOSED if ANY of:

          (1) a db row id NOT in ``chain_ids`` — its signed history was deleted;
          (2) a row_id chain-open but whose Order ROW is absent from ``db_ids`` — its row was
              deleted (round-4 gap 2: the prior one-directional ``select(Order)`` scan iterated
              only surviving rows, so a chain-open sibling whose ROW was deleted was never seen);
          (3) a db row whose DB state is open but whose ``chain_latest`` is NOT open — the chain
              was regressed/rewound below a genuinely-open row (round-4 gap 1: a partial-tail
              delete of the 'approved' step leaves ``chain_latest='proposed'`` while the DB row
              stays 'approved', and the old ``state not in open_states: continue`` SKIPPED it);
          (4) an order counted as open lacks a verified ``chain_notional``;
          (5) a db row in a TERMINAL state not backed by a verified transition whose 'to' is that
              terminal (round-5 bypass 2: a bare column UPDATE masks a genuinely-open sibling as
              closed — dropping it from BOTH open sets when paired with a chain-regressing delete —
              yet no signed terminal 'to' exists).

        The OPEN set summed is ``{ rid : chain_latest[rid] open OR db_rows[rid].state open }``;
        each contributes ``chain_notional[rid]`` — never the mutable column, so a chain-deleted
        db-open sibling contributes nothing but trips (1)/(4) => fail closed (round-2). The
        CURRENT order contributes ``max(chain_notional_for_current, row_notional)`` (round-3
        bypass A: a zeroed mutable ``order_row.notional`` cannot shrink it) and FAILS CLOSED with
        no verified chain notional. Every legitimately created/-transitioned order carries signed
        transitions with notional, so (1)-(4) never fire in normal operation.

        Defense-in-depth: ``state.audit.verify_chain()`` runs at the TOP (fail closed on an
        unhealed truncation/tamper before trusting any chain-derived exposure), then the SEC-11
        anchor consult — when an external witness is wired (``state.anchor_store``) the chain must
        verify AGAINST THE ANCHOR, else fail closed (skipped when none wired; every existing
        brokers test wires none)."""
        open_states = {"approved", "submitted", "partially_filled"}
        cap = self._cap(st)

        # Fail closed on an unhealed truncation/tamper before trusting any chain-derived exposure.
        if not st.audit.verify_chain().ok:
            return (False, Decimal("0"), cap)

        anchor_store = getattr(st, "anchor_store", None)
        if anchor_store is not None:
            anchor = anchor_store.latest()
            if anchor is None or not st.audit.verify_against_anchor(anchor[0], anchor[1]).ok:
                return (False, Decimal("0"), cap)   # anchor rollback -> trust no chain exposure

        # ONE verify_entry-filtered, seq-ascending scan -> per-row latest verified 'to' state +
        # latest verified payload notional + the set of row_ids with any verified transition. The
        # CURRENT order is included here (sized from its own verified chain notional below).
        with st.session_factory() as s:
            entries = s.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type == EVENT_ORDER_TRANSITION)
                .order_by(AuditEntry.seq)
            ).scalars().all()
        chain_latest: dict = {}          # row_id -> latest verified 'to' state
        chain_notional: dict = {}        # row_id -> MAX verified payload notional across history
        chain_ids: set = set()           # row_ids with >=1 verified transition
        chain_to_states: dict = {}       # row_id -> set of every verified 'to' state ever signed
        for e in entries:
            row_id = (e.refs or {}).get("row_id")
            if not row_id:
                continue
            if not st.audit.verify_entry(e):
                continue                 # unverified entries are attacker-writable; never trusted
            chain_ids.add(row_id)
            p = e.payload or {}
            if p.get("to"):
                chain_latest[row_id] = p["to"]          # seq-ascending scan: last verified wins
                chain_to_states.setdefault(row_id, set()).add(p["to"])
            if "notional" in p:
                n = _dec(p.get("notional"))
                if n.is_finite():
                    # ROUND-5 bypass 1 (notional laundering): take the MAX across the WHOLE signed
                    # history, NEVER latest-wins. The birth transition signs the real notional
                    # atomically (attacker cannot pre-zero within that txn), so a later laundered
                    # notional=0 (a keyless UPDATE order_row.notional=0 before a routine legit
                    # transition, which re-derives from the mutable column) cannot shrink the value.
                    prev = chain_notional.get(row_id)
                    chain_notional[row_id] = n if prev is None else max(prev, n)

        # Every persisted Order row EXCEPT the current order (sized separately below).
        with st.session_factory() as s:
            all_rows = s.execute(select(Order)).scalars().all()
        db_rows = {row.id: row for row in all_rows if row.id != order.id}
        db_ids = set(db_rows)

        tampered = False
        # (1) a db row with NO signed history at all -> its birth transition was deleted.
        for rid in db_ids:
            if rid not in chain_ids:
                tampered = True
        # (2) a chain-open row_id whose Order ROW was deleted (round-4 gap 2).
        for rid, state in chain_latest.items():
            if rid != order.id and state in open_states and rid not in db_ids:
                tampered = True
        # (3) a db-open row whose chain regressed below open (round-4 gap 1).
        for rid, row in db_rows.items():
            if row.state in open_states and chain_latest.get(rid) not in open_states:
                tampered = True
        # (5) ROUND-5 bypass 2 (terminal rewind without a signed transition): a db row whose state
        # is TERMINAL MUST be backed by a verified EVENT_ORDER_TRANSITION whose 'to' is that exact
        # terminal state. A bare column UPDATE to a terminal (masking a genuinely-open sibling as
        # closed so it drops from BOTH open sets — e.g. combined with a partial-tail delete that
        # regresses the chain below open) leaves no such signed 'to' -> fail closed. A legitimate
        # cancel/expire goes through transition(), which signs the terminal 'to', so this never
        # fires legitimately.
        for rid, row in db_rows.items():
            if row.state in TERMINAL_STATES and row.state not in chain_to_states.get(rid, set()):
                tampered = True

        # OPEN set = chain-open OR db-open (excluding the current order).
        open_ids = {rid for rid, state in chain_latest.items()
                    if rid != order.id and state in open_states}
        open_ids |= {rid for rid, row in db_rows.items() if row.state in open_states}

        pending = Decimal("0")
        for rid in open_ids:
            chain_n = chain_notional.get(rid)
            if chain_n is None:
                tampered = True          # (4) an open member with no verified chain notional
                continue                 # never fold the mutable column: understatement is fine
            pending += chain_n           # chain-anchored notional only, never the mutable column

        # The current order's OWN contribution, chain-anchored (round-3 bypass A): a zeroed
        # mutable order_row.notional cannot shrink it; no verified chain notional => fail closed.
        cur_chain_n = chain_notional.get(order.id)
        if cur_chain_n is None:
            return (False, pending, cap)
        total = pending + max(cur_chain_n, self._order_notional(order))
        return (total <= cap and not tampered, total, cap)

    # ---- public: pure dry-run re-check (no submit, no token consume) --------------------
    def review_equity_order(self, order, *, approval, risk_decision, ladder, breaker_engine,
                            risk_recheck, positions, producing_runtime,
                            analysis_vendor=None, analysis_generation=None, clock=None):
        """Run gates (a)-(f) + (h) WITHOUT submitting or consuming the capability token — a
        pure dry-run TOCTOU re-check. Returns a ``GateOutcome``. ``place_equity_order`` calls
        this first and refuses if it refuses (G7: review precedes place)."""
        st = self._resolve_state(ladder)
        clock = clock or st.clock
        resolved_hash = order_tuple_hash(self._resolve_tuple(order))
        with st.lock:
            fail = self._precheck(
                st, order, approval=approval, risk_decision=risk_decision, ladder=ladder,
                breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
                producing_runtime=producing_runtime, analysis_vendor=analysis_vendor,
                analysis_generation=analysis_generation, resolved_hash=resolved_hash, clock=clock,
            )
            if fail is not None:
                stage, event, reason = fail
                self._record_refusal(st, order, event=event or EVENT_ORDER_REFUSED,
                                     stage=stage, reason=f"[review] {reason}",
                                     actor=producing_runtime)
                return GateOutcome(False, stage, reason, order_tuple_hash=resolved_hash)
            ok, total, cap = self._pending_ok(st, order)
            if not ok:
                reason = f"pending exposure {total} + order would exceed cap {cap} (SEC-07)"
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="pending_exposure", reason=f"[review] {reason}",
                                     actor=producing_runtime)
                return GateOutcome(False, "pending_exposure", reason,
                                   order_tuple_hash=resolved_hash)
        return GateOutcome(True, "reviewed", order_tuple_hash=resolved_hash)

    # ---- public: the single LOCKED submit transaction (a)-(h) + two-phase submit --------
    def submit(self, order, *, approval, risk_decision, ladder, breaker_engine, risk_recheck,
               positions, token_nonce, producing_runtime, analysis_vendor=None,
               analysis_generation=None, clock=None,
               claim_fence: Callable[[], None] | None = None) -> GateOutcome:
        """Re-run EVERY gate immediately before the broker call, failing CLOSED on the first
        failure (audited; bypass events recorded so the gate sees them). Only if all pass:
        two-phase submit (intent -> broker.submit_order -> result), consume the single-use
        approval, and advance the order to ``submitted`` — all audited (Inv 1/6; SEC-13).

        ``claim_fence``, when given, is called before the final safety checks and transport
        (CSL-218): an external claim/lease owner (the paper-profile store) gets one last
        conditional-update check that its fencing token is still current, so a caller
        whose claim was already re-owned elsewhere is refused here instead of reaching
        the broker a second time. A raised exception aborts the same way as any other
        pre-broker safety refusal; the intent is already open and gets a covering result.
        ``CancelledError``, ``KeyboardInterrupt`` and ``SystemExit`` record that result
        and then propagate. A profile gateway (``_admits_paper_profile``) uses only the
        fence bound at construction. A different callable passed here is refused, and a
        fence that returns None does not admit. The fence must not call ``submit`` again
        on the same state and order. After it returns, the feed, risk re-run, approval
        TTL, consumed approval and order state are checked again on the pinned state clock.
        """
        st = self._resolve_state(ladder)
        clock = clock or st.clock
        resolved_hash = order_tuple_hash(self._resolve_tuple(order))
        canonical = self._resolve_tuple(order)

        with st.lock, self._single_submit(st, getattr(order, "id", None)) as submit_open:
            if not submit_open:
                reason = "re-entrant submit refused before the capability token is consumed"
                self._record_refusal(
                    st, order, event=EVENT_ORDER_REFUSED,
                    stage="reentrant_submit", reason=reason, actor=producing_runtime,
                )
                return GateOutcome(
                    False, "reentrant_submit", reason, order_tuple_hash=resolved_hash
                )
            # (a)-(f)
            fail = self._precheck(
                st, order, approval=approval, risk_decision=risk_decision, ladder=ladder,
                breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
                producing_runtime=producing_runtime, analysis_vendor=analysis_vendor,
                analysis_generation=analysis_generation, resolved_hash=resolved_hash, clock=clock,
            )
            if fail is not None:
                stage, event, reason = fail
                self._record_refusal(st, order, event=event or EVENT_ORDER_REFUSED,
                                     stage=stage, reason=reason, actor=producing_runtime)
                return GateOutcome(False, stage, reason, order_tuple_hash=resolved_hash)

            # Pre-broker state guard (SEC-07 duplicate-execution backstop): re-read the PERSISTED
            # order state under the lock and refuse anything not freshly submittable. The
            # approval-idempotency race can mint TWO distinct (genuine) approvals+tokens for ONE
            # order; both pass gate (e) with a fresh approval, but the first advances the row to
            # 'submitted' and the second is refused HERE before a duplicate broker call.
            db_order = self._load_row(st, Order, getattr(order, "id", None))
            if (db_order is None or db_order.state not in ("proposed", "approved")
                    or self._order_submitted_on_chain(st, getattr(order, "id", None))):
                # The chain check makes the guard tamper-evident: a keyless rewind of the mutable
                # order_row.state column cannot resurrect a submittable state (SEC-12).
                reason = f"order state {getattr(db_order, 'state', None)!r} is not submittable"
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="order_state", reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "order_state", reason, order_tuple_hash=resolved_hash)

            # (g) capability token — single-use consume, must match the SEC-07 binding.
            try:
                self._registry.consume(
                    nonce=token_nonce, proposal_id=order.proposal_id,
                    risk_decision_id=risk_decision.id, order_tuple_hash=resolved_hash,
                )
            except CapabilityError as exc:
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="capability", reason=f"capability token: {exc}",
                                     actor=producing_runtime)
                return GateOutcome(False, "capability", str(exc),
                                   order_tuple_hash=resolved_hash)

            # (h) pending-exposure cap.
            ok, total, cap = self._pending_ok(st, order)
            if not ok:
                reason = f"pending exposure {total} + order would exceed cap {cap} (SEC-07)"
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="pending_exposure", reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "pending_exposure", reason,
                                   order_tuple_hash=resolved_hash)

            # SEC-04 SYNCHRONOUS HALT re-check immediately before the broker call. Gate (a) probed
            # the sentinel ONCE at the top of _precheck; the still-unlocked-file work since then
            # (_load_row, _registry.consume, _pending_ok's verify_chain + full table scans) is a real
            # TOCTOU window in which the OUT-OF-BAND `python -m orchestrator.halt --set` — which never
            # takes st.lock — can write the sentinel. Re-probe the SAME default_halt_path() here so
            # NO unresolved intent and NO broker call is emitted once the flag is present (SEC-04's
            # required control: check the sentinel synchronously immediately before every MCP call).
            # An UNREADABLE flag counts as SET (fail closed). Additive: gates a-h and the two-phase
            # submit are unchanged.
            if _halt_active(default_halt_path()):
                reason = "HALT sentinel set before broker submit (SEC-04)"
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="halt", reason=reason, actor=producing_runtime)
                return GateOutcome(False, "halt", reason, order_tuple_hash=resolved_hash)

            # ALL gates passed -> two-phase submit (SEC-13). Intent BEFORE the broker call.
            intent = st.audit.intent(
                "order_submit",
                refs={"order_id": order.id, "proposal_id": order.proposal_id,
                      "risk_decision_id": risk_decision.id, "approval_id": approval.id,
                      "order_tuple_hash": resolved_hash},
                payload={"symbol": order.symbol, "side": order.side,
                         "qty": str(order.qty), "order_type": order.order_type,
                         "mode": ladder.current().value,
                         "execution_scope": self._execution_scope,
                         "producing_runtime": producing_runtime},
            )
            broker_request = dict(canonical)
            broker_request["client_order_id"] = f"cockpit-{order.id}"[:128]

            # BUG-159 / D4: the pending-exposure read happened before the intent and cannot
            # authorize the later transport entry. Verify the complete chain again from one
            # new pinned snapshot. Invalid/unreadable authority is sticky and no attempt to
            # log the refusal may force the broker call.
            audit_ok, audit_error = self._final_audit_authority(st)
            if not audit_ok:
                reason = "final_audit_unverified"
                st.admit_integrity_failure(
                    f"final audit verification failed: {audit_error or 'unknown error'}"
                )
                return GateOutcome(
                    False, "audit_integrity", reason, order_tuple_hash=resolved_hash
                )

            # Probe HALT after the potentially blocking audit read and before the serialized
            # admission decision. A refusal after token consumption retains the burned nonce
            # and resolves the exact intent when the verified ledger remains writable.
            if _halt_active(default_halt_path()):
                return self._abort_after_intent(
                    st,
                    order,
                    intent,
                    stage="halt",
                    reason="HALT set immediately before broker call (SEC-04)",
                    actor=producing_runtime,
                    resolved_hash=resolved_hash,
                )

            # Pin the transport before the potentially blocking, caller-supplied fence.
            # The callback cannot replace the adapter, its submit implementation, or the
            # paper scope. Profile transport calls the class function captured at init,
            # so a subclass override or a swapped instance method cannot run.
            broker = self._broker
            broker_type = type(broker)
            execution_scope = self._execution_scope
            # Profile paper admission is a subclass that sets this flag. The subclass
            # lives in its own module; this hook must not import it.
            paper_transport = bool(getattr(type(self), "_admits_paper_profile", False))
            from brokers.alpaca_paper import AlpacaPaperBroker
            admitted_paper_submit = self._admitted_paper_submit
            if paper_transport:
                if claim_fence is not None and claim_fence is not self._claim_fence:
                    return self._abort_after_intent(
                        st,
                        order,
                        intent,
                        stage="claim_fence",
                        reason="per-call claim fence is not the fence bound at setup",
                        actor=producing_runtime,
                        resolved_hash=resolved_hash,
                    )
                claim_fence = self._claim_fence
                if not callable(claim_fence):
                    return self._abort_after_intent(
                        st,
                        order,
                        intent,
                        stage="claim_fence",
                        reason="paper profile submission requires a claim fence",
                        actor=producing_runtime,
                        resolved_hash=resolved_hash,
                    )
            lease_remaining_s: float | None = None

            def _run_fence():
                nonlocal lease_remaining_s
                refused, value = self._call_claim_fence(
                    claim_fence, st, order, intent, producing_runtime, resolved_hash
                )
                if refused is not None:
                    return refused
                if paper_transport and value is None:
                    return self._abort_after_intent(
                        st,
                        order,
                        intent,
                        stage="claim_fence",
                        reason="claim fence returned no admission",
                        actor=producing_runtime,
                        resolved_hash=resolved_hash,
                    )
                lease_remaining_s = _lease_remaining_s(value)
                return None

            if claim_fence is not None:
                refused = _run_fence()
                if refused is not None:
                    return refused

            def _transport_changed() -> bool:
                return (
                    self._broker is not broker
                    or type(broker) is not broker_type
                    or self._execution_scope != execution_scope
                    or (paper_transport and (
                        self._execution_scope != "paper"
                        or not isinstance(broker, AlpacaPaperBroker)
                    ))
                )

            if _transport_changed():
                return self._abort_after_intent(
                    st,
                    order,
                    intent,
                    stage="transport_scope",
                    reason="broker transport changed during the claim fence",
                    actor=producing_runtime,
                    resolved_hash=resolved_hash,
                )

            def _refresh_validity():
                # Caller ``clock`` is not authority for this refresh. ``st.clock`` is.
                return self._refresh_time_sensitive(
                    st, order, intent,
                    approval=approval, risk_decision=risk_decision,
                    breaker_engine=breaker_engine, risk_recheck=risk_recheck,
                    positions=positions, clock=st.clock, actor=producing_runtime,
                    resolved_hash=resolved_hash,
                )

            def _refresh_after_fence():
                if claim_fence is None:
                    return None
                return _refresh_validity()

            refused = _refresh_after_fence()
            if refused is not None:
                return refused

            # D3 linearization point. A denial admitted first wins. Authorization first permits
            # this already-authorized call to enter and later admissions bar subsequent calls.
            # The fence has returned before admission. The short lock is released
            # before the final fail-closed HALT probe and private transport entry.
            # Acquisition itself is bounded: waiting forever would hold ``st.lock`` and
            # delay the in-process kill switch. A timeout aborts outside this lock.
            def _final_admission() -> tuple[str | None, str | None]:
                stage = None
                reason = None
                lock = st.safety_admission_lock
                if not lock.acquire(timeout=_ADMISSION_WAIT_BUDGET_S):
                    return (
                        "admission_lock",
                        "admission lock acquisition exceeded the claim lease budget",
                    )
                try:
                    reason = breaker_engine.watch_only_reason()
                    if reason is not None:
                        stage = "mode"
                    elif (
                        self._require_analysis_generation
                        or analysis_vendor is not None
                        or analysis_generation is not None
                    ):
                        try:
                            if analysis_vendor != producing_runtime:
                                raise StaleAnalysisError("analysis vendor mismatch")
                            breaker_engine.assert_fresh_analysis(
                                analysis_vendor, analysis_generation
                            )
                        except (StaleAnalysisError, TypeError, ValueError):
                            stage = "analysis_freshness"
                            reason = "stale_analysis"
                    return stage, reason
                finally:
                    lock.release()

            refused = self._cover_admission(
                st, order, intent, producing_runtime, resolved_hash, _final_admission
            )
            if refused is not None:
                return refused

            # The admission read can block past the claim lease, letting a reconciler take
            # over. Re-run the fence (generation + unexpired lease CAS) and then re-read
            # admission. An admission read that itself blocked past the budget is followed
            # by a fresh fence, so no unchecked wait precedes transport.
            if claim_fence is not None:
                for _round in range(_MAX_FENCE_ROUNDS):
                    refused = _run_fence()
                    if refused is not None:
                        return refused
                    if _transport_changed():
                        return self._abort_after_intent(
                            st,
                            order,
                            intent,
                            stage="transport_scope",
                            reason="broker transport changed during the claim fence",
                            actor=producing_runtime,
                            resolved_hash=resolved_hash,
                        )
                    # The post-fence risk refresh can block on broker HTTP too, so the
                    # wait budget covers it as well as the admission read. The budget is
                    # the lease remainder the fence just reported, capped at the ceiling.
                    started = time.monotonic()
                    refused = _refresh_after_fence()
                    if refused is not None:
                        return refused
                    refused = self._cover_admission(
                        st, order, intent, producing_runtime, resolved_hash, _final_admission
                    )
                    if refused is not None:
                        return refused
                    waited = time.monotonic() - started
                    refused = _refresh_validity()
                    if refused is not None:
                        return refused
                    limit = _ADMISSION_WAIT_BUDGET_S
                    if lease_remaining_s is not None:
                        limit = min(limit, lease_remaining_s)
                    if waited <= limit:
                        break
                else:
                    return self._abort_after_intent(
                        st,
                        order,
                        intent,
                        stage="claim_fence",
                        reason="admission wait exceeded the claim lease budget",
                        actor=producing_runtime,
                        resolved_hash=resolved_hash,
                    )

            # HALT may arrive while the claim store blocks or during the final
            # admission checks. Probe after both, immediately before transport.
            if _halt_active(default_halt_path()):
                return self._abort_after_intent(
                    st,
                    order,
                    intent,
                    stage="halt",
                    reason="HALT set immediately before broker call (SEC-04)",
                    actor=producing_runtime,
                    resolved_hash=resolved_hash,
                )

            # The private broker call (SEC-03: never a module global / string dispatch).
            # ``client_order_id`` is stable transport metadata and cannot change the approved
            # symbol/side/size/price/TIF tuple.
            try:
                # F1: the response-shape contract is validated INSIDE the protected region,
                # so a broker that RETURNS garbage (None/str/non-JSON-native fields) takes
                # the same covered abort path as a broker that raises.
                if paper_transport:
                    raw_result = admitted_paper_submit(broker, broker_request)
                else:
                    raw_result = broker.submit_order(broker_request)
                result = _sanitize_broker_response(raw_result)
            except BaseException as exc:                                 # noqa: BLE001
                # BUG-062: a broker failure between the intent and its result used to leave
                # the SEC-13 intent dangling FOREVER. Complete the
                # two-phase pair (same shape as the halt-abort above), record the refusal,
                # and fail closed. The capability token was consumed at gate (g) and stays
                # consumed: a failed submit needs a fresh human approval, not a free retry.
                # BaseException so a SystemExit/KeyboardInterrupt mid-call still covers the
                # intent before propagating (F1).
                #
                # AMBIGUITY IS RECORDED, NOT ERASED (F2): a timeout/5xx can be response
                # loss on an order that LANDED. The abort result resolves the SEC-13 pair,
                # and reconcile_startup alerts on every broker_error abort whose order row
                # is still non-terminal until the operator (or a future status poll)
                # settles it. A retry cannot double-execute at the broker: client_order_id
                # derives from the durable order id, so a landed original makes the retry
                # idempotent broker-side.
                reason = f"broker submit failed: {exc}"
                # BUG-058: this is the BRK_BROKER_ERRORS production signal — sampled and
                # reset by the intraday triage tick. Already under st.lock here.
                st.broker_errors_since_sample += 1
                st.audit.result(
                    intent, "order_submit",
                    refs={"order_id": order.id, "aborted": "broker_error"},
                    payload={"aborted": reason[:500]},
                )
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="broker_error", reason=reason,
                                     actor=producing_runtime)
                if not isinstance(exc, Exception):
                    raise
                return GateOutcome(False, "broker_error", reason,
                                   order_tuple_hash=resolved_hash)
            # Result AFTER the broker call — the two-phase pair keyed on the intent seq.
            # Every field below is sanitized JSON-native-or-None (F1).
            st.audit.result(
                intent, "order_submit",
                refs={"order_id": order.id, "broker_order_id": result["broker_order_id"]},
                payload={"status": result["status"],
                         "filled_qty": str(result["filled_qty"] if result["filled_qty"]
                                           is not None else 0),
                         "avg_fill_price": (None if result["avg_fill_price"] is None
                                            else str(result["avg_fill_price"]))},
            )

            # Consume the single-use approval (audited state change).
            approval.single_use_consumed = True
            st.audit.record_state_change(
                approval, EVENT_ADJUDICATION, actor=producing_runtime,
                payload={"kind": "approval_consumed", "proposal_id": order.proposal_id,
                         "order_id": order.id, "order_tuple_hash": resolved_hash},
            )

            # Advance the order proposed/approved -> submitted (audited; via the FSM).
            # BUG-125b: the in-memory order can be stale vs the durable row (a caller-held
            # object mutated after the pre-broker guard). BUG-125 makes transition() fail
            # CLOSED on that drift — but here that raise would escape as a raw exception
            # OVER an already-recorded broker submit. Convert it to an audited refusal
            # instead: the broker side-effect stands (covered by the intent/result pair +
            # client_order_id idempotency + reconcile_startup), and the operator sees a
            # refusal, not a traceback.
            order.approval_id = approval.id
            try:
                if order.state == "proposed":
                    transition(st, order, "approved", actor=producing_runtime,
                               reason="approval consumed", clock=clock)
                transition(
                    st, order, "submitted", actor=producing_runtime,
                    reason="submitted to broker", clock=clock,
                    broker_order_id=result["broker_order_id"],
                    filled_qty=result["filled_qty"] if result["filled_qty"] is not None else 0,
                    avg_fill_price=result["avg_fill_price"],
                )
            except IllegalOrderTransition as exc:
                reason = f"order FSM refused the post-submit transition (stale order): {exc}"
                log.warning("order %s: %s", order.id, reason)
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     stage="order_state", reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "order_state", reason,
                                   order_tuple_hash=resolved_hash)

            # BUG-052: consume the broker's ACK. A paper market order fills synchronously
            # at submit; leaving it 'submitted' meant the filled/cancelled FSM edges had no
            # production caller and _pending_ok counted the full notional as open FOREVER —
            # the SEC-07 cap degraded into a one-time budget that self-blocked the order
            # path. Only DEFINITIVE statuses advance the state; indeterminate ones stay
            # 'submitted' for the (future) order-status poll to resolve. F3: a terminal,
            # exposure-draining move additionally requires fill data CONSISTENT with the
            # claimed status — a bare status string cannot manufacture cap headroom; an
            # inconsistent ACK is audited as ignored and the order stays open (over-counted,
            # the fail-safe direction). F4: a transient DB failure on this LAST write must
            # not escape as a raw exception over an already-recorded submit — the order
            # stays 'submitted' (fail-safe) for the same poll to resolve; a tamper-shaped
            # AuditIntegrityError still propagates.
            ack_state = _BROKER_STATUS_TO_STATE.get((result["status"] or "").lower())
            if ack_state and ack_state != "submitted" and legal_order_transition(order.state, ack_state):
                if not _ack_consistent(ack_state, result, order):
                    st.audit.append(
                        "order_ack_ignored", actor=producing_runtime,
                        refs={"order_id": order.id},
                        payload={"status": result["status"],
                                 "filled_qty": (None if result["filled_qty"] is None
                                                else str(result["filled_qty"])),
                                 "qty": str(order.qty),
                                 "reason": "fill data inconsistent with claimed status (F3)"},
                    )
                else:
                    try:
                        transition(
                            st, order, ack_state, actor=producing_runtime,
                            reason=f"broker reported {result['status']!r} at submit",
                            clock=clock,
                            broker_order_id=result["broker_order_id"],
                            filled_qty=result["filled_qty"],
                            avg_fill_price=result["avg_fill_price"],
                        )
                    except (IntegrityError, OperationalError) as exc:
                        log.warning(
                            "order %s: ack transition to %r failed transiently (%s); order "
                            "stays 'submitted' (open, fail-safe) for a later poll to resolve",
                            order.id, ack_state, exc,
                        )
                    except IllegalOrderTransition as exc:
                        # BUG-125b: stale in-memory order vs the durable row — the FSM
                        # refused; the order stays 'submitted' (open, fail-safe) for the
                        # status poll to reconcile, same as the transient path above.
                        log.warning(
                            "order %s: ack transition to %r refused as stale (%s); order "
                            "stays 'submitted' (open, fail-safe) for a later poll to resolve",
                            order.id, ack_state, exc,
                        )

        return GateOutcome(True, "submitted", broker_order_id=result["broker_order_id"],
                           broker_status=result["status"], order_tuple_hash=resolved_hash)

    # ---- public: place = review THEN submit (review provably precedes submit) -----------
    def place_equity_order(self, order, *, approval, risk_decision, ladder, breaker_engine,
                           risk_recheck, positions, token_nonce, producing_runtime,
                           analysis_vendor=None, analysis_generation=None, clock=None,
                           claim_fence: Callable[[], None] | None = None) -> GateOutcome:
        """G7: PROVABLY calls ``review_equity_order`` first and refuses (no submit, no token
        consume) if the review refuses; otherwise runs the locked ``submit`` transaction.

        ``claim_fence`` is forwarded to ``submit``. A profile gateway still requires one.
        """
        review = self.review_equity_order(
            order, approval=approval, risk_decision=risk_decision, ladder=ladder,
            breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
            producing_runtime=producing_runtime, analysis_vendor=analysis_vendor,
            analysis_generation=analysis_generation, clock=clock,
        )
        if not review.ok:
            return GateOutcome(False, review.stage, f"review refused: {review.reason}",
                               order_tuple_hash=review.order_tuple_hash)
        return self.submit(
            order, approval=approval, risk_decision=risk_decision, ladder=ladder,
            breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
            token_nonce=token_nonce, producing_runtime=producing_runtime,
            analysis_vendor=analysis_vendor, analysis_generation=analysis_generation, clock=clock,
            claim_fence=claim_fence,
        )


def _verdict_get(verdict, key):
    """Read a field from a risk verdict that may be a dict or a dataclass/object."""
    if isinstance(verdict, dict):
        return verdict.get(key)
    return getattr(verdict, key, None)
