"""Volatility-targeted fractional-Kelly notionals. Decimal and stdlib only."""

from sidecar.sizer.kelly import SizerError, SizerResult, ewma_volatility, suggest_notional

__all__ = ["SizerError", "SizerResult", "ewma_volatility", "suggest_notional"]
