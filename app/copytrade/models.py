"""RW-16 — the pure domain types for congressional copy-trading.

PURE DATA. No I/O, no SQLAlchemy, no LLM. ``ingest/congress/`` produces
:class:`Disclosure` values off the wire; ``copytrade/engine.py`` turns them into
:class:`CopyIntent` values; ``workflows/copytrade_orders.py`` is the only module that
takes an intent anywhere near the order path — and it goes through
risk-pass -> mode -> approval -> ``OrderGateway`` like everything else (Invariant 1).

WHY A SEPARATE DOMAIN TYPE AND NOT JUST THE PARSED ROW
------------------------------------------------------
A periodic transaction report (PTR) is an UNTRUSTED DOCUMENT. It is typed by a
congressional staffer, sometimes handwritten and scanned, and its asset-description
field is free text that ends up in front of a model downstream. Every free-text field
that survives into this dataclass has already been through
``ingest.normalize.escape_untrusted`` (SEC-02/M3), and the structured fields are
pattern-validated at parse time. So the rule for anything consuming a ``Disclosure`` is:
the STRUCTURED fields are trustworthy shapes (not trustworthy *claims*), and
``asset_description`` is display/prompt text that is never parsed for meaning a second
time.

THE AMOUNT IS A RANGE, NOT A NUMBER. Congress discloses a bucket ("$1,001 - $15,000"),
never a fill. Anything that wants to size off it must go through ``copytrade/sizing.py``,
which is explicit about turning a bucket into a conviction weight rather than pretending
the midpoint is a quantity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# ---- transaction types as they appear on House/Senate PTRs ---------------------------
# The forms use single letters; the Senate spells some of them out. Parsers normalize to
# these values and REFUSE anything else (an unknown code is not guessed — see engine.py's
# ``unknown_tx_type`` drop reason).
TX_PURCHASE = "purchase"
TX_SALE = "sale"
TX_SALE_PARTIAL = "sale_partial"
TX_EXCHANGE = "exchange"
KNOWN_TX_TYPES = frozenset({TX_PURCHASE, TX_SALE, TX_SALE_PARTIAL, TX_EXCHANGE})

# ---- asset classes we distinguish ----------------------------------------------------
# ``option_call`` / ``option_put`` are deliberately SEPARATE from ``equity`` because the
# translation policy for them is not "the same thing, smaller" — it is a different rule
# (see copytrade/options.py). ``other`` covers bonds, funds we cannot map to a ticker,
# real estate, crypto and everything else; it is never traded.
ASSET_EQUITY = "equity"
ASSET_OPTION_CALL = "option_call"
ASSET_OPTION_PUT = "option_put"
ASSET_OTHER = "other"

CHAMBER_HOUSE = "house"
CHAMBER_SENATE = "senate"


@dataclass(frozen=True)
class AmountRange:
    """A disclosed dollar bucket. ``high is None`` means the open-ended top tier."""

    low: Decimal
    high: Decimal | None

    def __post_init__(self) -> None:
        if self.low < 0:
            raise ValueError("disclosed amount low bound cannot be negative")
        if self.high is not None and self.high < self.low:
            raise ValueError("disclosed amount high bound is below its low bound")

    @property
    def label(self) -> str:
        if self.high is None:
            return f"over ${self.low:,.0f}"
        return f"${self.low:,.0f} - ${self.high:,.0f}"


@dataclass(frozen=True)
class Disclosure:
    """One transaction line off one periodic transaction report.

    ``doc_id`` + ``line_no`` is the natural key: a single PTR carries many transactions
    and re-filing an amendment re-issues the whole document, so keying on the document
    alone would collapse a filer's whole day into one row.
    """

    # ---- provenance (never model-authored, always from the filing) -------------------
    chamber: str                          # house | senate
    doc_id: str                           # the clerk/eFD document id
    line_no: int                          # 0-based transaction index within the document
    filer_slug: str                       # canonical "last-first" key used by the follow list
    filer_display: str                    # escaped display name

    # ---- the transaction ------------------------------------------------------------
    tx_date: date                         # when the trade happened
    filed_date: date                      # when it was disclosed (STOCK Act: <= 45 days later)
    tx_type: str                          # one of KNOWN_TX_TYPES
    asset_type: str                       # ASSET_* above
    amount: AmountRange

    # ---- the instrument -------------------------------------------------------------
    symbol: str | None                    # uppercase US ticker, or None when unmappable
    asset_description: str                # ESCAPED free text, display/prompt only
    option_expiry: date | None = None     # parsed from the description when present
    option_strike: Decimal | None = None

    # ---- audit binding ---------------------------------------------------------------
    raw_ref: str = ""                     # locator into the raw store (Invariant 8)
    content_hash: str = ""                # binds the raw filing bytes into the audit chain

    @property
    def key(self) -> str:
        """Stable dedup / idempotency key. Content-free by design: an amended filing gets a
        new ``doc_id``, so it re-enters as new evidence rather than silently mutating a row
        the risk engine may already have acted on."""
        return f"{self.chamber}:{self.doc_id}:{self.line_no}"

    @property
    def disclosure_lag_days(self) -> int:
        """Days between the trade and its disclosure. This is the number that makes copying
        a politician structurally different from copying a fund: it is routinely 30-45."""
        return (self.filed_date - self.tx_date).days

    def age_days(self, today: date) -> int:
        """Days between the TRADE and now — the number the staleness gate uses.

        Deliberately measured from ``tx_date``, not ``filed_date``: what decays is the
        information, and the information is as old as the trade. Measuring from the filing
        would make a maximally-late disclosure look maximally fresh, which is exactly
        backwards.
        """
        return (today - self.tx_date).days

    @property
    def is_option(self) -> bool:
        return self.asset_type in (ASSET_OPTION_CALL, ASSET_OPTION_PUT)


@dataclass(frozen=True)
class CopyIntent:
    """A proposed mirror of a disclosed trade, sized for THIS account.

    Not an order. Not a proposal yet. This is the deterministic output of the copy engine,
    and it still has to survive schema validation, the risk engine, the mode ladder and a
    human (or an armed auto rung) before anything reaches a broker.

    ``notional`` is a dollar amount derived from the reader's own portfolio — never from
    the disclosed bucket, which describes a stranger's balance sheet and not yours.
    """

    symbol: str
    side: str                             # buy | sell
    notional: Decimal
    reason: str                           # short deterministic explanation, no model text
    source_key: str                       # the Disclosure.key that produced it
    source_refs: tuple[str, ...]          # raw_ref locators for the citation gate
    filer_display: str
    tx_date: date
    filed_date: date
    age_days: int
    amount_label: str
    # Set when the intent came out of the OPTIONS translation rather than a 1:1 equity copy.
    translated_from: str | None = None    # e.g. "option_call" — None for a plain equity copy
    sizing_notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Skip:
    """A disclosure the engine deliberately did NOT act on, and why.

    Every non-emitting outcome is COUNTED and NAMED — the same rule the ingest layer follows.
    A copy engine that silently drops two thirds of its input is indistinguishable from one
    that is broken, and "no trades today" must be a positive statement rather than an absence.
    """

    key: str
    symbol: str | None
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class CopyPlan:
    """The whole deterministic output of one planning pass."""

    intents: tuple[CopyIntent, ...] = ()
    skips: tuple[Skip, ...] = ()

    def summary(self) -> str:
        by_reason: dict[str, int] = {}
        for s in self.skips:
            by_reason[s.reason] = by_reason.get(s.reason, 0) + 1
        detail = ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items()))
        return (f"copytrade: {len(self.intents)} intent(s), {len(self.skips)} skip(s)"
                + (f" [{detail}]" if detail else ""))
