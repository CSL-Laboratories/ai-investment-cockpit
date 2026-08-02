"""WP1a — the ``Delta`` stored contract: one computed change extracted from a filing.

A ``Delta`` is the deterministic, LLM-free unit the whole product is built on. WP4's
``delta/`` engine mints one row per material change it finds when it diffs a new filing
against the prior same-form filing for that CIK — a section-aware text change or a
period-matched numeric XBRL change. Each row is *born* carrying the citation anchor:
the source accession number, the EDGAR archive URL, the byte offsets into the raw filing
blob, and the verbatim snippet at those offsets. WP5's ``cite/resolver.py`` later re-reads
the raw store at ``(raw_ref, snippet_start_offset, snippet_end_offset)`` and must reproduce
``snippet`` byte-for-byte; a mismatch blocks the whole brief (Inv 1, citation completeness).

Authority (every field cites the scaffold or is marked ``[est.]``):
- ``docs/build-scaffold-ultracode.md`` WP1a: "``Delta`` (kind text-section|numeric; source
  refs = accession + EDGAR URL + byte offsets + verbatim snippet; materiality score)".
- ``docs/build-scaffold-ultracode.md`` WP4: "``Delta`` rows born with accession + URL +
  offsets + verbatim snippet"; property test requires "every Delta has non-empty accession,
  URL, offset, snippet".
- Invariant 1 (citation completeness): the resolver reproduces ``snippet`` byte-for-byte
  from the raw store at the recorded offsets.
- Invariant 5 (raw/derived separation): "every derived row carries a ``raw_ref`` that
  resolves; no derived table stores raw document bodies". ``Delta`` is a derived row, so it
  carries ``raw_ref`` (a pointer into the write-once raw store) — never a raw document body.
  ``snippet`` is a short verbatim excerpt at the recorded offsets, not a stored body.

Barrier / shared-field conventions (must agree across every WP1a contract):
- ``delta_id`` is this contract's opaque ``String`` primary key; ``Citation.delta_id`` is the
  matching foreign-key reference (same type/nullability).
- ``accession_no`` is a ``String`` matching ``Filing.accession_no`` (unique *there*, a
  non-unique reference *here* — one filing yields many deltas).
- ``cik`` is a ``String`` matching ``Company``/``Filing``.
- ``raw_ref`` is a non-null ``String`` (Inv 5), matching every other derived row.

Auto-registered on ``Base.metadata`` by ``schemas.models.load_all`` (pkgutil scan).
This file adds ONLY the ``Delta`` model and imports the shared ``Base``; it does not touch
``schemas/models/__init__.py`` or any other contract. Pattern-sourced from the cockpit
``schemas/models/market_event.py`` / ``order.py`` (re-implemented, not copied).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base

# The two Delta kinds — single source of truth (scaffold WP1a: "kind text-section|numeric").
# text-section = a section-aware text diff (risk factors / MD&A / 8-K item); a CHECK
# constraint enforces membership so an unknown kind can never be persisted.
DELTA_KINDS = ("text-section", "numeric")


class Delta(Base):
    __tablename__ = "delta"

    __table_args__ = (
        # kind is one of the two enumerated values (scaffold WP1a).
        CheckConstraint(
            "kind IN ('text-section', 'numeric')",
            name="ck_delta_kind",
        ),
        # Byte offsets into the raw blob are a valid [start, end) span (Inv 1 anchor).
        CheckConstraint(
            "snippet_start_offset >= 0 AND snippet_end_offset >= snippet_start_offset",
            name="ck_delta_offsets",
        ),
    )

    # Opaque primary key; also the FK target for Citation.delta_id (barrier shared field).
    delta_id: Mapped[str] = mapped_column(String, primary_key=True)

    # kind: "text-section" | "numeric" (scaffold WP1a). Enforced by ck_delta_kind above.
    kind: Mapped[str] = mapped_column(String, index=True)

    # --- Source refs / citation anchor (scaffold WP1a + WP4; Inv 1) --------------------
    # accession_no of the source filing (scaffold: "accession"). Matches Filing.accession_no
    # in type; non-unique here because one filing produces many deltas.
    accession_no: Mapped[str] = mapped_column(String, index=True)

    # EDGAR archive URL for the source filing (scaffold: "EDGAR URL").
    edgar_url: Mapped[str] = mapped_column(String)

    # Byte offsets into the raw filing blob (scaffold: "byte offsets"). [start, end) half-open;
    # the WP5 resolver re-reads the raw store at exactly this span (Inv 1).
    snippet_start_offset: Mapped[int] = mapped_column(Integer)
    snippet_end_offset: Mapped[int] = mapped_column(Integer)

    # Verbatim snippet extracted at the offsets above (scaffold: "verbatim snippet").
    # A short excerpt — NOT a raw document body (Inv 5); the resolver reproduces it exactly.
    snippet: Mapped[str] = mapped_column(String)

    # Pointer into the write-once raw store the offsets are relative to (Inv 5). Non-null:
    # every derived row carries a resolvable raw_ref (barrier shared field).
    raw_ref: Mapped[str] = mapped_column(String)

    # Threshold-based materiality ranking score (scaffold WP1a: "materiality score";
    # WP4: "threshold-based materiality ranking"). Numeric so Decimal thresholds compare
    # exactly and round-trip without float drift.
    materiality_score: Mapped[Decimal] = mapped_column(Numeric(12, 6))

    # --- [est.] fields: load-bearing for the stated WP4 behavior, beyond the fixed list ---
    # [est.] cik of the registrant. WP4 diffs "vs the prior same-form filing for that CIK",
    # so the delta carries its CIK for grouping; barrier shared field (matches Company/Filing).
    cik: Mapped[str] = mapped_column(String, index=True)

    # [est.] locator for the changed unit: section id (risk factors / MD&A), 8-K item code,
    # or XBRL concept tag. WP4 parse "records raw_ref + byte offsets"; WP5 cite builder stores
    # a "section/fact ref". Nullable: not every delta needs a symbolic locator beyond offsets.
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    # [est.] the two compared XBRL values for a numeric delta (WP4: "period-matched numeric
    # XBRL deltas"). Null for text-section deltas, where the change lives in the snippet.
    prior_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)

    # [est.] row-creation time (present on every cockpit model; provenance). Timezone-aware.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
