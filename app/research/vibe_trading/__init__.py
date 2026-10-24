"""Optional Vibe-Trading research sidecar (CSL-192).

The cockpit does not vendor ``vibe-trading-ai``. When ``COCKPIT_VIBE_TRADING_ENABLED``
is off, or the ``vibe-trading-mcp`` binary is not installed, research features are
empty and the rest of the process is unchanged.

What crosses the boundary is untrusted feed data: alpha-bench IC and
alive/reversed/dead labels, a point-in-time backtest run card, and read-only
13F / ETF / event-contract rows. Those rows are schema-checked, normalized,
deduped, and trimmed to the decision context cap before they can appear in a
decision packet. Broker and trading tools are refused before any process starts.
Orders stay on risk/ and OrderGateway; this package does not import either.
"""

from research.vibe_trading.adapter import ResearchAdapter, ResearchBatch, ResearchRequest

__all__ = ["ResearchAdapter", "ResearchBatch", "ResearchRequest"]
