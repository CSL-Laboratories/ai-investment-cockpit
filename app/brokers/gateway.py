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

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from brokers.capability import CapabilityError, SubmitToken, TokenRegistry
from brokers.order_state_machine import transition
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from orchestrator.halt import default_halt_path, halt_is_set
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

# Modes with LIVE order authority (paper is simulated; read_only has none).
_ORDER_AUTHORITY_MODES = frozenset({"review_live", "auto_veto", "auto_full"})
_AUTO_MODES = frozenset({"auto_veto", "auto_full"})

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
    """SEC-04 fail-closed HALT probe: an UNREADABLE flag counts as SET."""
    try:
        return halt_is_set(path)
    except OSError:
        return True


class OrderGateway:
    def __init__(
        self,
        broker,
        *,
        token_registry: TokenRegistry | None = None,
        pending_exposure_cap: Decimal | None = None,
        approval_ttl_seconds: int | None = None,
    ) -> None:
        # SEC-03: broker + registry are PRIVATE instance state — never module globals, never
        # dispatched by string name.
        self._broker = broker
        self._registry = token_registry or TokenRegistry()
        self._cap_override = pending_exposure_cap
        self._ttl_override = approval_ttl_seconds

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

    def _record_refusal(self, st, order, *, event, reason, actor, extra=None):
        payload = {
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
                  risk_recheck, positions, producing_runtime, resolved_hash, clock):
        """Run gates (a) HALT, (b) mode, (c) SEC-01 breaker, (d) risk re-run, (e) approval
        validity, (f) runtime pin — in order. Returns ``(stage, event, reason)`` on the FIRST
        failure (``event`` is the audit event to record; ``None`` for the caller to default to
        ``EVENT_ORDER_REFUSED``), or ``None`` when all pass. Does NOT record audit itself and
        does NOT consume the token (that is gate (g), submit-only)."""
        # (a) HALT sentinel — an unreadable flag counts as SET (fail closed).
        if _halt_active(default_halt_path()):
            return ("halt", None, "HALT sentinel is set (SEC-04)")

        # (b) mode / watch-only.
        if breaker_engine.is_watch_only():
            return ("mode", None, "vendor usage window is watch-only; order path disabled")
        mode = ladder.current()
        mode_v = mode.value if hasattr(mode, "value") else str(mode)
        if mode_v not in _ORDER_AUTHORITY_MODES:
            return ("mode", None, f"mode {mode_v!r} has no live order authority")

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
        if ttl is not None and db_appr.decided_at is not None:
            age = (_naive_utc(clock.now()) - _naive_utc(db_appr.decided_at)).total_seconds()
            if age > ttl:
                return ("approval", None, f"approval expired ({age:.0f}s > {ttl}s TTL)")

        # (f) runtime pin (SEC-08) — auto modes refuse a non-claude producing runtime.
        try:
            ladder.assert_execution_runtime(mode, producing_runtime)
        except ExecutionRuntimeError as exc:
            return ("runtime", None, str(exc))

        return None

    def _pending_ok(self, st, order) -> tuple[bool, Decimal, Decimal]:
        """(h) SEC-07 pending-exposure cap: sum approved-but-unfilled notional across OPEN
        orders + this order's notional must be <= the cap."""
        open_states = ("approved", "submitted", "partially_filled")
        with st.session_factory() as s:
            rows = s.execute(
                select(Order).where(Order.state.in_(open_states))
            ).scalars().all()
        pending = sum((self._order_notional(o) for o in rows if o.id != order.id),
                      Decimal("0"))
        total = pending + self._order_notional(order)
        cap = self._cap(st)
        return (total <= cap, total, cap)

    # ---- public: pure dry-run re-check (no submit, no token consume) --------------------
    def review_equity_order(self, order, *, approval, risk_decision, ladder, breaker_engine,
                            risk_recheck, positions, producing_runtime, clock=None):
        """Run gates (a)-(f) + (h) WITHOUT submitting or consuming the capability token — a
        pure dry-run TOCTOU re-check. Returns a ``GateOutcome``. ``place_equity_order`` calls
        this first and refuses if it refuses (G7: review precedes place)."""
        st = ladder.st
        clock = clock or st.clock
        resolved_hash = order_tuple_hash(self._resolve_tuple(order))
        with st.lock:
            fail = self._precheck(
                st, order, approval=approval, risk_decision=risk_decision, ladder=ladder,
                breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
                producing_runtime=producing_runtime, resolved_hash=resolved_hash, clock=clock,
            )
            if fail is not None:
                stage, event, reason = fail
                self._record_refusal(st, order, event=event or EVENT_ORDER_REFUSED,
                                     reason=f"[review] {reason}", actor=producing_runtime)
                return GateOutcome(False, stage, reason, order_tuple_hash=resolved_hash)
            ok, total, cap = self._pending_ok(st, order)
            if not ok:
                reason = f"pending exposure {total} + order would exceed cap {cap} (SEC-07)"
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED,
                                     reason=f"[review] {reason}", actor=producing_runtime)
                return GateOutcome(False, "pending_exposure", reason,
                                   order_tuple_hash=resolved_hash)
        return GateOutcome(True, "reviewed", order_tuple_hash=resolved_hash)

    # ---- public: the single LOCKED submit transaction (a)-(h) + two-phase submit --------
    def submit(self, order, *, approval, risk_decision, ladder, breaker_engine, risk_recheck,
               positions, token_nonce, producing_runtime, clock=None) -> GateOutcome:
        """Re-run EVERY gate immediately before the broker call, failing CLOSED on the first
        failure (audited; bypass events recorded so the gate sees them). Only if all pass:
        two-phase submit (intent -> broker.submit_order -> result), consume the single-use
        approval, and advance the order to ``submitted`` — all audited (Inv 1/6; SEC-13)."""
        st = ladder.st
        clock = clock or st.clock
        resolved_hash = order_tuple_hash(self._resolve_tuple(order))
        canonical = self._resolve_tuple(order)

        with st.lock:
            # (a)-(f)
            fail = self._precheck(
                st, order, approval=approval, risk_decision=risk_decision, ladder=ladder,
                breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
                producing_runtime=producing_runtime, resolved_hash=resolved_hash, clock=clock,
            )
            if fail is not None:
                stage, event, reason = fail
                self._record_refusal(st, order, event=event or EVENT_ORDER_REFUSED,
                                     reason=reason, actor=producing_runtime)
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
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED, reason=reason,
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
                                     reason=f"capability token: {exc}", actor=producing_runtime)
                return GateOutcome(False, "capability", str(exc),
                                   order_tuple_hash=resolved_hash)

            # (h) pending-exposure cap.
            ok, total, cap = self._pending_ok(st, order)
            if not ok:
                reason = f"pending exposure {total} + order would exceed cap {cap} (SEC-07)"
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED, reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "pending_exposure", reason,
                                   order_tuple_hash=resolved_hash)

            # ALL gates passed -> two-phase submit (SEC-13). Intent BEFORE the broker call.
            intent = st.audit.intent(
                "order_submit",
                refs={"order_id": order.id, "proposal_id": order.proposal_id,
                      "risk_decision_id": risk_decision.id, "order_tuple_hash": resolved_hash},
                payload={"symbol": order.symbol, "side": order.side,
                         "qty": str(order.qty), "order_type": order.order_type,
                         "mode": ladder.current().value, "producing_runtime": producing_runtime},
            )
            # The private broker call (SEC-03: never a module global / string dispatch).
            result = self._broker.submit_order(canonical)
            # Result AFTER the broker call — the two-phase pair keyed on the intent seq.
            st.audit.result(
                intent, "order_submit",
                refs={"order_id": order.id, "broker_order_id": result.get("broker_order_id")},
                payload={"status": result.get("status"),
                         "filled_qty": str(result.get("filled_qty", 0)),
                         "avg_fill_price": (None if result.get("avg_fill_price") is None
                                            else str(result.get("avg_fill_price")))},
            )

            # Consume the single-use approval (audited state change).
            approval.single_use_consumed = True
            st.audit.record_state_change(
                approval, EVENT_ADJUDICATION, actor=producing_runtime,
                payload={"kind": "approval_consumed", "proposal_id": order.proposal_id,
                         "order_id": order.id, "order_tuple_hash": resolved_hash},
            )

            # Advance the order proposed/approved -> submitted (audited; via the FSM).
            if order.state == "proposed":
                transition(st, order, "approved", actor=producing_runtime,
                           reason="approval consumed", clock=clock)
            transition(
                st, order, "submitted", actor=producing_runtime,
                reason="submitted to broker", clock=clock,
                broker_order_id=result.get("broker_order_id"),
                filled_qty=result.get("filled_qty", 0),
                avg_fill_price=result.get("avg_fill_price"),
            )

        return GateOutcome(True, "submitted", broker_order_id=result.get("broker_order_id"),
                           broker_status=result.get("status"), order_tuple_hash=resolved_hash)

    # ---- public: place = review THEN submit (review provably precedes submit) -----------
    def place_equity_order(self, order, *, approval, risk_decision, ladder, breaker_engine,
                           risk_recheck, positions, token_nonce, producing_runtime,
                           clock=None) -> GateOutcome:
        """G7: PROVABLY calls ``review_equity_order`` first and refuses (no submit, no token
        consume) if the review refuses; otherwise runs the locked ``submit`` transaction."""
        review = self.review_equity_order(
            order, approval=approval, risk_decision=risk_decision, ladder=ladder,
            breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
            producing_runtime=producing_runtime, clock=clock,
        )
        if not review.ok:
            return GateOutcome(False, review.stage, f"review refused: {review.reason}",
                               order_tuple_hash=review.order_tuple_hash)
        return self.submit(
            order, approval=approval, risk_decision=risk_decision, ladder=ladder,
            breaker_engine=breaker_engine, risk_recheck=risk_recheck, positions=positions,
            token_nonce=token_nonce, producing_runtime=producing_runtime, clock=clock,
        )


def _verdict_get(verdict, key):
    """Read a field from a risk verdict that may be a dict or a dataclass/object."""
    if isinstance(verdict, dict):
        return verdict.get(key)
    return getattr(verdict, key, None)
