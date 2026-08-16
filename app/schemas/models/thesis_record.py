"""MEM-01 — ThesisRecord: the open position-thesis a proposal opened, and its invalidation.

WHY THIS EXISTS
----------------
The ``TradeProposal`` schema has required an ``invalidation`` field since WP8 — "the condition
under which this thesis is wrong and the position should be exited." Until now that field was
written, scrubbed, rendered to the operator once, and then never read by anything. Nothing in
the tree revisited it, so the system could not answer "did the reason I gave for this position
stop being true?"

``draft_proposal`` likewise read no prior proposals: every draft was produced with zero memory
of earlier theses on the same symbol. The system could propose buying a name on Monday and
again on Thursday without knowing Monday's thesis existed, or that its stated invalidation had
already fired.

That is a STATEFULNESS gap, not a retrieval gap, and it sits directly on the axis the corpus
says is the reliable ROI: **discipline**. A thesis you cannot be held to is not a thesis.

WHAT IT IS NOT
---------------
This is NOT evidence. ``thesis`` and ``invalidation`` are MODEL-AUTHORED prose carried over
from a previous proposal. They are shown to the model as context on later drafts, and they may
never corroborate anything — see ``workflows/theses.py`` for the self-grounding attack this
would otherwise open and the two independent mechanisms that close it.

CLOSING IS AN OPERATOR ACT
----------------------------
An invalidation condition is natural language ("a close below support"). Deciding whether it
fired requires judgement, and this system does not put a model on that decision any more than
it puts one on the order path. A thesis is opened automatically by a drafted proposal;
it is closed, invalidated, or superseded only by an allowlisted operator, and ``closed_by``
records which one. The system's job is to make sure the question gets ASKED — not to answer it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base

STATUS_OPEN = "open"
STATUS_INVALIDATED = "invalidated"
"""The stated invalidation condition fired. Distinct from ``closed`` on purpose: an
invalidated thesis is the one case worth counting, because a system that never records being
wrong cannot tell discipline from luck."""

STATUS_CLOSED = "closed"
STATUS_SUPERSEDED = "superseded"

TERMINAL_STATUSES = frozenset({STATUS_INVALIDATED, STATUS_CLOSED, STATUS_SUPERSEDED})


class ThesisRecord(Base):
    __tablename__ = "thesis_record"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    symbol: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)

    # open | invalidated | closed | superseded
    status: Mapped[str] = mapped_column(String, default=STATUS_OPEN, index=True)

    # SCRUBBED model prose, carried from the proposal that opened this thesis. Already through
    # scrub_output_fields (SEC-09) at draft time — this row never holds unscrubbed output.
    thesis: Mapped[str] = mapped_column(String)
    invalidation: Mapped[str] = mapped_column(String)

    # The proposal that opened it, and the raw refs that supported it AT THAT TIME. The refs
    # are kept for the audit trail, NOT so a later proposal can cite them: a ref's presence
    # here does not put it in a later packet's dispatched set.
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    source_refs: Mapped[list] = mapped_column(JSON, default=list)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    closed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    """The allowlisted operator id that closed it. NEVER a model, and never 'system' — if a
    thesis could close itself the invalidation condition would be advisory, which is the state
    this record exists to leave behind."""
