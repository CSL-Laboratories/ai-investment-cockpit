"""WP1a stored contract: ``XbrlFact`` — one normalized XBRL data point.

A single standardized financial fact pulled from the SEC ``companyfacts`` API and
normalized for the delta engine. The dossier (slug ``edgar-filing-delta-morning-brew``)
grounds this table:

  * data_flow: "Companyfacts API pulls standardized XBRL metrics."
  * architecture: "A parser extracts filing metadata, XBRL facts, document sections ...".
  * architecture / delta engine: numeric deltas over XBRL facts, "period-matched numeric
    XBRL deltas" (scaffold WP4 ``delta/xbrl_delta.py``).

The scaffold names ``XbrlFact`` in the WP1a contract list but — unlike Filing/Delta/Brief/
AuditEntry — does not enumerate its columns, so each column below is grounded in the SEC
``companyfacts`` JSON shape (a dossier ``sources`` entry:
https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json). A ``companyfacts`` fact
carries: taxonomy (``us-gaap``/``dei``), concept tag, unit (``USD``/``shares``/...), a
numeric ``val``, an ``accn`` (accession), ``fy``/``fp`` (fiscal year/period), ``form``,
``filed``, a period (``start`` for durations / ``end`` = instant), and an optional
``frame``. Structural columns map those keys; modeling choices (surrogate PK, column
types, the idempotency unique constraint) are marked ``[est.]``.

Invariants in scope:
  * Inv 1 (citation chain keys): ``accession_no`` + ``cik`` are the shared citation-chain
    keys this row contributes; the WP1a barrier check requires their type/nullability to
    agree with the other contracts.
  * Inv 5 (raw/derived separation): a derived row carries a ``raw_ref`` that resolves to
    the write-once raw store — this table never stores a raw document body.

Pattern-sourced from the cockpit ``schemas/models/`` (one file per contract on a shared
``Base``; re-implemented, not copied). Imports ``Base`` from the package ``__init__`` —
does NOT re-declare it and does NOT touch any other contract's file.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core.ids import new_id
from schemas.models.base import Base


class XbrlFact(Base):
    """One normalized XBRL fact from the SEC ``companyfacts`` API (LLM-free, derived)."""

    __tablename__ = "xbrl_fact"
    __table_args__ = (
        # [est.] Inv 11 (idempotent ingestion): a companyfacts fact is uniquely pinned by
        # its filing + concept + unit + period; replaying a feed must not duplicate it.
        # BUG-095: a plain UniqueConstraint over nullable ``period_start`` never deduped
        # INSTANT facts — SQLite (and the SQL standard) treats NULLs as distinct, so the
        # commonest fact shape (balance-sheet instants, NULL start) inserted a duplicate
        # row on every replay. A functional unique INDEX coalesces the NULL to a sentinel
        # date so instant facts share one identity. (Latent until fact persistence is
        # wired; fixed before it ships looking idempotent.)
        Index(
            "uq_xbrl_fact_identity",
            "accession_no",
            "taxonomy",
            "concept",
            "unit",
            text("coalesce(period_start, '0001-01-01')"),
            "period_end",
            unique=True,
        ),
    )

    # --- identity -------------------------------------------------------------------
    # [est.] surrogate opaque PK (cockpit per-row id pattern) — leaks nothing, never
    # collides across a fixture replay.
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("xbrlfact")
    )

    # --- citation-chain keys (Inv 1; barrier-checked shared fields) -----------------
    # cik: companyfacts is keyed by CIK; stored as String to preserve the zero-padded
    # 10-digit form (dossier data_flow: "Watchlist tickers map to CIKs.").
    cik: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # accession_no: the companyfacts fact's ``accn`` — the shared citation key that ties
    # this fact back to its Filing (scaffold: Filing.accession_no is unique).
    accession_no: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # --- the normalized fact (companyfacts JSON shape) ------------------------------
    # taxonomy: companyfacts top-level namespace, e.g. "us-gaap" / "dei".
    taxonomy: Mapped[str] = mapped_column(String, nullable=False)
    # concept: the XBRL tag, e.g. "RevenueFromContractWithCustomerExcludingAssessedTax".
    concept: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # unit: companyfacts units key, e.g. "USD" / "shares" / "USD/shares".
    unit: Mapped[str] = mapped_column(String, nullable=False)
    # value: the fact's ``val``. [est.] Numeric(28, 6) — Decimal round-trips exactly and
    # spans large monetary integers plus per-share decimals; the delta engine subtracts
    # these deterministically (Inv 2).
    value: Mapped[object] = mapped_column(Numeric(28, 6), nullable=False)

    # --- period + provenance (companyfacts per-fact metadata) -----------------------
    # period_start: companyfacts ``start`` — present for duration facts, NULL for an
    # instant fact (which carries only ``end``).
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    # period_end: companyfacts ``end`` — the instant date, or the duration's close.
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # fiscal_year / fiscal_period: companyfacts ``fy`` / ``fp`` ("FY", "Q1"...).
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String, nullable=True)
    # form: the form the fact was reported on (companyfacts ``form``, e.g. "10-K").
    form: Mapped[str | None] = mapped_column(String, nullable=True)
    # filed: companyfacts ``filed`` — the filing date of the source document.
    filed: Mapped[date | None] = mapped_column(Date, nullable=True)
    # frame: companyfacts ``frame`` — the calendar frame tag (e.g. "CY2023Q4I"); optional.
    frame: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- raw/derived separation (Inv 5) ---------------------------------------------
    # raw_ref resolves to the write-once raw store (the captured companyfacts payload);
    # this derived row never holds a raw document body. Matches Filing.raw_ref
    # (String, non-nullable) for the barrier check.
    raw_ref: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return (
            f"XbrlFact(id={self.id!r}, cik={self.cik!r}, concept={self.concept!r}, "
            f"unit={self.unit!r}, period_end={self.period_end!r}, value={self.value!r})"
        )
