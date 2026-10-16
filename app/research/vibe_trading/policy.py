"""Which Vibe-Trading tools the cockpit is allowed to call.

The sidecar process, once installed, still *registers* its broker tools. They
are unreachable from here: a trading name is refused before the executable is
looked up, and an allowlist edit cannot put one back. ``backtest`` stays,
because it is the research engine the run card comes from; it receives only a
directory the adapter already scanned.
"""
from __future__ import annotations

import re

# Research surface we actually speak. Anything else is denied, including tools
# this package never plans to call.
RESEARCH_TOOLS = frozenset({
    "alpha_zoo",
    "alpha_bench",
    "backtest",
    "get_institutional_holdings",
    "etf_holdings",
    "prediction_market",
})

# Beat the allowlist. These are the broker/trading tools in agent/SKILL.md plus
# the file and swarm tools that can stage a broker call or read one.
_UNREACHABLE = frozenset({
    "trading_connections",
    "trading_select_connection",
    "trading_check",
    "trading_account",
    "trading_positions",
    "trading_orders",
    "trading_quote",
    "trading_history",
    "write_file",
    "read_file",
    "read_run_artifact",
    "run_swarm",
    "retry_run",
})

_ARG_KEYS = {
    "alpha_zoo": frozenset({"action", "zoo", "theme", "alpha_id", "limit"}),
    "alpha_bench": frozenset({"zoo", "alpha_id", "universe", "period", "top", "horizon"}),
    "backtest": frozenset({"run_dir"}),
    "get_institutional_holdings": frozenset({"ticker", "manager"}),
    "etf_holdings": frozenset({"symbol"}),
    "prediction_market": frozenset({"action", "query", "contract_id", "limit"}),
}

_STRATEGY_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ORDER_NAMES = frozenset({
    "place_order",
    "submit_order",
    "place_equity_order",
    "place_crypto_order",
})


class SidecarToolDenied(Exception):
    """The cockpit will not send this tool, or these arguments, to the sidecar."""

    def __init__(self, tool: str):
        self.tool = tool
        super().__init__(f"sidecar tool denied: {tool}")


def is_trading_tool(name: str) -> bool:
    """True for broker/order tools, independent of the research allowlist."""
    if type(name) is not str:
        return True
    lowered = name.lower()
    if lowered in _UNREACHABLE or lowered.startswith((
        "trading_", "broker_", "connector_", "order_",
    )):
        return True
    return any(token in lowered for token in _ORDER_NAMES)


def assert_callable(tool: str) -> None:
    """Refuse trading tools even if a later edit adds them to ``RESEARCH_TOOLS``."""
    if is_trading_tool(tool) or tool not in RESEARCH_TOOLS:
        raise SidecarToolDenied(tool if type(tool) is str else type(tool).__name__)


def prepare_arguments(tool: str, arguments: dict) -> dict:
    """Copy only the keys that tool is allowed to receive. Unknown keys are a denial."""
    assert_callable(tool)
    if type(arguments) is not dict:
        raise SidecarToolDenied(tool)
    allowed = _ARG_KEYS[tool]
    if any(key not in allowed for key in arguments):
        raise SidecarToolDenied(tool)
    cleaned: dict = {}
    for key, value in arguments.items():
        if type(value) is bool or type(value) not in (str, int):
            raise SidecarToolDenied(tool)
        if type(value) is str and (len(value) > 512 or (key != "run_dir" and len(value) > 200)):
            raise SidecarToolDenied(tool)
        if type(value) is int and not 0 <= value <= 10_000:
            raise SidecarToolDenied(tool)
        cleaned[key] = value
    return cleaned


def assert_strategy_id(strategy_id: str) -> str:
    if type(strategy_id) is not str or _STRATEGY_ID.fullmatch(strategy_id) is None:
        raise SidecarToolDenied("backtest")
    return strategy_id
