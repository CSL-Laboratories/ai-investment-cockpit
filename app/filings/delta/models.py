"""WP4 — value objects the ``delta/`` engine produces before they are minted into ``Delta`` rows.

A :class:`TextDelta` (section-aware text change) and a :class:`NumericDelta` (period-matched
XBRL change) each carry the full citation anchor — ``raw_ref`` (accession), byte offsets, and
a verbatim snippet — so ``materiality.build_deltas`` can turn them into schema ``Delta`` rows
"born with accession + URL + offsets + verbatim snippet + materiality score" (scaffold WP4).

Pure data. No LLM, no subprocess (Invariant 2).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Average fiscal-quarter length in days (365 / 4). A reporting duration is classed by the
# number of quarters it spans — round(days / 91.25), computed with exact integer arithmetic
# (``(days * 8 + 365) // 730``) so the classification is fully deterministic (Invariant 2, no
# float). This cleanly separates a 13-14 week quarter (bucket 1) from a half-year (2), a
# nine-month year-to-date (3) and a full year (4), which is what makes a 90-day quarter and a
# 272-day YTD non-comparable for a period-over-period delta.
_QUARTER_DAYS_X4 = 365  # 4 * 91.25


def _quarter_span(days: int) -> int:
    """Number of fiscal quarters a reporting duration spans (round(days / 91.25))."""
    return (days * 8 + _QUARTER_DAYS_X4) // 730


@dataclass(frozen=True)
class TextDelta:
    """A section-aware text change in the current filing vs the prior same-form filing.

    ``byte_start``/``byte_end`` bound the changed text *in the current filing's raw blob*
    (``raw_ref`` = current accession); ``snippet`` is that exact byte slice. ``prior_text`` /
    ``current_text`` are the cleaned before/after strings (for scoring and prose context).
    """

    unit_id: str
    title: str
    change_type: str  # "replace" | "insert" | "new_section"
    raw_ref: str
    byte_start: int
    byte_end: int
    snippet: str
    prior_text: str
    current_text: str
    changed_runs: int
    total_runs: int
    part: str | None = None
    item: str | None = None

    @property
    def source_ref(self) -> str:
        return self.unit_id


@dataclass(frozen=True)
class NumericDelta:
    """A period-matched numeric XBRL change, cited to the value as reported in the filing.

    ``byte_start``/``byte_end`` locate the ``<ix:nonFraction>`` element reporting
    ``current_value`` inside the current filing's raw blob (``raw_ref`` = current accession);
    ``snippet`` is that verbatim element. ``is_restatement`` marks the exact-same-period case
    (the filing revised a previously reported figure) as distinct from an ordinary
    period-over-period change.
    """

    taxonomy: str
    concept: str
    unit: str
    raw_ref: str
    byte_start: int
    byte_end: int
    snippet: str
    prior_value: Decimal
    current_value: Decimal
    abs_change: Decimal
    pct_change: Decimal | None
    current_period_end: date
    prior_period_end: date
    current_period_start: date | None = None
    prior_period_start: date | None = None
    is_restatement: bool = False
    label: str | None = None

    @property
    def periods_comparable(self) -> bool:
        """True iff the prior and current facts span like-for-like reporting periods.

        A period-over-period delta is only meaningful when both sides cover a comparable-length
        period (both instant/balance facts, or two durations of the same fiscal-quarter span).
        A mismatch — e.g. a 90-day quarter differenced against a 272-day year-to-date roll-up —
        is not a like-for-like change and is suppressed upstream (Invariant 12); this property
        lets a downstream consumer re-check the spans that were recorded on the delta.
        """
        cur_inst = self.current_period_start is None
        prior_inst = self.prior_period_start is None
        if cur_inst or prior_inst:
            return cur_inst and prior_inst
        cur_days = (self.current_period_end - self.current_period_start).days
        prior_days = (self.prior_period_end - self.prior_period_start).days
        return _quarter_span(cur_days) == _quarter_span(prior_days)

    @property
    def source_ref(self) -> str:
        return f"{self.taxonomy}:{self.concept}/{self.unit}"
