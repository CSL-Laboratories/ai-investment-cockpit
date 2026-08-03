"""WP3 — ``companyfacts.py``: the data.sec.gov companyfacts (XBRL) reader, per CIK.

The SEC ``companyfacts`` endpoint returns every reported XBRL fact for a company, keyed by
taxonomy -> concept -> unit -> a list of period-tagged values::

    {"cik": 1045810, "entityName": "NVIDIA CORP",
     "facts": {"us-gaap": {"Revenues": {"label": "...", "units":
        {"USD": [{"start": "...", "end": "...", "val": 1, "accn": "...",
                  "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "...",
                  "frame": "CY2026Q1"}, ...]}}}}}

WP3's job is only to **fetch** this per CIK (through the single sec.gov client — Invariant
4) and offer a light, deterministic structural iterator that flattens the nested dict into
one row per reported value. It does NOT compute deltas or persist ``XbrlFact`` rows — the
period-matched numeric XBRL delta and the ``XbrlFact`` mapping are WP4's deterministic,
LLM-free core. This module is pure structure-walking: no network except the injected
fetcher, no LLM, no subprocess.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from filings.edgar.fetch import SecFetcher, companyfacts_url, pad_cik

log = logging.getLogger("filings.edgar.companyfacts")


@dataclass(frozen=True)
class XbrlFactRecord:
    """One reported XBRL value, flattened from the companyfacts nesting.

    Keyed by ``accn`` (accession) so a downstream delta can join a fact back to the filing
    that reported it — the citation-chain anchor for numeric claims.
    """

    taxonomy: str  # e.g. "us-gaap", "dei"
    concept: str  # e.g. "Revenues"
    label: str | None
    unit: str  # e.g. "USD", "shares"
    val: float | int | None
    accession: str | None  # "accn" — the reporting filing's accession number
    fiscal_year: int | None  # "fy"
    fiscal_period: str | None  # "fp" — FY / Q1 / Q2 / ...
    form: str | None  # "form" — 10-K / 10-Q / 8-K
    start: str | None  # period start (ISO date string, as reported)
    end: str | None  # period end (ISO date string, as reported)
    filed: str | None  # filing date (ISO date string, as reported)
    frame: str | None  # "frame" — e.g. "CY2026Q1" (present only on some facts)


def iter_facts(companyfacts: dict) -> Iterator[XbrlFactRecord]:
    """Flatten the nested companyfacts dict into one :class:`XbrlFactRecord` per value.

    Deterministic and total: a malformed concept / unit / row is skipped (per-item
    isolation), never crashing the walk. Iteration order follows the JSON's insertion
    order, so repeated walks over identical input yield identical sequences.
    """
    facts = companyfacts.get("facts")
    if not isinstance(facts, dict):
        return
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, dict):
            continue
        for concept, body in concepts.items():
            if not isinstance(body, dict):
                continue
            label = body.get("label")
            units = body.get("units")
            if not isinstance(units, dict):
                continue
            for unit, rows in units.items():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    yield XbrlFactRecord(
                        taxonomy=taxonomy,
                        concept=concept,
                        label=label,
                        unit=unit,
                        val=row.get("val"),
                        accession=row.get("accn"),
                        fiscal_year=row.get("fy"),
                        fiscal_period=row.get("fp"),
                        form=row.get("form"),
                        start=row.get("start"),
                        end=row.get("end"),
                        filed=row.get("filed"),
                        frame=row.get("frame"),
                    )


def facts_for_accession(companyfacts: dict, accession: str) -> list[XbrlFactRecord]:
    """All flattened facts reported by a specific filing (join key for numeric citations)."""
    return [r for r in iter_facts(companyfacts) if r.accession == accession]


def fetch_companyfacts(fetcher: SecFetcher, cik: str) -> dict:
    """Fetch the companyfacts JSON for one CIK through the single sec.gov client.

    Routes the read through :class:`SecFetcher` (rate-limited, UA-enforcing, distinct-error
    surfacing); this module opens no connection of its own (Invariant 4).
    """
    return fetcher.get_json(companyfacts_url(pad_cik(cik)))
