"""WP6 — the approval gateway CORE (Invariant 1/6; SEC-03/07/13).

The single place a HUMAN adjudication becomes order authority. It is fully testable WITHOUT
discord.py: ``bridge/bot.py`` only adapts buttons/modals onto these functions.

Guarantees enforced here:
  * (SEC actor auth) the actor MUST be the allowlisted operator; a foreign Discord user is
    REJECTED and audited, and NEVER mints an ``ApprovalRecord`` that could authorize an order.
  * (idempotency) a replayed ``interaction_id`` (Discord retries) returns the SAME
    ``ApprovalRecord`` and causes NO second effect (no second audit entry, no second token).
  * (SEC-07) an APPROVE persists an ``ApprovalRecord`` bound to
    ``(proposal_id, risk_decision.id, order_tuple_hash(resolved_order))`` via
    ``AuditLog.record_state_change`` (the state row + its ``EVENT_ADJUDICATION`` audit entry in
    ONE transaction — Inv 6), then mints the single-use submit capability token.
  * (modal double-confirm) a LIVE-mode order above the notional threshold requires
    ``modal_confirmed=True`` — refused otherwise.
  * (SEC-03) this module + ``bridge/veto.py`` are the ONLY places a submit token is minted.
    reject/modify/veto record the adjudication but mint NO token; a modify voids any prior live
    approval token for the proposal (a re-price needs a fresh approval).

``is_approval_valid`` is the read-side companion the WP7 submit path consults just before it
consumes the token: it voids an approval that was consumed, has gone stale (quote/RiskDecision
expiry), or no longer matches the resolved order (a Modify/re-price changes the tuple hash).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from brokers.order_tuple import order_tuple_hash
from config.settings import Mode
from core.ids import new_id
from schemas.models.approval_record import ApprovalRecord
from orchestrator.state import EVENT_ADJUDICATION

_DECISIONS = frozenset({"approve", "reject", "modify", "veto"})
_LIVE_MODES = frozenset({Mode.review_live, Mode.auto_veto, Mode.auto_full})

# Fallback modal-confirm threshold when Settings carries no ``modal_confirm_notional``. A live
# order at/above this notional needs the operator's second (modal) confirmation.
DEFAULT_MODAL_CONFIRM_NOTIONAL = Decimal("1000")

# Audit event types that record a refused/foreign attempt WITHOUT polluting the gate's
# adjudication metrics (they are NOT EVENT_ADJUDICATION, so compute_gate ignores them).
EVENT_UNAUTHORIZED = "unauthorized_actor"


class ApprovalError(Exception):
    """A malformed approval request the gateway refuses outright (not a policy rejection)."""


@dataclass(frozen=True)
class Rejection:
    """A refused decision. Carries the reason code so the bot can tell the operator why. A
    Rejection NEVER carries an ApprovalRecord and NEVER mints a token."""

    reason: str
    code: str
    proposal_id: str | None = None
    interaction_id: str | None = None


@dataclass(frozen=True)
class ValidityResult:
    valid: bool
    reason: str | None = None   # a machine code when invalid: consumed | stale | tuple_mismatch | ...


def _token_nonce(approval_id: str) -> str:
    """Deterministic submit-token nonce for an approval row (unique because the row id is)."""
    return f"subtok_{approval_id}"


def token_nonce_for_approval(approval_id: str) -> str:
    """Public read-only derivation used by trusted operator surfaces after approval.

    This value is replay protection, not order authority: the gateway independently requires
    the persisted HMAC-signed adjudication that binds the exact order tuple.
    """
    return _token_nonce(approval_id)


def _dec(x) -> Decimal | None:
    if x is None:
        return None
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _derived_notional(resolved_order) -> Decimal | None:
    """BUG-060: the modal gate's notional, derived from the RESOLVED ORDER rather than
    trusted to a caller parameter. qty*limit_price for a priced order; a MARKET order has
    no client-side price, so its notional is UNKNOWABLE here -> None, which the gate
    treats as above-threshold (fail closed)."""
    if not isinstance(resolved_order, dict):
        return None
    qty = _dec(resolved_order.get("qty"))
    px = _dec(resolved_order.get("limit_price"))
    if qty is None or px is None:
        return None
    return abs(qty * px)


def _modal_threshold(settings) -> Decimal:
    raw = getattr(settings, "modal_confirm_notional", None)
    d = _dec(raw)
    return d if d is not None else DEFAULT_MODAL_CONFIRM_NOTIONAL


def _current_mode(st, mode):
    if mode is not None:
        return mode if isinstance(mode, Mode) else Mode(mode)
    # Read the live mode from the signed chain (fail-closed to read_only inside ModeLadder).
    from orchestrator.mode_ladder import ModeLadder
    return ModeLadder(st).current()


def _existing_by_interaction(st, interaction_id) -> ApprovalRecord | None:
    if not interaction_id:
        return None
    with st.session_factory() as s:
        row = s.execute(
            select(ApprovalRecord).where(ApprovalRecord.interaction_id == interaction_id)
        ).scalars().first()
        if row is not None:
            s.expunge(row)
        return row


def _void_prior_tokens(st, token_registry, proposal_id: str) -> None:
    """A modify/re-price voids any still-live approval token for this proposal: without a live
    token the WP7 submit path cannot execute the stale-price order (capability-based void). We
    can reconstruct each prior token's binding from its persisted ApprovalRecord columns."""
    if token_registry is None:
        return
    with st.session_factory() as s:
        priors = s.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.proposal_id == proposal_id,
                ApprovalRecord.user_decision == "approve",
                ApprovalRecord.single_use_consumed == False,  # noqa: E712 - SQL boolean
            )
        ).scalars().all()
        priors = [(p.id, p.risk_decision_id, p.order_tuple_hash) for p in priors]
    for appr_id, rd_id, oth in priors:
        nonce = _token_nonce(appr_id)
        if not token_registry.is_live(nonce):
            continue
        try:
            token_registry.consume(nonce=nonce, proposal_id=proposal_id,
                                    risk_decision_id=rd_id, order_tuple_hash=oth)
        except Exception:                                  # noqa: BLE001 - best-effort revoke
            pass                                           # a burned/mismatched token is already void


def record_decision(
    st,
    *,
    proposal_id: str,
    risk_decision,
    resolved_order: dict,
    actor_user_id,
    user_decision: str,
    surface: str,
    interaction_id,
    message_id=None,
    device=None,
    clock=None,
    modal_confirmed: bool = False,
    notional=None,
    token_registry=None,
    mode=None,
) -> "ApprovalRecord | Rejection":
    """Adjudicate a proposal. Returns the persisted ``ApprovalRecord`` (approve/reject/modify/
    veto by the operator) or a ``Rejection`` (foreign actor / missing modal confirm / bad input).
    ONLY an ``approve`` mints a submit token (into ``token_registry``)."""
    clock = clock or st.clock

    # The WHOLE read->persist->mint runs under the shared reentrant lock so the interaction_id
    # idempotency check-then-act is atomic within the process: two concurrent deliveries of the
    # SAME interaction_id (Discord retry / concurrent callbacks) cannot both mint a record+token
    # (the reproduced duplicate-execution race). The UNIQUE constraint on ApprovalRecord.
    # interaction_id is the cross-process backstop (caught below).
    with st.lock:
        # (ii) idempotency FIRST: a replayed interaction_id returns the same record, no 2nd effect.
        existing = _existing_by_interaction(st, interaction_id)
        if existing is not None:
            return existing

        # (i) actor authentication: a foreign user is refused and audited, never minting a record.
        from bridge.operators import is_authorized
        if not is_authorized(actor_user_id, st.settings):
            st.audit.append(
                EVENT_UNAUTHORIZED, actor=str(actor_user_id),
                refs={"proposal_id": proposal_id},
                payload={"proposal_id": proposal_id, "interaction_id": interaction_id,
                         "attempted_decision": user_decision, "surface": surface},
            )
            return Rejection("actor is not an allowlisted operator", "unauthorized",
                             proposal_id, interaction_id)

        decision = str(user_decision).strip().lower()
        if decision not in _DECISIONS:
            raise ApprovalError(f"user_decision must be one of {sorted(_DECISIONS)}, got {user_decision!r}")

        # The resolved order's canonical hash — computed for every decision so the audit payload
        # and the approval binding record exactly which order was adjudicated. Fail-closed on a
        # bad order.
        oth = order_tuple_hash(resolved_order)

        if decision == "approve":
            # (iv) modal double-confirm for a live order above threshold. BUG-060: the gate
            # used to fire only when the CALLER supplied a notional — and the bot's default
            # is notional=None, so SEC-15 was opt-in. The notional now falls back to the
            # resolved order itself, and an UNKNOWABLE notional (a live market order, or
            # garbage) requires the modal rather than skipping it.
            live = _current_mode(st, mode) in _LIVE_MODES
            n = _dec(notional)
            if n is None:
                n = _derived_notional(resolved_order)
            if live and not modal_confirmed and (n is None or n > _modal_threshold(st.settings)):
                shown = "unknowable" if n is None else str(n)
                return Rejection(
                    f"live order notional {shown} exceeds modal-confirm threshold "
                    f"{_modal_threshold(st.settings)}; a modal double-confirm is required",
                    "modal_required", proposal_id, interaction_id,
                )

        if decision == "modify":
            # A re-price voids any prior live approval token for this proposal (capability void).
            _void_prior_tokens(st, token_registry, proposal_id)

        now = clock.now()
        row = ApprovalRecord(
            id=new_id("appr"),
            proposal_id=proposal_id,
            risk_decision_id=risk_decision.id,
            actor_user_id=str(actor_user_id),
            user_decision=decision,
            surface=surface,
            interaction_id=interaction_id,
            message_id=message_id,
            device=device,
            order_tuple_hash=oth,
            single_use_consumed=False,
            notes=None,
            decided_at=now,
            created_at=now,
        )
        payload = {
            "decision": decision,           # gate reads this: 'approve'/'reject'/'modify'/'veto'
            "proposal_id": proposal_id,
            "risk_decision_id": risk_decision.id,
            "order_tuple_hash": oth,
            "actor": str(actor_user_id),
            "interaction_id": interaction_id,
            "surface": surface,
        }
        # (iii) state row + covering audit entry in ONE transaction (Inv 6). A cross-process
        # duplicate loses the UNIQUE race -> IntegrityError -> return the winner idempotently.
        try:
            persisted, _entry = st.audit.record_state_change(
                row, EVENT_ADJUDICATION, actor=str(actor_user_id), payload=payload
            )
        except IntegrityError:
            existing = _existing_by_interaction(st, interaction_id)
            if existing is not None:
                return existing                 # the other writer won; no second token
            raise

        # (v) ONLY an approve mints the single-use submit token (SEC-03).
        if decision == "approve" and token_registry is not None:
            token_registry.mint(
                nonce=_token_nonce(persisted.id),
                proposal_id=proposal_id,
                risk_decision_id=risk_decision.id,
                order_tuple_hash=oth,
            )
        return persisted


def is_approval_valid(approval, *, risk_decision, resolved_order, now: datetime,
                      max_age_seconds: float) -> bool:
    """Read-side gate the WP7 submit path consults immediately before consuming the token. VOID
    (returns False) if the approval was consumed, is not an approve, is bound to a different
    RiskDecision, has gone stale past the quote/RiskDecision expiry, or no longer matches the
    resolved order (a Modify/re-price yields a different tuple hash). A void approval must be
    re-priced + re-posted for a fresh approval."""
    return approval_validity(
        approval, risk_decision=risk_decision, resolved_order=resolved_order,
        now=now, max_age_seconds=max_age_seconds,
    ).valid


def approval_validity(approval, *, risk_decision, resolved_order, now: datetime,
                      max_age_seconds: float) -> ValidityResult:
    """Same check as ``is_approval_valid`` but returns the machine reason code (the 're-price +
    re-post' signal) so the bot can tell the operator WHY an approval went void."""
    if approval is None:
        return ValidityResult(False, "missing")
    if getattr(approval, "user_decision", None) != "approve":
        return ValidityResult(False, "not_an_approval")
    if getattr(approval, "single_use_consumed", False):
        return ValidityResult(False, "consumed")
    if approval.risk_decision_id != getattr(risk_decision, "id", None):
        return ValidityResult(False, "risk_decision_mismatch")

    # Stale quote / RiskDecision expiry — measured from when the risk/quote was evaluated.
    evaluated_at = getattr(risk_decision, "evaluated_at", None)
    if evaluated_at is not None:
        age = (_aware(now) - _aware(evaluated_at)).total_seconds()
        if age > max_age_seconds:
            return ValidityResult(False, "stale")

    # Modify / re-price: the resolved order must still hash to the approved tuple.
    if order_tuple_hash(resolved_order) != approval.order_tuple_hash:
        return ValidityResult(False, "tuple_mismatch")

    return ValidityResult(True, None)
