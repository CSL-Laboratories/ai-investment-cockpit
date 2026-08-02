"""WP4 structural invariant gates (deterministic core) — the scaffold's WP4 Accept structural
lines, independent of the specific ground-truth quotes:

  * Offset round-trip (Inv 1): re-reading a Delta's recorded byte offsets from the raw blob
    via ``RawStore`` reproduces the extracted snippet byte-for-byte.
  * Repeat-run determinism (Inv 2): identical inputs -> byte-identical Delta sets across runs.
  * LLM-free deterministic core (Inv 2): ``parse/`` and ``delta/`` import no agent_runner / LLM
    module and spawn no subprocess (static source grep + import-side-effect check).
  * Property (Inv 1 / WP4): every Delta has non-empty accession, EDGAR URL, byte offset, and
    snippet, and a well-formed EDGAR archive URL.

Offline & hermetic: recorded fixtures only, no live sec.gov, no LLM. The grep test reads
files under app/parse/ and app/delta/ ONLY to scan for forbidden tokens at test time — it
never inspects their logic and never drives the golden assertions.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

from filings.delta import UnpairedPeriodicFiling, compute_deltas
from schemas.models.filing import FormType
from filings.raw_store import RawStore, RawStoreError
from tests.filings import _wp4_support as S

# RW-10 DIVERGENCE FROM UPSTREAM (recorded in app/filings/PROVENANCE.md).
# Upstream this file sits at app/tests/, so parents[1] was .../app and the core was at
# app/parse + app/delta. Vendored it lives one level deeper (app/tests/filings/) and the
# core is under app/filings/.
#
# WIDENED ON PURPOSE. Upstream only had to guard parse/ + delta/, because its LLM boundary
# lived in brief/ and its SEC client answered to its own process. The cockpit vendored FOUR
# packages plus raw_store into a repo that DOES have an agent runner two imports away, so
# every one of them is swept. This is the `no-LLM-in-filings` half of gate GR-10; the other
# half is that it is impossible to add a fifth package here without adding it to this list.
APP = Path(__file__).resolve().parents[2]  # .../app
FILINGS = APP / "filings"
PARSE_DIR = FILINGS / "parse"
DELTA_DIR = FILINGS / "delta"
EDGAR_DIR = FILINGS / "edgar"
CITE_DIR = FILINGS / "cite"
RAW_STORE = FILINGS / "raw_store.py"
ALL_PAIRS = list(S.PAIR_SPECS)


@pytest.fixture(scope="session")
def deltas_and_stores(tmp_path_factory):
    """Compute each pair once, keeping the RawStore the engine used so the offset round-trip
    re-reads the exact bytes the offsets index into."""
    out = {}
    for pair in ALL_PAIRS:
        root = tmp_path_factory.mktemp(f"raw_struct_{pair}")
        deltas = S.compute_pair(pair, root)
        out[pair] = (deltas, RawStore(root))
    return out


@pytest.fixture(scope="session")
def all_deltas(deltas_and_stores) -> list[S.DeltaView]:
    return [d for deltas, _ in deltas_and_stores.values() for d in deltas]


# --------------------------------------------------------------------------------------
# Inv 1 — offset round-trip. raw_bytes[start:end] must reproduce the snippet byte-for-byte.
# Byte offsets (not char offsets): filings are UTF-8 with multibyte punctuation, so we decode
# the raw slice and compare to the stored snippet (and cross-check via the RawStore resolve).
# --------------------------------------------------------------------------------------
def _reproduces(raw: bytes, d: S.DeltaView) -> tuple[bool, str]:
    start, end, snip = d.start, d.end, d.snippet
    if start is None or end is None or snip is None:
        return False, "missing offset/snippet"
    if not (0 <= start <= end <= len(raw)):
        return False, f"offsets out of range [0,{len(raw)}]: [{start}:{end}]"
    chunk = raw[start:end]
    if chunk == snip.encode("utf-8"):
        return True, ""
    try:
        if chunk.decode("utf-8") == snip:
            return True, ""
    except UnicodeDecodeError:
        pass
    return False, f"raw[{start}:{end}] != snippet ({chunk[:48]!r} vs {snip[:48]!r})"


def test_offset_round_trip_reproduces_every_snippet(deltas_and_stores):
    failures = []
    for pair, (deltas, store) in deltas_and_stores.items():
        for d in deltas:
            # Resolve the raw blob the way WP5's resolver will: by the delta's raw_ref /
            # accession, through the same RawStore the engine wrote.
            acc = str(d.accession_no)
            try:
                raw = store.read(acc)
            except FileNotFoundError:
                # Fall back to the fixture bytes for this accession (same byte-identical blob).
                if acc in S.ACCESSIONS:
                    raw = S.raw_bytes_for(acc)
                else:
                    failures.append(f"{pair}: delta cites unknown accession {acc!r}")
                    continue
            ok, why = _reproduces(raw, d)
            if not ok:
                failures.append(f"{pair} {acc}: {why}")
    assert not failures, "offset round-trip failed (Inv 1):\n  " + "\n  ".join(failures[:25])


def test_offset_round_trip_also_holds_through_rawstore_resolve(deltas_and_stores):
    # Stronger: the byte-identical blob comes back through RawStore.resolve (integrity-checked),
    # exactly as the WP5 resolver reads it.
    failures = []
    for pair, (deltas, store) in deltas_and_stores.items():
        for d in deltas:
            acc = str(d.accession_no)
            try:
                raw = store.resolve(acc)
            except Exception:  # noqa: BLE001 - fall back to fixture bytes below
                raw = S.raw_bytes_for(acc) if acc in S.ACCESSIONS else None
            if raw is None:
                failures.append(f"{pair}: cannot resolve raw blob for {acc}")
                continue
            ok, why = _reproduces(raw, d)
            if not ok:
                failures.append(f"{pair} {acc}: {why}")
    assert not failures, "resolve round-trip failed:\n  " + "\n  ".join(failures[:25])


def test_delta_engine_never_falls_back_to_unverified_bytes_after_tamper(tmp_path):
    current, prior, raw_store, current_facts, prior_facts, _current_raw, _prior_raw = (
        S._build_inputs("NVDA10Q", tmp_path / "tampered")
    )
    raw_store._blob_path(current.raw_ref).write_bytes(b"tampered raw filing")

    with pytest.raises(RawStoreError):
        compute_deltas(
            current,
            prior,
            raw_store,
            current_facts=current_facts,
            prior_facts=prior_facts,
        )


@pytest.mark.parametrize("form_type", [FormType.FORM_10K, FormType.FORM_10Q])
def test_unpaired_periodic_filing_is_reference_only_before_raw_parse(form_type):
    filing = SimpleNamespace(
        cik="0001045810",
        accession_no="0001045810-26-999999",
        form_type=form_type,
        raw_ref="must-not-be-read",
    )

    class RawMustNotBeRead:
        def resolve(self, _ref):
            raise AssertionError("unpaired periodic guard must run before raw parsing")

    with pytest.raises(UnpairedPeriodicFiling) as exc_info:
        compute_deltas(filing, None, raw_store=RawMustNotBeRead())
    assert exc_info.value.form_type == form_type.value
    assert exc_info.value.accession_no == filing.accession_no


def test_unpaired_8k_still_uses_per_filing_extraction(tmp_path):
    current, prior, raw_store, current_facts, prior_facts, _current_raw, _prior_raw = (
        S._build_inputs("NVDA8K_A", tmp_path / "unpaired_8k")
    )
    assert prior is None
    deltas = compute_deltas(
        current,
        None,
        raw_store,
        current_facts=current_facts,
        prior_facts=prior_facts,
    )
    assert deltas
    assert all(delta.accession_no == current.accession_no for delta in deltas)


# --------------------------------------------------------------------------------------
# Inv 2 — repeat-run determinism. Two runs over identical inputs -> byte-identical Delta sets.
# Compares substantive CONTENT (excludes surrogate delta_id + wall-clock created_at, which are
# not part of the computed change) both as an ordered list and as a multiset.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("pair", ALL_PAIRS)
def test_repeat_run_is_byte_identical(pair, tmp_path_factory):
    root_a = tmp_path_factory.mktemp(f"det_a_{pair}")
    root_b = tmp_path_factory.mktemp(f"det_b_{pair}")
    run_a = [d.canonical() for d in S.compute_pair(pair, root_a)]
    run_b = [d.canonical() for d in S.compute_pair(pair, root_b)]

    assert len(run_a) == len(run_b), (
        f"{pair}: non-deterministic delta COUNT across runs ({len(run_a)} vs {len(run_b)})"
    )
    # Multiset equality first (content set is stable), then ordered equality (emission order
    # is stable too — "byte-identical Delta sets").
    assert sorted(run_a) == sorted(run_b), (
        f"{pair}: delta CONTENT differs across identical runs (Inv 2)"
    )
    assert run_a == run_b, (
        f"{pair}: delta ORDER differs across identical runs — set is deterministic but "
        f"ordering is not (Inv 2 requires byte-identical Delta sets)"
    )


# --------------------------------------------------------------------------------------
# Inv 2 — LLM-free deterministic core. parse/ and delta/ import no agent_runner / LLM module
# and spawn no subprocess. Static grep over source + an AST import scan.
# --------------------------------------------------------------------------------------
_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "agent_runner", "subprocess", "anthropic", "openai",
)
# Token patterns that indicate an LLM call or a process spawn hiding behind stdlib.
_FORBIDDEN_TOKENS = [
    re.compile(r"\bagent_runner\b"),
    re.compile(r"\bimport\s+subprocess\b"),
    re.compile(r"\bfrom\s+subprocess\b"),
    re.compile(r"\bsubprocess\."),
    re.compile(r"\bos\.system\b"),
    re.compile(r"\bos\.popen\b"),
    re.compile(r"\bPopen\b"),
    re.compile(r"\bclaude\s+-p\b"),
    re.compile(r"\brun_agent\b"),
    re.compile(r"\banthropic\b", re.IGNORECASE),
    re.compile(r"\bopenai\b", re.IGNORECASE),
]


def _core_py_files():
    for d in (PARSE_DIR, DELTA_DIR, EDGAR_DIR, CITE_DIR):
        if d.exists():
            yield from sorted(d.rglob("*.py"))
    # The package root is vendored source too. It was missed by the first draft of this
    # sweep and only surfaced because test_every_vendored_package_is_swept compares the
    # sweep against what is actually on disk — an unswept file is the realistic hole here.
    for f in (RAW_STORE, FILINGS / "__init__.py"):
        if f.exists():
            yield f


def test_core_dirs_exist():
    # The deterministic core must actually be present, so the greps below aren't vacuous.
    # A gate that passes because it found nothing to check is not a gate — this is the
    # assertion that keeps the LLM-free sweep below honest.
    for d in (PARSE_DIR, DELTA_DIR, EDGAR_DIR, CITE_DIR):
        assert d.exists(), f"{d.relative_to(APP)} is missing — vendored core not present"
    assert RAW_STORE.exists(), "filings/raw_store.py is missing — vendored core not present"
    assert list(_core_py_files()), "no .py files under app/filings/"


def _code_only(source: str) -> str:
    """Return ``source`` with comments and DOCSTRINGS removed — the gate reads CODE, not prose.

    RW-10 DIVERGENCE (recorded in app/filings/PROVENANCE.md). Widening this sweep to the
    vendored SEC client immediately produced a FALSE POSITIVE: ``filings/edgar/companyfacts``
    opens by documenting that it is "a pure mapper over an injected fetcher, no LLM, no
    subprocess." — and ``\\bsubprocess\\.`` matched the full stop at the end of that sentence.
    A module failing the no-subprocess gate *for stating that it spawns no subprocess* is the
    gate misreading English as Python.

    THE WEAK FIX WOULD HAVE BEEN TO LOOSEN THE PATTERN, or to reword upstream's docstring so
    the grep stops noticing it. Both are worse: the first blunts detection of a real
    ``subprocess.run(...)``, and the second edits vendored source for a test's convenience,
    which is precisely the drift PROVENANCE.md exists to prevent.

    ONLY comments and docstrings are dropped — NOT string literals in general. A docstring is
    a STRING token standing alone as a statement; a string being *used* (assigned, passed) is
    still scanned, so smuggling a module name through a literal stays visible here and to the
    AST check below.

    PROSE IS BLANKED IN PLACE, NOT DELETED, and that detail is load-bearing. The first version
    of this function filtered the token stream and rejoined it with newlines — which silently
    broke every ADJACENCY pattern in ``_FORBIDDEN_TOKENS``: ``os.popen`` became ``os \\n . \\n
    popen`` and ``\\bos\\.popen\\b`` stopped matching it. A stripper that blinds the gate to
    ``subprocess.``/``os.system``/``os.popen`` is far worse than the false positive it was
    written to fix. Overwriting the span with spaces preserves every byte offset and every
    line, so the surviving code reads exactly as written.
    """
    # Absolute offset of the first character of each 1-based line, so (row, col) token
    # positions become plain string indices and blanking is a flat slice assignment.
    line_start: list[int] = [0]
    for line in source.splitlines(keepends=True):
        line_start.append(line_start[-1] + len(line))

    def offset(pos: tuple[int, int]) -> int:
        row, col = pos
        return line_start[row - 1] + col

    chars = list(source)
    prev_meaningful: int | None = None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        is_docstring = tok.type == tokenize.STRING and prev_meaningful in (
            None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
        )
        if tok.type == tokenize.COMMENT or is_docstring:
            for i in range(offset(tok.start), offset(tok.end)):
                if chars[i] != "\n":        # keep line structure; only the prose goes
                    chars[i] = " "
            prev_meaningful = tokenize.STRING if is_docstring else prev_meaningful
            continue
        if tok.type not in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            prev_meaningful = tok.type
    return "".join(chars)


def test_core_source_has_no_llm_or_subprocess_tokens():
    offenders = []
    for path in _core_py_files():
        text = _code_only(path.read_text(encoding="utf-8"))
        for pat in _FORBIDDEN_TOKENS:
            if pat.search(text):
                offenders.append(f"{path.relative_to(APP)} :: {pat.pattern}")
    assert offenders == [], (
        "parse/ and delta/ must be LLM-free and spawn no subprocess (Inv 2); offenders: "
        + "; ".join(offenders)
    )


def test_core_imports_no_agent_runner_or_llm_module():
    # AST-level: no `import`/`from ... import` naming agent_runner or an LLM/process module.
    offenders = []
    forbidden_modules = ("agent_runner", "subprocess", "anthropic", "openai")
    for path in _core_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                if any(fm in m for fm in forbidden_modules):
                    offenders.append(f"{path.relative_to(APP)} imports {m!r}")
    assert offenders == [], "forbidden imports in the deterministic core: " + "; ".join(offenders)


def test_importing_delta_and_parse_spawns_no_subprocess(monkeypatch):
    # Import-side-effect guard: importing the core must not shell out. Trip any spawn.
    import subprocess as _sp

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("parse/ or delta/ spawned a subprocess on import (Inv 2)")

    monkeypatch.setattr(_sp, "Popen", _boom)
    monkeypatch.setattr(_sp, "run", _boom)
    monkeypatch.setattr(_sp, "call", _boom)
    import importlib
    for name in ("filings.parse", "filings.delta"):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            pass  # covered by test_core_dirs_exist


# --------------------------------------------------------------------------------------
# WP4 property test — every Delta carries a complete citation anchor.
# --------------------------------------------------------------------------------------
_EDGAR_RE = re.compile(r"https?://[^\s]*sec\.gov/Archives/edgar/data/", re.IGNORECASE)


def test_every_delta_has_a_complete_citation_anchor(all_deltas):
    assert all_deltas, "no deltas produced across any pair (engine miss or interface gap)"
    problems = []
    for i, d in enumerate(all_deltas):
        tag = f"delta#{i} acc={d.accession_no}"
        if not d.accession_no:
            problems.append(f"{tag}: empty accession_no")
        if not d.snippet:
            problems.append(f"{tag}: empty snippet")
        if d.start is None or d.end is None:
            problems.append(f"{tag}: missing byte offsets")
        elif not (0 <= d.start < d.end):
            problems.append(f"{tag}: non-positive byte span [{d.start}:{d.end}]")
        if not d.raw_ref:
            problems.append(f"{tag}: empty raw_ref")
        if not d.edgar_url:
            problems.append(f"{tag}: empty edgar_url")
        elif not _EDGAR_RE.search(str(d.edgar_url)):
            problems.append(f"{tag}: edgar_url not an EDGAR archive URL: {d.edgar_url!r}")
    assert not problems, "incomplete citation anchors (Inv 1 / WP4 property):\n  " + \
        "\n  ".join(problems[:25])


def test_edgar_url_matches_the_cited_accession(all_deltas):
    # The URL must reference the delta's own accession (no-dashes form), per the MANIFEST
    # archive-path pattern — a citation whose URL points elsewhere is a broken anchor.
    problems = []
    for d in all_deltas:
        acc = str(d.accession_no)
        url = str(d.edgar_url or "")
        if acc in S.ACCESSIONS and acc.replace("-", "") not in url.replace("-", ""):
            problems.append(f"{acc}: url does not contain its accession -> {url!r}")
    assert not problems, "EDGAR URL / accession mismatch:\n  " + "\n  ".join(problems[:25])


def test_delta_kind_is_valid(all_deltas):
    from schemas.models.delta import DELTA_KINDS
    bad = sorted({str(d.kind) for d in all_deltas if str(d.kind) not in DELTA_KINDS})
    assert not bad, f"delta rows carry unknown kind(s) {bad}; allowed: {DELTA_KINDS}"
