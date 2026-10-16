"""WP4 — deterministic XBRL normalization + inline-XBRL citation anchoring (LLM-free).

Two halves, both pure and byte-exact:

1. **``companyfacts`` normalization.** Flatten the per-CIK ``companyfacts`` aggregate into
   :class:`ParsedFact` rows (Decimal values, parsed dates), reusing the proven WP3 structural
   walker (``filings.edgar.companyfacts.iter_facts``) rather than re-deriving it. ``select_primary``
   picks each concept+unit's *headline* fact for a filing — the latest reporting period, and
   among a tie the shortest duration (the quarter, not the year-to-date roll-up) — so the
   numeric delta compares like-for-like across two consecutive filings.

2. **Inline-XBRL anchoring.** ``companyfacts`` is a per-CIK aggregate that is NOT in the raw
   store, so a numeric delta cannot cite it. Instead it cites the value *as reported in the
   filing*: this module locates the ``<ix:nonFraction>`` element inside the filing's raw HTML
   whose reported value (``displayed x 10**scale x sign``) equals the ``companyfacts`` value
   for the primary period, and returns its exact ``[byte_start, byte_end)`` span. That span
   indexes into the write-once raw blob keyed by accession, so WP5's resolver reproduces the
   snippet byte-for-byte (Invariant 1).

No LLM, no subprocess, no network (Invariant 2). All ordering is derived from the JSON's
insertion order and document byte order, so repeated runs are byte-identical.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from filings.edgar.companyfacts import iter_facts
from filings.parse._html import in_spans, ix_hidden_spans, verbatim
from filings.parse.models import InlineFact, ParsedFact

# ---------------------------------------------------------------------------------------
# companyfacts -> ParsedFact
# ---------------------------------------------------------------------------------------


def _pdate(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def normalize_companyfacts(companyfacts: dict, *, cik: str | None = None) -> list[ParsedFact]:
    """Flatten a ``companyfacts`` payload into :class:`ParsedFact` rows (Decimal + dates).

    A row is dropped only if it lacks the two fields a numeric delta cannot do without: a
    parseable ``val`` and an ``end`` date. Everything else is preserved. Deterministic:
    iteration follows the JSON insertion order.
    """
    resolved_cik = cik
    if resolved_cik is None:
        raw_cik = companyfacts.get("cik")
        resolved_cik = str(raw_cik).zfill(10) if raw_cik is not None else ""

    out: list[ParsedFact] = []
    for rec in iter_facts(companyfacts):
        if rec.val is None or rec.end is None:
            continue
        try:
            value = Decimal(str(rec.val))
        except (InvalidOperation, ValueError):
            continue
        end = _pdate(rec.end)
        if end is None:
            continue
        out.append(
            ParsedFact(
                taxonomy=rec.taxonomy,
                concept=rec.concept,
                unit=rec.unit,
                value=value,
                period_end=end,
                period_start=_pdate(rec.start),
                accession=rec.accession or "",
                cik=resolved_cik,
                fiscal_year=rec.fiscal_year,
                fiscal_period=rec.fiscal_period,
                form=rec.form,
                filed=_pdate(rec.filed),
                frame=rec.frame,
                label=rec.label,
            )
        )
    return out


def facts_for_accession(
    companyfacts: dict, accession: str, *, cik: str | None = None
) -> list[ParsedFact]:
    """All normalized facts a specific filing reported (join key for numeric citations)."""
    return [f for f in normalize_companyfacts(companyfacts, cik=cik) if f.accession == accession]


def _primary_sort_key(f: ParsedFact) -> tuple:
    # Headline fact = latest period end; tie -> shortest duration (quarter over YTD);
    # tie -> latest filing date. Deterministic, total ordering.
    filed_key = f.filed.toordinal() if f.filed else 0
    return (f.period_end.toordinal(), -f.duration_days, filed_key)


def select_primary(facts: list[ParsedFact]) -> dict[tuple[str, str], ParsedFact]:
    """Map ``(concept, unit)`` -> the single headline fact for that concept in this filing."""
    best: dict[tuple[str, str], ParsedFact] = {}
    for f in facts:
        key = (f.concept, f.unit)
        cur = best.get(key)
        if cur is None or _primary_sort_key(f) > _primary_sort_key(cur):
            best[key] = f
    return best


# ---------------------------------------------------------------------------------------
# Inline-XBRL (filing HTML) -> InlineFact, with exact byte offsets
# ---------------------------------------------------------------------------------------

_CONTEXT_RE = re.compile(
    rb"<xbrli:context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</xbrli:context>",
    re.IGNORECASE | re.DOTALL,
)
_START_RE = re.compile(rb"<xbrli:startDate>\s*([0-9-]+)\s*</xbrli:startDate>", re.IGNORECASE)
_END_RE = re.compile(rb"<xbrli:endDate>\s*([0-9-]+)\s*</xbrli:endDate>", re.IGNORECASE)
_INSTANT_RE = re.compile(rb"<xbrli:instant>\s*([0-9-]+)\s*</xbrli:instant>", re.IGNORECASE)
# A context is dimensional (NOT the consolidated as-reported figure) when it carries either an
# ``<xbrli:segment>`` OR an ``<xbrli:scenario>`` container — both hold explicit/typed dimensional
# members (e.g. a business-segment breakdown under ``segment``, or a ``srt:RestatementAxis`` "As
# Previously Reported" member under ``scenario``). Matching only ``segment`` mislabels a
# scenario-dimensioned context as consolidated, so an inline anchor could cite a prev-reported /
# member cell instead of the as-reported element — a silent WRONG citation that still round-trips
# byte-for-byte (round-2 defect B). Both containers are detected the same way.
_SEGMENT_RE = re.compile(rb"<xbrli:segment\b", re.IGNORECASE)
_SCENARIO_RE = re.compile(rb"<xbrli:scenario\b", re.IGNORECASE)

# One inline numeric fact element. Content is the displayed number only (no child tags).
_NONFRACTION_RE = re.compile(
    rb"<ix:nonFraction\b([^>]*)>([^<]*)</ix:nonFraction>", re.IGNORECASE
)
_ATTR_RE = re.compile(rb'([\w:-]+)\s*=\s*"([^"]*)"')

def parse_contexts(
    raw: bytes,
) -> dict[str, tuple[date | None, date | None, date | None, bool, bool]]:
    """Map ``contextRef`` -> ``(start, end, instant, has_segment, has_scenario)`` from the XBRL.

    ``has_segment`` / ``has_scenario`` flag the two dimensional containers separately; a context
    is consolidated (safe to anchor a numeric citation to) only when BOTH are false.
    """
    out: dict[str, tuple[date | None, date | None, date | None, bool, bool]] = {}
    for m in _CONTEXT_RE.finditer(raw):
        cid = m.group(1).decode("latin-1")
        body = m.group(2)
        sd = _START_RE.search(body)
        ed = _END_RE.search(body)
        inst = _INSTANT_RE.search(body)
        out[cid] = (
            _pdate(sd.group(1).decode("latin-1")) if sd else None,
            _pdate(ed.group(1).decode("latin-1")) if ed else None,
            _pdate(inst.group(1).decode("latin-1")) if inst else None,
            bool(_SEGMENT_RE.search(body)),
            bool(_SCENARIO_RE.search(body)),
        )
    return out


def _parse_displayed(text: str, scale: int, sign: int) -> Decimal | None:
    """Reconstruct the reported value from a displayed inline number + scale/sign."""
    s = text.strip()
    if not s:
        return None
    negative = sign < 0
    if s.startswith("(") and s.endswith(")"):
        negative = not negative
        s = s[1:-1]
    s = s.replace(",", "").replace("\xa0", "").replace("&#160;", "").strip()
    if s in ("", "-", "—", "–"):
        return None
    try:
        val = Decimal(s)
    except InvalidOperation:
        return None
    val = val * (Decimal(10) ** scale)
    return -val if negative else val


def iter_inline_facts(
    raw: bytes,
    contexts: dict[str, tuple[date | None, date | None, date | None, bool, bool]] | None = None,
) -> list[InlineFact]:
    """Every displayed ``<ix:nonFraction>`` fact in the filing HTML, byte-anchored.

    Hidden-block facts are excluded so a citation always anchors to a *displayed* number.
    """
    if contexts is None:
        contexts = parse_contexts(raw)
    hidden = ix_hidden_spans(raw)

    out: list[InlineFact] = []
    for m in _NONFRACTION_RE.finditer(raw):
        if in_spans(hidden, m.start()):
            continue
        attrs = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in _ATTR_RE.findall(m.group(1))
        }
        name = attrs.get("name")
        if not name:
            continue
        try:
            scale = int(attrs.get("scale", "0"))
        except ValueError:
            scale = 0
        sign = -1 if attrs.get("sign", "").strip() == "-" else 1
        value = _parse_displayed(m.group(2).decode("latin-1"), scale, sign)
        if value is None:
            continue
        cid = attrs.get("contextRef", "")
        start, end, instant, has_seg, has_scen = contexts.get(
            cid, (None, None, None, False, False)
        )
        out.append(
            InlineFact(
                concept=name,
                unit_ref=attrs.get("unitRef", ""),
                context_id=cid,
                value=value,
                scale=scale,
                sign=sign,
                byte_start=m.start(),
                byte_end=m.end(),
                period_start=start,
                period_end=end,
                instant=instant,
                has_segment=has_seg,
                has_scenario=has_scen,
            )
        )
    return out


def find_inline_anchor(
    fact: ParsedFact,
    inline_facts: list[InlineFact],
) -> InlineFact | None:
    """Locate the displayed inline-XBRL element that reports ``fact``'s primary value.

    Match = same concept (``taxonomy:concept``), a FULLY non-dimensional (consolidated) context
    — neither ``<xbrli:segment>`` NOR ``<xbrli:scenario>`` — whose period matches the fact's
    period, and an exactly equal reconstructed value. Requiring both dimensional containers to be
    absent keeps the citation on the as-reported element and off a scenario prev-reported / member
    cell that coincidentally shares the value (round-2 defect B). Returns the first such element in
    document order, or ``None`` if the value cannot be cited in the filing (the caller then skips
    the delta — fail-closed: never an uncited number).
    """
    want_name = f"{fact.taxonomy}:{fact.concept}"
    for inf in inline_facts:
        if inf.concept != want_name or inf.has_segment or inf.has_scenario:
            continue
        if fact.period_start is None:
            # Instant/balance fact: match on the instant (or endDate) == fact.period_end.
            ctx_end = inf.instant or inf.period_end
            if ctx_end != fact.period_end:
                continue
        else:
            if inf.period_start != fact.period_start or inf.period_end != fact.period_end:
                continue
        if inf.value != fact.value:
            continue
        return inf
    return None


def anchor_snippet(raw: bytes, inf: InlineFact) -> str:
    """Verbatim citation snippet for an inline fact (round-trips from the raw store)."""
    return verbatim(raw, inf.byte_start, inf.byte_end)
