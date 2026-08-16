"""MEM-01 — the thesis memory layer: open theses, their invalidations, and the packet block.

THE GAP THIS CLOSES
--------------------
``draft_proposal`` read no prior proposals, and ``invalidation`` was written but never read
back. So every draft was stateless: the system could propose the same name twice with
contradictory reasoning, and could never notice that a condition it had itself declared
disqualifying had already occurred.

This module gives the deep call a memory of what it already believes, and gives the operator
a standing list of claims the system is on the hook for.

THE SELF-GROUNDING ATTACK, AND WHY THIS IS NOT A VECTOR STORE
---------------------------------------------------------------
The obvious version of "give the model memory" is to retrieve relevant past material and put
it in the prompt. In THIS system that is unsafe, because SEC-14 corroboration is anchored on
``Packet.dispatched_refs``: a proposal is grounded only if every ``source_ref`` it cites was
in the dispatched set and resolves out of the verified raw store.

A thesis is not raw source data — it is a previous model's prose. If thesis material entered
the dispatched set, a hijacked model could BOOTSTRAP ITS OWN EVIDENCE: emit a confident thesis
today, then tomorrow cite that thesis as the corroborating source for acting on it. The
citation would check out. Grounding would pass. Nothing external would ever have said it.

Two independent mechanisms prevent it, and both are tested:

  1. ``thesis_context_block`` produces PROMPT TEXT ONLY. Thesis refs are never added to
     ``Packet.items``, so they are never in ``dispatched_refs``, so a citation of one fails
     the "cited ref not in dispatched packet" check in ``grounding.corroborate``.
  2. Thesis refs use the ``thesis://`` namespace, which is not a parquet ref. Even if
     mechanism 1 were somehow bypassed, ``events.resolve_raw`` raises ``RawUnavailableError``
     for a non-parquet ref and corroboration fails closed on resolution.

The block is still wrapped in ``<untrusted-content>`` and re-escaped, because its text
originated from a model and therefore carries the same injection risk as a headline. Prior
scrubbing (SEC-09) is not a reason to skip the wrapper; it is a different control.

OPENING IS AUTOMATIC, CLOSING IS NOT
--------------------------------------
A drafted, unflagged proposal opens a thesis. Deciding whether "a close below support"
happened is judgement, and this system does not put a model on judgement calls that carry
authority — the same reason ``risk/`` is pure and the order path is human-gated. ``close`` is
an operator action and records which operator took it.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from core.ids import new_id
from ingest.normalize import escape_untrusted
from schemas.models.thesis_record import (
    STATUS_INVALIDATED,
    STATUS_OPEN,
    STATUS_SUPERSEDED,
    TERMINAL_STATUSES,
    ThesisRecord,
)
from workflows.packets import wrap_untrusted

EVENT_THESIS = "thesis"

THESIS_REF_SCHEME = "thesis://"
"""Namespace for a thesis in operator-facing text. Deliberately NOT a ``parquet://`` ref:
``resolve_raw`` refuses it, so it can never corroborate even if it reaches a citation."""

MAX_THESIS_CHARS = 600
"""Per-field cap on thesis prose entering a prompt. A thesis is context, not evidence, and an
unbounded one would let a single verbose draft crowd the actual source material out of the
window it is supposed to be read alongside."""


class ThesisError(ValueError):
    """An illegal thesis transition."""


@dataclass(frozen=True)
class ThesisView:
    """What the operator surface shows. Plain types only — this reaches an embed."""

    id: str
    symbol: str
    side: str
    status: str
    thesis: str
    invalidation: str
    proposal_id: str
    opened_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id, "symbol": self.symbol, "side": self.side, "status": self.status,
            "thesis": self.thesis, "invalidation": self.invalidation,
            "proposal_id": self.proposal_id, "opened_at": self.opened_at,
            "ref": f"{THESIS_REF_SCHEME}{self.id}",
        }


# ---- reads -------------------------------------------------------------------------------


def open_theses(state, *, symbol: str | None = None) -> list[ThesisRecord]:
    """Open theses, newest first. Detached from the session so callers may hold them."""
    with state.session_factory() as s:
        stmt = select(ThesisRecord).where(ThesisRecord.status == STATUS_OPEN)
        if symbol is not None:
            stmt = stmt.where(ThesisRecord.symbol == symbol.upper())
        rows = s.execute(stmt.order_by(ThesisRecord.opened_at.desc())).scalars().all()
        for row in rows:
            s.expunge(row)
    return list(rows)


def views(records) -> list[ThesisView]:
    return [
        ThesisView(
            id=r.id, symbol=r.symbol, side=r.side, status=r.status,
            thesis=r.thesis, invalidation=r.invalidation, proposal_id=r.proposal_id,
            opened_at=r.opened_at.isoformat() if r.opened_at is not None else "",
        )
        for r in records
    ]


# ---- the packet block --------------------------------------------------------------------


def thesis_context_block(records) -> str:
    """Prompt text for the open theses on a symbol. NEVER added to ``dispatched_refs``.

    Returns "" when there is nothing open, so a first-time symbol produces byte-identical
    prompt text to the pre-MEM-01 behaviour and the change is additive rather than a silent
    rewrite of every prompt.
    """
    if not records:
        return ""
    blocks = []
    for r in records:
        inner = escape_untrusted(
            f"OPEN THESIS ({r.side.upper()} {r.symbol}, opened "
            f"{r.opened_at.date().isoformat() if r.opened_at else 'unknown'}):\n"
            f"{r.thesis[:MAX_THESIS_CHARS]}\n"
            f"INVALIDATION: {r.invalidation[:MAX_THESIS_CHARS]}"
        )
        # Wrapped as untrusted even though it was scrubbed at draft time: it is model-authored
        # prose re-entering a prompt, which is the exact shape of an injection carrier.
        blocks.append(wrap_untrusted(
            inner, source="prior-thesis", thesis_ref=f"{THESIS_REF_SCHEME}{r.id}",
            status=r.status,
        ))
    return "\n\n".join(blocks)


# ---- transitions -------------------------------------------------------------------------


def open_thesis(state, clock, *, proposal: dict, proposal_id: str) -> ThesisRecord | None:
    """Open a thesis from a drafted proposal. Returns None when the proposal carries no
    invalidation condition to be held to.

    Called only for a DRAFTED, UNFLAGGED proposal. A flagged (grounding-failed) or quarantined
    proposal must not open a standing claim — it is review-only, and recording its thesis as
    something the system believes would launder a rejected draft into durable state.
    """
    thesis = str(proposal.get("thesis") or "").strip()
    invalidation = str(proposal.get("invalidation") or "").strip()
    if not thesis or not invalidation:
        return None

    now = clock.now()
    record = ThesisRecord(
        id=new_id("thesis"),
        symbol=str(proposal.get("symbol", "")).upper(),
        side=str(proposal.get("side", "")),
        status=STATUS_OPEN,
        thesis=thesis,
        invalidation=invalidation,
        proposal_id=proposal_id,
        source_refs=list(proposal.get("source_refs") or []),
        opened_at=now, updated_at=now,
    )
    state.audit.record_state_change(
        record, EVENT_THESIS, actor="proposals",
        payload={"thesis_id": record.id, "symbol": record.symbol, "side": record.side,
                 "status": STATUS_OPEN, "proposal_id": proposal_id},
    )
    return record


def close_thesis(state, clock, *, thesis_id: str, status: str, reason: str,
                 operator_id: str) -> ThesisRecord:
    """Close / invalidate / supersede a thesis. AN OPERATOR ACTION.

    ``operator_id`` is required and recorded. There is deliberately no path for a model or a
    scheduled job to call this: whether "a close below support" happened is a judgement, and a
    system that lets the model retire its own invalidation conditions has an invalidation
    field that means nothing.
    """
    if status not in TERMINAL_STATUSES:
        raise ThesisError(
            f"{status!r} is not a terminal thesis status; expected one of "
            f"{sorted(TERMINAL_STATUSES)}"
        )
    if not operator_id:
        raise ThesisError("closing a thesis requires an operator id; it is never automatic")

    with state.session_factory() as s:
        row = s.get(ThesisRecord, thesis_id)
        if row is None:
            raise ThesisError(f"no thesis {thesis_id!r}")
        if row.status != STATUS_OPEN:
            raise ThesisError(
                f"thesis {thesis_id!r} is already {row.status}; a closed thesis does not reopen"
            )
        s.expunge(row)

    now = clock.now()
    row.status = status
    row.closed_at = now
    row.updated_at = now
    row.closed_reason = reason
    row.closed_by = operator_id
    state.audit.record_state_change(
        row, EVENT_THESIS, actor=f"operator:{operator_id}",
        payload={"thesis_id": row.id, "symbol": row.symbol, "status": status,
                 "reason": reason, "closed_by": operator_id},
    )
    return row


def supersede_open_theses(state, clock, *, symbol: str, side: str, new_proposal_id: str,
                          operator_id: str) -> list[ThesisRecord]:
    """Retire open theses on one side of a symbol in favour of a new proposal.

    Also operator-gated, and for the same reason: replacing a standing claim is a decision
    about whether the old reasoning still holds, not a bookkeeping step that should happen
    silently because a newer draft arrived.
    """
    out = []
    for row in open_theses(state, symbol=symbol):
        if row.side == side:
            out.append(close_thesis(
                state, clock, thesis_id=row.id, status=STATUS_SUPERSEDED,
                reason=f"superseded by proposal {new_proposal_id}", operator_id=operator_id,
            ))
    return out


def invalidated_count(state, *, symbol: str | None = None) -> int:
    """How many theses were recorded as INVALIDATED — the system's own error count.

    Surfaced because a record of being wrong is the only thing that distinguishes discipline
    from a run of luck, and it is the number a system is least likely to volunteer.
    """
    with state.session_factory() as s:
        stmt = select(ThesisRecord).where(ThesisRecord.status == STATUS_INVALIDATED)
        if symbol is not None:
            stmt = stmt.where(ThesisRecord.symbol == symbol.upper())
        return len(s.execute(stmt).scalars().all())
