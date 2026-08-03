"""WP4 — shared value objects for the deterministic, LLM-free parse layer.

These are the immutable results the ``parse/`` segmenters hand to the ``delta/`` engine.
Every parsed unit carries **byte offsets back into the raw filing blob** (the citation
chain's anchor, Invariant 1): a downstream ``Delta`` is born with those offsets, and WP5's
resolver re-reads the raw store at exactly ``raw[byte_start:byte_end]`` and must reproduce
the stored snippet byte-for-byte.

Nothing here imports an LLM / agent-orchestration module and nothing spawns a child process
(Invariant 2). A malformed filing that cannot be segmented raises :class:`ParseError`; the
caller turns that into a quarantined ``Filing`` row rather than a partial, silently-derived
one (Invariant 12, fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class ParseError(ValueError):
    """A filing could not be deterministically segmented (fail-closed, Invariant 12).

    The caller MUST NOT derive rows from a filing that raised this — it quarantines the
    ``Filing`` instead. Raised only for structural failure (e.g. no recognizable items),
    never for an ordinary "nothing changed" outcome.
    """


@dataclass(frozen=True)
class TextRun:
    """One contiguous run of character data between two HTML tags, with its raw byte span.

    ``text`` is the *cleaned* form (entities unescaped, whitespace collapsed) used for
    diffing. The *verbatim* citation bytes are always ``raw[byte_start:byte_end]`` — a run
    sits between two tags, so that slice contains NO interior markup and round-trips exactly
    (the fixtures are pure ASCII, so a byte offset equals a character offset).
    """

    byte_start: int
    byte_end: int
    text: str


@dataclass(frozen=True)
class ParsedUnit:
    """A segmented section (10-K/10-Q item) or 8-K item, byte-anchored into the raw blob.

    ``unit_id`` is the stable, format-independent key the text differ matches on across two
    filings of the same form (e.g. ``"part2_item1a"`` for Risk Factors, ``"item_5.07"`` for
    an 8-K vote result). ``byte_start``/``byte_end`` bound the whole unit in the raw filing;
    ``runs`` are the individual text runs inside it (each independently byte-anchored) that
    the differ compares.
    """

    unit_id: str
    kind: str  # "section" (10-K/10-Q) | "item" (8-K)
    title: str
    byte_start: int
    byte_end: int
    runs: tuple[TextRun, ...]
    part: str | None = None  # "I" / "II" for 10-K/10-Q; None for 8-K
    item: str | None = None  # "2", "1A", "5.07", ...

    @property
    def text(self) -> str:
        """Cleaned, whitespace-joined body text of the unit (for diffing / scoring)."""
        return " ".join(r.text for r in self.runs if r.text)


@dataclass(frozen=True)
class ParsedFiling:
    """The deterministic segmentation of one raw filing document.

    ``raw`` is the exact bytes fetched into the write-once raw store (keyed by
    ``accession``); every offset in every ``unit``/``run`` indexes into it. ``filename`` is
    the primary-document name used to build the EDGAR archive URL for citations.
    """

    cik: str
    accession: str
    form_type: str  # "10-K" | "10-Q" | "8-K"
    filename: str
    raw: bytes
    units: tuple[ParsedUnit, ...]

    @property
    def unit_map(self) -> dict[str, ParsedUnit]:
        """``unit_id`` -> unit. Insertion order preserved (deterministic iteration)."""
        return {u.unit_id: u for u in self.units}


@dataclass(frozen=True)
class ParsedFact:
    """One normalized XBRL fact flattened from a ``companyfacts`` payload.

    ``companyfacts`` is a per-CIK aggregate that is NOT stored in the raw store, so a
    ``ParsedFact`` carries no offsets of its own — the numeric delta that uses it anchors its
    citation to the *filing's* inline XBRL (see :class:`InlineFact`), which IS in the raw
    store keyed by accession.
    """

    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    period_end: date
    accession: str
    cik: str
    period_start: date | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form: str | None = None
    filed: date | None = None
    frame: str | None = None
    label: str | None = None

    @property
    def duration_days(self) -> int:
        """Length of the reporting period in days (0 for an instant/balance fact)."""
        if self.period_start is None:
            return 0
        return (self.period_end - self.period_start).days

    @property
    def period_key(self) -> tuple[str | None, str]:
        """(start, end) ISO strings — the exact-period identity used to detect restatements."""
        return (
            self.period_start.isoformat() if self.period_start else None,
            self.period_end.isoformat(),
        )


@dataclass(frozen=True)
class InlineFact:
    """One inline-XBRL fact located INSIDE a filing's raw HTML, with its raw byte span.

    This is the citation anchor for a numeric delta (Invariant 1): the ``<ix:nonFraction>``
    element at ``[byte_start, byte_end)`` in the raw filing whose reported value equals the
    ``companyfacts`` value for the primary period. ``value`` is reconstructed as
    ``displayed x 10**scale x sign`` so it can be matched exactly against a
    :class:`ParsedFact`.
    """

    concept: str
    unit_ref: str
    context_id: str
    value: Decimal
    scale: int
    sign: int
    byte_start: int
    byte_end: int
    period_start: date | None = None
    period_end: date | None = None
    instant: date | None = None
    has_segment: bool = False
    has_scenario: bool = False  # <xbrli:scenario> dimensional member -> not consolidated either
