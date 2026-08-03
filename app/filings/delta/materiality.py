"""WP4 — threshold-based materiality scoring + ``Delta`` row assembly (LLM-free).

Turns the raw :class:`TextDelta` / :class:`NumericDelta` findings into schema ``Delta`` rows
"born with accession + URL + offsets + verbatim snippet + materiality score" (scaffold WP4),
ranked by a deterministic materiality heuristic and optionally filtered by a threshold / top-N.

Scoring is intentionally simple, bounded, and comparable across both delta kinds so a single
ranking is meaningful:

* **Numeric** — magnitude of the percent change (saturating at 1.0 for a >= 100% move), with a
  small bump for headline concepts (revenue, net income, assets, ...) so a material headline
  line surfaces even on a modest percent move, and a larger bump for a restatement (a revised
  past figure is inherently material).
* **Text** — the fraction of the section's runs that changed, weighted up for the sections the
  dossier calls out (Risk Factors, MD&A) and for a brand-new section.

Determinism (Invariant 2): the score is a pure function of the delta; ``delta_id`` is derived
from the delta's content (never a random UUID), and rows are sorted by a total key — so
identical inputs and a fixed clock produce byte-identical ``Delta`` sets across runs. No LLM
and no child-process spawn. ``created_at`` is taken from an injected
:class:`~core.clock.Clock` so tests can freeze it.
"""
from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal

from core.clock import Clock, default_clock
from filings.delta.models import NumericDelta, TextDelta
from filings.delta.text_diff import diff_filings
from filings.delta.xbrl_delta import diff_companyfacts
from filings.edgar.fetch import archive_document_url
from filings.parse.models import ParsedFiling
from schemas.models.delta import Delta

# Headline financial concepts — a change here is material even at a modest percent move.
_HEADLINE_CONCEPTS = frozenset(
    {
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "NetIncomeLoss",
        "OperatingIncomeLoss",
        "GrossProfit",
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "Assets",
        "Liabilities",
        "StockholdersEquity",
        "CashAndCashEquivalentsAtCarryingValue",
        "EarningsPerShareBasic",
        "EarningsPerShareDiluted",
        "ResearchAndDevelopmentExpense",
        "OperatingExpenses",
    }
)

# Sections the dossier architecture calls out as the material narrative comparisons, keyed by
# (part, item) so Part I Item 2 (MD&A) is boosted while Part II Item 2 (Unregistered Sales) is
# not. Risk Factors is Part II Item 1A.
_HEADLINE_SECTIONS = frozenset({("II", "1A"), ("I", "2")})

_SCORE_Q = Decimal("0.000001")  # Numeric(12,6) quantum


def _q(value: Decimal) -> Decimal:
    """Clamp to a non-negative Numeric(12,6)-safe score."""
    if value < 0:
        value = -value
    if value > Decimal("999999"):
        value = Decimal("999999")
    return value.quantize(_SCORE_Q, rounding=ROUND_HALF_UP)


# Absolute-magnitude normalizer: bit-length of the integer dollar/share change, scaled so a
# ~$1e12 change saturates at 1.0. Integer bit_length keeps this fully deterministic (no float),
# and it lets a large-dollar headline move outrank a tiny-dollar line item with a big percent.
_MAG_BITS_FULL = Decimal(40)  # 2**40 ~ 1.1e12


def score_numeric(nd: NumericDelta) -> Decimal:
    """Materiality score for a numeric delta (bounded, deterministic).

    Blends the *percent* change (saturating at a >= 100% move) with the *absolute* magnitude of
    the change, so neither a huge-percent tiny-dollar line item nor a small-percent huge-dollar
    headline move alone dominates the ranking. Headline concepts and restatements get a bump.
    """
    if nd.pct_change is None:
        pct_norm = Decimal("1")  # prior was zero, now non-zero -> full-scale percent move
    else:
        mag = abs(nd.pct_change)
        pct_norm = mag if mag < Decimal("1") else Decimal("1")

    change_int = int(abs(nd.abs_change))
    mag_norm = Decimal(change_int.bit_length()) / _MAG_BITS_FULL
    if mag_norm > Decimal("1"):
        mag_norm = Decimal("1")

    base = Decimal("0.5") * pct_norm + Decimal("0.5") * mag_norm
    if nd.concept in _HEADLINE_CONCEPTS:
        base += Decimal("0.25")
    if nd.is_restatement:
        base += Decimal("0.35")
    return _q(base)


def score_text(td: TextDelta) -> Decimal:
    """Materiality score for a text-section delta (bounded, deterministic)."""
    ratio = (
        Decimal(td.changed_runs) / Decimal(td.total_runs)
        if td.total_runs
        else Decimal("0")
    )
    weight = Decimal("1.0")
    if (td.part, td.item) in _HEADLINE_SECTIONS:
        weight = Decimal("1.5")
    base = ratio * weight
    if td.change_type == "new_section":
        base += Decimal("0.5")
    return _q(base)


def _delta_id(kind: str, cik: str, accession: str, source_ref: str, start: int, end: int) -> str:
    payload = f"{kind}|{cik}|{accession}|{source_ref}|{start}|{end}".encode()
    return "delta_" + hashlib.sha256(payload).hexdigest()[:24]


def _text_to_row(td: TextDelta, *, cik: str, filename: str, created_at) -> Delta:
    return Delta(
        delta_id=_delta_id(
            "text-section", cik, td.raw_ref, td.source_ref, td.byte_start, td.byte_end
        ),
        kind="text-section",
        accession_no=td.raw_ref,
        edgar_url=archive_document_url(cik, td.raw_ref, filename),
        snippet_start_offset=td.byte_start,
        snippet_end_offset=td.byte_end,
        snippet=td.snippet,
        raw_ref=td.raw_ref,
        materiality_score=score_text(td),
        cik=cik,
        source_ref=td.source_ref,
        prior_value=None,
        current_value=None,
        created_at=created_at,
    )


def _numeric_to_row(nd: NumericDelta, *, cik: str, filename: str, created_at) -> Delta:
    return Delta(
        delta_id=_delta_id("numeric", cik, nd.raw_ref, nd.source_ref, nd.byte_start, nd.byte_end),
        kind="numeric",
        accession_no=nd.raw_ref,
        edgar_url=archive_document_url(cik, nd.raw_ref, filename),
        snippet_start_offset=nd.byte_start,
        snippet_end_offset=nd.byte_end,
        snippet=nd.snippet,
        raw_ref=nd.raw_ref,
        materiality_score=score_numeric(nd),
        cik=cik,
        source_ref=nd.source_ref,
        prior_value=nd.prior_value,
        current_value=nd.current_value,
        created_at=created_at,
    )


def _rank_key(row: Delta) -> tuple:
    # Highest materiality first; then a stable total tiebreak so ordering is byte-identical.
    return (
        -row.materiality_score,
        row.kind,
        row.source_ref or "",
        row.snippet_start_offset,
        row.snippet_end_offset,
    )


def build_deltas(
    text_deltas: list[TextDelta],
    numeric_deltas: list[NumericDelta],
    *,
    cik: str,
    filename: str,
    clock: Clock = default_clock,
    threshold: Decimal | None = None,
    top_n: int | None = None,
) -> list[Delta]:
    """Mint schema ``Delta`` rows from computed findings, ranked by materiality.

    :param threshold: if given, drop rows scoring strictly below it (default: keep all).
    :param top_n: if given, keep only the top-N after ranking.
    """
    created_at = clock.now()
    rows: list[Delta] = [
        _text_to_row(td, cik=cik, filename=filename, created_at=created_at)
        for td in text_deltas
    ]
    rows += [
        _numeric_to_row(nd, cik=cik, filename=filename, created_at=created_at)
        for nd in numeric_deltas
    ]
    rows.sort(key=_rank_key)
    if threshold is not None:
        rows = [r for r in rows if r.materiality_score >= threshold]
    if top_n is not None:
        rows = rows[:top_n]
    return rows


def compute_filing_deltas(
    current: ParsedFiling,
    prior: ParsedFiling,
    companyfacts: dict | None = None,
    *,
    clock: Clock = default_clock,
    threshold: Decimal | None = None,
    top_n: int | None = None,
) -> list[Delta]:
    """End-to-end deterministic delta computation for one filing vs its prior same-form filing.

    Runs the section-aware text diff and (when ``companyfacts`` is supplied) the period-matched
    numeric XBRL diff, then mints ranked ``Delta`` rows. Every returned row carries a resolvable
    ``raw_ref`` + byte offsets + verbatim snippet (Invariant 1); none is derived through an LLM
    (Invariant 2).
    """
    text_deltas = diff_filings(current, prior)
    numeric_deltas: list[NumericDelta] = []
    if companyfacts is not None:
        numeric_deltas = diff_companyfacts(
            companyfacts,
            current_accession=current.accession,
            prior_accession=prior.accession,
            current_raw=current.raw,
            cik=current.cik,
        )
    return build_deltas(
        text_deltas,
        numeric_deltas,
        cik=current.cik,
        filename=current.filename,
        clock=clock,
        threshold=threshold,
        top_n=top_n,
    )
