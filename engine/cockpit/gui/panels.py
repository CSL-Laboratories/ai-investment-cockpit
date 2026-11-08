"""Cockpit's plugin data for Recursion's GUI: required venue classes and panel registry.

Recursion's GUI is the page its daemon serves (PRODUCT.md O68 bar 4). It owns the
shared surface: the O7 reads, the three interventions, Stop and the route badges,
including the O50 beta tag. Cockpit does not serve a page of its own. It hands the
shared GUI two plain values:

* ``REQUIRED``, the route classes Cockpit needs reported, passed as ``required=``;
  any it does not see the shared GUI lists as a beta gap.
* ``PANELS``, Cockpit-specific screens. Each names the O7 reads it needs and the venue
  use it covers. Recursion has no panel-mount API yet, so this is a registry, not HTML.

Every panel is read-only: no intervention, no Stop and no order verb. Data is plain
Python, so nothing here imports Recursion; the composition root passes it across.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["PANELS", "REQUIRED", "registry", "withhold_venue_data"]

REQUIRED = MappingProxyType({
    "venue_use": ("paper:own_trading", "kalshi:model_fed_read", "kalshi:model_fed_propose"),
})

PANELS = (
    MappingProxyType({
        "id": "cockpit.paper_own_trading", "title": "Paper own trading: positions and collateral",
        "venue_use": "paper:own_trading",
        "reads": ("operator.positions.inspect", "operator.decisions.inspect", "operator.budgets.inspect"),
        "withhold_venue_data": False,
    }),
    MappingProxyType({
        "id": "cockpit.kalshi_model_fed_read", "title": "Kalshi model-fed reads: evidence and provenance",
        "venue_use": "kalshi:model_fed_read",
        "reads": ("operator.evidence.inspect", "operator.uncertainty.inspect"),
        "withhold_venue_data": True,
    }),
    MappingProxyType({
        "id": "cockpit.kalshi_model_fed_propose", "title": "Kalshi model-fed proposals (propose only)",
        "venue_use": "kalshi:model_fed_propose",
        "reads": ("operator.decisions.inspect", "operator.profiles.inspect"),
        "withhold_venue_data": True,
    }),
)


def registry() -> dict:
    """A plain, mutable copy of the plugin data for the composition root to hand over."""
    return {"required": {k: list(v) for k, v in REQUIRED.items()},
            "panels": [{**p, "reads": list(p["reads"])} for p in PANELS]}


def withhold_venue_data(value: object) -> object:
    """Hide venue data unless the control API marks its switch exactly ``on``."""
    if type(value) is dict:
        out = {k: withhold_venue_data(v) for k, v in value.items()}
        if "venue_data" in value and value.get("venue_data_switch") != "on":
            out["venue_data"] = "[withheld: venue data switch is off]"
        return out
    if type(value) is list:
        return [withhold_venue_data(v) for v in value]
    return value
