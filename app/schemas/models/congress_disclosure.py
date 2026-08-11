"""RW-16 stored contract: ``CongressDisclosure`` — one transaction line off one PTR.

WHY THE EVIDENCE IS STORED AT ALL (Invariant 8: raw kept separate from derived)
-------------------------------------------------------------------------------
The copy engine (``copytrade/engine.py``) is a pure function of disclosures + policy + portfolio.
That is only USEFUL if the disclosures survive the fetch: a policy change, a sizing bug or an
adversarial review round has to be re-runnable against the evidence WITHOUT re-fetching a small
government file server that publishes each PTR once, in a daily batch, behind a window that
closes. This table is that evidence. It stores the parsed row and a ``raw_ref`` pointer + a
``content_hash`` that chain back to the fetched document — never the document body itself.

Nothing derived lives here. No intent, no notional, no "did we copy it". A row is what Congress
published, and the engine's opinion about it is a separate concern with a separate lifetime.

WHY THE PRIMARY KEY IS ``Disclosure.key`` AND NOT A SURROGATE
--------------------------------------------------------------
``chamber:doc_id:line_no`` is the natural key of a disclosed transaction, and making it the
PRIMARY KEY is what makes replay safe (Invariant 11): the DATABASE refuses a second copy rather
than trusting a caller to have deduped. A surrogate id would let a re-fetched window insert the
whole batch again, and this feed re-fetches its window BY CONSTRUCTION (the cursor is a
filed-date high-water mark and filings arrive in daily batches).

The key is deliberately CONTENT-FREE. An amended filing gets a new ``doc_id`` from the clerk, so
it enters as NEW evidence rather than silently mutating a row the copy engine may already have
acted on. A row here is therefore append-only in practice: nothing in this module updates one.

Field provenance — every column is a field of ``copytrade.models.Disclosure``, which is itself
built only from the filing (never model-authored). Conventions follow the sibling contracts
(``filing.py``, ``market_event.py``): dates are ``Date``, money is ``Numeric(18, 2)``, prices are
``Numeric(18, 4)``, hashes are hex strings, and every model carries ``created_at``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import Date, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from copytrade.models import AmountRange, Disclosure
from schemas.models.base import Base

log = logging.getLogger("cockpit.ingest.congress")

# Bound on one ``load_disclosures`` call. A cap that must be typed is not a cap: an unbounded
# read of a table that grows with every congressional filing day would eventually be the thing
# that makes a scheduled copy cycle time out, and a slow cycle is a cycle that silently stops.
DEFAULT_LOAD_LIMIT = 500


def _utc_now() -> datetime:
    """Row-creation stamp. Real UTC by default so the column is never null-by-omission."""
    return datetime.now(timezone.utc)


class CongressDisclosure(Base):
    """One disclosed transaction line, keyed by ``chamber:doc_id:line_no``.

    Stores the AMOUNT as its disclosed BUCKET (``amount_low`` / ``amount_high``), never a
    midpoint. Congress discloses a range and never a fill; collapsing it to a number here would
    manufacture a quantity the filing does not contain, and every consumer downstream would then
    be sizing off a fabrication. ``amount_high IS NULL`` is the open-ended top tier ("over
    $50,000,000") and is a real, meaningful value — not a missing one.
    """

    __tablename__ = "congress_disclosure"

    # PK = ``Disclosure.key``. See the module docstring: the natural key IS the idempotency key.
    key: Mapped[str] = mapped_column(String(160), primary_key=True)

    # ---- provenance -----------------------------------------------------------------------
    # house | senate. Kept as its own column (not only inside ``key``) so a chamber-scoped
    # query never has to LIKE-match a composite string.
    chamber: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # The clerk/eFD document id, ASCII-pattern-gated at parse time (ptr_parse._DOC_ID) because
    # it is interpolated into a URL path and into ``raw_ref``.
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 0-based transaction index within the document. One PTR carries many transactions.
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Canonical "last-first" slug the follow list matches on (copytrade.policy.normalize_filer).
    filer_slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # ESCAPED display name (SEC-02, applied at the parse boundary in ptr_parse.build_disclosure).
    filer_display: Mapped[str] = mapped_column(String(200), nullable=False)

    # ---- the transaction -------------------------------------------------------------------
    # When the trade happened. Indexed because it is what ``age_days`` — and therefore the
    # staleness gate and the deterministic load order — is measured from.
    tx_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # When it was disclosed. Indexed because it is the ingest cursor's high-water mark and the
    # column ``load_disclosures(since=...)`` filters on.
    filed_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # One of copytrade.models.KNOWN_TX_TYPES — an unknown code is refused at parse time and
    # never lands here, so this column has no "unknown" member by construction.
    tx_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # ASSET_EQUITY | ASSET_OPTION_CALL | ASSET_OPTION_PUT | ASSET_OTHER.
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # The disclosed bucket's LOW bound, always present (the tiers start at $1,001).
    amount_low: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    # The bucket's HIGH bound. NULLABLE, and the null MEANS something: it is the open-ended top
    # tier. A default ceiling here would quietly turn "over $50,000,000" into a finite number.
    amount_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    # ---- the instrument ---------------------------------------------------------------------
    # Uppercase US ticker, or NULL when the asset maps to no ticker (a bond, a fund, real estate,
    # crypto). Nullable and indexed: null is the common, expected case on a PTR and the engine
    # never trades one, while the index is what makes a per-symbol lookback cheap.
    symbol: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)

    # ESCAPED free text (SEC-02). Display/prompt only — never parsed for meaning a second time.
    asset_description: Mapped[str] = mapped_column(String(500), nullable=False)

    # Option terms, parsed from the description when present. Both NULL on a non-option row, and
    # ptr_parse clears them on a non-option row so ``is_option`` and these can never disagree.
    option_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    option_strike: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # ---- audit binding ------------------------------------------------------------------------
    # Locator of the fetched document (Invariant 8). The bytes live outside the DB.
    raw_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # SEC-13: binds this row to (document digest + the untouched row text). An operator re-fetches
    # the primary document at ``raw_ref``, re-hashes, and can prove the row came from those bytes.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)

    # Row-creation time (present on every model).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (f"CongressDisclosure(key={self.key!r}, symbol={self.symbol!r}, "
                f"tx_type={self.tx_type!r}, filed_date={self.filed_date!r})")


class LoadedDisclosures(list):
    """The ``load_disclosures`` return: domain rows PLUS what could not be returned.

    A plain ``list`` cannot say "and 3 stored rows were unreadable", so a corrupted row would
    shrink the result silently and the copy engine would plan against a quietly smaller universe.
    Same device, same reason as ``ingest.finra_short_interest.FinraBatch``: it IS a list (every
    caller keeps working) and it carries its own non-emitting outcomes.

    ``truncated`` and ``window_start`` describe the COVERAGE of the batch, and they exist because
    ``copytrade.engine._contested_sides`` depends on it. That pass reads a symbol's contest off
    the whole batch, ahead of every time-varying gate, so that an opposing filing cannot age out
    and leave a one-sided BUY of a name a followed member exited — but the guarantee only holds
    while the opposing filing is IN THE BATCH. A bare row cap does not guarantee that: order by
    ``tx_date DESC LIMIT 500`` evicts an older sale while keeping the newer buy that contests it,
    and the cycle then emits the uncontested buy with nothing counting it. So the coverage is
    reported rather than assumed, and ``workflows.copytrade_orders.run_copy_cycle`` refuses a
    cycle whose batch cannot support the check (``disclosure_window_truncated`` /
    ``disclosure_window_too_narrow``).
    """

    def __init__(self, rows=(), *, unreadable=(), truncated=False, window_start=None):
        super().__init__(rows)
        self.unreadable: list[tuple[str, str]] = list(unreadable)
        self.truncated: bool = bool(truncated)
        """The row cap BIT: rows matching the query were dropped. The batch is not the window."""
        self.window_start: date | None = window_start
        """The earliest ``tx_date`` this load asked for, or ``None`` for "no window requested"."""


def persist_disclosures(session_factory, disclosures, *, clock=None) -> int:
    """Store ``disclosures``, skipping any ``key`` already present. Returns rows WRITTEN.

    IDEMPOTENT ON THE PRIMARY KEY, and idempotent WITHIN one batch. Both halves are load-bearing
    and they fail differently:

      * ACROSS cycles — this feed re-fetches its window every cycle by construction (the cursor
        is a filed-date high-water mark; filings arrive in daily batches), so a re-presented row
        is the NORMAL case, not an anomaly. Pre-selecting the existing keys turns it into a
        clean no-op that reports 0 written rather than an ``IntegrityError``.
      * WITHIN one batch — two providers with overlapping windows, or one document fetched twice
        in a cycle, can put the same key in the list twice. Without the in-batch guard that is a
        flush-time ``IntegrityError`` that costs the WHOLE batch its evidence, which is exactly
        the "one bad row destroys the good ones" failure this feed cannot have.

    Mirrors ``ingest.edgar_pipeline._persist_deltas`` deliberately: same shape, same reasoning,
    so the two idempotency stories cannot drift into two different answers.

    The return value is the count ACTUALLY WRITTEN, never ``len(disclosures)``. A caller that
    reported the batch size would show steady throughput on a feed that has stored nothing new
    for a week.
    """
    from sqlalchemy import select

    rows = list(disclosures or ())
    if not rows:
        return 0
    now = clock() if clock is not None else _utc_now()
    written = 0
    with session_factory() as s, s.begin():
        existing = set(
            s.execute(
                select(CongressDisclosure.key).where(
                    CongressDisclosure.key.in_([d.key for d in rows])
                )
            ).scalars().all()
        )
        for d in rows:
            if d.key in existing:
                continue
            s.add(_to_row(d, now))
            existing.add(d.key)          # guard against duplicates WITHIN one batch too
            written += 1
    return written


def load_disclosures(session_factory, *, since: date | None = None,
                     window_start: date | None = None,
                     limit: int = DEFAULT_LOAD_LIMIT) -> LoadedDisclosures:
    """Read stored evidence back as DOMAIN objects, newest-trade-last, bounded.

    Returns ``copytrade.models.Disclosure`` values and NOT ORM rows, on purpose. The copy engine
    is a pure function over the domain types (no I/O, no session, no lazy-loading); handing it
    detached ORM instances would smuggle a database session into a module whose whole value is
    that it can be replayed and reasoned about without one.

    ``since`` filters on ``filed_date`` — the date the information became PUBLIC, which is the
    same clock the ingest cursor runs on. Filtering on ``tx_date`` instead would make a
    maximally-late disclosure invisible to a caller asking "what is new since my last run",
    because its trade date can precede that run by 45 days.

    THE LIMIT SELECTS THE NEWEST ROWS, AND THAT IS THE WHOLE POINT OF IT. The first build
    ordered ASCENDING and then applied ``LIMIT``, which selects the OLDEST ``limit`` rows: once
    the table grew past the cap the copy engine could only ever see filings that had already
    aged out of ``policy.max_age_days``, so every cycle skipped everything on staleness, no
    order was ever drafted, and nothing anywhere reported an error — the feature stops trading
    while looking perfectly healthy. A cap that hides the only rows worth reading is worse than
    no cap. The read therefore orders DESCENDING to choose the window, and the selected rows
    are reversed before they are returned.

    ORDER IS DETERMINISTIC (``tx_date``, then ``key``) so two runs over the same table plan the
    same way (Invariant 2). ``tx_date`` first because that is the axis the staleness gate cares
    about; ``key`` as the tiebreak because it is unique, so the order is total — and because it
    is total, reversing the descending window reproduces exactly the ascending order callers
    already relied on.

    ``window_start`` IS THE WINDOW THE COPY ENGINE NEEDS, AND THE CAP IS ONLY A MEMORY BOUND.
    It filters on ``tx_date`` — the axis ``policy.max_age_days`` measures — so a caller asks for
    "every filing whose TRADE is inside the staleness window" and gets it, rather than getting
    "the newest 500 rows and hope". The distinction is not cosmetic: ``copytrade.engine``'s
    contest pass reads a symbol's disagreement off the whole batch, and a row cap that evicts an
    older SALE while keeping the newer BUY that contests it hands the cycle a one-sided buy of a
    symbol a followed member exited, with nothing counting it. When the cap bites anyway the
    result says so (``truncated``), and ``run_copy_cycle`` refuses rather than planning against a
    batch it cannot trust.

    THE CAP IS CLAMPED, NEVER SKIPPED. ``limit<=0`` used to disable the ``LIMIT`` clause outright,
    so any caller forwarding an unset or zero config value — the natural spelling of "no
    override" — read the entire table, which is the unbounded read the cap exists to prevent.

    A stored row that will not round-trip into a VALID ``Disclosure`` is SKIPPED AND COUNTED on
    the result's ``unreadable`` list — never returned half-built. A ``Disclosure`` with a
    fabricated amount or a missing date would flow straight into sizing; a named absence is
    recoverable, a plausible-looking wrong row is not.
    """
    from sqlalchemy import select

    stmt = select(CongressDisclosure)
    if since is not None:
        stmt = stmt.where(CongressDisclosure.filed_date >= since)
    if window_start is not None:
        stmt = stmt.where(CongressDisclosure.tx_date >= window_start)
    # DESCENDING to pick the window (newest first), so the cap keeps the rows a copy cycle can
    # still act on; reversed below to hand callers the documented ascending order.
    stmt = stmt.order_by(CongressDisclosure.tx_date.desc(), CongressDisclosure.key.desc())
    bound = (DEFAULT_LOAD_LIMIT if limit is None
             else max(1, min(int(limit), DEFAULT_LOAD_LIMIT)))
    # One row MORE than the bound, purely to learn whether the cap bit. Cheaper than a COUNT(*)
    # and exact.
    with session_factory() as s:
        rows = list(s.execute(stmt.limit(bound + 1)).scalars().all())
    truncated = len(rows) > bound
    rows = rows[:bound]
    rows.reverse()

    out: list[Disclosure] = []
    unreadable: list[tuple[str, str]] = []
    for row in rows:
        try:
            out.append(_to_domain(row))
        except Exception as exc:            # noqa: BLE001 - a bad row is counted, never guessed
            unreadable.append((str(getattr(row, "key", "?")), type(exc).__name__))
            log.warning("congress_disclosure row %r is unreadable (%s); skipped, not repaired",
                        getattr(row, "key", "?"), type(exc).__name__)
    if unreadable:
        log.warning("congress_disclosure: %d stored row(s) could not be round-tripped into a "
                    "Disclosure and were EXCLUDED from this load", len(unreadable))
    if truncated:
        log.warning("congress_disclosure: the %d-row cap TRUNCATED this load; the batch does not "
                    "cover the requested window and a copy cycle will refuse it", bound)
    return LoadedDisclosures(out, unreadable=unreadable, truncated=truncated,
                             window_start=window_start)


# ------------------------------------------------------------------------------- conversions

def _to_row(d: Disclosure, now: datetime) -> CongressDisclosure:
    """Domain -> ORM. Explicit field-by-field; no ``**asdict`` shortcut.

    The shortcut would silently start persisting any field a future ``Disclosure`` grows —
    including one that has NOT been through ``escape_untrusted`` — and this table's whole job is
    to be the thing a citation resolves against.
    """
    return CongressDisclosure(
        key=d.key,
        chamber=d.chamber,
        doc_id=d.doc_id,
        line_no=int(d.line_no),
        filer_slug=d.filer_slug,
        filer_display=d.filer_display,
        tx_date=d.tx_date,
        filed_date=d.filed_date,
        tx_type=d.tx_type,
        asset_type=d.asset_type,
        amount_low=d.amount.low,
        amount_high=d.amount.high,          # None IS the open-ended top tier — never defaulted
        symbol=d.symbol,
        asset_description=d.asset_description,
        option_expiry=d.option_expiry,
        option_strike=d.option_strike,
        raw_ref=d.raw_ref or "",
        content_hash=d.content_hash or "",
        created_at=now,
    )


def _to_domain(row: CongressDisclosure) -> Disclosure:
    """ORM -> domain. RAISES on anything it cannot rebuild faithfully.

    Raising is the point: ``load_disclosures`` catches it and counts the row out. Every branch
    here would otherwise have to invent a value (a zero amount, today's date, an empty chamber)
    and hand the copy engine something that looks like a filing and is not one.
    """
    return Disclosure(
        chamber=_require_str(row.chamber, "chamber"),
        doc_id=_require_str(row.doc_id, "doc_id"),
        line_no=int(row.line_no),
        filer_slug=row.filer_slug or "",
        filer_display=row.filer_display or "",
        tx_date=_require_date(row.tx_date, "tx_date"),
        filed_date=_require_date(row.filed_date, "filed_date"),
        tx_type=_require_str(row.tx_type, "tx_type"),
        asset_type=_require_str(row.asset_type, "asset_type"),
        # AmountRange validates its own bounds (low >= 0, high >= low) and RAISES otherwise, so a
        # corrupted bucket is refused by the domain type rather than reconstructed around.
        amount=AmountRange(_require_decimal(row.amount_low, "amount_low"),
                           _optional_decimal(row.amount_high, "amount_high")),
        symbol=row.symbol or None,
        asset_description=row.asset_description or "",
        option_expiry=row.option_expiry if isinstance(row.option_expiry, date) else None,
        option_strike=_optional_decimal(row.option_strike, "option_strike"),
        raw_ref=row.raw_ref or "",
        content_hash=row.content_hash or "",
    )


def _require_str(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"congress_disclosure.{field} is empty or not a string: {value!r}")
    return value


def _require_date(value, field: str) -> date:
    # ``datetime`` is a ``date`` subclass; a stored datetime would compare and serialize
    # differently from every other row's date, so narrow it rather than letting it through.
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise ValueError(f"congress_disclosure.{field} is not a date: {value!r}")
    return value


def _require_decimal(value, field: str) -> Decimal:
    result = _optional_decimal(value, field)
    if result is None:
        raise ValueError(f"congress_disclosure.{field} is required and is missing")
    return result


def _optional_decimal(value, field: str) -> Decimal | None:
    """Coerce a stored numeric to ``Decimal``, or None. Raises on an uncoercible non-null.

    SQLite has no decimal type: SQLAlchemy round-trips ``Numeric`` through a float and hands
    back a ``Decimal`` quantized to the column's scale. Accepting int/float here is therefore
    about the STORAGE layer, not about tolerating junk — a value that is neither null nor
    numeric is a corrupted row and takes the row out of the result.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):             # bool is an int subclass; it is not a money value
        raise ValueError(f"congress_disclosure.{field} is a bool, not a number")
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"congress_disclosure.{field} is not a number: {value!r}") from exc
    raise ValueError(f"congress_disclosure.{field} is not a number: {value!r}")
