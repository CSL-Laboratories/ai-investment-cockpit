"""WP4 — deterministic, offset-preserving text extraction from raw filing HTML.

The SEC primary documents in scope are Workiva-generated **inline-XBRL XHTML**: pure ASCII
bytes, non-ASCII rendered as numeric entities (``&#160;`` ...), no ``<script>`` / ``<style>``
blocks. Displayed text lives in character-data runs *between* tags. This module tokenizes the
raw bytes into those runs while preserving each run's exact ``[byte_start, byte_end)`` span,
so a segment's citation offsets index straight back into the write-once raw blob (Invariant 1).

Design choices (adversarially reviewed WP — keep them):

* **Regex tag tokenizer, not a stateful HTML parser.** ``<[^>]*>`` matches every tag/comment/
  processing-instruction; the gaps between matches are the text runs, and their byte offsets
  are exact by construction. ``html.parser`` only exposes line/col, which is fragile to map
  back to bytes once ``convert_charrefs`` merges entities. The fixtures never put ``>`` inside
  an attribute value (Workiva inline styles are safe), so the tokenizer is exact for them.
* **Cleaned text ≠ verbatim bytes.** ``clean()`` unescapes entities and collapses whitespace
  for *matching/diffing only*. The stored citation snippet is always the raw byte slice
  (``verbatim()``), which is what the WP5 resolver reproduces byte-for-byte.
* **The ``<ix:hidden>`` block is skipped for displayed-text segmentation** — it holds
  non-rendered facts that would otherwise pollute the top of the document.

Pure functions only: no LLM, no subprocess, no network (Invariant 2).
"""
from __future__ import annotations

import html
import re
from collections.abc import Iterator

from filings.parse.models import TextRun

# Every tag, comment, or processing instruction. The gaps between matches are text runs.
_TAG_RE = re.compile(rb"<[^>]*>")

# The inline-XBRL hidden fact block — non-displayed content, excluded from segmentation.
_HIDDEN_RE = re.compile(rb"<ix:hidden\b.*?</ix:hidden>", re.IGNORECASE | re.DOTALL)

# A run that is only whitespace / non-breaking spaces once cleaned carries no signal.
_WS_ONLY = re.compile(r"^\s*$")


def verbatim(raw: bytes, start: int, end: int) -> str:
    """The exact citation snippet: ``raw[start:end]`` decoded as **utf-8**.

    utf-8 is the Citation stored contract (``schemas.models.citation``): the resolver
    re-reads ``raw[start:end]`` and reproduces this snippet, and the recorded offsets satisfy
    ``byte_end - byte_start == len(snippet.encode("utf-8"))``. All citation spans begin/end at
    ASCII tag boundaries (``<`` / ``>``), so a multibyte character is never split and the slice
    is always well-formed utf-8. For the pure-ASCII fixtures utf-8, latin-1 and ascii agree
    exactly; on real non-ASCII content only utf-8 keeps the byte offsets and the snippet
    consistent (a latin-1 decode of ``®`` = ``C2 AE`` would be 2 chars re-encoding to 4 bytes,
    breaking the offset contract).
    """
    return raw[start:end].decode("utf-8")


def clean(chunk: bytes) -> str:
    """Cleaned text of a raw run: entities unescaped, whitespace collapsed. For matching.

    Decoded as utf-8 (the same encoding as the stored snippet, :func:`verbatim`) so the matching
    text and the citation snippet never disagree on non-ASCII content; ``errors="replace"`` keeps
    a stray malformed byte from aborting segmentation (matching is best-effort — the citation
    itself is always the exact :func:`verbatim` bytes).
    """
    return re.sub(r"\s+", " ", html.unescape(chunk.decode("utf-8", errors="replace"))).strip()


def _hidden_spans(raw: bytes) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _HIDDEN_RE.finditer(raw)]


def iter_runs(raw: bytes, *, skip_hidden: bool = True) -> Iterator[TextRun]:
    """Yield every non-empty cleaned text run with its exact raw byte span, in document order.

    Runs whose cleaned form is empty/whitespace-only are dropped (they carry no diffable
    signal), but their byte offsets are never reused — each yielded run's
    ``raw[byte_start:byte_end]`` reproduces its own verbatim slice.
    """
    hidden = _hidden_spans(raw) if skip_hidden else []

    def in_hidden(pos: int) -> bool:
        return any(a <= pos < b for a, b in hidden)

    pos = 0
    for m in _TAG_RE.finditer(raw):
        if m.start() > pos:
            chunk = raw[pos:m.start()]
            if not in_hidden(pos):
                text = clean(chunk)
                if text and not _WS_ONLY.match(text):
                    yield TextRun(byte_start=pos, byte_end=m.start(), text=text)
        pos = m.end()
    if pos < len(raw):
        chunk = raw[pos:]
        if not in_hidden(pos):
            text = clean(chunk)
            if text and not _WS_ONLY.match(text):
                yield TextRun(byte_start=pos, byte_end=len(raw), text=text)


def runs(raw: bytes, *, skip_hidden: bool = True) -> list[TextRun]:
    """Eager :func:`iter_runs` — the full ordered list of byte-anchored text runs."""
    return list(iter_runs(raw, skip_hidden=skip_hidden))


# Tags that begin a NEW visual line / table row / paragraph. Two runs separated by any of
# these render on different lines and are distinct citable units; two runs separated only by
# *inline* markup (``<span>``, the ``<ix:*>`` inline-XBRL fact wrappers, or a table CELL
# boundary ``</td><td>``) sit on the same visual line. Coalescing across the inline gaps is
# what re-assembles an inline-XBRL-fragmented figure ("...were $<ix:nonFraction>26</ix:...>
# billion") and a full table row ("iPhone $ 56,994 $ 46,841 22 %") into ONE complete citation,
# instead of a run-level citation that truncates at the ``$`` before the number.
_LINE_BREAK_TAGS = frozenset(
    {
        "p", "div", "tr", "table", "thead", "tbody", "tfoot", "br", "hr",
        "li", "ul", "ol", "dl", "caption", "h1", "h2", "h3", "h4", "h5", "h6",
    }
)
_TAG_NAME_RE = re.compile(rb"<\s*/?\s*([a-zA-Z][a-zA-Z0-9:-]*)")


def _gap_is_line_break(gap: bytes) -> bool:
    """True if the markup between two runs opens/closes a line-breaking block element."""
    return any(
        m.group(1).decode("latin-1").lower() in _LINE_BREAK_TAGS
        for m in _TAG_NAME_RE.finditer(gap)
    )


def coalesce_lines(raw: bytes, run_list: list[TextRun]) -> list[TextRun]:
    """Merge adjacent text runs into citable *lines* (a coarser, complete-figure granularity).

    Consecutive runs are joined into one line unless the raw markup between them contains a
    line-breaking block tag (:data:`_LINE_BREAK_TAGS`). Each returned :class:`TextRun` spans
    ``[first_run.byte_start, last_run.byte_end)`` — so its ``raw[byte_start:byte_end]`` slice
    still round-trips byte-for-byte (Invariant 1) — and its ``text`` is the space-joined cleaned
    text of the merged runs (matching the block-tag → space normalization the ground truth uses,
    so a figure/table-row quote is a contiguous substring of the line). Deterministic: a pure
    function of ``raw`` and ``run_list`` in document order.
    """
    lines: list[TextRun] = []
    start: int | None = None
    end = 0
    parts: list[str] = []
    prev_end = 0
    for r in run_list:
        if start is None:
            start, end, parts, prev_end = r.byte_start, r.byte_end, [r.text], r.byte_end
            continue
        if _gap_is_line_break(raw[prev_end : r.byte_start]):
            lines.append(TextRun(byte_start=start, byte_end=end, text=" ".join(parts)))
            start, end, parts = r.byte_start, r.byte_end, [r.text]
        else:
            end = r.byte_end
            parts.append(r.text)
        prev_end = r.byte_end
    if start is not None:
        lines.append(TextRun(byte_start=start, byte_end=end, text=" ".join(parts)))
    return lines
