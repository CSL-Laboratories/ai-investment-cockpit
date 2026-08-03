"""WP4 golden-fixture tests — hand-verified delta sets over the real filing pairs.

Separate test-author deliverable (playbook 9). Driven by two sources:
  1. the scaffold's WP4 Accept line (docs/build-scaffold-ultracode.md WP4), and
  2. the byte-verified ground truth in docs/fixture-ground-truth-draft.md.

Shape (per the task spec):
  * NVDA 10-Q pair + AAPL 10-Q pair -> before/after DELTAS (>=2 registrant formats).
  * NVDA 8-K pair (5.02 / 5.07) -> PER-FILING EXTRACTION (two unrelated events, not a diff).
  * For each candidate delta bullet: the engine produces a Delta whose verbatim snippet,
    tag-stripped/entity-decoded, CONTAINS the ground-truth quote, for the correct accession;
    numeric bullets additionally match the cited companyfacts prior/current values.
  * "Deliberately NOT material" items are NOT flagged as focused material deltas.
  * "Call-only context" items NEVER appear in any delta snippet.

Offline & hermetic: recorded fixtures only (NO live sec.gov), NO LLM. The engine surface is
pinned in app/tests/_wp4_support.py (the single interface-coupling point); this file asserts
BEHAVIOR against the ground truth, never the implementer's source.
"""
from __future__ import annotations

import pytest

from tests.filings import _wp4_support as S

ALL_PAIRS = list(S.PAIR_SPECS)


# --------------------------------------------------------------------------------------
# One engine run per pair, cached for the session (each pair's raw store lives under a
# caller-owned temp dir, never the real data/).
# --------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def deltas_by_pair(tmp_path_factory) -> dict[str, list[S.DeltaView]]:
    out: dict[str, list[S.DeltaView]] = {}
    for pair in ALL_PAIRS:
        root = tmp_path_factory.mktemp(f"raw_{pair}")
        out[pair] = S.compute_pair(pair, root)
    return out


@pytest.fixture(scope="session")
def all_deltas(deltas_by_pair) -> list[S.DeltaView]:
    return [d for ds in deltas_by_pair.values() for d in ds]


# --------------------------------------------------------------------------------------
# Ground-truth self-check — proves the golden data itself is sound BEFORE it judges the
# engine (a substring assertion is only meaningful if the quote really is in the fixture).
# This never touches the engine; it certifies the test corpus.
# --------------------------------------------------------------------------------------
def test_ground_truth_quotes_are_substrings_of_their_fixture():
    misses = [
        e.id for e in S.TEXT_DELTAS
        if e.quote not in S.normalized_fixture(e.fixture_key)
    ]
    assert misses == [], f"ground-truth quotes not found in their fixture: {misses}"


def test_quarantine_phrases_truly_absent_from_every_fixture():
    # If a "call-only" phrase actually appeared in a filing, quarantining it would be wrong.
    leaks = []
    for phrase in S.QUARANTINE_ABSENT_PHRASES:
        for key in S.META:
            if phrase in S.normalized_fixture(key):
                leaks.append(f"{phrase!r} in {key}")
    assert leaks == [], f"quarantine premise broken (phrase present in a fixture): {leaks}"


def test_required_numeric_values_match_captured_companyfacts():
    # Certify the numeric ground truth against companyfacts.json independent of the engine.
    from decimal import Decimal
    problems = []
    for e in S.NUMERIC_DELTAS_REQUIRED:
        cur_meta = S.META[e.newer_key]
        prior_key = S.PAIR_SPECS[e.pair][1]
        assert prior_key is not None
        cur_facts = {(f.concept): f for f in S.xbrl_facts_for(cur_meta.accession, cur_meta.cik)}
        prior_facts = {(f.concept): f for f in
                       S.xbrl_facts_for(S.META[prior_key].accession, cur_meta.cik)}
        cf = cur_facts.get(e.concept)
        pf = prior_facts.get(e.concept)
        if cf is None or Decimal(str(cf.value)) != e.current_value:
            problems.append(f"{e.id}: current {e.concept} != {e.current_value}")
        if pf is None or Decimal(str(pf.value)) != e.prior_value:
            problems.append(f"{e.id}: prior {e.concept} != {e.prior_value}")
    assert problems == [], problems


# --------------------------------------------------------------------------------------
# TEXT deltas — every candidate bullet must yield a Delta whose normalized snippet contains
# the ground-truth quote, anchored to the correct accession. Parametrized so a MISS names the
# exact bullet that was dropped (fail loudly, one id per failure).
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("exp", S.TEXT_DELTAS, ids=lambda e: e.id)
def test_text_delta_is_produced(exp: S.TextExpectation, deltas_by_pair):
    deltas = deltas_by_pair[exp.pair]
    hits = [
        d for d in deltas
        if str(d.accession_no) == exp.accession and exp.quote in d.norm_snippet
    ]
    if hits:
        return
    # Diagnose: was the quote matched but on the wrong accession? (a citation-anchor bug)
    wrong_acc = [str(d.accession_no) for d in deltas if exp.quote in d.norm_snippet]
    assert hits, (
        f"[{exp.id}] no Delta in pair {exp.pair} cites accession {exp.accession} with a "
        f"snippet containing the hand-verified quote:\n  {exp.quote[:120]!r}\n"
        + (f"(quote WAS found under accession(s) {wrong_acc} — wrong citation anchor)"
           if wrong_acc else
           "(quote not found in ANY delta snippet for this pair — the change was missed)")
    )


# --------------------------------------------------------------------------------------
# NUMERIC deltas — the unambiguously-material XBRL changes must appear as numeric Deltas with
# the exact captured companyfacts prior/current values, cited to the newer filing.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("exp", S.NUMERIC_DELTAS_REQUIRED, ids=lambda e: e.id)
def test_numeric_delta_is_produced_with_correct_values(exp: S.NumericExpectation,
                                                        deltas_by_pair):
    deltas = deltas_by_pair[exp.pair]
    numeric = [d for d in deltas
               if d.prior_value is not None and d.current_value is not None]
    assert numeric, (
        f"[{exp.id}] the engine produced NO numeric deltas for pair {exp.pair}; a "
        f"period-matched XBRL delta for {exp.concept} "
        f"({exp.prior_value} -> {exp.current_value}) was expected."
    )
    value_match = [
        d for d in numeric
        if d.prior_value == exp.prior_value and d.current_value == exp.current_value
    ]
    assert value_match, (
        f"[{exp.id}] no numeric Delta carries the captured companyfacts values "
        f"prior={exp.prior_value} current={exp.current_value} for {exp.concept}. "
        f"Numeric deltas present: "
        + ", ".join(f"({d.prior_value}->{d.current_value})" for d in numeric[:12])
        + (" ..." if len(numeric) > 12 else "")
    )
    # The numeric delta must cite the newer filing (the change is 'born' on the new filing).
    assert any(str(d.accession_no) == exp.accession for d in value_match), (
        f"[{exp.id}] the numeric delta was produced but not cited to the newer accession "
        f"{exp.accession}; got {sorted({str(d.accession_no) for d in value_match})}."
    )


@pytest.mark.parametrize("exp", S.NUMERIC_DELTAS_REQUIRED, ids=lambda e: e.id)
def test_numeric_delta_is_not_half_fabricated(exp: S.NumericExpectation, deltas_by_pair):
    # Anti-fabrication: a numeric delta that reports the correct current_value must NOT pair it
    # with a wrong prior_value (or vice versa) — a real citation must never wear a wrong number.
    deltas = deltas_by_pair[exp.pair]
    bad = [
        (d.prior_value, d.current_value) for d in deltas
        if d.prior_value is not None and d.current_value is not None
        and (
            (d.current_value == exp.current_value and d.prior_value != exp.prior_value)
            or (d.prior_value == exp.prior_value and d.current_value != exp.current_value)
        )
    ]
    assert not bad, (
        f"[{exp.id}] a numeric delta pairs a correct value with a wrong counterpart "
        f"(expected {exp.prior_value}->{exp.current_value}); saw {bad}"
    )


# --------------------------------------------------------------------------------------
# NOT-material — unchanged boilerplate must not surface as a FOCUSED delta (a delta whose
# snippet is essentially that text). Degrades safely if the engine emits whole-section
# snippets (then no focused delta exists, and the assertion passes without a false failure).
# --------------------------------------------------------------------------------------
_FOCUS_MARGIN = 600  # chars beyond the quote length that still counts as a 'focused' delta


@pytest.mark.parametrize("nm", S.NOT_MATERIAL_FOCUSED, ids=lambda n: n.id)
def test_not_material_item_is_not_a_focused_delta(nm: S.NotMaterial, deltas_by_pair):
    deltas = deltas_by_pair[nm.pair]
    focused_hits = [
        d for d in deltas
        if nm.quote in d.norm_snippet and len(d.norm_snippet) <= len(nm.quote) + _FOCUS_MARGIN
    ]
    assert not focused_hits, (
        f"[{nm.id}] an unchanged / not-material item surfaced as a focused delta "
        f"({len(focused_hits)} hit(s)); it must not be flagged as material.\n"
        f"  quote: {nm.quote[:120]!r}"
    )


# --------------------------------------------------------------------------------------
# QUARANTINE — call-only context (never in the filing text) must NEVER appear in any delta
# snippet, across every pair. Bulletproof: these phrases are absent from all fixtures.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", S.QUARANTINE_ABSENT_PHRASES)
def test_call_only_context_never_appears_as_a_delta(phrase: str, all_deltas):
    hits = [
        f"{d.accession_no}@[{d.start}:{d.end}]"
        for d in all_deltas
        if phrase in (d.snippet or "") or phrase in d.norm_snippet
    ]
    assert not hits, (
        f"call-only phrase {phrase!r} (never present in any filing) leaked into a delta "
        f"snippet: {hits}"
    )


# --------------------------------------------------------------------------------------
# Coverage guards — the golden run actually exercised all three fixture shapes, so a
# vacuously-empty engine result cannot pass the parametrized suite silently.
# --------------------------------------------------------------------------------------
def test_every_pair_produced_at_least_one_delta(deltas_by_pair):
    empty = [p for p, ds in deltas_by_pair.items() if not ds]
    assert not empty, (
        f"pairs produced ZERO deltas (engine miss or interface gap): {empty}"
    )


def test_eightk_extraction_is_per_filing_not_cross_pair(deltas_by_pair):
    # Each 8-K's deltas cite only that 8-K's accession (per-filing extraction, not a diff).
    for pair in ("NVDA8K_A", "NVDA8K_B"):
        expected_acc = S.META[S.PAIR_SPECS[pair][0]].accession
        stray = sorted({str(d.accession_no) for d in deltas_by_pair[pair]
                        if str(d.accession_no) != expected_acc})
        assert not stray, (
            f"{pair}: extraction deltas cite foreign accessions {stray}; expected only "
            f"{expected_acc}"
        )
