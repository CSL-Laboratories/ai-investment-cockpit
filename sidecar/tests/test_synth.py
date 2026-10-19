"""Synthetic stress stays off the order path and does not invent a model completion."""

from decimal import Decimal
from pathlib import Path

from combined.broker import FLAT_COST_BPS, execution_price
from combined.synth.calibrate import BLOCK, fit
from combined.synth.market import bars_are_ordered, generate_bootstrap, generate_markov
from combined.synth.news import (
    CATALOG,
    flow_demonstration,
    market_event_attempt,
    mechanism_table,
)
from combined.synth.scenarios import SHOCK_AT, earnings_gaps, flash_crash, grind_2022, rate_shock, volmageddon
from combined.synth.stress import render_report, run_stress
from combined.synth.surface import snapshot

ROOT = Path(__file__).resolve().parents[1]


def test_fit_window_is_the_fixture_not_2016():
    cal = fit()
    assert cal.first_date == "2021-10-18"
    assert cal.last_date == "2026-10-16"
    assert cal.symbols_in_tape == ("SPY", "QQQ", "IWM")
    assert cal.requested_span.startswith("2016-01-01")
    assert cal.single_names["NEXA"]["source"] == "assumption"
    assert cal.single_names["BRKQ"]["company"] == "Brickline Holdings"
    assert "2016-01-01" in " ".join(cal.notes)
    assert cal.n_panel > 200
    for row in cal.transition.values():
        assert abs(sum(row.values()) - 1.0) < 1e-9
    assert cal.spread["crisis"] > cal.spread["bull"]
    assert cal.depth_scale["crisis"] < cal.depth_scale["bull"]
    assert cal.garch_alpha + cal.garch_beta < 0.99


def test_markov_crisis_correlation_exceeds_bull_and_bars_stay_ordered():
    cal = fit()
    path = generate_markov(cal, n_sessions=3000, seed=7)
    assert bars_are_ordered(path)
    assert path.generator == "markov"
    spy = __import__("numpy").array(path.returns["SPY"])
    name = __import__("numpy").array(path.returns["NEXA"])
    regimes = __import__("numpy").array(path.regimes[1:])
    bull = regimes == "bull"
    crisis = regimes == "crisis"
    assert int(crisis.sum()) > 50
    assert int(bull.sum()) > 50
    bull_corr = float(__import__("numpy").corrcoef(spy[bull], name[bull])[0, 1])
    crisis_corr = float(__import__("numpy").corrcoef(spy[crisis], name[crisis])[0, 1])
    assert crisis_corr > bull_corr + 0.05
    crisis_days = [day for day, regime in zip(path.sessions, path.regimes, strict=True) if regime == "crisis"]
    assert path.micro[crisis_days[0]]["SPY"]["half_spread"] > path.micro[path.sessions[0]]["SPY"]["half_spread"] or path.regimes[0] == "crisis"


def test_bootstrap_blocks_are_contiguous_fixture_slices():
    cal = fit()
    path = generate_bootstrap(cal, n_sessions=81, seed=3)
    assert path.block_starts
    assert len(path.block_index) == 80
    for offset, index in enumerate(path.block_index):
        start = path.block_starts[offset // BLOCK]
        assert index == (start + (offset % BLOCK)) % cal.n_panel
        assert abs(path.returns["SPY"][offset] - float(cal.panel_spy[index])) < 1e-9
        assert abs(path.returns["QQQ"][offset] - float(cal.panel_qqq[index])) < 1e-9
        assert abs(path.returns["IWM"][offset] - float(cal.panel_iwm[index])) < 1e-9
    assert bars_are_ordered(path)


def test_scenarios_have_the_shape_they_are_named_for():
    cal = fit()
    flash = flash_crash(cal)
    assert min(flash.returns["SPY"]) < -0.07
    assert flash.returns["SPY"][SHOCK_AT + 1] > 0
    grind = grind_2022(cal)
    total = 1.0
    for value in grind.returns["SPY"]:
        total *= 1.0 + value
        assert value > -0.01
    assert total - 1.0 < -0.10
    burst = volmageddon(cal)
    import numpy as np

    calm = np.std(burst.returns["SPY"][:40], ddof=1)
    wild = np.std(burst.returns["SPY"][SHOCK_AT : SHOCK_AT + 5], ddof=1)
    assert wild > calm
    shock = rate_shock(cal)
    day = SHOCK_AT
    assert abs(shock.returns["SPY"][day] - shock.returns["NEXA"][day]) < 1e-12
    assert abs(shock.returns["SPY"][day] + 0.035) < 1e-12
    names = earnings_gaps(cal)
    assert abs(names.returns["NEXA"][SHOCK_AT] - 0.08) < 1e-12
    assert abs(names.returns["SPY"][SHOCK_AT]) < 1e-12
    assert abs(names.returns["BRKQ"][SHOCK_AT + 10] + 0.06) < 1e-12


def test_put_wing_is_richer_than_the_call_and_flat_vol():
    row = snapshot(0.16, "crisis")
    assert row["put_iv_95"] > row["atm_iv"] > row["call_iv_105"]
    assert row["put_price_skew"] > row["put_price_atm_vol"]
    bull = snapshot(0.16, "bull")
    assert row["atm_iv"] > bull["atm_iv"]


def test_default_half_spread_keeps_the_historical_flat_fee():
    _price, fee = execution_price(
        side="buy",
        arrival=Decimal("100"),
        qty=Decimal("1"),
        sigma=Decimal("0.01"),
        adv=Decimal("1000000"),
        cost_model="flat",
    )
    assert fee == (Decimal("100") * FLAT_COST_BPS / Decimal(10000)).quantize(Decimal("0.0001"))
    _wider, stressed = execution_price(
        side="buy",
        arrival=Decimal("100"),
        qty=Decimal("1"),
        sigma=Decimal("0.01"),
        adv=Decimal("1000000"),
        cost_model="flat",
        half_spread=Decimal("0.0015"),
    )
    assert stressed > fee
    tca, _fee = execution_price(
        side="buy",
        arrival=Decimal("100"),
        qty=Decimal("1"),
        sigma=Decimal("0"),
        adv=Decimal("1000000"),
        cost_model="tca",
        half_spread=Decimal("0.0015"),
    )
    assert tca == Decimal("100") * Decimal("1.0015")


def test_headlines_do_not_become_market_events_or_orders():
    table = {row["kind"]: row for row in mechanism_table()}
    assert table["earnings_beat"]["in_score_session"] is False
    assert table["earnings_beat"]["alert"] is True
    assert table["earnings_beat"]["scored_by"] == "same_formula_outside_allowlist"
    assert table["earnings_beat"]["cockpit_limit"]["buy_filled"] is False
    assert table["earnings_beat"]["chase"]["buy_filled"] is True
    assert table["earnings_miss"]["alert"] is False
    assert table["earnings_miss"]["probe"]["holding_loss_through_gap"] > 0
    assert table["ma_rumor_false"]["alert"] is True
    assert table["ma_rumor_false"]["truth"] == "false"
    assert table["ma_rumor_false"]["cockpit_limit"]["buy_filled"] is False
    assert table["ma_rumor_false"]["chase"]["pnl"] < 0
    assert table["priced_in_beat"]["priced_in"] is True
    assert table["priced_in_beat"]["alert"] is True
    assert table["priced_in_beat"]["cockpit_limit"]["buy_filled"] is True
    assert table["priced_in_beat"]["cockpit_limit"]["pnl"] < 0
    assert table["hoax"]["alert"] is False
    assert table["hoax"]["market_event_accepted"] is False
    assert "system:" not in table["hoax"]["escaped_headline"]
    assert "[role-marker]" in table["hoax"]["escaped_headline"]
    assert table["macro_print"]["scored_by"] == "score_session"
    assert table["macro_print"]["alert"] is False
    assert all(row["orders_submitted"] == 0 for row in table.values())
    assert all(row["llm"]["llm_calls"] == 0 and row["llm"]["cost_usd"] == 0 for row in table.values())
    refused = market_event_attempt("plain text", "NEXA")
    assert refused["accepted"] is False
    assert "&lt;" in market_event_attempt("a < b", "NEXA")["escaped_headline"]


def test_flow_reads_a_chain_and_not_a_headline():
    demo = flow_demonstration()
    assert demo["headline_was_an_argument"] is False
    assert demo["scanned_contracts"] >= 20
    assert demo["orders_submitted"] == 0


def test_tiny_monte_carlo_reports_failure_modes_and_submits_nothing():
    payload = run_stress(n_paths=2, n_sessions=80, news_paths=2, generators=("markov",), variants=("cockpit", "tca"))
    text = render_report(payload)
    assert "do not prove an edge" in text
    assert "2021-10-18" in text
    cockpit = payload["monte_carlo"]["generators"]["markov"]["variants"]["cockpit"]
    assert cockpit["orders_submitted"] == 0
    assert cockpit["cagr"]["n"] == 2
    assert "prob_beat_spy" in cockpit
    assert payload["orders_submitted"] == 0
    assert any(spec["kind"] == "hoax" for spec in CATALOG)


def test_synth_sources_do_not_import_the_broker_package():
    root = ROOT / "combined" / "synth"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import brokers" not in text
        assert "submit_order" not in text
        assert "OrderGateway" not in text
