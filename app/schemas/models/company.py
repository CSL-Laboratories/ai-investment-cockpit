"""WP1a — the ``Company`` stored contract: the watchlist ticker↔CIK mapping.

One row per watchlist company: the anchor that binds a human-facing ticker to the SEC
Central Index Key (CIK) every downstream ingest keys on. The dossier's data flow opens
with "Watchlist tickers map to CIKs" and the submissions/companyfacts APIs are addressed
by CIK, so this row is the head of the reference chain
``Company.cik -> Filing.cik -> Delta -> Citation``.

Authority (fields are FIXED by the scaffold/dossier; nothing invented):
- docs/build-scaffold-ultracode.md WP1a: "``Company`` (ticker↔CIK)" — the two load-bearing
  fields are ``ticker`` and ``cik``.
- Same doc, WP1a Accept barrier: ``cik`` is a shared field that must "agree on type/
  nullability across all schemas" — so it is a non-null ``String`` here and everywhere.
- Dossier entry ``edgar-filing-delta-morning-brew`` (build_reports.py) data_flow[0]:
  "Watchlist tickers map to CIKs"; architecture[0] polls "for watchlist tickers".
- Invariant 1 (citation chain keys): ``cik`` is the join key the whole chain resolves on.

Fields beyond ``ticker``/``cik`` are marked ``[est.]`` — they are not in the scaffold's
one-line contract, only supported by the dossier's surrounding architecture, and are kept
minimal.

Field conventions (shared across contracts, cockpit pattern-source): opaque ``String``
primary key; timestamps are ``DateTime(timezone=True)``; ``ticker`` is an uppercase US
ticker; ``cik`` is the SEC CIK as a zero-padded 10-digit string. Auto-registered on
``Base.metadata`` by ``load_all()`` — this file never edits ``__init__.py``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from schemas.models.base import Base


class Company(Base):
    __tablename__ = "company"

    # Opaque string primary key (house convention across every contract).
    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Uppercase US ticker the watchlist tracks. Unique: one row per company.
    # (scaffold "ticker↔CIK"; dossier data_flow "Watchlist tickers map to CIKs")
    ticker: Mapped[str] = mapped_column(String, unique=True, index=True)

    # SEC Central Index Key — the key every ingest resolves on (submissions/companyfacts
    # APIs are addressed by CIK). Stored as a zero-padded 10-digit string. UNIQUE and
    # NON-NULL: the barrier shared field, and the head of the Inv-1 citation chain.
    # (scaffold WP1a "ticker↔CIK" + barrier `cik`; dossier data_flow[0])
    cik: Mapped[str] = mapped_column(String, unique=True, index=True)

    # [est.] Registrant display name (dossier architecture: "parser extracts filing
    # metadata"); convenience for rendered briefs, not in the scaffold's one-line contract.
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    # [est.] Watchlist-membership toggle so the 10→25 scale (deferred item 8) and dropping
    # a company are config flips, not row deletes. Not in the scaffold contract.
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # [est.] Row-creation time (present on every model, cockpit convention).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
