"""WP5 acceptance-test support — the SINGLE coupling point to the ``cite/`` surface.

Separate blind *test-author* deliverable for the integrity-critical citation core (playbook
9: the implementer never solely authors the tests that certify their own claim gate). Like
``_wp4_support`` this is a plain helper (leading underscore, no ``test_`` prefix) so pytest
does not collect it; the acceptance assertions live in the ``test_wp5_*.py`` files and import
from here.

It deliberately does **not** read the implementer's in-progress ``app/cite/`` source. Instead
it pins, in one place, the *public contract* WP5 must expose (``builder`` / ``resolver`` /
``claim_gate`` / ``digest``) and fails LOUDLY with a clear ``AssertionError`` if that surface
is absent or the wrong shape — so an interface drift is one obvious reconciliation point, never
a silent skip. The ``cite`` import is LAZY (inside :func:`cite_surface`) so this module and the
test files import cleanly even before the implementer branch is merged; only the cite-dependent
assertions fail (loudly) until then.

All inputs are the recorded fixtures under ``app/tests/fixtures/sec/`` (helpers reused from
``_wp4_support``): NEVER a live sec.gov call, NEVER an LLM call. Deltas are produced by the real
WP4 engine (``filings.delta.compute_deltas``) over the fixture pairs, then fed through
builder -> resolver -> claim_gate -> digest exactly as the pipeline will.

Filing rows are constructed WITH ``primary_doc=META[key].filename`` so the WP4 engine mints
``Delta.edgar_url`` ending in the REAL primary-document filename (a working archive URL), which
the builder copies onto each Citation and the digest renders.
"""
from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from core.clock import FrozenClock
from schemas.models.filing import Filing
from filings.raw_store import RawStore
from tests.filings import _wp4_support as S

# A pinned instant so any citation ``created_ts`` / digest timestamp is byte-stable across runs
# (the DETERMINISM acceptance clause). Injected into build_citations / render_digest.
FROZEN = FrozenClock(datetime(2026, 7, 9, 8, 0, 0, tzinfo=UTC))

# accession string -> FilingMeta, for the expected working EDGAR URL of any cited filing.
META_BY_ACCESSION = {m.accession: m for m in S.META.values()}


# --------------------------------------------------------------------------------------
# Filing construction — WITH primary_doc so the engine builds real working URLs.
# --------------------------------------------------------------------------------------
def filing_for(meta) -> Filing:
    """A WP1a ``Filing`` row carrying the REAL primary-document filename.

    ``_wp4_support.make_filing`` omits ``primary_doc`` (the WP4 golden tests don't need real
    URLs); here we set it so ``Delta.edgar_url`` ends in the actual document name and the
    digest's citation URLs are the working ones a reader clicks.
    """
    return Filing(
        cik=meta.cik,
        accession_no=meta.accession,
        form_type=meta.form,
        filing_date=meta.filed,
        detection_ts=datetime(2026, 7, 8, 9, 30, tzinfo=UTC),
        raw_ref=meta.accession,
        primary_doc=meta.filename,
    )


def expected_url(accession: str) -> str:
    """The working EDGAR archive URL the engine/digest must produce for ``accession``
    (``/Archives/edgar/data/<cik-int>/<accession-nodash>/<real-filename>``)."""
    return META_BY_ACCESSION[accession].edgar_url


def _ensure_written(store: RawStore, meta) -> None:
    if not store.exists(meta.accession):
        store.write(meta.accession, S.raw_bytes_for(meta.accession))


def _facts(pair: str, cur_meta, prior_key):
    cur_facts: list = []
    prior_facts: list = []
    if pair in S.PAIRS_WITH_FACTS:
        cur_facts = S.xbrl_facts_for(cur_meta.accession, cur_meta.cik)
        if prior_key is not None:
            prior_facts = S.xbrl_facts_for(S.META[prior_key].accession, cur_meta.cik)
    return cur_facts, prior_facts


# --------------------------------------------------------------------------------------
# Delta-set builders — run the real WP4 engine over fixtures into a caller-owned store.
# --------------------------------------------------------------------------------------
def pair_set(pair: str, root: Path, *, clock=FROZEN):
    """Compute the WP4 ``Delta`` rows for one fixture pair into a fresh ``RawStore`` under
    ``root`` and return ``(deltas, filings, store)``.

    ``filings`` maps EVERY accession the deltas cite (current AND, for a before/after pair, the
    prior filing — a REMOVED passage is cited to the prior filing) to a ``Filing`` with
    ``primary_doc`` set, so the builder never fails closed for a missing filing and the digest
    renders real URLs.
    """
    from filings.delta import compute_deltas

    cur_key, prior_key = S.PAIR_SPECS[pair]
    cur_meta = S.META[cur_key]
    store = RawStore(root)
    _ensure_written(store, cur_meta)

    prior = None
    filings: dict[str, Filing] = {cur_meta.accession: filing_for(cur_meta)}
    if prior_key is not None:
        prior_meta = S.META[prior_key]
        _ensure_written(store, prior_meta)
        prior = filing_for(prior_meta)
        filings[prior_meta.accession] = prior

    cur_facts, prior_facts = _facts(pair, cur_meta, prior_key)
    deltas = compute_deltas(
        filings[cur_meta.accession], prior, store, cur_facts, prior_facts, clock=clock
    )
    _assert_covered(deltas, filings, pair)
    return deltas, filings, store


def two_8k_set(root: Path, *, clock=FROZEN):
    """A SMALL, fast, multi-accession set: the two NVDA 8-Ks (each an unpaired per-filing
    extraction) computed into ONE store and combined into a 2-delta set spanning 2 accessions.

    Ideal for the corruption / orphan / reuse cases (they need >=2 deltas and a second, valid
    accession in the same store to swap a ``raw_ref`` to) without paying the cost of the 600+
    delta 10-Q pairs.
    """
    from filings.delta import compute_deltas

    store = RawStore(root)
    deltas: list = []
    filings: dict[str, Filing] = {}
    for key in ("NVDA_8K_A", "NVDA_8K_B"):
        meta = S.META[key]
        _ensure_written(store, meta)
        filing = filing_for(meta)
        filings[meta.accession] = filing
        deltas.extend(compute_deltas(filing, None, store, [], [], clock=clock))
    _assert_covered(deltas, filings, "NVDA8K_A+B")
    return deltas, filings, store


def _assert_covered(deltas, filings, label: str) -> None:
    cited = {d.accession_no for d in deltas}
    missing = cited - set(filings)
    assert not missing, (
        f"{label}: engine emitted deltas citing accessions absent from the filings map "
        f"{sorted(missing)} — fixture set-up bug, not an implementer bug"
    )
    assert deltas, f"{label}: engine produced zero deltas over the fixtures (fixture bug)"


# --------------------------------------------------------------------------------------
# Direct byte-anchor check (the resolver must reproduce this mechanically).
# --------------------------------------------------------------------------------------
def raw_slice(store: RawStore, raw_ref: str, byte_start: int, byte_end: int) -> str:
    """``raw_store.resolve(raw_ref)[byte_start:byte_end].decode('utf-8')`` — the anchor
    contract the resolver reproduces. Integrity-checked (``resolve``, not ``read``)."""
    return store.resolve(raw_ref)[byte_start:byte_end].decode("utf-8")


def blob_path(root: Path, accession: str) -> Path:
    """On-disk path of a stored raw blob (RawStore layout ``<root>/<accession>/raw.bin``) —
    used to MUTATE bytes below the integrity layer for the tamper-detection case."""
    return Path(root) / accession / "raw.bin"


# --------------------------------------------------------------------------------------
# The cite surface — the single interface-coupling point (lazy import, loud on drift).
# --------------------------------------------------------------------------------------
_REQUIRED = [
    ("filings.cite.builder", "build_citations"),
    ("filings.cite.builder", "build_citation"),
    ("filings.cite.resolver", "resolve_citation"),
    ("filings.cite.resolver", "resolve_all"),
    ("filings.cite.claim_gate", "evaluate"),
    ("filings.cite.digest", "render_digest"),
]

_CONTRACT_MSG = (
    "WP5 cite/ public surface not importable/complete. The acceptance tests require the "
    "modules filings.cite.builder (build_citations/build_citation), filings.cite.resolver "
    "(resolve_citation/resolve_all), filings.cite.claim_gate (evaluate) and filings.cite.digest "
    "(render_digest) per the pinned WP5 contract."
)


def cite_surface() -> SimpleNamespace:
    """Import and return the ``cite`` modules (``.builder/.resolver/.claim_gate/.digest``),
    raising a clear ``AssertionError`` if the surface is missing or the wrong shape."""
    mods: dict[str, object] = {}
    for mod_name, fn_name in _REQUIRED:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:  # pragma: no cover - pre-merge only
            raise AssertionError(f"{_CONTRACT_MSG}\nmissing module {mod_name}: {exc}") from exc
        if not callable(getattr(mod, fn_name, None)):
            raise AssertionError(f"{_CONTRACT_MSG}\n{mod_name} lacks callable {fn_name}")
        mods[mod_name.rsplit(".", 1)[-1]] = mod
    return SimpleNamespace(
        builder=mods["builder"],
        resolver=mods["resolver"],
        claim_gate=mods["claim_gate"],
        digest=mods["digest"],
    )
