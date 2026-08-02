"""WP4 regression tests for the heavy-4c adversarial-review defects (all LLM-free, offline).

Each test here FAILS on the pre-fix engine and passes on the fixed one. They are named for the
four confirmed defects so a re-review can map a red test straight back to the defect it guards:

  * FIX 1 — period-length comparability. A period-over-period numeric delta whose prior/current
    facts span non-comparable-length periods (a 90-day quarter vs a 272-day year-to-date
    roll-up) must be suppressed fail-closed (Invariant 12), while the like-for-like
    quarter-vs-quarter / balance-vs-balance golden deltas survive. Asserted on the REAL NVDA
    fixtures.
  * FIX 2 — cross-filing section identity. A Part divider must be recognized length-independently
    so the SAME logical section maps to the SAME ``unit_id`` across two filings even when one
    filing's divider run is long / list-marker-prefixed — otherwise byte-identical Part II
    sections mint false ``new_section`` deltas.
  * FIX 3 — snippet encoding vs byte offsets. A citation snippet that contains non-ASCII content
    must satisfy the Citation stored contract: ``byte_end - byte_start ==
    len(snippet.encode("utf-8"))`` and ``raw[byte_start:byte_end].decode("utf-8") == snippet``.
  * FIX 4 — duplicate-period collision. A within-accession duplicate period (same concept+unit+
    period reported twice with different values in the prior filing) must NOT mint a fabricated
    ``is_restatement`` delta; it is dropped fail-closed instead of silently last-write-wins.

Offline & hermetic: recorded fixtures + tiny in-test blobs only; NO live sec.gov, NO LLM.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from filings.delta import diff_companyfacts, diff_facts
from filings.delta.text_diff import diff_filings
from filings.parse._html import verbatim
from filings.parse.models import ParsedFact, ParseError
from filings.parse.sections_10kq import parse_10kq
from filings.parse.xbrl_facts import find_inline_anchor, iter_inline_facts, parse_contexts
from tests.filings import _wp4_support as S

# --------------------------------------------------------------------------------------
# FIX 1 — period-length comparability, asserted on the real NVDA 10-Q fixture pair.
# --------------------------------------------------------------------------------------
# These cash-flow concepts pair a 90-day quarter (current, 2026-01-26..2026-04-26) against a
# 272-day fiscal-YTD roll-up (prior, 2025-01-27..2025-10-26). A naive (concept, unit) pairing
# emits a large false period-over-period "change" for each (e.g. buybacks look like they FELL
# 46.8% when the per-quarter figure actually rose). They must be suppressed.
_MISMATCHED_PERIOD_CONCEPTS = {
    "PaymentsForRepurchaseOfCommonStock",
    "PaymentsOfDividends",
    "ShareBasedCompensation",
    "NetCashProvidedByUsedInOperatingActivities",
}

# The scaffold-required golden numeric deltas: quarter-vs-quarter (Revenues/GrossProfit/
# NetIncomeLoss) and balance-vs-balance (InventoryNet / non-marketable equity). All comparable —
# the guard must NOT drop these.
_REQUIRED_COMPARABLE_CONCEPTS = {
    "Revenues",
    "GrossProfit",
    "NetIncomeLoss",
    "InventoryNet",
    "EquitySecuritiesWithoutReadilyDeterminableFairValueAmount",
}


def _nvda_numeric_deltas():
    cf = json.loads((S.FIX / "NVDA" / "companyfacts.json").read_text(encoding="utf-8"))
    cur = S.META["NVDA_NEW"]
    prior = S.META["NVDA_OLD"]
    return diff_companyfacts(
        cf,
        current_accession=cur.accession,
        prior_accession=prior.accession,
        current_raw=cur.path.read_bytes(),
        cik=S.NVDA_CIK,
    )


def test_fix1_mismatched_period_cashflow_deltas_are_suppressed():
    deltas = _nvda_numeric_deltas()
    emitted = {d.concept for d in deltas}
    leaked = sorted(_MISMATCHED_PERIOD_CONCEPTS & emitted)
    assert not leaked, (
        "period-over-period deltas were emitted for concepts whose prior fact is a 272-day YTD "
        f"while the current fact is a 90-day quarter (non-comparable, Inv 12): {leaked}"
    )


def test_fix1_comparable_golden_numeric_deltas_survive_the_guard():
    deltas = _nvda_numeric_deltas()
    emitted = {d.concept for d in deltas}
    missing = sorted(_REQUIRED_COMPARABLE_CONCEPTS - emitted)
    assert not missing, (
        "the period-comparability guard over-suppressed like-for-like golden numeric deltas "
        f"(these are quarter-vs-quarter or balance-vs-balance and MUST survive): {missing}"
    )


def test_fix1_every_emitted_numeric_delta_spans_comparable_periods():
    # General Inv-12 guarantee: nothing the engine emits pairs non-comparable-length periods.
    deltas = _nvda_numeric_deltas()
    bad = [
        (d.concept, d.prior_period_start, d.prior_period_end,
         d.current_period_start, d.current_period_end)
        for d in deltas
        if not d.periods_comparable
    ]
    assert not bad, f"emitted numeric deltas that pair non-comparable periods: {bad[:10]}"


def test_fix1_specific_buyback_ytd_vs_quarter_pair_is_absent():
    # The exact false pair the review reproduced: buybacks prior 36,271,000,000 (272d YTD) vs
    # current 19,312,000,000 (90d quarter) => -46.8%. Must never appear as a delta.
    deltas = _nvda_numeric_deltas()
    false_pair = (Decimal(36271000000), Decimal(19312000000))
    hits = [d for d in deltas if (d.prior_value, d.current_value) == false_pair]
    assert not hits, (
        "the YTD-vs-quarter buyback false delta (36,271,000,000 -> 19,312,000,000) was emitted"
    )


# --------------------------------------------------------------------------------------
# FIX 2 — cross-filing section identity under an asymmetric-length Part divider.
# --------------------------------------------------------------------------------------
# A Part II divider that runs long in ONE filing (here the "current") but short in the other
# ("prior") must still map Risk Factors (Part II, Item 1A) and Exhibits (Part II, Item 6) to the
# SAME unit_id in both — otherwise the pre-fix len<=60 gate drops the long divider, reparents
# them to Part I, and byte-identical sections diff as false new_section deltas.
_LONG_PART_II = (
    "Part II — Other Information And Exhibits Filed With This Quarterly Report On Form 10-Q"
)
_SHORT_PART_II = "Part II — Other Information"
_RISK_TEXT = (
    "Other than the risk factors listed below, there have been no material changes from the "
    "risk factors previously described in our most recent Annual Report on Form 10-K."
)
_EXHIBIT_TEXT = "The following exhibits are filed with this report as required by Item 601."


def _mk_10q(part_ii_divider: str) -> bytes:
    parts = [
        "<html><body>",
        "<p>Part I — Financial Information</p>",
        "<p>Item 2. Management's Discussion and Analysis of Financial Condition</p>",
        "<p>Revenue increased during the quarter for the reasons discussed below.</p>",
        f"<p>{part_ii_divider}</p>",
        "<p>Item 1A. Risk Factors</p>",
        f"<p>{_RISK_TEXT}</p>",
        "<p>Item 6. Exhibits</p>",
        f"<p>{_EXHIBIT_TEXT}</p>",
        "</body></html>",
    ]
    return "".join(parts).encode("utf-8")


def _parse(raw: bytes):
    return parse_10kq(raw, cik="0000000000", accession="ACC", form_type="10-Q", filename="f.htm")


def test_fix2_long_part_divider_still_maps_to_part_ii():
    assert len(_LONG_PART_II) > 60  # the pre-fix gate would have dropped this divider
    current = _parse(_mk_10q(_LONG_PART_II))
    ids = {u.unit_id for u in current.units}
    assert "partII_item1A" in ids, (
        "Risk Factors reparented off Part II because the long Part II divider was dropped "
        f"(got unit_ids {sorted(ids)})"
    )
    assert "partII_item6" in ids, "Exhibits reparented off Part II (long divider dropped)"


def test_fix2_identical_part_ii_sections_do_not_mint_false_new_section_deltas():
    current = _parse(_mk_10q(_LONG_PART_II))   # long Part II divider
    prior = _parse(_mk_10q(_SHORT_PART_II))    # short Part II divider, identical section bodies
    # Same logical sections must share unit_ids across the two filings.
    assert {u.unit_id for u in current.units} == {u.unit_id for u in prior.units}
    deltas = diff_filings(current, prior)
    false_new = [d for d in deltas if d.change_type == "new_section"]
    assert not false_new, (
        "byte-identical Part II sections produced false new_section deltas because the two "
        f"filings assigned them different unit_ids: {[(d.unit_id, d.title) for d in false_new]}"
    )
    # And the identical Risk Factors body yields no text delta at all (it truly did not change).
    risk_deltas = [d for d in deltas if d.unit_id == "partII_item1A"]
    assert not risk_deltas, f"unchanged Risk Factors produced spurious deltas: {risk_deltas}"


# --------------------------------------------------------------------------------------
# FIX 3 — non-ASCII snippet round-trip (utf-8 byte offsets ⇔ snippet).
# --------------------------------------------------------------------------------------
# Real non-ASCII BYTES (® = C2 AE, em-dash = E2 80 94) in the raw blob, exercised through the
# actual parse + section diff, then the Citation byte-offset contract checked on the emitted
# snippet. On the pre-fix latin-1 decode, len(snippet.encode("utf-8")) exceeds the byte span.
_NONASCII_RISK = (
    "Tariffs on NVIDIA® accelerated-computing products rose—sharply—this "
    "quarter, which could adversely affect our gross margin and results of operations."
)
_PLAIN_RISK = "There have been no material changes to our previously disclosed risk factors."


def _mk_10q_riskbody(risk_text: str) -> bytes:
    parts = [
        "<html><body>",
        "<p>Part II — Other Information</p>",
        "<p>Item 1A. Risk Factors</p>",
        f"<p>{risk_text}</p>",
        "</body></html>",
    ]
    return "".join(parts).encode("utf-8")


def test_fix3_nonascii_snippet_satisfies_utf8_byte_offset_contract():
    cur_raw = _mk_10q_riskbody(_NONASCII_RISK)
    prior_raw = _mk_10q_riskbody(_PLAIN_RISK)
    assert any(b > 127 for b in cur_raw)  # the current blob really does carry non-ASCII bytes
    current = _parse(cur_raw)
    prior = _parse(prior_raw)
    deltas = diff_filings(current, prior)
    assert deltas, "expected at least one text delta for the changed Risk Factors section"

    # Every emitted delta must round-trip byte-for-byte through utf-8 (the Citation contract).
    for d in deltas:
        assert d.raw_ref == "ACC"
        span = d.byte_end - d.byte_start
        assert span == len(d.snippet.encode("utf-8")), (
            f"byte span {span} != len(snippet.encode('utf-8')) {len(d.snippet.encode('utf-8'))} "
            f"for snippet {d.snippet!r}"
        )
        assert cur_raw[d.byte_start:d.byte_end].decode("utf-8") == d.snippet

    # And at least one snippet genuinely carries the non-ASCII content (® / em-dash), so the
    # contract was exercised on multibyte characters, not only ASCII.
    assert any(any(ord(c) > 127 for c in d.snippet) for d in deltas), (
        "no emitted snippet contained the non-ASCII content — the round-trip was not exercised "
        "on multibyte characters"
    )


def test_fix3_verbatim_helper_round_trips_multibyte_bytes():
    # Direct guard on the shared snippet helper: a byte slice bounded by ASCII markup decodes to
    # a snippet whose utf-8 re-encoding is exactly those bytes.
    raw = "prefix <b>rosé café — 25°</b> suffix".encode()  # utf-8 (Python default)
    start = raw.index(b"<b>") + len(b"<b>")
    end = raw.index(b"</b>")
    snippet = verbatim(raw, start, end)
    assert end - start == len(snippet.encode("utf-8"))
    assert raw[start:end].decode("utf-8") == snippet
    assert any(ord(c) > 127 for c in snippet)  # exercised on multibyte chars


# --------------------------------------------------------------------------------------
# FIX 4 — a within-accession duplicate period must not mint a fabricated restatement.
# --------------------------------------------------------------------------------------
# One inline-XBRL anchor for the concept in the current filing, so a restatement CAN be cited
# (the pre-fix engine would emit one against a last-write-wins prior value).
_FIX4_RAW = (
    b'<html><body>'
    b'<xbrli:context id="c1"><xbrli:entity>e</xbrli:entity>'
    b'<xbrli:period><xbrli:instant>2026-04-26</xbrli:instant></xbrli:period>'
    b'</xbrli:context>'
    b'<p>Inventories were '
    b'<ix:nonFraction name="us-gaap:InventoryNet" contextRef="c1" unitRef="usd" scale="0">'
    b'100</ix:nonFraction> million.</p>'
    b'</body></html>'
)


def _inv_fact(value: str) -> ParsedFact:
    # Instant (balance-sheet) fact for a single period; the value differs per call.
    return ParsedFact(
        taxonomy="us-gaap", concept="InventoryNet", unit="USD",
        value=Decimal(value), period_end=date(2026, 4, 26), period_start=None,
        accession="ACC", cik="0000000000",
    )


def test_fix4_duplicate_period_collision_does_not_mint_a_restatement():
    current = [_inv_fact("100")]  # the value the inline anchor reports
    # Same concept+unit+period reported TWICE in the prior filing with different values.
    colliding_prior = [_inv_fact("70"), _inv_fact("80")]
    deltas = diff_facts(current, colliding_prior, current_accession="ACC", current_raw=_FIX4_RAW)
    restatements = [d for d in deltas if d.is_restatement]
    assert not restatements, (
        "a within-accession duplicate-period collision minted a fabricated is_restatement delta "
        f"(last-write-wins over the true prior): "
        f"{[(d.prior_value, d.current_value) for d in restatements]}"
    )


def test_fix4_control_single_prior_still_emits_a_restatement():
    # Control: with an unambiguous single prior of a different value, the restatement DOES fire —
    # proving the suppression above is specifically due to the collision, not a broken fixture.
    current = [_inv_fact("100")]
    single_prior = [_inv_fact("70")]
    deltas = diff_facts(current, single_prior, current_accession="ACC", current_raw=_FIX4_RAW)
    restatements = [d for d in deltas if d.is_restatement]
    assert restatements, (
        "control failed: an unambiguous restated prior value did not produce an is_restatement "
        "delta (so the collision test above would be vacuous)"
    )
    assert restatements[0].prior_value == Decimal(70)
    assert restatements[0].current_value == Decimal(100)


# ======================================================================================
# ROUND-2 defects — two REAL defects that survived round 1 (second heavy 5-skeptic pass).
# Each test below FAILS on the round-1 engine and passes on the round-2 fix.
#
#   * DEFECT A — section identity was fragile AND failed OPEN. A Part divider whose roman
#     numeral is not at the run start ("Other Information (Part II)") dropped the Part II
#     boundary, reparenting partII_* -> partI_*; byte-identical sections then minted false
#     new_section deltas while the real Part II changes were silently dropped. Fix = robust
#     position-independent Part detection PLUS a fail-CLOSED section-identity reconciliation
#     that quarantines an unmatched whole Part+item section instead of failing open (Inv 12).
#   * DEFECT B — a <xbrli:scenario>-dimensioned context (e.g. srt:RestatementAxis "As
#     Previously Reported") was treated as consolidated (only <xbrli:segment> was detected),
#     so a numeric citation could anchor a prev-reported / member cell. Fix = require a FULLY
#     non-dimensional context (neither segment nor scenario) for inline anchoring (Inv 1).
# ======================================================================================


def _parse_meta(meta) -> object:
    return parse_10kq(
        meta.path.read_bytes(), cik=meta.cik, accession=meta.accession,
        form_type="10-Q", filename=meta.filename,
    )


# ---- DEFECT A: Part-divider rendering variance + fail-closed reconciliation ----
_MOVED_PART_II = "Other Information (Part II)"       # roman NOT at the run start (was dropped)
_UNRECOGNIZED_PART_II = "Other Information"          # no "Part <roman>" token at all -> unparsable


def test_round2_defectA_moved_part_label_divider_is_recognized_and_reconciles():
    # A Part II divider whose "Part II" label sits AFTER the title must still resolve to Part II,
    # so the SAME logical section gets the SAME unit_id in both filings -> no false new_section.
    current = _parse(_mk_10q(_MOVED_PART_II))     # divider label moved to the end
    prior = _parse(_mk_10q(_SHORT_PART_II))       # ordinary "Part II — Other Information"
    ids_cur = {u.unit_id for u in current.units}
    assert {"partII_item1A", "partII_item6"} <= ids_cur, (
        "a moved-label Part II divider ('Other Information (Part II)') was not recognized; "
        f"Risk Factors / Exhibits reparented off Part II (got {sorted(ids_cur)})"
    )
    assert ids_cur == {u.unit_id for u in prior.units}  # same sections -> same ids across the pair
    deltas = diff_filings(current, prior)  # structures reconcile -> must NOT raise
    false_new = [d for d in deltas if d.change_type == "new_section"]
    assert not false_new, (
        "byte-identical sections minted false new_section deltas because the two filings "
        f"assigned them different unit_ids: {[(d.unit_id, d.title) for d in false_new]}"
    )


def test_round2_defectA_unrecognized_part_divider_fails_closed_not_open():
    # The Part II label is entirely absent from the divider, so even robust detection cannot
    # recover the boundary and Item 1A / Item 6 reparent partII_* -> partI_*. The round-1 engine
    # FAILED OPEN here (byte-identical sections mint false new_section deltas AND the real Part II
    # changes are silently dropped). The reconciliation must instead QUARANTINE fail-closed.
    current = _parse(_mk_10q(_UNRECOGNIZED_PART_II))
    prior = _parse(_mk_10q(_SHORT_PART_II))
    # Precondition: the divider really did drop the Part II boundary (reparenting happened),
    # so a whole prior partII_* section is now unmatched in the current filing.
    assert "partII_item1A" not in {u.unit_id for u in current.units}
    assert "partII_item1A" in {u.unit_id for u in prior.units}
    # Fail-closed: quarantine (ParseError) rather than emit false new_section / drop real changes.
    with pytest.raises(ParseError):
        diff_filings(current, prior)


def test_round2_defectA_real_10q_pairs_reconcile_cleanly():
    # The reconciliation must fire ONLY on genuine mismatches: the real consecutive same-form
    # 10-Q pairs have matching structure, so they must diff WITHOUT quarantining and mint no
    # false new_section deltas (their golden deltas are unaffected).
    for pair, (cur_key, prior_key) in S.PAIR_SPECS.items():
        if prior_key is None:
            continue  # 8-K per-filing extraction, not a paired diff
        current = _parse_meta(S.META[cur_key])
        prior = _parse_meta(S.META[prior_key])
        assert {u.unit_id for u in current.units if u.part} == \
               {u.unit_id for u in prior.units if u.part}, f"{pair}: real pair not symmetric"
        deltas = diff_filings(current, prior)  # must NOT raise on a matching real pair
        assert not [d for d in deltas if d.change_type == "new_section"], (
            f"{pair}: a real symmetric pair produced a false new_section delta"
        )


# ---- DEFECT B: <xbrli:scenario>-dimensioned XBRL must not be treated as consolidated ----
# Scenario ("As Previously Reported", srt:RestatementAxis) cell FIRST in document order, the
# consolidated cell SECOND. Both report the SAME concept+value+period, so the DIMENSIONAL
# EXCLUSION (not a value mismatch) is what must steer the anchor to the consolidated element.
_SCENARIO_RAW = (
    b'<html><body>'
    b'<xbrli:context id="cprev">'
    b'<xbrli:entity>e</xbrli:entity>'
    b'<xbrli:period><xbrli:instant>2026-04-26</xbrli:instant></xbrli:period>'
    b'<xbrli:scenario>'
    b'<xbrldi:explicitMember dimension="srt:RestatementAxis">'
    b'srt:ScenarioPreviouslyReportedMember</xbrldi:explicitMember>'
    b'</xbrli:scenario>'
    b'</xbrli:context>'
    b'<p>Prev: <ix:nonFraction name="us-gaap:InventoryNet" contextRef="cprev" '
    b'unitRef="usd" scale="0">100</ix:nonFraction></p>'
    b'<xbrli:context id="ccons">'
    b'<xbrli:entity>e</xbrli:entity>'
    b'<xbrli:period><xbrli:instant>2026-04-26</xbrli:instant></xbrli:period>'
    b'</xbrli:context>'
    b'<p>As reported: <ix:nonFraction name="us-gaap:InventoryNet" contextRef="ccons" '
    b'unitRef="usd" scale="0">100</ix:nonFraction></p>'
    b'</body></html>'
)

# Only the scenario cell exists — the value cannot be cited consolidated at all.
_SCENARIO_ONLY_RAW = (
    b'<html><body>'
    b'<xbrli:context id="cprev">'
    b'<xbrli:entity>e</xbrli:entity>'
    b'<xbrli:period><xbrli:instant>2026-04-26</xbrli:instant></xbrli:period>'
    b'<xbrli:scenario><xbrldi:explicitMember dimension="srt:RestatementAxis">'
    b'srt:ScenarioPreviouslyReportedMember</xbrldi:explicitMember></xbrli:scenario>'
    b'</xbrli:context>'
    b'<p><ix:nonFraction name="us-gaap:InventoryNet" contextRef="cprev" '
    b'unitRef="usd" scale="0">100</ix:nonFraction></p>'
    b'</body></html>'
)


def _inv_fact_b(value: str) -> ParsedFact:
    return ParsedFact(
        taxonomy="us-gaap", concept="InventoryNet", unit="USD",
        value=Decimal(value), period_end=date(2026, 4, 26), period_start=None,
        accession="ACC", cik="0000000000",
    )


def test_round2_defectB_scenario_container_is_detected_as_dimensional():
    inline = {i.context_id: i for i in iter_inline_facts(_SCENARIO_RAW)}
    assert inline["cprev"].has_scenario and not inline["cprev"].has_segment, (
        "an <xbrli:scenario> dimensional member was not detected as dimensional"
    )
    assert not inline["ccons"].has_scenario and not inline["ccons"].has_segment, (
        "the consolidated context was wrongly flagged as dimensional"
    )
    # The scenario detection is also visible on the parse_contexts contract (segment, scenario).
    ctx = parse_contexts(_SCENARIO_RAW)
    assert ctx["cprev"][3:] == (False, True) and ctx["ccons"][3:] == (False, False)


def test_round2_defectB_numeric_anchor_skips_scenario_cell_for_consolidated():
    inline = iter_inline_facts(_SCENARIO_RAW)
    anchor = find_inline_anchor(_inv_fact_b("100"), inline)
    assert anchor is not None and anchor.context_id == "ccons", (
        "inline anchor did not select the consolidated (non-dimensional) element; got "
        f"{anchor.context_id if anchor else None} (the scenario cell precedes it in doc order)"
    )
    # End-to-end through the numeric-delta engine: the cited snippet is the consolidated element,
    # never the earlier scenario prev-reported cell (which coincidentally shares the value).
    deltas = diff_facts(
        [_inv_fact_b("100")], [_inv_fact_b("70")],
        current_accession="ACC", current_raw=_SCENARIO_RAW,
    )
    assert deltas, "expected a numeric delta anchored to the consolidated cell"
    for d in deltas:
        assert 'contextRef="ccons"' in d.snippet, (
            f"numeric delta not anchored to the consolidated element: {d.snippet!r}"
        )
        assert 'contextRef="cprev"' not in d.snippet, (
            f"numeric citation anchored to a scenario prev-reported cell: {d.snippet!r}"
        )


def test_round2_defectB_scenario_only_fact_is_uncitable_fail_closed():
    # When the ONLY inline element for the concept sits in a scenario-dimensioned context, the
    # value cannot be cited consolidated -> no delta (fail-closed), never a wrong-cell citation.
    assert find_inline_anchor(_inv_fact_b("100"), iter_inline_facts(_SCENARIO_ONLY_RAW)) is None
    deltas = diff_facts(
        [_inv_fact_b("100")], [_inv_fact_b("70")],
        current_accession="ACC", current_raw=_SCENARIO_ONLY_RAW,
    )
    assert deltas == [], f"a scenario-only fact was cited (should be uncitable): {deltas}"
