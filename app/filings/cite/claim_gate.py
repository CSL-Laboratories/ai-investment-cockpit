"""WP5 — the claim gate: "zero uncited factual claims", made executable (Inv 1 + 12).

:func:`evaluate` is where the product's headline metric becomes a hard, all-or-nothing,
fail-closed check. A brief is ``approvable`` ONLY when every one of these holds:

  (a) every ``Delta`` (claim) has >= 1 ``Citation`` — NO uncited claim;
  (b) every ``Citation`` re-verifies byte-for-byte via the WP5 resolver;
  (c) the orphan sweep is clean — NO citation whose ``delta_id`` is not in the delta set,
      and NO delta with zero citations;
  (d) NO duplicate / reused ``citation_id``.

Any single failure flips the WHOLE brief to ``approvable=False`` (Invariant 12: no
partial, silently-degraded, or uncited brief ever ships). The gate is deterministic and
LLM-free; it delegates byte verification to :func:`filings.cite.resolver.resolve_all`.
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from filings.cite.resolver import resolve_all
from filings.edgar.fetch import is_archive_document_url

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from schemas.models.citation import Citation
    from schemas.models.delta import Delta


# --- numeric grounding: derive a numeric delta's figure from its CITED snippet bytes ---------
# A numeric delta publishes ``current_value`` as a [cited] fact, but current_value is NOT an
# anchor field (not re-read by the resolver), so the gate must independently re-derive the figure
# from the resolver-verified ``<ix:nonFraction>`` snippet and require it to match — otherwise a
# tampered/divergent figure (wrong scale/sign attribute, a persistence divergence, a direct
# mutation) ships as a clean cited number (WP5 4c round-4; Inv 1 + 12). Validated against every
# numeric delta in the fixture corpus (180/180 reproduce exactly).
_IX_NONFRACTION_RE = re.compile(r"<ix:nonfraction\b([^>]*)>(.*?)</ix:nonfraction>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_NUMERIC_TEXT_RE = re.compile(r"-?\d+(\.\d+)?")
_IX_NAME_RE = re.compile(r"name\s*=\s*[\"']([^\"']+)[\"']", re.I)
_SOURCE_REF_FORMAT = re.compile(r"[A-Za-z0-9 .:_/\-]{1,256}\Z")


def concept_from_snippet(snippet: Any) -> str | None:
    """The XBRL concept an inline ``<ix:nonFraction>`` snippet byte-encodes (its ``name=``
    attribute, e.g. ``us-gaap:NetIncomeLoss``), or ``None``. This is the ground truth for the
    line-item label the digest publishes: a numeric delta's ``source_ref`` concept must match it,
    else a figure could ship under the WRONG line item (WP5 4c round-5)."""
    if not isinstance(snippet, str):
        return None
    m = _IX_NONFRACTION_RE.search(snippet)
    if not m:
        return None
    name = _IX_NAME_RE.search(m.group(1))
    return name.group(1) if name else None


def numeric_value_from_snippet(snippet: Any) -> Decimal | None:
    """The reported value an inline-XBRL ``<ix:nonFraction>`` snippet byte-encodes, or ``None``.

    ``value = displayed_number (thousands-separators stripped) × 10**scale``, negated when the
    element carries ``sign="-"`` (inline XBRL renders a negative figure without a minus). This is
    the SAME quantity WP4 anchored the snippet to, so a numeric delta's ``current_value`` must
    equal it. ``None`` when the snippet is not a parseable single ``<ix:nonFraction>`` — the gate
    treats that as ungrounded (fail-closed).
    """
    if not isinstance(snippet, str):
        return None
    m = _IX_NONFRACTION_RE.search(snippet)
    if not m:
        return None
    attrs, inner = m.group(1), m.group(2)
    # BUG-073: delegate the displayed-number parse to THE routine WP4 anchored the value
    # with (``xbrl_facts._parse_displayed``). The private re-implementation here accepted
    # only a bare ``-?\d+(\.\d+)?`` — no parenthesized negatives, and ``html.unescape``
    # turns ``&#160;`` into a literal NBSP that stayed inside the number — so a single
    # ``(1,234)`` or ``1&#160;234`` figure WP4 parsed fine made ``grounded`` None and the
    # all-or-nothing gate flipped the ENTIRE brief unapprovable, permanently. One parser,
    # one home; a WP4 display-format fix now reaches this gate automatically.
    from filings.parse.xbrl_facts import _parse_displayed

    text = html.unescape(_TAG_RE.sub("", inner))
    scale_m = re.search(r"scale\s*=\s*[\"']?(-?\d+)", attrs, re.I)
    scale = int(scale_m.group(1)) if scale_m else 0
    sign = -1 if re.search(r"sign\s*=\s*[\"']?-", attrs, re.I) else 1
    return _parse_displayed(text, scale, sign)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


# The anchor fields a Citation copies verbatim from the Delta it cites. In the honest 1:1
# builder path they always agree; a disagreement means the citation was BOUND (by delta_id) to a
# delta it does not actually cite — a wrong-cited claim the gate must block (WP5 4c round-2).
def _anchor_mismatch(delta: Delta, citation: Citation) -> str | None:
    """First anchor field on which ``citation`` disagrees with the ``delta`` it cites, else None.

    Binding a Citation to a Delta by ``delta_id`` alone let a byte-perfect citation for a
    DIFFERENT filing masquerade as this delta's citation (it resolves its own bytes, so the
    resolver is happy). The gate is the legal shield: it must verify the covering citation
    actually cites the delta it is attached to, field for field.
    """
    checks = (
        ("accession_no", citation.accession_no, delta.accession_no),
        ("raw_ref", citation.raw_ref, delta.raw_ref),
        ("snippet", citation.snippet, delta.snippet),
        ("byte_start", citation.byte_start, delta.snippet_start_offset),
        ("byte_end", citation.byte_end, delta.snippet_end_offset),
        ("edgar_url", citation.edgar_url, delta.edgar_url),
    )
    for name, cval, dval in checks:
        if cval != dval:
            return name
    # Builder uses a deterministic non-empty fallback when a Delta has no symbolic
    # source_ref.  Compare against that exact fallback too: otherwise a nullable Delta
    # source_ref lets a citation attach a real snippet to a fabricated section/fact label.
    source_ref = getattr(delta, "source_ref", None)
    expected_source_ref = (
        source_ref
        if isinstance(source_ref, str) and source_ref.strip()
        else f"{delta.kind}:{delta.accession_no}:{delta.snippet_start_offset}"
    )
    if citation.source_ref != expected_source_ref:
        return "source_ref"
    if not _SOURCE_REF_FORMAT.fullmatch(expected_source_ref):
        return "unsafe-source-ref"
    if not is_archive_document_url(citation.edgar_url, citation.accession_no):
        return "invalid-edgar-url"
    return None


@dataclass(frozen=True)
class GateResult:
    """Verdict of the claim gate over a candidate brief's deltas + citations.

    :ivar approvable: ``True`` IFF every gate condition (a)-(d) holds.
    :ivar failures: one dict per blocking problem, each ``{"reason": str, ...}`` optionally
        carrying ``delta_id`` / ``citation_id`` / ``detail``. Empty IFF ``approvable``.
    :ivar resolved_count: how many citations re-verified byte-for-byte.
    :ivar item_count: how many delta claims were considered.
    :ivar unresolved: citation ids that failed the resolver (subset of ``failures``).
    """

    approvable: bool
    failures: list[dict[str, Any]]
    resolved_count: int
    item_count: int
    unresolved: list[str] = field(default_factory=list)


def evaluate(
    deltas: Iterable[Delta],
    citations: Iterable[Citation],
    raw_store: Any,
) -> GateResult:
    """Evaluate the claim gate — all-or-nothing, fail-closed (Inv 1 + 12).

    A single uncited delta, orphan citation, reused id, or unresolvable citation makes the
    entire brief non-approvable. Ordering of the returned ``failures`` is deterministic
    (duplicate ids, then uncited deltas, then orphans, then resolver failures).
    """
    delta_list = list(deltas)
    citation_list = list(citations)
    delta_ids = {d.delta_id for d in delta_list}
    failures: list[dict[str, Any]] = []

    # (e) duplicate delta_id in the delta set — two DISTINCT claims sharing one id would let an
    #     uncited claim ride another delta's citation (the uncited/orphan sweeps key on delta_id
    #     as a set). delta_id is a content hash AND the Delta primary key, so this is unreachable
    #     on any real data path; but the exported gate must fail closed for direct callers
    #     (WP6/WP8 pass DB-loaded deltas) — WP5 4c round-6 defense-in-depth. Symmetric with (d).
    seen_delta_ids: set[str] = set()
    dup_delta_reported: set[str] = set()
    for d in delta_list:
        if d.delta_id in seen_delta_ids and d.delta_id not in dup_delta_reported:
            failures.append({"reason": "duplicate-delta-id", "delta_id": d.delta_id})
            dup_delta_reported.add(d.delta_id)
        seen_delta_ids.add(d.delta_id)

    # (d) duplicate / reused citation_id — report each reused id exactly once.
    seen_ids: set[str] = set()
    dup_reported: set[str] = set()
    for c in citation_list:
        cid = c.citation_id
        if cid in seen_ids and cid not in dup_reported:
            failures.append({"reason": "duplicate-citation-id", "citation_id": cid})
            dup_reported.add(cid)
        seen_ids.add(cid)

    # Index citations by the delta they claim to cite.
    by_delta: dict[str, list[Citation]] = defaultdict(list)
    for c in citation_list:
        by_delta[c.delta_id].append(c)

    # (a) + (c-part-1) every delta has >= 1 citation (no uncited claim / no dangling delta).
    reported_uncited: set[str] = set()
    for d in delta_list:
        if not by_delta.get(d.delta_id) and d.delta_id not in reported_uncited:
            failures.append({"reason": "uncited-delta", "delta_id": d.delta_id})
            reported_uncited.add(d.delta_id)

    # (c-part-2) orphan citations — a citation whose delta is not in the delta set.
    for c in citation_list:
        if c.delta_id not in delta_ids:
            failures.append(
                {
                    "reason": "orphan-citation",
                    "citation_id": c.citation_id,
                    "delta_id": c.delta_id,
                }
            )

    # (a-strict) the covering citation must actually cite ITS delta — every anchor field must
    # match the delta it is bound to by delta_id. Binding by delta_id alone let a byte-perfect
    # citation for a DIFFERENT filing pass (WP5 4c round-2); a field mismatch is a wrong-cited
    # claim -> fail-closed.
    delta_by_id: dict[str, Delta] = {d.delta_id: d for d in delta_list}
    for c in citation_list:
        d = delta_by_id.get(c.delta_id)
        if d is None:
            continue  # already reported as orphan-citation above
        field = _anchor_mismatch(d, c)
        if field is not None:
            failures.append(
                {
                    "reason": "citation-delta-anchor-mismatch",
                    "delta_id": c.delta_id,
                    "citation_id": c.citation_id,
                    "detail": field,
                }
            )

    # (a-numeric) a numeric delta publishes ``current_value`` as a cited FIGURE, but that field
    # is not itself an anchor (the resolver only re-reads the snippet). Re-derive the value from
    # the cited ``<ix:nonFraction>`` snippet and require current_value to equal it — else a
    # divergent/tampered figure would ship as a clean [cited] number (WP5 4c round-4; Inv 1 + 12).
    for d in delta_list:
        if str(d.kind) != "numeric":
            continue
        grounded = numeric_value_from_snippet(d.snippet)
        current = _as_decimal(d.current_value)
        if grounded is None or current is None or grounded != current:
            failures.append(
                {
                    "reason": "numeric-figure-ungrounded",
                    "delta_id": d.delta_id,
                    "detail": (
                        f"current_value={d.current_value!r} is not byte-encoded by the cited "
                        f"snippet (snippet value={grounded})"
                    ),
                }
            )
        # The published line-item label (source_ref concept) must match the cited snippet's
        # name= attribute — else the correct figure ships under the WRONG concept (round-5).
        snippet_concept = concept_from_snippet(d.snippet)
        # source_ref is "taxonomy:concept/unit"; the unit may itself contain a slash (USD/shares),
        # so the concept is everything before the FIRST slash, never the last.
        delta_concept = (d.source_ref or "").split("/", 1)[0]
        if not snippet_concept or snippet_concept != delta_concept:
            failures.append(
                {
                    "reason": "numeric-concept-ungrounded",
                    "delta_id": d.delta_id,
                    "detail": (
                        f"source_ref concept {delta_concept!r} != cited snippet name "
                        f"{snippet_concept!r}"
                    ),
                }
            )

    # (b) every citation re-verifies byte-for-byte via the resolver (fail-closed).
    resolved_count = 0
    unresolved: list[str] = []
    for c, result in zip(citation_list, resolve_all(citation_list, raw_store), strict=True):
        if result.ok:
            resolved_count += 1
        else:
            unresolved.append(c.citation_id)
            failures.append(
                {
                    "reason": "unresolvable-citation",
                    "citation_id": c.citation_id,
                    "delta_id": c.delta_id,
                    "detail": result.reason,
                }
            )

    return GateResult(
        approvable=not failures,
        failures=failures,
        resolved_count=resolved_count,
        item_count=len(delta_list),
        unresolved=unresolved,
    )
