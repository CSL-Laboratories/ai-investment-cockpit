"""WP5 — the plain, deterministic, LLM-free cited-delta digest (first useful artifact).

:func:`render_digest` is the first personally-useful thing the product produces, BEFORE any
LLM exists: it builds citations for the computed deltas, runs them through the claim gate,
and — only if the gate says the brief is approvable AND every rendered EDGAR URL is
format-valid for its accession — renders a readable morning digest, one block per delta,
highest materiality first. Otherwise it renders a clearly-marked ``UNPUBLISHABLE`` report
that lists the blocking failures and presents NO gate-failing item as a verified fact
(fail-closed, Invariant 12).

Determinism (Invariant 2): the rendered text is a pure function of the inputs — no
wall-clock time, no LLM, no subprocess, no network appears in the output — so identical
inputs produce byte-identical text. The injected clock only stamps the (non-rendered)
``created_ts`` of the built citations.
"""
from __future__ import annotations

import html
import re
import textwrap
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from filings.cite import claim_gate
from filings.cite.builder import CitationBuildError, build_citations
from filings.cite.claim_gate import GateResult
from core.clock import Clock, default_clock
from filings.edgar.fetch import is_archive_document_url

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Mapping

    from schemas.models.citation import Citation
    from schemas.models.delta import Delta
    from schemas.models.filing import Filing


@dataclass(frozen=True)
class DigestResult:
    """A rendered digest plus the gate verdict that produced it.

    :ivar approvable: mirrors the (URL-augmented) gate verdict.
    :ivar text: the readable cited digest, or an ``UNPUBLISHABLE`` report.
    :ivar gate: the :class:`GateResult` (its ``failures`` include any malformed-URL
        failures found while rendering, so ``gate.approvable == approvable``).
    """

    approvable: bool
    text: str
    gate: GateResult


def edgar_url_matches_accession(
    url: Any, accession: Any, *, cik: Any = None, filename: Any = None
) -> bool:
    """True IFF ``url`` is a genuine EDGAR archive URL for this exact filing.

    Delegates to :func:`filings.edgar.fetch.is_archive_document_url` — the sec.gov URL knowledge
    (and its literal) lives in the single fair-access module (Invariant 4), never re-literalized
    in ``cite/``. That validator is scheme+host-anchored and checks the CIK/accession/filename,
    so a phishing host embedding the archive path, or a wrong-CIK/wrong-filename URL, is
    rejected. A URL failing it is a gate failure, never rendered (the legal shield, hardened
    after the WP5 4c pass).
    """
    return is_archive_document_url(url, accession, cik=cik, filename=filename)


def _form_label(filing: Any) -> str:
    ft = getattr(filing, "form_type", None)
    if ft is None:
        return "?"
    return str(getattr(ft, "value", ft))


_RULE = "=" * 78
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MONEY_UNITS = (
    (Decimal("1000000000000"), "T"),
    (Decimal("1000000000"), "B"),
    (Decimal("1000000"), "M"),
    (Decimal("1000"), "K"),
)


def _rank_key(delta: Any):
    # Highest materiality first, then a fully-deterministic total tiebreak.
    return (
        -delta.materiality_score,
        str(delta.kind),
        delta.source_ref or "",
        delta.snippet_start_offset,
        delta.snippet_end_offset,
        delta.delta_id,
    )


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _humanize(d: Decimal) -> str:
    """``58321000000`` -> ``58.3B`` (K/M/B/T), small values as-is. No currency symbol."""
    sign = "-" if d < 0 else ""
    a = abs(d)
    for div, suf in _MONEY_UNITS:
        if a >= div:
            s = f"{(a / div).quantize(Decimal('0.1'))}"
            if s.endswith(".0"):
                s = s[:-2]
            return f"{sign}{s}{suf}"
    return f"{sign}{a.normalize():f}"


def _unit_ref(snippet: Any) -> str:
    """The inline ``<ix:nonFraction unitRef="...">`` id from the CITED snippet bytes — the
    denomination category (money / shares / dimensionless) is read from here, not from the
    ungrounded ``source_ref`` unit string (WP5 4c round-5)."""
    if not isinstance(snippet, str):
        return ""
    m = re.search(r"unitref\s*=\s*[\"']([^\"']+)[\"']", snippet, re.I)
    return m.group(1) if m else ""


def _denominate(value: Any, unit_ref: str) -> str:
    """Render a numeric figure in the denomination its CITED ``unitRef`` implies — so a share
    count or a rate is never shown as dollars (WP5 4c round-5, honest-pipeline breach). ``$`` only
    for USD money units; a bare ``shares`` count; the raw ratio for dimensionless (pure/number)."""
    d = _dec(value)
    if d is None:
        return "n/a"
    u = unit_ref.lower()
    money = "usd" in u or "iso4217" in u
    shares = "share" in u
    if money and shares:  # e.g. usdPerShare (EPS) — a per-share dollar amount, unscaled
        return f"${d.normalize():f} per share"
    if money:
        return "$" + _humanize(d)
    if shares:
        return _humanize(d) + " shares"
    return f"{d.normalize():f}"  # dimensionless (pure/number rate or ratio) — never a $


def _fmt_score(score: Any) -> str:
    try:
        return f"{Decimal(score):.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return str(score)


def _concept_label(delta: Any) -> str:
    """``us-gaap:NetIncomeLoss/USD`` -> ``NetIncomeLoss`` (the gate-grounded concept; the unit is
    NOT shown here — the denomination lives in the figure, read from the cited ``unitRef``)."""
    # The concept is everything before the FIRST slash; the unit (which may itself be "USD/shares")
    # follows it. Splitting on the last slash would mangle a per-share concept.
    concept = (delta.source_ref or "").split("/", 1)[0]
    if ":" in concept:
        concept = concept.split(":", 1)[1]  # drop the taxonomy prefix
    return concept or "(numeric)"


def _readable(snippet: str) -> str:
    """A readable one-line rendering of a raw (possibly tag/entity-laden) snippet.

    The verbatim bytes are preserved on the stored ``Citation`` (what the resolver reproduces);
    the digest is the human view, so a coalesced table row or inline-XBRL fragment is presented
    tag-stripped + entity-decoded + whitespace-collapsed. Deterministic (pure string ops).
    """
    text = html.unescape(_TAG_RE.sub(" ", snippet)).replace(" ", " ")
    # Filing text is untrusted external input. Whitespace is normalized below, but terminal
    # escape/control characters are not necessarily whitespace; remove them before rendering
    # into a console-like approval surface.
    text = "".join(char if char.isprintable() else " " for char in text)
    return _WS_RE.sub(" ", text).strip()


_PREVIEW_CHARS = 400


def _preview(text: str) -> str:
    """Truncate a long change snippet to a readable preview at a word boundary (Bloomberg-style
    headline density). The FULL verbatim snippet stays on the stored Citation and behind the
    cited URL — the preview is a grounded prefix of the resolver-verified bytes, clearly marked
    truncated, never a silent cut that could change meaning."""
    if len(text) <= _PREVIEW_CHARS:
        return text
    cut = text.rfind(" ", 0, _PREVIEW_CHARS)
    if cut < _PREVIEW_CHARS // 2:
        cut = _PREVIEW_CHARS
    return f"{text[:cut].rstrip()} ... [+{len(text) - cut} chars — full text at source]"


def _source_tag(citation: Any, filing: Any) -> str:
    cik = str(getattr(filing, "cik", "unknown-cik"))
    return f"CIK {cik} | {_form_label(filing)} {citation.filing_date.isoformat()}"


def _item(n: int, delta: Any, citation: Any, filing: Any) -> str:
    """One ranked, cited digest line (Bloomberg-terminal style)."""
    tag = _source_tag(citation, filing)
    url_line = f"     > {citation.edgar_url}"
    if delta.kind == "numeric":
        # Option A (Lucas 2026-07-09): cite only the CURRENT filing's figure — every rendered
        # part is grounded in the resolver-verified snippet: the concept (gate-checked vs the
        # name= attr), the value (gate-checked vs the number x 10^scale), and the denomination
        # (read from the cited unitRef, so shares/rates are never shown as $ — round-5). The
        # prior value lives in the prior filing and is NOT rendered (that would be uncited);
        # the prior->current change narrative is WP6's grounded-prose job.
        figure = _denominate(delta.current_value, _unit_ref(citation.snippet))
        head = f"{n:>3} | FIN  {_concept_label(delta)}  |  {figure}  |  {tag} [cited]"
        return f"{head}\n{url_line}"
    body = textwrap.fill(
        _preview(_readable(delta.snippet)),
        width=78,
        initial_indent="     ",
        subsequent_indent="     ",
    )
    return f"{n:>3} | CHG  {tag}\n{body}\n{url_line}"


_SUBRULE = "-" * 78


def _section(title: str, triples: list[tuple], limit: int | None) -> list[str]:
    """Render one panel (a Bloomberg-terminal-style section): a titled, materiality-ranked,
    optionally-capped list of cited items. ``triples`` are ``(delta, citation, filing)``."""
    ranked = sorted(triples, key=lambda t: _rank_key(t[0]))
    shown = ranked if limit is None else ranked[:limit]
    dropped = len(ranked) - len(shown)
    lines = [f"  {title}", _SUBRULE]
    for i, (d, c, f) in enumerate(shown):
        lines.append("")
        lines.append(_item(i + 1, d, c, f))
    if dropped > 0:
        cutoff = _fmt_score(shown[-1][0].materiality_score) if shown else "0"
        lines.append("")
        lines.append(f"  ... +{dropped} more (materiality < {cutoff}) | all cited & stored")
    return lines


def _render_publishable(
    deltas: list[Delta],
    citations_by_delta: dict[str, Citation],
    filings: Mapping[str, Filing],
    *,
    limit: int | None = None,
) -> str:
    # Two panels, like a terminal: disclosures/events (text deltas) and reported financials
    # (numeric deltas). Each is materiality-ranked and independently capped, so the material
    # EVENTS surface alongside the headline figures rather than being buried under a flat
    # top-N of big-dollar balance-sheet lines (materiality *tuning* is deferred to the soak).
    triples = [
        (d, citations_by_delta[d.delta_id], filings.get(d.accession_no)) for d in deltas
    ]
    changes = [t for t in triples if t[0].kind != "numeric"]
    financials = [t for t in triples if t[0].kind == "numeric"]
    out: list[str] = [
        _RULE,
        "  EDGAR FILING DELTA | MORNING BREW",
        f"  {len(triples)} cited change(s) | ranked by materiality | "
        "all stored & byte-verified",
        _RULE,
    ]
    if not triples:
        out += ["", "  No cited changes in this brief.", _RULE]
        return "\n".join(out) + "\n"
    if changes:
        out.append("")
        out += _section("MATERIAL CHANGES (disclosures & events)", changes, limit)
    if financials:
        out.append("")
        out += _section("REPORTED FINANCIALS (current filing, cited)", financials, limit)
    out.append(_RULE)
    return "\n".join(out) + "\n"


def _render_unpublishable(gate: GateResult) -> str:
    lines = [
        "UNPUBLISHABLE — this brief cannot be approved (fail-closed).",
        f"{len(gate.failures)} blocking citation-integrity failure(s); "
        "no item below is a verified cited fact.",
    ]
    for f in gate.failures:
        parts = [f"reason={f.get('reason')}"]
        if f.get("delta_id"):
            parts.append(f"delta_id={f['delta_id']}")
        if f.get("citation_id"):
            parts.append(f"citation_id={f['citation_id']}")
        if f.get("detail"):
            parts.append(f"detail={f['detail']}")
        lines.append("  - " + " ".join(parts))
    return "\n".join(lines) + "\n"


def render_digest(
    deltas: Iterable[Delta],
    filings: Mapping[str, Filing],
    raw_store: Any,
    *,
    clock: Clock = default_clock,
    limit: int | None = None,
) -> DigestResult:
    """Build citations, run the claim gate, and render the digest (fail-closed).

    Publishable ONLY when the gate approves AND every rendered EDGAR URL is format-valid
    for its accession; otherwise an ``UNPUBLISHABLE`` report. The returned ``gate`` includes
    any malformed-URL failures, so ``result.gate.approvable == result.approvable``.

    :param limit: if given, render only the top ``limit`` changes by materiality with a
        ``+N more`` footer (the WP8 morning run passes a small N for a scannable brief); the
        default ``None`` renders every cited change. All deltas are cited + stored either way.
    """
    delta_list = list(deltas)

    # Build citations. A missing filing (hence an un-guessable date) is itself fail-closed.
    try:
        citations = build_citations(delta_list, filings, clock=clock)
    except CitationBuildError as exc:
        gate = GateResult(
            approvable=False,
            failures=[{"reason": "citation-build-failed", "detail": str(exc)}],
            resolved_count=0,
            item_count=len(delta_list),
        )
        return DigestResult(False, _render_unpublishable(gate), gate)

    gate = claim_gate.evaluate(delta_list, citations, raw_store)

    # URL format sweep — a malformed/spoofed/wrong-filing EDGAR URL is a gate failure, never
    # silently rendered. Verify each URL against the SOURCE filing's real CIK + primary_doc
    # (not just its own shape), so a wrong-CIK or wrong-filename URL is rejected (WP5 4c).
    url_failures: list[dict[str, Any]] = []
    for c in citations:
        src = filings.get(c.accession_no)
        src_name = getattr(src, "primary_doc", None)
        src_cik = getattr(src, "cik", None)
        if not src_cik:
            # No known source CIK -> the URL's CIK segment cannot be verified. Fail CLOSED
            # rather than skip the check (WP5 4c round-3; unreachable in the pipeline since
            # Filing.cik is non-null, but the shield must not fail open).
            url_failures.append(
                {
                    "reason": "unverifiable-edgar-url",
                    "citation_id": c.citation_id,
                    "delta_id": c.delta_id,
                    "detail": "source filing has no CIK; cannot verify the archive URL",
                }
            )
        elif not src_name:
            # No known real filename -> the engine could only synthesize a non-working
            # <accession>.htm URL. The digest presents WORKING links only (fail-closed toward
            # the reader who clicks them); a citation without a real primary_doc is not rendered
            # as a clean cited fact (WP5 4c round-2, secondary).
            url_failures.append(
                {
                    "reason": "unverifiable-edgar-url",
                    "citation_id": c.citation_id,
                    "delta_id": c.delta_id,
                    "detail": "source filing has no primary_doc; cannot guarantee a working link",
                }
            )
        elif not edgar_url_matches_accession(
            c.edgar_url, c.accession_no, cik=src_cik, filename=src_name
        ):
            url_failures.append(
                {
                    "reason": "malformed-edgar-url",
                    "citation_id": c.citation_id,
                    "delta_id": c.delta_id,
                    "detail": str(c.edgar_url),
                }
            )
    if url_failures:
        gate = GateResult(
            approvable=False,
            failures=list(gate.failures) + url_failures,
            resolved_count=gate.resolved_count,
            item_count=gate.item_count,
            unresolved=gate.unresolved,
        )

    if not gate.approvable:
        return DigestResult(False, _render_unpublishable(gate), gate)

    # Approvable: one citation per delta is guaranteed by the gate (a)+(c).
    citations_by_delta: dict[str, Citation] = {}
    for c in citations:
        citations_by_delta.setdefault(c.delta_id, c)
    text = _render_publishable(delta_list, citations_by_delta, filings, limit=limit)
    return DigestResult(True, text, gate)
