"""W12 SEC-13 — startup reconciliation of the two-phase audit ledger.

The audit spine (``storage/audit.py``) writes every order submit as an atomic two-phase
pair: an ``intent`` row BEFORE the broker call and a ``result`` row AFTER it (gateway.submit
L624-643). A crash BETWEEN the broker ACK and the result-commit therefore leaves an
``intent`` with no covering ``result`` — the order may have LANDED at the broker while the
ledger shows it unfinished. That gap is invisible to ``verify_chain()`` (the chain is still
a valid HMAC sequence); only a reconciliation that consults the BROKER can tell "crashed
before we recorded a landed order" from "the order genuinely never went through".

``reconcile_startup`` closes the loop, FAIL-CLOSED:
  * For every ``unresolved_intents()`` seq, resolve the intent's ``refs.order_id`` and ask
    the injected ``broker_history_fn(order_id)`` whether the order landed.
      - Landed  -> write the missing ``result()`` (HEAL). The pair is now complete and
        ``verify_chain()`` stays green; the freed intent leaves ``unresolved_intents()``.
      - Not landed / unidentifiable -> ALERT (``#system``); the intent stays unresolved and
        the report is NOT ``ok`` (a submit we can neither confirm nor disprove is a defect,
        not a clean state — fail closed).
  * Then the 1:1 completeness gate over the load-bearing domain models: any ``orphan_result``
    (a result with no intent) or any ``verify_completeness`` gap (a domain row with NO
    covering AuditEntry — e.g. a bare ``session.add`` that bypassed ``record_state_change``)
    is ALERTED and forces ``ok=False``.

SCOPE of the completeness gate (honest boundary — do NOT over-claim). It detects a row that
has NO covering AuditEntry: a FRESH row a keyless writer bare-``session.add``ed, bypassing
``record_state_change``. That is its in-scope guarantee. It does NOT detect a
deleted-then-reused-id tamper — an arbitrary keyless ``DELETE`` of an audited row followed by a
bare ``INSERT`` of a NEW, unaudited row under the SAME id whose ORIGINAL covering AuditEntry
survives: the gate reads that row as covered because the surviving entry's ``row_id`` still
matches. That id-reuse is the KEYLESS-WRITER / SEC-11 domain-row-binding threat class, addressed
by SEC-11 (which remains OPEN pending its external witness) — and, for the order table
specifically, already caught at the order path by the gateway's signed ``EVENT_ORDER_TRANSITION``
reconcile (``_pending_ok`` / ``_order_submitted_on_chain`` fail closed when the chain lacks the
new row's signed history). Closing it GENERALLY (binding each row's covering AuditEntry ``seq``
into the row at write time) is a cross-model schema change, out of scope for this thin gate,
which reuses the existing audit primitives rather than re-implementing them.

This is COMPLEMENTARY to the SEC-11 external witness (``bridge/anchor.py``): the witness
proves the chain was not rolled back; this proves the chain is COMPLETE with respect to the
broker and the domain tables. Neither substitutes for the other. No ``brokers/`` /
``bridge/`` import — the broker-history reader and the alerter are INJECTED (W17 wires the
live readers; here and in tests they are mocks / a logger).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from schemas.models.agent_job import AgentJob
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.breaker_state import BreakerState
from schemas.models.brief_record import BriefRecord
from schemas.models.market_event import MarketEvent
from schemas.models.mode_state import ModeState
from schemas.models.order import Order
from schemas.models.portfolio_snapshot import PortfolioSnapshot
from schemas.models.proposal_record import ProposalRecord
from schemas.models.risk_decision import RiskDecision

# C3 / R3-C2 (coverage-set gap) fail-closed: gate EVERY load-bearing model persisted through an
# AUDITED write, whether the singular ``record_state_change`` path OR the batch ``append_in`` path
# (grep-verified across app/, 2026-07-09 — the earlier note claimed 'record_state_change' only and
# MISSED the append_in ingest path). A bare ``session.add`` on ANY of these tables now trips
# ``verify_completeness`` instead of reading ok=True. Each entry below is a real model class
# written through an audited state change:
#   ApprovalRecord    — bridge/approval.py, brokers/gateway.py        (record_state_change)
#   RiskDecision      — workflows/proposals.py                        (record_state_change)
#   Order             — brokers/order_state_machine.py                (record_state_change)
#   ProposalRecord    — workflows/proposals.py                        (record_state_change)
#   BriefRecord       — workflows/morning_brew.py                     (record_state_change)
#   AgentJob          — workflows/agent_calls.py, orchestrator/app.py (record_state_change)
#   PortfolioSnapshot — brokers/alpaca_paper.py                       (record_state_change)
#   BreakerState      — orchestrator/breakers.py                      (record_state_change)
#   ModeState         — orchestrator/mode_ladder.py                   (record_state_change)
#   MarketEvent       — workflows/events.py                           (append_in, refs.row_ids batch)
# MarketEvent is audited via ``append_in`` with a PLURAL ``refs.row_ids`` (EVENT_INGEST_BATCH), so
# it was invisible to a singular-only coverage read; verify_completeness now unions row_id + row_ids
# (R3-C2a) so a legit ingest batch reads covered (no cry wolf) while a bare-added MarketEvent trips.
_DEFAULT_MODELS = (
    ApprovalRecord, RiskDecision, Order,
    ProposalRecord, BriefRecord, AgentJob, PortfolioSnapshot, BreakerState, ModeState,
    MarketEvent,
)


@dataclass(frozen=True)
class ReconcileReport:
    """The outcome of a startup reconciliation pass (frozen — a settled verdict).

    * ``healed``          — intent seqs whose missing result was written after the broker
                            confirmed the order landed.
    * ``alerted``         — intent seqs the broker could NOT confirm (never completed) that
                            were flagged to ``#system``.
    * ``orphan_results``  — result seqs with no matching intent.
    * ``uncovered``       — ``{table: (row_id, ...)}`` domain rows with no covering AuditEntry.
    * ``ok``              — clean state: no alerted intent, no orphan, no uncovered row.
    """

    healed: tuple[int, ...]
    alerted: tuple[int, ...]
    orphan_results: tuple[int, ...]
    uncovered: dict[str, tuple[str, ...]]
    ok: bool
    # order_submit intents resolved as aborted:broker_error whose ORDER ROW is still
    # non-terminal — the submit outcome is AMBIGUOUS: a timeout/5xx can be response loss
    # on an order that LANDED at the broker, and the covering abort result would otherwise
    # make it invisible to every later look (BUG-052/062 adversarial round, F2). Alerted at
    # every startup, and ``ok`` stays False, until the operator (or a future status poll)
    # settles the order to a terminal state.
    ambiguous_aborts: tuple[int, ...] = ()


# C1 (false-heal) fail-closed: an explicit terminal-LANDED status set. A broker reply is
# POSITIVE evidence of landing ONLY if it references the queried order id AND carries one of
# these statuses (or filled_qty>0). A rejected/canceled/expired/not-found reply, an error
# object, an empty reply, or a record for a DIFFERENT order id is NOT evidence and must read
# as not-landed => ALERT, not HEAL.
_LANDED_STATUSES = frozenset({"filled", "partially_filled", "accepted", "submitted", "done"})

# C1' (round-2 false-heal via broker_order_id) fail-closed: a broker assigns a
# ``broker_order_id`` at SUBMIT, not at fill — real brokers stamp one onto REJECTED orders too
# (gateway.py records the identical {broker_order_id,status} shape). So a truthy broker_order_id
# is NOT fill evidence, and a matched-id record carrying any of these TERMINAL-NEGATIVE statuses
# is a settled NON-landing that must read not-landed even WITH a broker_order_id present:
# healing it would record a rejected/canceled order in the tamper-evident spine as landed.
_TERMINAL_NEGATIVE = frozenset({
    "rejected", "canceled", "cancelled", "expired", "replaced", "pending_cancel",
    "stopped", "suspended", "done_for_day", "rejected_by_broker", "failed",
})


def _order_landed(history, order_id) -> bool:
    """Require POSITIVE evidence that ``order_id`` landed at the broker before healing (C1/C1').

    A truthy reply is NOT sufficient. Among the records that reference the SAME ``order_id`` we
    queried, a ``_TERMINAL_NEGATIVE`` status (rejected/canceled/expired/…) ANYWHERE is a GLOBAL
    VETO — a settled non-landing before OR after any landed record blocks the heal (C1''); the
    old per-record ``continue`` false-healed ``[{id,accepted},{id,canceled}]`` by returning on
    the first landed record. Only with NO terminal-negative anywhere does an explicit landed
    signal — a status in ``_LANDED_STATUSES`` or ``filled_qty > 0`` — count as landed. A
    ``broker_order_id`` is assigned at SUBMIT (brokers stamp one onto rejected orders too), so it
    is NOT by itself fill evidence (C1'). A rejected/canceled/expired/not-found record, an
    error/exception object, an empty/``None`` reply, or a record whose ``order_id`` differs from
    the queried id all read as NOT landed. Fail closed: anything unreadable as an explicit
    positive-landed signal for THIS order — with no vetoing negative — is 'not landed' (=> alert,
    the intent stays unresolved)."""
    if not history or isinstance(history, BaseException):
        return False
    records = history if isinstance(history, (list, tuple)) else [history]
    # Restrict to records that reference the QUERIED order id — a record for a DIFFERENT order
    # is never evidence about this one (C1).
    matched = [rec for rec in records
               if isinstance(rec, dict) and rec.get("order_id") == order_id]

    # C1'' (GLOBAL VETO) fail-closed: a terminal-negative is a GLOBAL veto over the WHOLE history,
    # not a per-record ``continue``. The old per-record skip returned True on the FIRST landed
    # record even when a later record for the SAME id settled the order canceled/rejected/expired
    # (e.g. [{id,accepted},{id,canceled}] -> healed a settled non-landing). A settled non-landing
    # ANYWHERE in this order's history — before OR after any landed record — vetoes the heal. So
    # PRE-SCAN every matched record first: if ANY carries a terminal-negative status, return False
    # immediately, regardless of any landed record present.
    for rec in matched:
        status = str(rec.get("status", "")).strip().lower()
        if status in _TERMINAL_NEGATIVE:
            return False
    # ONLY with NO terminal-negative anywhere: positive landed evidence heals. A ``broker_order_id``
    # is assigned at SUBMIT (brokers stamp one onto rejected orders too), so it is NOT by itself
    # fill evidence (C1'); require an explicit landed status or filled_qty > 0.
    for rec in matched:
        status = str(rec.get("status", "")).strip().lower()
        if status in _LANDED_STATUSES:
            return True
        try:
            if float(rec.get("filled_qty") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def reconcile_startup(
    st,
    *,
    broker_history_fn: Callable[[str], object],
    alerter: Callable[[str], None] | None = None,
    models=_DEFAULT_MODELS,
) -> ReconcileReport:
    """Reconcile the two-phase ledger against the broker + the domain tables at startup.

    ``broker_history_fn(order_id)`` is INJECTED (a mock in tests; W17 supplies the live
    broker-history reader). ``alerter(msg)`` is INJECTED too; it defaults to logging a
    warning on ``cockpit.reconcile``. See the module docstring for the fail-closed contract.
    """
    log = logging.getLogger("cockpit.reconcile")
    if alerter is None:
        def alerter(msg: str) -> None:
            log.warning(msg)

    audit = st.audit

    # Snapshot the intent rows once so we can resolve each unresolved seq's order_id + op
    # WITHOUT re-querying per intent. (result() re-fetches the real intent by seq internally,
    # so healing only needs a bare AuditEntry(seq=...) sentinel — no detached-row access.)
    from sqlalchemy import select

    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry)).scalars().all()
        intents_meta = {
            r.seq: ((r.refs or {}).get("order_id"), (r.payload or {}).get("_op", "order_submit"))
            for r in rows
            if (r.payload or {}).get("_phase") == "intent"
        }

    healed: list[int] = []
    alerted: list[int] = []
    for seq in audit.unresolved_intents():
        order_id, op = intents_meta.get(seq, (None, "order_submit"))
        landed = False
        if order_id is not None:
            try:
                landed = _order_landed(broker_history_fn(order_id), order_id)
            except Exception as exc:   # a broker-history read failure is fail-closed => not landed
                log.warning("reconcile: broker history read failed for order %s: %s", order_id, exc)
                landed = False
        if landed:
            # HEAL: the order landed at the broker but the crash lost the result-commit.
            audit.result(
                AuditEntry(seq=seq), op,
                refs={"order_id": order_id, "reconciled": True},
                payload={"reconciled_by": "reconcile_startup", "broker_confirmed": True},
            )
            healed.append(seq)
        else:
            alerter(
                f"#system reconcile: order intent seq {seq} (order_id={order_id}) has NO covering "
                f"result and the broker does not confirm it landed — order never completed."
            )
            alerted.append(seq)

    # F2 (BUG-052/062 adversarial round): a broker_error abort is AMBIGUOUS, not settled.
    # The covering result keeps SEC-13's 1:1 pairing tidy, but a timeout can be response
    # loss on an order that LANDED — so every broker_error abort whose order row is still
    # non-terminal is re-alerted here until something settles it. Erasing the ambiguity
    # would understate pending exposure, the exact direction _pending_ok fails closed on.
    from brokers.order_state_machine import TERMINAL_STATES
    from schemas.models.order import Order

    ambiguous: list[int] = []
    with st.session_factory() as s:
        rows2 = s.execute(select(AuditEntry)).scalars().all()
        aborted = [((r.refs or {}).get("intent_seq"), (r.refs or {}).get("order_id"))
                   for r in rows2
                   if (r.payload or {}).get("_phase") == "result"
                   and (r.refs or {}).get("aborted") == "broker_error"]
        for iseq, oid in aborted:
            row = s.get(Order, oid) if oid else None
            if row is None or row.state not in TERMINAL_STATES:
                ambiguous.append(int(iseq) if iseq is not None else -1)
                alerter(
                    f"#system reconcile: order intent seq {iseq} (order_id={oid}) was aborted "
                    f"on a broker error but the order is not settled "
                    f"(state={getattr(row, 'state', None)!r}) — the submit outcome is "
                    "AMBIGUOUS; confirm against the broker and cancel or settle the order."
                )

    # 1:1 completeness gate — orphan results + uncovered domain rows (bypassed record_state_change).
    orphans = tuple(audit.orphan_results())
    for oseq in orphans:
        alerter(f"#system reconcile: orphan result seq {oseq} has no matching intent.")

    uncovered: dict[str, tuple[str, ...]] = {}
    for m in models:
        gaps = audit.verify_completeness(m)
        if gaps:
            uncovered[m.__tablename__] = tuple(gaps)
            alerter(
                f"#system reconcile: {len(gaps)} row(s) of {m.__tablename__} have NO covering "
                f"audit entry (state change bypassed record_state_change): {list(gaps)}"
            )

    ok = not alerted and not orphans and not uncovered and not ambiguous
    return ReconcileReport(
        healed=tuple(healed),
        alerted=tuple(alerted),
        orphan_results=orphans,
        uncovered=uncovered,
        ok=ok,
        ambiguous_aborts=tuple(ambiguous),
    )
