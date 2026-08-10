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
from brokers.order_state_machine import TERMINAL_STATES, legal_order_transition, transition

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

# Broker-specific authority sets. A paper transport must never be reachable from a live mode,
# and a live transport must never become reachable merely because the operator selected paper.
_ORDER_AUTHORITY_BY_SCOPE = {
    "paper": frozenset({"paper"}),
    "live": frozenset({"review_live", "auto_veto", "auto_full"}),
}
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
        if mode_v not in self._allowed_modes:
            return (
                "mode",
                None,
                f"mode {mode_v!r} cannot use {self._execution_scope!r} broker transport",
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
                            risk_recheck, positions, producing_runtime, clock=None):
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
        st = self._resolve_state(ladder)
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
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED, reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "halt", reason, order_tuple_hash=resolved_hash)

            # ALL gates passed -> two-phase submit (SEC-13). Intent BEFORE the broker call.
            intent = st.audit.intent(
                "order_submit",
                refs={"order_id": order.id, "proposal_id": order.proposal_id,
                      "risk_decision_id": risk_decision.id, "order_tuple_hash": resolved_hash},
                payload={"symbol": order.symbol, "side": order.side,
                         "qty": str(order.qty), "order_type": order.order_type,
                         "mode": ladder.current().value, "producing_runtime": producing_runtime},
            )
            # SEC-04 (R3-S1) FINAL fail-closed HALT re-check — AFTER the intent is written and
            # IMMEDIATELY before the broker call. The pre-broker re-check above runs BEFORE the
            # two-phase intent write; an out-of-band `python -m orchestrator.halt --set` (which never
            # takes st.lock) landing DURING the intent append is a fresh TOCTOU window that would
            # still fire the broker submit. Re-probe the SAME default_halt_path() here so the
            # sentinel is honored right up to the call. On trip: COMPLETE the two-phase pair (a
            # covering result so this intent does NOT dangle — SEC-13's 1:1 pairing and
            # unresolved_intents() stay intact), record the refusal, and return WITHOUT calling the
            # broker. An UNREADABLE flag counts as SET (fail closed).
            if _halt_active(default_halt_path()):
                reason = "HALT set immediately before broker call (SEC-04)"
                st.audit.result(
                    intent, "order_submit",
                    refs={"order_id": order.id, "aborted": "halt"},
                    payload={"aborted": reason},
                )
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED, reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "halt", reason, order_tuple_hash=resolved_hash)
            # The private broker call (SEC-03: never a module global / string dispatch).
            # ``client_order_id`` is a broker-side idempotency backstop derived from the durable
            # local order id. It is transport metadata, deliberately outside the operator-approved
            # canonical tuple/hash, and cannot change symbol/side/size/price/TIF.
            broker_request = dict(canonical)
            broker_request["client_order_id"] = f"cockpit-{order.id}"[:128]
            try:
                result = self._broker.submit_order(broker_request)
            except Exception as exc:                                     # noqa: BLE001
                # BUG-062: a broker failure between the intent and its result used to leave
                # the SEC-13 intent dangling FOREVER — the production reconciler is wired
                # with _no_broker_history and can only alert a not-landed intent, never heal
                # it — while the raw exception escaped the locked submit. Complete the
                # two-phase pair (same shape as the halt-abort above), record the refusal,
                # and fail closed. The capability token was consumed at gate (g) and stays
                # consumed: a failed submit needs a fresh human approval, not a free retry.
                reason = f"broker submit failed: {exc}"
                st.audit.result(
                    intent, "order_submit",
                    refs={"order_id": order.id, "aborted": "broker_error"},
                    payload={"aborted": reason[:500]},
                )
                self._record_refusal(st, order, event=EVENT_ORDER_REFUSED, reason=reason,
                                     actor=producing_runtime)
                return GateOutcome(False, "broker_error", reason,
                                   order_tuple_hash=resolved_hash)
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

            # BUG-052: consume the broker's ACK. A paper market order fills synchronously
            # at submit; leaving it 'submitted' meant the filled/cancelled FSM edges had no
            # production caller and _pending_ok counted the full notional as open FOREVER —
            # the SEC-07 cap degraded into a one-time budget that self-blocked the order
            # path. Only DEFINITIVE statuses advance the state; indeterminate ones stay
            # 'submitted' for the (future) order-status poll to resolve.
            ack_state = _BROKER_STATUS_TO_STATE.get(str(result.get("status") or "").lower())
            if ack_state and ack_state != "submitted" and legal_order_transition(order.state, ack_state):
                transition(
                    st, order, ack_state, actor=producing_runtime,
                    reason=f"broker reported {result.get('status')!r} at submit", clock=clock,
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
