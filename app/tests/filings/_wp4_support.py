"""WP4 golden-test support — the ONE place every engine-interface assumption lives.

This module is the separate *test-author*'s side of the WP4 contract (playbook 9: the
implementer never solely authors the tests that certify their own delta engine). It is a
plain helper (leading underscore, no ``test_`` prefix), so pytest does not collect it — the
golden assertions live in ``test_wp4_golden_deltas.py`` and ``test_wp4_structural.py`` and
import from here.

It deliberately does **not** read the implementer's ``app/parse/`` or ``app/delta/`` source.
Instead it pins, in one place, the *public surface* WP4's deterministic core must expose so
these golden tests can drive it, and it fails LOUDLY (a clear ``AssertionError`` naming the
required contract) if that surface is absent — so an interface drift is a single, obvious
reconciliation point for the integrator, never a silent skip.

The required WP4 delta-engine contract
--------------------------------------
A callable resolvable as one of (tried in order):

  * ``filings.delta.compute_deltas``      (canonical)
  * ``filings.delta.engine.compute_deltas``
  * ``filings.delta.deltas_for_pair`` / ``compute`` / ``diff_filings`` / ``build_deltas`` / ``run``
  * ``filings.delta.engine.{compute,run}``

that accepts a *current* filing and a *prior* filing (``None`` for an unpaired 8-K
extraction) plus the raw bytes / raw store and — for numeric XBRL deltas — the current and
prior ``XbrlFact`` rows, and RETURNS an iterable of ``Delta`` rows (the WP1a
``schemas.models.delta.Delta`` contract, or a mapping with the same fields). Arguments
are bound by parameter *name* (many aliases accepted) with positional-convention fallbacks,
so the engine's exact signature is flexible; only the returned ``Delta`` shape is fixed.

Every returned Delta is expected (WP4 property invariant) to carry a non-empty
``accession_no``, ``edgar_url``, byte ``snippet_start_offset``/``snippet_end_offset``,
verbatim ``snippet``, and a resolvable ``raw_ref``; a numeric delta additionally carries
``prior_value`` / ``current_value``.

All data is the recorded fixtures under ``app/tests/fixtures/sec/`` — NEVER a live sec.gov
call, NEVER an LLM call. Ground truth = ``docs/fixture-ground-truth-draft.md`` (byte-verified
2026-07-08); every quote below was independently re-confirmed as a contiguous substring of
the normalized fixture bytes, and every numeric value against the captured companyfacts.json.
"""
from __future__ import annotations

import html
import importlib
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache, lru_cache
from pathlib import Path

from schemas.models.filing import Filing, FormType
from schemas.models.xbrlfact import XbrlFact
from filings.raw_store import RawStore

FIX = Path(__file__).resolve().parent / "fixtures" / "sec"

# --------------------------------------------------------------------------------------
# Fixture registry (keys -> accession / cik / form / path / filed-date), from MANIFEST.md
# --------------------------------------------------------------------------------------
NVDA_CIK = "0001045810"
AAPL_CIK = "0000320193"


@dataclass(frozen=True)
class FilingMeta:
    key: str
    accession: str
    cik: str
    form: FormType
    rel_path: str
    filed: date

    @property
    def path(self) -> Path:
        return FIX / self.rel_path

    @property
    def filename(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def edgar_url(self) -> str:
        # MANIFEST pattern: .../Archives/edgar/data/<cik-int>/<accession-no-dashes>/<file>
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/"
            f"{self.accession.replace('-', '')}/{self.filename}"
        )


META: dict[str, FilingMeta] = {
    "NVDA_NEW": FilingMeta("NVDA_NEW", "0001045810-26-000052", NVDA_CIK, FormType.FORM_10Q,
                           "NVDA/0001045810-26-000052/nvda-20260426.htm", date(2026, 5, 20)),
    "NVDA_OLD": FilingMeta("NVDA_OLD", "0001045810-25-000230", NVDA_CIK, FormType.FORM_10Q,
                           "NVDA/0001045810-25-000230/nvda-20251026.htm", date(2025, 11, 19)),
    "AAPL_NEW": FilingMeta("AAPL_NEW", "0000320193-26-000013", AAPL_CIK, FormType.FORM_10Q,
                           "AAPL/0000320193-26-000013/aapl-20260328.htm", date(2026, 5, 1)),
    "AAPL_OLD": FilingMeta("AAPL_OLD", "0000320193-26-000006", AAPL_CIK, FormType.FORM_10Q,
                           "AAPL/0000320193-26-000006/aapl-20251227.htm", date(2026, 1, 30)),
    "NVDA_8K_A": FilingMeta("NVDA_8K_A", "0001045810-26-000060", NVDA_CIK, FormType.FORM_8K,
                            "NVDA/0001045810-26-000060/nvda-20260628.htm", date(2026, 7, 2)),
    "NVDA_8K_B": FilingMeta("NVDA_8K_B", "0001045810-26-000056", NVDA_CIK, FormType.FORM_8K,
                            "NVDA/0001045810-26-000056/nvda-20260624.htm", date(2026, 6, 30)),
}

# accession -> fixture path, for offset round-trip (the raw blob the offsets index into).
ACCESSION_TO_PATH: dict[str, Path] = {m.accession: m.path for m in META.values()}
ACCESSIONS = {m.accession for m in META.values()}

# pair -> (current_key, prior_key | None). The two 10-Q pairs are before/after DELTAS; the
# two 8-Ks are per-filing EXTRACTION (prior=None) — unrelated events (Item 5.02 vs 5.07).
PAIR_SPECS: dict[str, tuple[str, str | None]] = {
    "NVDA10Q": ("NVDA_NEW", "NVDA_OLD"),
    "AAPL10Q": ("AAPL_NEW", "AAPL_OLD"),
    "NVDA8K_A": ("NVDA_8K_A", None),
    "NVDA8K_B": ("NVDA_8K_B", None),
}
PAIRS_WITH_FACTS = {"NVDA10Q", "AAPL10Q"}
CIK_COMPANYFACTS = {NVDA_CIK: "NVDA/companyfacts.json", AAPL_CIK: "AAPL/companyfacts.json"}


# --------------------------------------------------------------------------------------
# Text normalization — MUST match docs/fixture-ground-truth-draft.md "How to review", item 1.
# --------------------------------------------------------------------------------------
_BLOCK_TAGS = {
    "p", "div", "td", "th", "tr", "table", "br", "li", "ul", "ol",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9:-]*)\b[^>]*?>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def normalize(raw: str) -> str:
    """Tag-strip + entity-decode + NBSP-map + whitespace-collapse, exactly as the ground
    truth documents: block-level tags -> one space, all other tags removed, HTML entities
    decoded, U+00A0 -> space, whitespace runs collapsed. This is the robust matching space —
    ground-truth quotes are contiguous substrings *here*, not in the raw bytes."""
    def _repl(m: re.Match) -> str:
        return " " if m.group(2).lower() in _BLOCK_TAGS else ""

    txt = _TAG_RE.sub(_repl, raw)
    txt = _COMMENT_RE.sub(" ", txt)
    txt = _ANY_TAG_RE.sub("", txt)
    txt = html.unescape(txt)
    txt = txt.replace(" ", " ")
    return _WS_RE.sub(" ", txt).strip()


@cache
def normalized_fixture(key: str) -> str:
    return normalize(META[key].path.read_bytes().decode("utf-8", errors="replace"))


@cache
def raw_bytes_for(accession: str) -> bytes:
    return ACCESSION_TO_PATH[accession].read_bytes()


# --------------------------------------------------------------------------------------
# XbrlFact loading from the captured companyfacts.json (facts belonging to one accession).
# --------------------------------------------------------------------------------------
def _pdate(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@cache
def _companyfacts(cik: str) -> dict:
    return json.loads((FIX / CIK_COMPANYFACTS[cik]).read_text(encoding="utf-8"))


def xbrl_facts_for(accession: str, cik: str) -> list[XbrlFact]:
    """Every companyfacts data point tagged to ``accession`` -> XbrlFact rows (the numeric
    delta engine's input for that filing). Deterministic order for byte-stable Delta sets."""
    data = _companyfacts(cik)
    rows: list[XbrlFact] = []
    facts = data.get("facts", {})
    for taxonomy, concepts in facts.items():
        for concept, body in concepts.items():
            for unit, points in body.get("units", {}).items():
                for p in points:
                    if p.get("accn") != accession:
                        continue
                    rows.append(XbrlFact(
                        cik=cik, accession_no=accession, taxonomy=taxonomy,
                        concept=concept, unit=unit, value=Decimal(str(p["val"])),
                        period_start=_pdate(p.get("start")), period_end=_pdate(p["end"]),
                        fiscal_year=p.get("fy"), fiscal_period=p.get("fp"),
                        form=p.get("form"), filed=_pdate(p.get("filed")),
                        frame=p.get("frame"), raw_ref=accession,
                    ))
    rows.sort(key=lambda r: (r.taxonomy, r.concept, r.unit,
                             str(r.period_start), str(r.period_end)))
    return rows


def make_filing(meta: FilingMeta) -> Filing:
    return Filing(
        cik=meta.cik, accession_no=meta.accession, form_type=meta.form,
        filing_date=meta.filed,
        detection_ts=datetime(2026, 7, 8, 9, 30, tzinfo=UTC),
        raw_ref=meta.accession,
    )


# --------------------------------------------------------------------------------------
# Engine resolution + flexible invocation (the single interface-coupling point).
# --------------------------------------------------------------------------------------
_FUNC_CANDIDATES: list[tuple[str, str]] = [
    ("filings.delta", "compute_deltas"),
    ("filings.delta.engine", "compute_deltas"),
    ("filings.delta", "deltas_for_pair"),
    ("filings.delta", "compute"),
    ("filings.delta", "diff_filings"),
    ("filings.delta", "build_deltas"),
    ("filings.delta", "run"),
    ("filings.delta.engine", "compute"),
    ("filings.delta.engine", "run"),
]

_CONTRACT_MSG = (
    "WP4 delta-engine entry point not found. The golden tests require a callable such as\n"
    "  filings.delta.compute_deltas(current_filing, prior_filing, raw_store=..., "
    "current_facts=..., prior_facts=...)\n"
    "returning an iterable of Delta rows (WP1a schemas.models.delta.Delta, or a mapping "
    "with the same fields). prior_filing is None for an unpaired 8-K extraction. See the "
    "contract docstring in app/tests/_wp4_support.py."
)


@lru_cache(maxsize=1)
def resolve_engine():
    for mod_name, fn_name in _FUNC_CANDIDATES:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            return fn
    raise AssertionError(
        _CONTRACT_MSG + "\nTried: "
        + "; ".join(f"{m}.{f}" for m, f in _FUNC_CANDIDATES)
    )


def _invoke(fn, current, prior, raw_store, current_facts, prior_facts,
            current_bytes, prior_bytes):
    """Call ``fn`` binding arguments by parameter NAME (many aliases), with positional
    fallbacks. ``inspect.Signature.bind`` gates each convention, so a signature mismatch
    falls through while a genuine error raised *inside* the engine propagates."""
    pool = {
        "current_filing": current, "current": current, "new_filing": current,
        "newer": current, "filing": current, "current_row": current, "new": current,
        "target": current, "later": current,
        "prior_filing": prior, "prior": prior, "old_filing": prior, "older": prior,
        "previous": prior, "baseline": prior, "prior_row": prior, "old": prior,
        "predecessor": prior, "earlier": prior,
        "raw_store": raw_store, "store": raw_store, "raws": raw_store, "raw": raw_store,
        "rawstore": raw_store,
        "current_facts": current_facts, "current_xbrl": current_facts,
        "new_facts": current_facts, "facts_current": current_facts,
        "prior_facts": prior_facts, "prior_xbrl": prior_facts,
        "old_facts": prior_facts, "facts_prior": prior_facts,
        "facts": list(current_facts) + list(prior_facts),
        "xbrl_facts": list(current_facts) + list(prior_facts),
        "all_facts": list(current_facts) + list(prior_facts),
        "current_bytes": current_bytes, "prior_bytes": prior_bytes,
        "current_raw": current_bytes, "prior_raw": prior_bytes,
    }
    sig = inspect.signature(fn)
    names = set(sig.parameters)
    named = {k: v for k, v in pool.items() if k in names}
    extra_named = {k: v for k, v in {
        "raw_store": raw_store, "current_facts": current_facts,
        "prior_facts": prior_facts,
    }.items() if k in names}

    conventions: list[tuple[tuple, dict]] = [
        ((current, prior), extra_named),
        ((current, prior), {}),
        ((current, prior, raw_store), {}),
        ((current, prior, current_facts, prior_facts), {}),
        ((current, prior, raw_store, current_facts, prior_facts), {}),
    ]
    # Prefer binding by NAME when the engine actually names its parameters (so a rich signature
    # gets current/prior/raw_store/facts by keyword); only used when it binds something useful,
    # so a purely variadic ``(*args, **kwargs)`` engine falls through to the positional forms
    # below instead of being called with zero arguments.
    if named:
        conventions.insert(0, ((), named))
    last_err: TypeError | None = None
    for args, kwargs in conventions:
        try:
            sig.bind(*args, **kwargs)
        except TypeError as exc:
            last_err = exc
            continue
        return fn(*args, **kwargs)
    raise AssertionError(
        _CONTRACT_MSG + f"\nCould not bind engine signature {sig}; last error: {last_err}"
    )


# --------------------------------------------------------------------------------------
# DeltaView — a uniform read surface over an ORM Delta or a mapping.
# --------------------------------------------------------------------------------------
_NUMERIC = (int, float, Decimal, str)


@dataclass
class DeltaView:
    obj: object

    def _get(self, name):
        if isinstance(self.obj, dict):
            return self.obj.get(name)
        return getattr(self.obj, name, None)

    @property
    def kind(self):
        return self._get("kind")

    @property
    def accession_no(self):
        return self._get("accession_no") or self._get("accession")

    @property
    def edgar_url(self):
        return self._get("edgar_url") or self._get("url")

    @property
    def start(self):
        v = self._get("snippet_start_offset")
        return self._get("byte_start") if v is None else v

    @property
    def end(self):
        v = self._get("snippet_end_offset")
        return self._get("byte_end") if v is None else v

    @property
    def snippet(self):
        return self._get("snippet")

    @property
    def raw_ref(self):
        return self._get("raw_ref")

    @property
    def materiality_score(self):
        return self._get("materiality_score")

    @property
    def cik(self):
        return self._get("cik")

    @property
    def source_ref(self):
        return self._get("source_ref")

    def _num(self, name):
        v = self._get(name)
        if v is None or not isinstance(v, _NUMERIC):
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    @property
    def prior_value(self):
        return self._num("prior_value")

    @property
    def current_value(self):
        return self._num("current_value")

    @property
    def norm_snippet(self):
        s = self.snippet
        return normalize(s) if isinstance(s, str) else ""

    def canonical(self) -> tuple:
        """Content identity for determinism comparison — EXCLUDES surrogate ``delta_id`` and
        wall-clock ``created_at`` (not part of the computed change; Inv 2 is about the delta
        SET, not the surrogate key), includes everything substantive."""
        return (
            str(self.kind), str(self.accession_no), str(self.edgar_url),
            int(self.start) if self.start is not None else None,
            int(self.end) if self.end is not None else None,
            self.snippet, str(self.raw_ref),
            None if self.materiality_score is None else str(Decimal(str(self.materiality_score))),
            str(self.cik) if self.cik is not None else None,
            self.source_ref,
            None if self.prior_value is None else str(self.prior_value),
            None if self.current_value is None else str(self.current_value),
        )


# --------------------------------------------------------------------------------------
# compute_pair — build inputs from fixtures, call the engine, return DeltaViews.
# --------------------------------------------------------------------------------------
def _build_inputs(pair: str, raw_root: Path):
    cur_key, prior_key = PAIR_SPECS[pair]
    cur_meta = META[cur_key]
    store = RawStore(raw_root)
    store.write(cur_meta.accession, cur_meta.path.read_bytes())
    current = make_filing(cur_meta)
    current_bytes = raw_bytes_for(cur_meta.accession)

    prior = None
    prior_bytes = None
    if prior_key is not None:
        prior_meta = META[prior_key]
        store.write(prior_meta.accession, prior_meta.path.read_bytes())
        prior = make_filing(prior_meta)
        prior_bytes = raw_bytes_for(prior_meta.accession)

    current_facts: list[XbrlFact] = []
    prior_facts: list[XbrlFact] = []
    if pair in PAIRS_WITH_FACTS:
        current_facts = xbrl_facts_for(cur_meta.accession, cur_meta.cik)
        if prior_key is not None:
            prior_facts = xbrl_facts_for(META[prior_key].accession, cur_meta.cik)
    return current, prior, store, current_facts, prior_facts, current_bytes, prior_bytes


def compute_pair(pair: str, raw_root: Path) -> list[DeltaView]:
    """Run the WP4 delta engine over one fixture pair and return DeltaViews. ``raw_root`` is a
    caller-owned temp dir (never the real data/), so the offset round-trip can also re-read the
    store the engine used."""
    fn = resolve_engine()
    current, prior, store, cf, pf, cb, pb = _build_inputs(pair, raw_root)
    result = _invoke(fn, current, prior, store, cf, pf, cb, pb)
    if result is None:
        raise AssertionError(
            f"{pair}: engine returned None; the WP4 contract requires an iterable of Delta "
            "rows to be RETURNED (these tests do not assume a shared DB session). "
            + _CONTRACT_MSG
        )
    views = [DeltaView(d) for d in result]
    # Restrict to deltas actually anchored in this pair's fixtures (an engine may legitimately
    # only ever emit those; this also keeps cross-pair assertions clean).
    return views


# --------------------------------------------------------------------------------------
# GROUND TRUTH — byte-verified candidate golden deltas (docs/fixture-ground-truth-draft.md).
# Each text bullet: which fixture the verbatim quote lives in (=> expected accession) + quote.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TextExpectation:
    id: str
    pair: str
    fixture_key: str  # the filing the verbatim quote is a substring of => expected accession
    quote: str

    @property
    def accession(self) -> str:
        return META[self.fixture_key].accession


TEXT_DELTAS: list[TextExpectation] = [
    # ---- Pair 1: NVDA 10-Q (before/after) ----
    TextExpectation("nvda-riskbaseline", "NVDA10Q", "NVDA_NEW",
        "Other than the risk factors listed below, there have been no material changes from "
        "the risk factors previously described under Item 1A of our Annual Report on Form 10-K "
        "for the fiscal year ended January 25, 2026."),
    TextExpectation("nvda-competition-added", "NVDA10Q", "NVDA_NEW",
        "Competition could adversely impact our market share and financial results."),
    TextExpectation("nvda-acquisitions-removed", "NVDA10Q", "NVDA_OLD",
        "We may not be able to realize the potential benefits of business investments or "
        "acquisitions, and we may not be able to successfully integrate acquired companies"),
    TextExpectation("nvda-h200-event", "NVDA10Q", "NVDA_NEW",
        "Beginning in February 2026, the USG granted licenses that would allow us to ship "
        "small amounts of H200 products to specific China-based customers."),
    TextExpectation("nvda-h20-old", "NVDA10Q", "NVDA_OLD",
        "In August 2025, the USG granted licenses that would allow us to ship certain H20 "
        "products to certain China-based customers, but to date, we have generated "
        "approximately $50 million in H20 revenue under those licenses."),
    TextExpectation("nvda-rubin", "NVDA10Q", "NVDA_NEW",
        "We expect our Rubin platform to start shipping in the second half of fiscal year 2027."),
    TextExpectation("nvda-new-framework", "NVDA10Q", "NVDA_NEW",
        "Following the rapid evolution in our businesses, we are transitioning to a new "
        "reporting framework that better reflects our current and future growth drivers."),
    TextExpectation("nvda-commit-119", "NVDA10Q", "NVDA_NEW",
        "As of April 26, 2026, these commitments were $119 billion for which $95 billion will "
        "be paid in the remainder of fiscal year 2027"),
    TextExpectation("nvda-commit-50-old", "NVDA10Q", "NVDA_OLD",
        "As of October 26, 2025, these commitments were $50.3 billion, of which substantially "
        "all will be paid through fiscal year 2027."),
    TextExpectation("nvda-cloud-30", "NVDA10Q", "NVDA_NEW",
        "Multi-year cloud service agreement commitments as of April 26, 2026, were $30 billion"),
    TextExpectation("nvda-cloud-26-old", "NVDA10Q", "NVDA_OLD",
        "Multi-year cloud service agreement commitments as of October 26, 2025, were $26 billion"),
    TextExpectation("nvda-invest-27", "NVDA10Q", "NVDA_NEW",
        "Total Investment commitments were $27 billion as of April 26, 2026, subject to "
        "certain contingencies, which we expect will be made through the remainder of fiscal "
        "year 2027."),
    TextExpectation("nvda-invest-65-old", "NVDA10Q", "NVDA_OLD",
        "Investment commitments are $6.5 billion as of October 26, 2025, including $5 billion "
        "in Intel Corporation which is subject to regulatory approval."),
    TextExpectation("nvda-customers-3", "NVDA10Q", "NVDA_NEW",
        "For the first quarter of fiscal year 2027, three direct customers represented 21%, "
        "17%, and 16% of total revenue, all of which was primarily attributable to the "
        "Compute & Networking segment."),
    TextExpectation("nvda-dividend-25x", "NVDA10Q", "NVDA_NEW",
        "On May 18, 2026, we increased our quarterly cash dividend from $0.01 per share to "
        "$0.25 per share to all shareholders of record on June 4, 2026. Our quarterly cash "
        "dividend will be paid on June 26, 2026."),
    TextExpectation("nvda-buyback-80", "NVDA10Q", "NVDA_NEW",
        "On May 18, 2026, our Board of Directors approved an additional $80.0 billion in "
        "share repurchase authorization, without expiration."),
    TextExpectation("nvda-buyback-60-old", "NVDA10Q", "NVDA_OLD",
        "On August 26, 2025, our Board of Directors approved an additional $60.0 billion in "
        "share repurchase authorization, without expiration."),
    TextExpectation("nvda-classcert", "NVDA10Q", "NVDA_NEW",
        "On March 25, 2026, the district court granted plaintiffs’ motion for class "
        "certification and certified a class of investors consisting of all persons or "
        "entities who purchased or otherwise acquired NVIDIA common stock between August 10, "
        "2017, and November 15, 2018, inclusive"),
    TextExpectation("nvda-23f-appeal", "NVDA10Q", "NVDA_NEW",
        "On April 8, 2026, NVIDIA filed a petition with the Ninth Circuit for permission to "
        "appeal the district court’s order pursuant to Federal Rule of Civil Procedure "
        "23(f)."),
    TextExpectation("nvda-repurchase-text", "NVDA10Q", "NVDA_NEW",
        "We repurchased 108 million shares of our common stock for $20.2 billion during the "
        "first quarter of fiscal year 2027."),
    # ---- Pair 2: AAPL 10-Q (before/after) ----
    TextExpectation("aapl-riskbaseline", "AAPL10Q", "AAPL_NEW",
        "Except as set forth below, there have been no material changes to the Company’s "
        "risk factors since the 2025 Form 10-K."),
    TextExpectation("aapl-gm-riskfactor", "AAPL10Q", "AAPL_NEW",
        "The Company’s net sales and gross margins are subject to volatility and downward "
        "pressure due to a variety of factors."),
    TextExpectation("aapl-buyback-100", "AAPL10Q", "AAPL_NEW",
        "On April 30, 2026, the Company announced the Board of Directors had authorized an "
        "additional program to repurchase up to $100 billion of the Company’s common stock"),
    TextExpectation("aapl-dividend-27", "AAPL10Q", "AAPL_NEW",
        "On April 30, 2026, the Company also announced the Board of Directors raised the "
        "Company’s quarterly cash dividend from $0.26 to $0.27 per share, beginning with "
        "the dividend to be paid during the third quarter of 2026."),
    TextExpectation("aapl-supply-constraint", "AAPL10Q", "AAPL_NEW",
        "The Company is experiencing a period of supply constraints and increasing costs for "
        "components driven by factors such as industry supply-demand imbalances for "
        "components, including advanced semiconductors, storage (NAND) and memory (DRAM). The "
        "Company expects these trends to intensify"),
    TextExpectation("aapl-repatriation", "AAPL10Q", "AAPL_NEW",
        "During the first six months of 2026, the Company paid the remaining $8.8 billion "
        "balance of the deemed repatriation tax payable imposed by the U.S. Tax Cuts and Jobs "
        "Act of 2017."),
    TextExpectation("aapl-epic-removed", "AAPL10Q", "AAPL_OLD",
        "The deadline for the Company to file a petition for rehearing is March 2, 2026."),
    TextExpectation("aapl-iphone-table", "AAPL10Q", "AAPL_NEW",
        "iPhone $ 56,994 $ 46,841 22 %"),
    TextExpectation("aapl-services-table", "AAPL10Q", "AAPL_NEW",
        "Services 30,976 26,645 16 %"),
    TextExpectation("aapl-china-table", "AAPL10Q", "AAPL_NEW",
        "Greater China 20,497 16,002 28 %"),
    TextExpectation("aapl-china-md", "AAPL10Q", "AAPL_NEW",
        "Greater China net sales increased during the second quarter and first six months of "
        "2026 compared to the same periods in 2025 due to higher net sales of iPhone."),
    # ---- Pair 3A: NVDA 8-K Item 5.02 (per-filing extraction) ----
    TextExpectation("8ka-puri-retire", "NVDA8K_A", "NVDA_8K_A",
        "On June 28, 2026, Ajay K. Puri, Executive Vice President, Worldwide Field Operations "
        "of NVIDIA Corporation, or the Company, notified the Company of his intention to "
        "retire from his role effective upon the employment commencement date of his successor."),
    TextExpectation("8ka-puri-advisory", "NVDA8K_A", "NVDA_8K_A",
        "Mr. Puri, a 21-year executive at NVIDIA, is expected to remain with the Company in a "
        "senior advisory role to ensure a seamless transition to his successor."),
    TextExpectation("8ka-parker-appoint", "NVDA8K_A", "NVDA_8K_A",
        "On July 1, 2026, the Company appointed Nicholas Parker, age 55, as Executive Vice "
        "President, Worldwide Field Operations of the Company, effective upon the commencement "
        "of his employment with the Company, which is anticipated to be on August 24, 2026."),
    TextExpectation("8ka-parker-msft", "NVDA8K_A", "NVDA_8K_A",
        "Mr. Parker will join the Company after 26 years at Microsoft Corporation"),
    TextExpectation("8ka-salary", "NVDA8K_A", "NVDA_8K_A",
        "Mr. Parker’s annual base salary will be $1,000,000 and he will receive a "
        "sign-on bonus of $5,000,000."),
    TextExpectation("8ka-varcomp", "NVDA8K_A", "NVDA_8K_A",
        "target annualized cash variable compensation of $1,500,000"),
    TextExpectation("8ka-rsu", "NVDA8K_A", "NVDA_8K_A",
        "restricted stock units, or RSUs, with a target value of $35,000,000"),
    TextExpectation("8ka-psu", "NVDA8K_A", "NVDA_8K_A",
        "multi-year performance-based RSUs, or MY PSUs, with a target value of $5,000,000"),
    # ---- Pair 3B: NVDA 8-K Item 5.07 (per-filing extraction) ----
    TextExpectation("8kb-meeting", "NVDA8K_B", "NVDA_8K_B",
        "On June 24, 2026, at the 2026 Annual Meeting of Stockholders of NVIDIA Corporation, "
        "or the 2026 Annual Meeting, the following proposals were adopted or rejected by the "
        "margin indicated."),
    TextExpectation("8kb-directors", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders approved the election of each of our ten (10) director nominees to hold "
        "office until the 2027 Annual Meeting of Stockholders of NVIDIA Corporation and until "
        "his or her successor is elected or appointed."),
    TextExpectation("8kb-huang-votes", "NVDA8K_B", "NVDA_8K_B",
        "16,650,193,763"),
    TextExpectation("8kb-saypay", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders approved, on an advisory basis, the compensation of our named executive "
        "officers as disclosed in our definitive proxy statement on Schedule 14A for the 2026 "
        "Annual Meeting filed with the Securities and Exchange Commission on May 12, 2026."),
    TextExpectation("8kb-pwc", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders approved the ratification of the selection of PricewaterhouseCoopers "
        "LLP as our independent registered accounting firm for our fiscal year ending "
        "January 31, 2027."),
    TextExpectation("8kb-supermajority", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders approved the non-binding stockholder proposal to replace the "
        "supermajority voting provisions in our charter and bylaws with a simple majority "
        "voting standard."),
    TextExpectation("8kb-supermajority-votes", "NVDA8K_B", "NVDA_8K_B",
        "14,589,671,908"),
    TextExpectation("8kb-faith-rejected", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders did not approve the non-binding stockholder proposal to request an "
        "evaluation and report on faith-based community resource groups."),
    TextExpectation("8kb-dei-rejected", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders did not approve the non-binding stockholder proposal to request an "
        "evaluation and report on civil rights and non-discrimination related to diversity, "
        "equity, and inclusion."),
    TextExpectation("8kb-ghg-rejected", "NVDA8K_B", "NVDA_8K_B",
        "Stockholders did not approve the non-binding stockholder proposal to request a report "
        "disclosing GHG emissions from the use of the NVIDIA Corporation’s sold products."),
]


# Numeric deltas that NO reasonable materiality threshold can drop (>=29% change), each with
# the exact captured companyfacts values (dollars). Asymmetric-period concepts (YTD-vs-quarter
# repurchases) and near-flat concepts (cash, non-current marketable securities) are
# deliberately excluded from the REQUIRED set — they are covered by text deltas or are
# legitimately immaterial, so requiring them would over-constrain the engine's threshold.
@dataclass(frozen=True)
class NumericExpectation:
    id: str
    pair: str
    newer_key: str
    concept: str
    prior_value: Decimal
    current_value: Decimal

    @property
    def accession(self) -> str:
        return META[self.newer_key].accession


def _d(x: int) -> Decimal:
    return Decimal(x)


NUMERIC_DELTAS_REQUIRED: list[NumericExpectation] = [
    NumericExpectation("nvda-revenue", "NVDA10Q", "NVDA_NEW",
                       "Revenues", _d(57006000000), _d(81615000000)),
    NumericExpectation("nvda-grossprofit", "NVDA10Q", "NVDA_NEW",
                       "GrossProfit", _d(41849000000), _d(61157000000)),
    NumericExpectation("nvda-netincome", "NVDA10Q", "NVDA_NEW",
                       "NetIncomeLoss", _d(31910000000), _d(58321000000)),
    NumericExpectation("nvda-inventory", "NVDA10Q", "NVDA_NEW",
                       "InventoryNet", _d(19784000000), _d(25797000000)),
    NumericExpectation("nvda-nonmktable-equity", "NVDA10Q", "NVDA_NEW",
                       "EquitySecuritiesWithoutReadilyDeterminableFairValueAmount",
                       _d(8187000000), _d(42336000000)),
]


# "Deliberately NOT material" (unchanged between the two filings) — must not surface as a
# FOCUSED delta. Checked with the focused-length heuristic (a delta whose snippet is
# essentially this text), which degrades safely if the engine emits whole-section snippets.
@dataclass(frozen=True)
class NotMaterial:
    id: str
    pair: str
    quote: str


NOT_MATERIAL_FOCUSED: list[NotMaterial] = [
    NotMaterial("aapl-eu-fine-unchanged", "AAPL10Q",
        "the Commission fined the Company €500 million in the Article 5(4) Investigation"),
    NotMaterial("aapl-safe-harbor-unchanged", "AAPL10Q",
        "forward-looking statements, within the meaning of the Private Securities Litigation "
        "Reform Act of 1995, that involve risks and uncertainties."),
    NotMaterial("nvda-mellanox-antitrust-unchanged", "NVDA10Q",
        "September 15, 2025"),
]


# "Call-only context" — discussed by pros/press but NEVER present in the filing text. These
# must NEVER appear in any delta snippet, across every pair (the quarantine assertion).
QUARANTINE_ABSENT_PHRASES: list[str] = [
    "John Ternus",          # AAPL CEO transition — press/call only
    "largely conceded",     # Jensen Huang on China — CFO commentary/CNBC only
    "installed base",       # AAPL Services call framing
    "$109.73",              # AAPL consensus beat — 9to5Mac only
    "91.0 billion",         # NVDA Q2 FY27 guidance — 8-K exhibit, not the 10-Q
    "whisper",              # whisper-number framing — Fortune only
]
