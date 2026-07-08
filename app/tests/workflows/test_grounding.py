"""G8 — SEC-14 fail-closed grounding tests (docs/security-hardening-backlog.md SEC-14;
docs/prompt-injection-defense.md §7).

Enforces: deterministic corroboration (cited refs ⊆ dispatched packet + resolvable;
thesis entities literally present in cited raw text — catches citation-laundering); the
verifier reads an extracted slice, not raw prose; absent/errored/invalid verification ==
flagged; a model grounded=true NEVER clears; injection_predicate is the deterministic
SEC-01/SEC-21 breaker seam.
"""
from __future__ import annotations

from agent_runner.transport import ProcResult
from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from tests.workflows.conftest import FakeTransport, envelope
from workflows.constants import EVENT_GROUNDING
from workflows.grounding import (
    Corroboration,
    corroborate,
    evaluate_grounding,
    extract_slice,
    injection_predicate,
    run_grounding_check,
)

REF = "parquet://2026-07-07/events-b#0"
DISPATCHED = frozenset({REF, "parquet://2026-07-07/events-b#1"})

RAW_ITEM = {
    "source": "alpaca:benzinga", "article_id": "a-0", "event_type": "news",
    "symbols": ["AAPL"], "headline": "AAPL beats on iPhone revenue",
    "summary": "Apple raised guidance; margin at 46.2%", "ts": "2026-07-07T13:00:00+00:00",
    "raw_ref": "alpaca:benzinga:a-0", "content_hash": "c" * 64,
}


def proposal(**kw):
    base = {
        "proposal_id": "prop-1", "symbol": "AAPL", "side": "buy", "asset_type": "equity",
        "order_type": "limit", "quantity": 5, "notional": None, "time_horizon": "short",
        "thesis": "AAPL guidance raise supports upside", "invalidation": "guidance cut",
        "sizing_basis": "fixed-fraction", "source_refs": [REF],
    }
    base.update(kw)
    return base


def resolver(ref):
    if ref == REF:
        return dict(RAW_ITEM)
    raise KeyError(ref)


def check_body(**kw):
    base = {"proposal_id": "prop-1", "grounded": True, "injection_suspected": False,
            "unsupported_claims": [], "source_refs_checked": [REF]}
    base.update(kw)
    return base


# ---- corroborate (deterministic, non-LLM) ------------------------------------------
def test_corroborate_happy_path():
    c = corroborate(proposal(), DISPATCHED, resolver)
    assert isinstance(c, Corroboration)
    assert c.cited_ok and c.entities_ok and c.ok


def test_no_refs_fails():
    c = corroborate(proposal(source_refs=[]), DISPATCHED, resolver)
    assert not c.cited_ok and not c.ok


def test_foreign_ref_fails_citation_laundering():
    c = corroborate(proposal(source_refs=["parquet://2026-07-07/events-x#9"]),
                    DISPATCHED, resolver)
    assert not c.cited_ok
    assert any("not in dispatched" in r for r in c.reasons)


def test_unresolvable_ref_fails():
    c = corroborate(proposal(source_refs=["parquet://2026-07-07/events-b#1"]),
                    DISPATCHED, resolver)   # in packet but resolver raises
    assert not c.cited_ok


def test_symbol_absent_from_cited_text_fails():
    c = corroborate(proposal(symbol="TSLA", thesis="upside is coming"), DISPATCHED, resolver)
    assert not c.entities_ok
    assert any("TSLA" in r for r in c.reasons)


def test_thesis_entity_absent_fails():
    c = corroborate(proposal(thesis="AAPL beats; NVDA momentum spills over"),
                    DISPATCHED, resolver)
    assert not c.entities_ok
    assert any("NVDA" in r for r in c.reasons)


# ---- SEC-14 adversarial regressions (substring laundering / lowercase thesis / int flag) --
def test_symbol_substring_collision_flags_regression():
    """FIX 1: ticker 'AI' must NOT corroborate off substrings inside ordinary prose
    ('GAINS'/'RETAIL'/'MAINTAINS'/'AVAILABLE'/'SAID'). Old substring test passed; the
    word-token test flags it. (was entities_ok True)."""
    item = {
        "source": "x", "event_type": "news", "symbols": ["MSFT"],
        "headline": "Retail gains as chip maker maintains guidance",
        "summary": "available capacity said to persist", "ts": "", "raw_ref": "x",
    }

    def res(ref):
        if ref == REF:
            return dict(item)
        raise KeyError(ref)

    c = corroborate(proposal(symbol="AI", thesis="upside coming"), DISPATCHED, res)
    assert c.entities_ok is False
    assert any(r.startswith("proposal symbol AI") for r in c.reasons)


def test_lowercase_thesis_ticker_in_universe_flags_regression():
    """FIX 2: a lowercase/mixed-case thesis naming a known ticker (symbol_universe),
    absent from cited raw, must still require corroboration. Old UPPERCASE-only extraction
    yielded zero checked tokens (was entities_ok True)."""
    c = corroborate(proposal(thesis="rotation into nvda momentum"),
                    DISPATCHED, resolver, symbol_universe=frozenset({"NVDA"}))
    assert c.entities_ok is False
    assert any("NVDA" in r for r in c.reasons)


def test_positive_control_aapl_still_corroborates_regression():
    """G8 positive control: a corroborated AAPL proposal stays entities_ok True even with a
    symbol_universe supplied — the hardening must not introduce false-positives."""
    c = corroborate(proposal(), DISPATCHED, resolver,
                    symbol_universe=frozenset({"AAPL", "NVDA"}))
    assert c.entities_ok is True and c.ok


def test_symbol_from_prose_not_structured_symbols_flags_regression():
    """ROUND 2 FIX: a proposal symbol equal to a capitalized English word ('IT') that appears
    in the cited article PROSE — but is NOT a tagged structured symbol — must NOT corroborate.
    The MSFT article's summary contains the word 'it'; only symbols=['MSFT'] is trusted.
    (Prose-token extraction previously accepted 'IT' -> entities_ok was True.)"""
    item = {
        "source": "x", "event_type": "news", "symbols": ["MSFT"],
        "headline": "Microsoft ships it to enterprise as demand holds",
        "summary": "the company said it will expand it across all regions", "ts": "",
        "raw_ref": "x",
    }

    def res(ref):
        if ref == REF:
            return dict(item)
        raise KeyError(ref)

    c = corroborate(proposal(symbol="IT", thesis="upside coming"), DISPATCHED, res)
    assert c.entities_ok is False
    assert any(r.startswith("proposal symbol IT") for r in c.reasons)


def test_thesis_ticker_from_prose_not_structured_symbols_flags_regression():
    """ROUND 2 FIX (thesis leg): an ALL-CAPS thesis ticker present in cited PROSE but not a
    tagged structured symbol must not corroborate. 'ALL' appears in the summary word 'all'
    yet symbols=['MSFT'] only."""
    item = {
        "source": "x", "event_type": "news", "symbols": ["MSFT"],
        "headline": "Microsoft raises outlook", "summary": "gains across all product lines",
        "ts": "", "raw_ref": "x",
    }

    def res(ref):
        if ref == REF:
            return dict(item)
        raise KeyError(ref)

    c = corroborate(proposal(symbol="MSFT", thesis="MSFT strength; ALL segments up"),
                    DISPATCHED, res)
    assert c.entities_ok is False
    assert any(r == "thesis entity ALL absent from cited symbols" for r in c.reasons)


def test_injection_predicate_int_grounding_flag_regression():
    """FIX 3: a round-tripped int grounding_flagged==1 must trip (was False under
    strict `is True` identity)."""
    assert injection_predicate({"grounding_flagged": 1}) is True
    assert injection_predicate({"grounding_flagged": 0}) is False


# ---- extract_slice -------------------------------------------------------------------
def test_extract_slice_is_entities_not_prose():
    s = extract_slice([dict(RAW_ITEM)])
    assert "symbols=AAPL" in s
    assert "46.2%" in s
    assert "AAPL beats on iPhone revenue" in s        # escaped headline is included
    assert "Apple raised guidance" not in s           # raw summary prose is NOT


# ---- run_grounding_check -------------------------------------------------------------
def test_run_grounding_check_valid(st):
    t = FakeTransport([envelope(check_body())])
    out = run_grounding_check(st, proposal(), [dict(RAW_ITEM)], transport=t)
    assert out == check_body()
    assert "--allowedTools" in t.calls[0]["argv"] and "none" in t.calls[0]["argv"]


def test_run_grounding_check_vendor_error_returns_none(st):
    t = FakeTransport([ProcResult(1, "", "boom", False)])
    assert run_grounding_check(st, proposal(), [dict(RAW_ITEM)], transport=t) is None


def test_run_grounding_check_invalid_schema_returns_none(st):
    bad = envelope({"nope": True})
    t = FakeTransport([bad, bad])   # repair also fails -> quarantine -> None
    assert run_grounding_check(st, proposal(), [dict(RAW_ITEM)], transport=t) is None


# ---- evaluate_grounding (fail-closed) --------------------------------------------------
def _grounding_entries(st):
    with st.session_factory() as s:
        return s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_GROUNDING)).scalars().all()


def test_clean_proposal_not_flagged(st):
    t = FakeTransport([envelope(check_body())])
    out = evaluate_grounding(st, proposal(), DISPATCHED, resolver, transport=t)
    assert out.flagged is False and out.corroboration.ok
    entries = _grounding_entries(st)
    assert len(entries) == 1
    p = entries[0].payload
    assert p["proposal_id"] == "prop-1" and p["flagged"] is False
    assert p["corroborated"] is True and p["grounded"] is True


def test_grounded_true_never_clears_failed_corroboration(st):
    # verifier says grounded=true but the thesis cites a laundered ref -> still flagged
    t = FakeTransport([envelope(check_body())])
    out = evaluate_grounding(st, proposal(source_refs=[]), DISPATCHED, resolver, transport=t)
    assert out.flagged is True
    assert _grounding_entries(st)[0].payload["flagged"] is True


def test_errored_check_flags(st):
    t = FakeTransport([ProcResult(1, "", "usage limit reached", False)])
    out = evaluate_grounding(st, proposal(), DISPATCHED, resolver, transport=t)
    assert out.flagged is True and out.check is None


def test_injection_suspected_flags(st):
    t = FakeTransport([envelope(check_body(injection_suspected=True))])
    out = evaluate_grounding(st, proposal(), DISPATCHED, resolver, transport=t)
    assert out.flagged is True


def test_unsupported_claims_flag(st):
    t = FakeTransport([envelope(check_body(unsupported_claims=["made-up catalyst"]))])
    out = evaluate_grounding(st, proposal(), DISPATCHED, resolver, transport=t)
    assert out.flagged is True


def test_grounded_false_flags(st):
    t = FakeTransport([envelope(check_body(grounded=False))])
    out = evaluate_grounding(st, proposal(), DISPATCHED, resolver, transport=t)
    assert out.flagged is True


# ---- injection_predicate (SEC-01 / SEC-21 seam) ---------------------------------------
def test_injection_predicate():
    assert injection_predicate({}) is False
    assert injection_predicate(None) is False
    assert injection_predicate({"grounding_flagged": True}) is True
    assert injection_predicate({"grounding_flagged": False}) is False
    assert injection_predicate({"same_ticker_burst": {"symbol": "XYZ", "count": 4}}) is True
    assert injection_predicate({"same_ticker_burst": {"symbol": "XYZ", "count": 3}}) is False
    assert injection_predicate({"same_ticker_burst": {"symbol": "XYZ", "count": "many"}}) is True
