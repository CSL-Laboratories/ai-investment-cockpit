from decimal import Decimal

import pytest

from sidecar.sizer import SizerError, ewma_volatility, suggest_notional


def test_suggestion_never_exceeds_the_cap_and_quantizes_down():
    result = suggest_notional(
        Decimal("1"),
        Decimal("0.01"),
        Decimal("0.05"),
        Decimal("1"),
        Decimal("100.019"),
        Decimal("1000000"),
    )
    assert result.suggested == Decimal("100.01")
    assert result.suggested <= Decimal("100.019")
    assert result.binding == "cap"


def test_nonpositive_edge_sizes_to_zero():
    result = suggest_notional(
        Decimal("0"),
        Decimal("0.02"),
        Decimal("0.01"),
        Decimal("0.25"),
        Decimal("5000"),
        Decimal("10000"),
    )
    assert result.suggested == Decimal("0.00")
    assert result.binding == "nonpositive_edge"


def test_higher_volatility_shrinks_the_vol_target():
    common = dict(
        edge=Decimal("1"),
        target_vol=Decimal("0.05"),
        kelly_frac=Decimal("1"),
        caps=Decimal("100000"),
        equity=Decimal("1000"),
    )
    low = suggest_notional(vol=Decimal("0.10"), **common)
    high = suggest_notional(vol=Decimal("0.20"), **common)
    assert low.binding == "vol"
    assert high.suggested == low.suggested / 2


def test_fractional_kelly_shrinks_a_full_kelly_book():
    full = suggest_notional(
        Decimal("0.01"),
        Decimal("0.10"),
        Decimal("1"),
        Decimal("1"),
        Decimal("100000"),
        Decimal("1000"),
    )
    quarter = suggest_notional(
        Decimal("0.01"),
        Decimal("0.10"),
        Decimal("1"),
        Decimal("0.25"),
        Decimal("100000"),
        Decimal("1000"),
    )
    assert quarter.binding == "kelly"
    assert quarter.suggested == Decimal("250.00")
    assert quarter.suggested < full.suggested


def test_floats_and_bad_volatility_are_rejected():
    with pytest.raises(SizerError):
        suggest_notional(0.01, Decimal("0.1"), Decimal("0.1"), Decimal("0.25"), Decimal("1"), Decimal("1"))
    with pytest.raises(SizerError):
        suggest_notional(
            Decimal("0.01"),
            Decimal("0"),
            Decimal("0.1"),
            Decimal("0.25"),
            Decimal("10"),
            Decimal("10"),
        )
    with pytest.raises(SizerError):
        suggest_notional(
            Decimal("0.01"),
            Decimal("0.1"),
            Decimal("0.1"),
            Decimal("1.5"),
            Decimal("10"),
            Decimal("10"),
        )


def test_ewma_does_not_use_a_later_return_as_the_seed():
    series = [Decimal("0.01"), Decimal("0.00"), Decimal("0.00")]
    vol = ewma_volatility(series, Decimal("0.5"))
    # Seed is 0.01^2. Later zeros pull it down, but not below the recursion.
    assert vol < Decimal("0.01")
    assert vol > 0
