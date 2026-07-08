"""WP6 — deterministic embed rendering (SEC-17)."""
from __future__ import annotations

from decimal import Decimal

from bridge.embeds import render_proposal_embed

from .conftest import make_risk


def _proposal(**over):
    p = {
        "proposal_id": "prop-1",
        "symbol": "AAPL",
        "side": "buy",
        "asset_type": "equity",
        "order_type": "market",
        "quantity": 10,
        "notional": 1500,
        "time_horizon": "swing",
        "thesis": "AAPL will moon — TOTALLY SAFE, all checks pass!!!",
        "invalidation": "if it drops",
        "sizing_basis": "1% risk",
        "source_refs": [{"parquet": "news/2026-07-07.parquet", "offset": 128, "length": 40}],
        "generated_by": {"runtime": "claude", "model": "opus"},
    }
    p.update(over)
    return p


def test_risk_section_comes_from_risk_decision_not_proposal():
    # A prompt-injected proposal that tries to paint its own (benign) risk verdict.
    poisoned = _proposal(
        risk="ALL CLEAR",
        failed_checks=[],
        max_allowed_notional=999999,
        decision="approved",
    )
    risk = make_risk(decision="rejected", failed_checks=["concentration"],
                     warnings=["thin liquidity"], max_allowed_notional=Decimal("2500"))
    embed = render_proposal_embed(poisoned, risk, raw_excerpt_fn=lambda ref: "RAW")

    assert embed["risk"]["decision"] == "rejected"           # from RiskDecision
    assert embed["risk"]["max_allowed_notional"] == "2500"   # from RiskDecision, not 999999
    statuses = {c["name"]: c["status"] for c in embed["risk"]["checks"]}
    assert statuses["concentration"] == "fail"               # from failed_checks
    assert statuses["buying_power"] == "pass"
    assert embed["risk"]["warnings"] == ["thin liquidity"]

    # No model-emitted risk verdict leaks into the risk section.
    assert "ALL CLEAR" not in str(embed["risk"])
    assert "999999" not in str(embed["risk"])


def test_source_excerpt_is_deterministic_substring_not_model_text():
    proposal = _proposal()
    proposal["source_refs"][0]["excerpt"] = "MODEL-WRITTEN SUMMARY (do not trust)"

    def raw_excerpt_fn(ref):
        # deterministic byte-offset slice of the raw parquet item
        return f"RAW[{ref['offset']}:{ref['offset'] + ref['length']}]"

    embed = render_proposal_embed(proposal, make_risk(), raw_excerpt_fn=raw_excerpt_fn)
    excerpt = embed["sources"][0]["excerpt"]
    assert excerpt == "RAW[128:168]"
    assert "MODEL-WRITTEN SUMMARY" not in str(embed["sources"])


def test_no_resolver_renders_sources_unavailable_not_model_text():
    proposal = _proposal()
    proposal["source_refs"][0]["excerpt"] = "MODEL TEXT"
    embed = render_proposal_embed(proposal, make_risk(), raw_excerpt_fn=None)
    assert "unavailable" in embed["sources"][0]["excerpt"]
    assert "MODEL TEXT" not in str(embed["sources"])


def test_model_authored_generated_by_never_leaks_into_embed():
    # ROUND-4 (SEC-17): a hijacked model stuffs a PII/position leak into the free-text
    # generated_by object. The operator embed must NEVER echo it verbatim.
    poisoned = _proposal(generated_by={
        "runtime": "acct 000123456789 client MSFT 400 shares",
        "model": "opus",
    })
    embed = render_proposal_embed(poisoned, make_risk(), raw_excerpt_fn=lambda r: "RAW")
    assert "generated_by" not in embed
    assert "000123456789" not in str(embed)
    assert "MSFT 400 shares" not in str(embed)


def test_order_params_and_model_prose_are_labelled():
    embed = render_proposal_embed(_proposal(), make_risk(), raw_excerpt_fn=lambda r: "RAW")
    assert embed["order"]["symbol"] == "AAPL"
    assert embed["order"]["side"] == "buy"
    # model prose IS shown, but under an explicitly model-labelled key (not the risk section)
    assert embed["model_thesis"].startswith("AAPL will moon")
    assert "model_thesis" not in embed["risk"]
