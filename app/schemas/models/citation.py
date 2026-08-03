"""WP1a stored contract: ``Citation`` — one stored citation per computed ``Delta``.

A ``Citation`` is the load-bearing link in the deterministic citation chain
(claim -> Delta -> Citation -> raw filing bytes at accession+offset). WP5's
``cite/builder.py`` mints exactly one per Delta and ``cite/resolver.py``
mechanically re-reads the raw blob at the recorded offsets to reproduce the
snippet byte-for-byte.

Fields are FIXED by ``docs/build-scaffold-ultracode.md`` WP1a — every column below
cites the scaffold (or the dossier entry, slug ``edgar-filing-delta-morning-brew``).
Nothing here is invented; the one convenience column beyond the fixed set is marked
``[est.]``.

Invariants in scope:
  * Inv 1 (citation chain keys): ``citation_id`` PK, ``delta_id`` (every Citation
    resolves to a computed Delta), ``accession_no`` + ``edgar_url`` + verbatim
    ``snippet`` + recorded ``byte_start``/``byte_end`` — the mechanical resolver
    reproduces the snippet from the raw store at those offsets.
    (scaffold Invariant 1; WP5 builder/resolver lines.)
  * Inv 5 (raw_ref, not raw bodies): a resolvable ``raw_ref`` pointer + a small
    verbatim ``snippet`` excerpt — never the raw document body. (scaffold Invariant 5.)

Pattern-sourced from the cockpit ``schemas/models`` one-file-per-contract layout
(re-implemented, not copied). Imports the shared ``Base`` from this package;
does not touch any sibling contract file.

Chain keys (``delta_id``, ``accession_no``) are soft references — indexed columns,
not DB-level ForeignKeys — so this contract's table is independently
``create_all``-able in isolation (its per-contract round-trip) before the sibling
Delta/Filing tables exist; the chain is enforced mechanically by WP5's resolver +
claim gate (scaffold Invariant 1 is phrased as logical resolution, not a DB
constraint). The G1a barrier loads every model together and checks shared fields
agree on type/nullability.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class Citation(Base):
    """A stored citation: one per computed Delta, mechanically re-verifiable."""

    __tablename__ = "citation"

    # PK — opaque id (core.ids.new_id("citation")). Shared barrier field `citation_id`;
    # WP5 guards against `citation_id` reuse. (scaffold WP1a barrier list; WP5.)
    citation_id: Mapped[str] = mapped_column(String, primary_key=True)

    # Every Citation resolves to a computed Delta (Inv 1). Shared barrier field
    # `delta_id`. Soft reference (indexed, no DB FK — see module docstring).
    delta_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    # Accession number of the source filing. Shared barrier field `accession_no`
    # (Filing.accession_no is UNIQUE); consistent String type. Soft reference.
    # (Inv 1 "accession number"; WP5 builder.)
    accession_no: Mapped[str] = mapped_column(String, index=True, nullable=False)

    # EDGAR archive URL for the source filing. (Inv 1 "EDGAR URL"; WP5 builder
    # "EDGAR archive URL".)
    edgar_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Filing date of the source filing. (WP5 builder "filing date".)
    filing_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Section ref (10-K/10-Q/8-K text delta) OR XBRL fact ref (numeric delta).
    # (WP5 builder "section/fact ref".)
    source_ref: Mapped[str] = mapped_column(String, nullable=False)

    # Verbatim snippet — a small excerpt, NOT the raw document body (Inv 5).
    # The resolver reproduces exactly this from the raw store. (Inv 1; WP5 builder.)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)

    # Recorded byte offsets into the raw blob — the resolver re-reads [start, end)
    # to reproduce `snippet` byte-for-byte. (Inv 1 "recorded offsets"; WP5 resolver.)
    byte_start: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_end: Mapped[int] = mapped_column(Integer, nullable=False)

    # Resolvable pointer into the write-once raw store (data/raw/<accession>/).
    # Every derived row carries a raw_ref that resolves (Inv 5). Shared barrier
    # field `raw_ref`.
    raw_ref: Mapped[str] = mapped_column(String, nullable=False)

    # [est.] Convenience audit timestamp (not in the fixed WP1a field set; nullable
    # so it never constrains the contract). Timezone-aware to match core.clock UTC.
    created_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Citation(citation_id={self.citation_id!r}, delta_id={self.delta_id!r}, "
            f"accession_no={self.accession_no!r}, bytes=[{self.byte_start}:{self.byte_end}))"
        )
