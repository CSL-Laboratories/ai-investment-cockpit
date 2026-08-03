"""WP1a — PollCursor stored SQLAlchemy model (ONE contract per file).

The persisted ingestion cursor for the SEC submissions poller (WP3
`edgar/submissions_poll.py`, which polls the data.sec.gov submissions API
**per CIK**). It is the durable half of **Invariant 11 (idempotent ingestion)**:
"an accession number is never processed twice (cursor + unique constraint);
replaying any feed produces zero duplicate Filings, Deltas, or Briefs." One row
per watchlist CIK records the last accession already ingested for that CIK, so a
feed replay — or a restart — resumes past it and dedups by accession
(scaffold WP3: "cursor dedup by accession … PollCursor survives restart";
scaffold WP1a Accept: "PollCursor survives restart").

The accession-level uniqueness that actually blocks a double-ingest lives on
`Filing.accession_no` (UNIQUE); this cursor is the forward high-water mark the
poller reads to avoid re-fetching what it already has.

Fields grounded in `docs/build-scaffold-ultracode.md` (WP1a model list, WP3,
Invariant 11) and the dossier (slug `edgar-filing-delta-morning-brew`,
`build_reports.py` architecture/data_flow: "A scheduler polls SEC submissions …
per CIK"; "Submissions API finds new filings and accession numbers"). Fields not
pinned by the scaffold are pattern-conventions ported from the cockpit models
(`../ai-investment-cockpit/app/schemas/models/` — re-implemented, not copied) and
marked [est.].

Barrier-consistency (WP1a Accept): shared columns keep the cross-schema types —
`cik` is a non-null `String` (as on Company/Filing); the cursor's accession
pointer is deliberately named `last_accession_no` (not `accession_no`) because it
is *nullable* until the first poll, whereas a Filing's own `accession_no` is a
non-null unique key — a same-named nullable column would violate the barrier's
"shared fields agree on nullability" check.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class PollCursor(Base):
    __tablename__ = "poll_cursor"

    # [est.] opaque row id (cockpit convention: every model carries a `new_id()` PK
    # that leaks nothing and never collides across a fixture replay).
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Grounded: the poll is per-CIK (scaffold WP3; dossier data_flow "Watchlist
    # tickers map to CIKs"). One cursor per CIK -> UNIQUE natural key. Non-null and
    # `String` to match `cik` on the Company/Filing contracts (barrier consistency).
    cik: Mapped[str] = mapped_column(String, unique=True, index=True)

    # Grounded (Inv 11 / WP3 "cursor dedup by accession"): the last accession already
    # ingested for this CIK — the forward high-water mark the poller resumes past.
    # NULLABLE: a freshly-seeded cursor for a never-polled CIK has no prior accession.
    # Named `last_accession_no` (not `accession_no`) so its nullability does not
    # collide with Filing.accession_no (non-null unique) under the barrier check.
    last_accession_no: Mapped[str | None] = mapped_column(String, nullable=True)

    # [est.] restart/observability: when the poller last advanced this cursor. Lets a
    # restarted run confirm "survives restart" and reason about staleness. Nullable
    # until the first successful poll writes it.
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # [est.] cockpit convention: creation / last-mutation timestamps on a mutable
    # state row. `updated_at` advances every time the cursor moves forward.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
