from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sidecar.options.iv_proxy import assert_proxy_url, merge_proxy_rows, parse_proxy_csv
from sidecar.options.runner import OptionsRunnerError, iv_rank_from_history


def test_cboe_and_fred_closes_become_decimal_proxy_rows():
    cboe = "DATE,OPEN,HIGH,LOW,CLOSE\n1/2/2024,13,14,12,13.20\n1/3/2024,.,.,.,.\n"
    rows = parse_proxy_csv(cboe, underlying="SPY", proxy_symbol="VIX", vendor="cboe")
    assert rows == [
        {
            "date": "2024-01-02",
            "symbol": "SPY",
            "iv": "0.132",
            "source": "proxy",
            "proxy_symbol": "VIX",
            "proxy_vendor": "cboe",
        }
    ]
    fred = "observation_date,VIXCLS\n2024-01-02,13.20\n2024-01-03,.\n"
    fred_rows = parse_proxy_csv(fred, underlying="SPY", proxy_symbol="VIXCLS", vendor="fred")
    assert fred_rows[0]["iv"] == "0.132"
    assert fred_rows[0]["proxy_vendor"] == "fred"


def test_proxy_merge_keeps_a_chain_row(tmp_path: Path):
    path = tmp_path / "iv.jsonl"
    path.write_text(
        '{"date": "2024-01-02", "iv": "0.2", "source": "chain", "symbol": "SPY"}\n',
        encoding="utf-8",
    )
    incoming = parse_proxy_csv(
        "DATE,CLOSE\n1/2/2024,13.20\n1/3/2024,14.00\n",
        underlying="SPY",
        proxy_symbol="VIX",
        vendor="cboe",
    )
    result = merge_proxy_rows(path, incoming)
    text = path.read_text(encoding="utf-8")
    assert result["appended"] == 1
    assert result["skipped_existing"] == 1
    assert text.splitlines()[0].find('"iv": "0.2"') != -1
    assert "2024-01-03" in text


def test_proxy_url_stays_on_cboe_or_fred():
    assert_proxy_url("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
    assert_proxy_url("https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS")
    with pytest.raises(OptionsRunnerError):
        assert_proxy_url("https://example.com/VIX_History.csv")


def _rows(count: int, *, source: str | None) -> list[dict]:
    start = date(2024, 1, 1)
    rows = []
    for index in range(count):
        row = {
            "date": (start + timedelta(days=index)).isoformat(),
            "symbol": "SPY",
            "iv": "0.10",
        }
        if source is not None:
            row["source"] = source
            row["proxy_symbol"] = "VIX"
        rows.append(row)
    return rows


def test_short_proxy_history_is_labeled_and_a_full_window_is_not():
    short = iv_rank_from_history(
        _rows(60, source="proxy"),
        symbol="SPY",
        today="2024-06-01",
        current=Decimal("0.20"),
        minimum=60,
    )
    assert short["rank"] == Decimal(1)
    assert short["window_used"] == 60
    assert short["gap"] is not None
    assert "252" in short["gap"]
    assert "proxy" in short["gap"]
    too_short = iv_rank_from_history(
        _rows(59, source="proxy"),
        symbol="SPY",
        today="2024-06-01",
        current=Decimal("0.20"),
        minimum=60,
    )
    assert too_short["rank"] is None
    full = _rows(252, source=None)
    full.insert(0, {"date": "2026-01-01", "symbol": "SPY", "iv": "0.99"})
    ranked = iv_rank_from_history(
        full,
        symbol="SPY",
        today="2025-01-01",
        current=Decimal("0.20"),
        minimum=60,
    )
    assert ranked["window_used"] == 252
    assert ranked["gap"] is None
    assert ranked["rank"] == Decimal(1)
    with pytest.raises(OptionsRunnerError):
        iv_rank_from_history([], symbol="SPY", today="2024-06-01", current=Decimal("0.2"), minimum=1)
