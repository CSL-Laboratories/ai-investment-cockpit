"""G8 — SEC-09 outbound scrubber tests (docs/security-hardening-backlog.md SEC-09:
default-on deterministic redaction of every model free-text field; blocking classes force
quarantine; nothing with account digits or non-subject position sizes may reach Discord).
"""
from __future__ import annotations

from sqlalchemy import select

from schemas.models.audit_entry import AuditEntry
from workflows.constants import EVENT_SCRUB
from workflows.scrubber import ScrubResult, scrub_output_fields, scrub_text


def test_account_number_blocks():
    r = scrub_text("wire from account 12345678901 today")
    assert "[acct]" in r.clean and "12345678901" not in r.clean
    assert r.blocked is True
    assert any(h.kind == "account_number" for h in r.hits)


def test_masked_account_blocks():
    r = scrub_text("account ****1234 balance")
    assert "[acct]" in r.clean and "1234" not in r.clean
    assert r.blocked is True


def test_dollar_amounts_redacted_not_blocking():
    r = scrub_text("bought at $1,234.56 and added USD 500 later")
    assert r.clean.count("[amount]") == 2
    assert "$" not in r.clean
    assert r.blocked is False
    assert all(h.kind == "dollar_amount" for h in r.hits)


def test_share_counts_redacted():
    r = scrub_text("holding 123 shares plus 50 sh extra", subject_symbols=frozenset({"AAPL"}))
    assert r.clean.count("[qty]") == 2
    assert r.blocked is False


def test_cost_basis_redacted():
    r = scrub_text("my cost basis is 53 dollars per share here")
    assert "[basis]" in r.clean
    assert "cost basis" not in r.clean
    assert any(h.kind == "cost_basis" for h in r.hits)


def test_foreign_ticker_size_blocks():
    r = scrub_text("portfolio is TSLA 40% and NVDA: 120 shares",
                   subject_symbols=frozenset({"AAPL"}))
    assert r.clean.count("[position]") == 2
    assert "TSLA 40%" not in r.clean and "120 shares" not in r.clean
    assert r.blocked is True
    assert all(h.kind == "foreign_ticker_size" for h in r.hits)


def test_reverse_order_foreign_position_blocks():
    # ROUND-4: reverse phrasing (NUMBER -> TICKER -> UNIT) must still classify as a foreign
    # position leak. '500 TSLA shares' previously evaded _TICKER_SIZE/_SHARES -> blocked=False.
    r = scrub_text("500 TSLA shares", subject_symbols=frozenset({"AAPL"}))
    assert "[position]" in r.clean
    assert "TSLA" not in r.clean and "500" not in r.clean
    assert r.blocked is True
    assert any(h.kind == "foreign_ticker_size" for h in r.hits)


def test_subject_symbol_size_not_a_position_leak():
    r = scrub_text("add AAPL 100 shares", subject_symbols=frozenset({"AAPL"}))
    assert "[position]" not in r.clean
    assert "[qty]" in r.clean            # count still redacted by the share rule
    assert r.blocked is False


def test_hard_truncation():
    r = scrub_text("y" * 9000, max_len=4000)
    assert isinstance(r, ScrubResult) and len(r.clean) == 4000


def test_scrub_output_fields_audits_and_blocks(st):
    obj = {"thesis": "acct 987654321 says TSLA 40%", "invalidation": "clean text",
           "sizing_basis": "fixed-fraction"}
    out, blocked = scrub_output_fields(
        st, obj, ["thesis", "invalidation", "sizing_basis"],
        subject_symbols=frozenset({"AAPL"}), target="proposal:prop-1")
    assert blocked is True
    assert "[acct]" in out["thesis"] and "[position]" in out["thesis"]
    assert obj["thesis"].startswith("acct 987654321")   # place-copy: original untouched
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_SCRUB)).scalars().all()
    assert len(entries) == 1
    p = entries[0].payload
    assert p["target"] == "proposal:prop-1" and p["blocked"] is True
    assert set(p["hits"]) == {"account_number", "foreign_ticker_size"}
    # audit payload carries hit KINDS only — never the matched PII text
    assert "987654321" not in str(p)


def test_scrub_output_fields_no_hits_no_audit(st):
    obj = {"thesis": "all clean"}
    out, blocked = scrub_output_fields(st, obj, ["thesis"],
                                       subject_symbols=frozenset(), target="t")
    assert out == obj and blocked is False
    with st.session_factory() as s:
        entries = s.execute(select(AuditEntry).where(
            AuditEntry.event_type == EVENT_SCRUB)).scalars().all()
    assert entries == []


def test_scrub_nested_prose_arrays(st):
    # MorningBrief prose arrays are untyped: strings and nested objects both get scrubbed
    obj = {"risk_flags": ["exposure at $5,000", {"note": "basis: cost basis 53"}],
           "market_regime": "risk-on with USD 200 hedge"}
    out, blocked = scrub_output_fields(st, obj, ["risk_flags", "market_regime"],
                                       subject_symbols=frozenset(), target="brief")
    assert out["risk_flags"][0] == "exposure at [amount]"
    assert "[basis]" in out["risk_flags"][1]["note"]
    assert "[amount]" in out["market_regime"]
    assert blocked is False
