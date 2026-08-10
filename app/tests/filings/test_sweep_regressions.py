"""Cockpit regressions for the 2026-08-09 sweep's vendored-tree fixes.

BUG-070/071/072/073/086/087. The fixes live UPSTREAM (edgar-morning-brew ``92027d7``,
re-pinned in PROVENANCE.md per its contract — vendored code is fixed at its one home,
never forked in place); these tests pin the behavior on the vendored side so a future
re-sync cannot silently regress it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from filings.cite.claim_gate import numeric_value_from_snippet
from filings.delta import diff_facts
from filings.delta.text_diff import _reconcile_sections, diff_filings
from filings.parse._html import runs
from filings.parse.models import ParsedFact, ParsedFiling, ParsedUnit, ParseError, TextRun


# ---- BUG-070: the tokenizer on legal-but-non-Workiva HTML ------------------------------
def test_gt_inside_a_quoted_attribute_does_not_split_the_tag():
    raw = b'<html><body><a href="x>y" title="t">real text</a></body></html>'
    assert [r.text for r in runs(raw)] == ["real text"]


def test_script_and_style_bodies_are_not_citable_text():
    raw = (b"<html><style>p { color: red; }</style>"
           b"<script>var s = 'not disclosure text';</script>"
           b"<p>the disclosure</p></html>")
    assert [r.text for r in runs(raw)] == ["the disclosure"]


def test_comment_containing_gt_is_one_token():
    raw = b"<html><!-- if a > b then --><p>kept</p></html>"
    assert [r.text for r in runs(raw)] == ["kept"]


# ---- helpers for the diff-level tests ---------------------------------------------------
def _one_unit_filing(accession, unit_id, body: bytes, *, form="8-K"):
    raw = b"<p>" + body + b"</p>"
    run = TextRun(byte_start=3, byte_end=3 + len(body),
                  text=body.decode("utf-8", errors="replace"))
    unit = ParsedUnit(unit_id=unit_id, kind="item", title=unit_id, byte_start=0,
                      byte_end=len(raw), runs=(run,), part=None, item=None)
    return ParsedFiling(cik="1", accession=accession, form_type=form, filename="f.htm",
                        raw=raw, units=(unit,))


def _kq_unit(unit_id, part, item):
    return ParsedUnit(unit_id=unit_id, kind="section", title=unit_id, byte_start=0,
                      byte_end=0, runs=(), part=part, item=item)


def _kq_filing(accession, units):
    return ParsedFiling(cik="1", accession=accession, form_type="10-Q", filename="f.htm",
                        raw=b"", units=tuple(units))


# ---- BUG-071: the reversed pass must not mint removals as new_section -------------------
def test_reversed_pass_labels_prior_only_sections_removed():
    """Consecutive 8-Ks have disjoint item sets, so EVERY pair used to publish the OLD
    filing's items as brand-new ``new_section`` deltas (materiality bump included) cited
    to the prior filing."""
    prior = _one_unit_filing("ACC-PRIOR", "item_5.02", b"CEO departure disclosed today")
    current = _one_unit_filing("ACC-CUR", "item_2.02", b"Results of operations reported")
    deltas = diff_filings(prior, current, removed_direction=True)   # the engine's reversed pass
    assert [d.change_type for d in deltas] == ["removed_section"]
    assert deltas[0].raw_ref == "ACC-PRIOR"

    forward = diff_filings(current, prior)
    assert [d.change_type for d in forward] == ["new_section"]      # forward pass unchanged


# ---- BUG-072: legitimate 10-Q structure variation is not a quarantine -------------------
def test_clean_item_drop_is_not_quarantined():
    """A 10-Q legitimately omits Item 1A when risk factors are unchanged; that used to
    ParseError the WHOLE filing, silently costing every delta it had."""
    prior = _kq_filing("A1", [_kq_unit("part1_item2", "I", "2"),
                              _kq_unit("part2_item1a", "II", "1A")])
    current = _kq_filing("A2", [_kq_unit("part1_item2", "I", "2")])
    _reconcile_sections(current, prior)                             # must not raise


def test_reparented_item_still_quarantines():
    """The failure class the guard exists for — the same item under a DIFFERENT part
    (an unparsed Part divider) — must still fail closed."""
    prior = _kq_filing("A1", [_kq_unit("part2_item1a", "II", "1A")])
    current = _kq_filing("A2", [_kq_unit("part1_item1a", "I", "1A")])
    with pytest.raises(ParseError):
        _reconcile_sections(current, prior)


# ---- BUG-073: the claim gate parses what WP4 parses -------------------------------------
def test_paren_negative_figure_grounds():
    snippet = '<ix:nonFraction name="us-gaap:X" contextRef="c" scale="3">(1,234)</ix:nonFraction>'
    assert numeric_value_from_snippet(snippet) == Decimal("-1234000")


def test_nbsp_separated_figure_grounds():
    snippet = '<ix:nonFraction name="us-gaap:X" contextRef="c">1&#160;234</ix:nonFraction>'
    assert numeric_value_from_snippet(snippet) == Decimal("1234")


# ---- BUG-086: a non-utf8 span costs one delta, never the filing -------------------------
def test_non_utf8_bytes_cost_one_delta_not_the_filing():
    good_prior = b"the original agreement text stands as previously disclosed here"
    good_cur = b"the amended agreement text differs materially from the disclosure"
    bad_cur = b"caf\xe9 legacy encoding bytes inside this changed span here today"

    raw_prior = b"<p>" + good_prior + b"</p><p>" + b"old latin section text here today" + b"</p>"
    a_run_p = TextRun(3, 3 + len(good_prior), good_prior.decode())
    b_body_p = b"old latin section text here today"
    b_start_p = 3 + len(good_prior) + 7
    b_run_p = TextRun(b_start_p, b_start_p + len(b_body_p), b_body_p.decode())

    raw_cur = b"<p>" + good_cur + b"</p><p>" + bad_cur + b"</p>"
    a_run_c = TextRun(3, 3 + len(good_cur), good_cur.decode())
    b_start_c = 3 + len(good_cur) + 7
    b_run_c = TextRun(b_start_c, b_start_c + len(bad_cur),
                      bad_cur.decode("utf-8", errors="replace"))

    def filing(accession, raw, runs_a, runs_b):
        units = (
            ParsedUnit(unit_id="item_1.01", kind="item", title="a", byte_start=0,
                       byte_end=len(raw), runs=(runs_a,), part=None, item=None),
            ParsedUnit(unit_id="item_8.01", kind="item", title="b", byte_start=0,
                       byte_end=len(raw), runs=(runs_b,), part=None, item=None),
        )
        return ParsedFiling(cik="1", accession=accession, form_type="8-K",
                            filename="f.htm", raw=raw, units=units)

    prior = filing("ACC-P", raw_prior, a_run_p, b_run_p)
    current = filing("ACC-C", raw_cur, a_run_c, b_run_c)

    deltas = diff_filings(current, prior)          # used to raise UnicodeDecodeError
    assert [d.unit_id for d in deltas] == ["item_1.01"], (
        "the clean section's delta must survive; only the uncitable span is skipped")


# ---- BUG-087: current-side duplicate-period collision -----------------------------------
_ANCHOR_RAW = (
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
    return ParsedFact(
        taxonomy="us-gaap", concept="InventoryNet", unit="USD",
        value=Decimal(value), period_end=date(2026, 4, 26), period_start=None,
        accession="ACC", cik="0000000000",
    )


def test_current_side_duplicate_period_collision_mints_no_restatement():
    """An amended companyfacts payload can list one concept+period twice (as-reported and
    as-restated). Both rows used to pair against the single prior value and mint TWO
    contradictory is_restatement deltas."""
    colliding_current = [_inv_fact("100"), _inv_fact("90")]
    deltas = diff_facts(colliding_current, [_inv_fact("70")],
                        current_accession="ACC", current_raw=_ANCHOR_RAW)
    assert not [d for d in deltas if d.is_restatement]


def test_control_single_current_still_emits_a_restatement():
    deltas = diff_facts([_inv_fact("100")], [_inv_fact("70")],
                        current_accession="ACC", current_raw=_ANCHOR_RAW)
    restatements = [d for d in deltas if d.is_restatement]
    assert restatements and restatements[0].current_value == Decimal(100)
