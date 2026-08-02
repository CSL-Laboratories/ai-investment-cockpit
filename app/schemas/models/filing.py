"""WP1a stored contract: ``Filing`` — one SEC filing detected for the watchlist.

A ``Filing`` is the head of the citation chain (Invariant 1): every downstream
``Delta`` / ``Citation`` resolves back to a filing by ``accession_no``. The row stores
ONLY metadata + a ``raw_ref`` pointer into the write-once raw store — never the raw
document body (Invariant 5: raw/derived separation). Idempotent ingestion (Invariant 11)
rides on ``accession_no`` being ``UNIQUE``.

Field provenance (every field is cited; nothing invented):

* ``id``               — surrogate primary key so other contracts (Delta, XbrlFact)
                         reference a stable filing row; scaffold WP1a calls this a
                         "stored ORM row". [est.] (standard ORM surrogate key)
* ``cik``              — dossier data_flow ("Watchlist tickers map to CIKs";
                         "Submissions API finds new filings and accession numbers") +
                         WP1a barrier shared-field list (``cik`` agrees across schemas).
* ``accession_no``     — scaffold WP1a: "``accession_no`` unique". UNIQUE, non-null.
* ``form_type``        — scaffold WP1a: enum accepts **all five** of
                         10-K/10-Q/8-K/Form 4/13F (Form 4/13F deferred but additive).
* ``filing_date``      — dossier architecture/citation builder: stores
                         "source URLs, accession numbers, filing dates".
* ``detection_ts``     — scaffold WP1a: "``detection_ts``" — starts the dossier
                         15-minute metric clock. tz-aware UTC (``core.clock``).
* ``raw_ref``          — scaffold WP1a: "``raw_ref``"; Invariant 5 — resolves into the
                         write-once raw store (``data/raw/<accession>/``).

Pattern-sourced from the cockpit ``schemas/models/`` one-file-per-contract layout
(re-implemented, not copied). Imports the shared ``Base`` from this package; does not
edit ``__init__.py`` or any sibling contract file.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.ids import new_id
from schemas.models import Base


class FormType(enum.StrEnum):
    """SEC form types the ``Filing`` table accepts — all five per scaffold WP1a.

    Form 4 / 13F *processing* is deferred (scaffold deferred item 1), but the enum and
    the poller accept them from day one so the deferral is additive, not a rewrite.
    """

    FORM_10K = "10-K"
    FORM_10Q = "10-Q"
    FORM_8K = "8-K"
    FORM_4 = "Form 4"
    FORM_13F = "13F"


def _new_filing_id() -> str:
    return new_id("filing")


class Filing(Base):
    """A single SEC filing detected for a watchlist company (metadata + raw pointer).

    Deliberately stores NO raw document body — only ``raw_ref`` points at the write-once
    raw store (Invariant 5). ``accession_no`` is UNIQUE so replaying a feed never ingests
    the same filing twice (Invariant 11).
    """

    __tablename__ = "filing"

    # Surrogate PK — stable handle for downstream Delta/XbrlFact rows. [est.]
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=_new_filing_id)

    # SEC Central Index Key (zero-padded string preserves leading zeros, e.g. "0000320193").
    cik: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # SEC accession number, e.g. "0000320193-24-000123" — UNIQUE (scaffold WP1a; Inv 11).
    accession_no: Mapped[str] = mapped_column(
        String(25), nullable=False, unique=True, index=True
    )

    # Form type — all five accepted; stored as the literal SEC label ("10-K", "Form 4", ...).
    form_type: Mapped[FormType] = mapped_column(
        SAEnum(
            FormType,
            name="form_type_enum",
            values_callable=lambda e: [m.value for m in e],
            validate_strings=True,
        ),
        nullable=False,
    )

    # Date the filing was filed with the SEC (dossier: "filing dates").
    filing_date: Mapped[date] = mapped_column(Date, nullable=False)

    # When our poller first detected the filing — starts the 15-min metric clock (Inv-adjacent).
    detection_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Pointer into the write-once raw store (data/raw/<accession>/) — Invariant 5.
    raw_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    # Primary-document filename from the SEC submissions API (e.g. "nvda-20260426.htm"),
    # captured at ingest so the citation's EDGAR archive URL resolves to the REAL document
    # rather than a synthesized name. Load-bearing for the dossier citation architecture,
    # which stores "source URLs, accession numbers, filing dates" — a working source URL's
    # last path segment IS this filename (the accession alone does not name the document).
    # Nullable [est.]: additive to the fixed WP1a field set and to rows ingested before this
    # column existed — the delta engine falls back to a synthesized name when it is absent
    # (see app/delta/engine.py ``_primary_filename``). The SEC submissions feed already
    # supplies it (``filings.recent.primaryDocument``); WP3's poller now persists it here.
    primary_doc: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Filing(id={self.id!r}, cik={self.cik!r}, "
            f"accession_no={self.accession_no!r}, form_type={self.form_type!r})"
        )
