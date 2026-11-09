"""Cockpit's plugin data for Recursion's GUI: required venue classes and panel registry.

Recursion's GUI is the page its daemon serves (PRODUCT.md O68 bar 4). It owns the
shared surface: the O7 reads, the three interventions, Stop and the route badges,
including the O50 beta tag. Cockpit does not serve a page of its own. It hands the
shared GUI two plain values:

* ``REQUIRED``, the route classes Cockpit needs reported, passed as ``required=``;
  any it does not see the shared GUI lists as a beta gap.
* ``PANELS``, Cockpit-specific screens. Each names the O7 reads it needs and the venue
  use it covers. Its explicit ``venue`` is the post-read row filter, not a label.
  The daemon composition root mounts these at /panel?id=<id> via
  recursion_core.gui.compose(plugins=(registry(),)). Cockpit never imports that API.

Configure the installed plugin with the daemon flag --plugin cockpit.gui:registry.
The core root calls this factory and passes its plain required/panels mapping across;
the shared renderer must execute only the named, unscoped O7 reads, select rows matching
the panel's declared ``venue``, then apply the venue-data switch (Recursion #330). The
core inventory stays present when these venue classes are added (Nov 8 correction).

Each panel's venue data is governed by its own per-venue, per-use switch, named by its
``venue_use`` (``panel_switch``). ``withhold_venue_data``/``redact_panel`` take the switch
state from the caller, never from the read payload, and withhold everything when the
switch is not exactly ``on``.

Every panel is read-only: no intervention, no Stop and no order verb. Data is plain
Python, so nothing here imports Recursion; the composition root passes it across.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["DEFAULT_SWITCH_STATES", "PANELS", "REQUIRED", "panel_switch", "redact_panel", "registry", "withhold_venue_data"]

# Default switch state: every venue-use switch off (Kalshi model-fed off by default, 5.3).
DEFAULT_SWITCH_STATES = MappingProxyType({})
WITHHELD_NO_SWITCH = "[no switch declared]"

REQUIRED = MappingProxyType({
    "venue_use": ("paper:own_trading", "kalshi:model_fed_read", "kalshi:model_fed_propose"),
})

PANELS = (
    MappingProxyType({
        "id": "cockpit.paper_own_trading", "title": "Paper own trading: positions and collateral",
        "venue": "paper", "venue_use": "paper:own_trading",
        "reads": ("operator.positions.inspect", "operator.decisions.inspect", "operator.budgets.inspect"),
        "withhold_venue_data": False,
    }),
    MappingProxyType({
        "id": "cockpit.kalshi_model_fed_read", "title": "Kalshi model-fed reads: evidence and provenance",
        "venue": "kalshi", "venue_use": "kalshi:model_fed_read",
        "reads": ("operator.evidence.inspect", "operator.uncertainty.inspect"),
        "withhold_venue_data": True,
    }),
    MappingProxyType({
        "id": "cockpit.kalshi_model_fed_propose", "title": "Kalshi model-fed proposals (propose only)",
        "venue": "kalshi", "venue_use": "kalshi:model_fed_propose",
        "reads": ("operator.decisions.inspect", "operator.profiles.inspect"),
        "withhold_venue_data": True,
    }),
)


def registry() -> dict:
    """A plain, mutable copy of the plugin data for the composition root to hand over."""
    return {"required": {k: list(v) for k, v in REQUIRED.items()},
            "panels": [{**p, "reads": list(p["reads"])} for p in PANELS]}


def panel_switch(panel: Mapping) -> str | None:
    """The switch id governing a panel's venue data, or None if it is not behind one.

    The switch is per venue and per use (lane T4): a panel that withholds venue data is
    governed by the switch named exactly by its ``venue_use`` (``kalshi:model_fed_read``
    and ``kalshi:model_fed_propose`` are separate switches). ``venue_use`` doubles as the
    switch id because the shared panel validator accepts no other panel keys. A panel
    whose ``withhold_venue_data`` is not exactly ``False`` fails closed onto its switch.
    """
    if panel.get("withhold_venue_data", True) is False:
        return None
    switch = panel.get("venue_use")
    return switch if type(switch) is str and switch else WITHHELD_NO_SWITCH


def withhold_venue_data(value: object, switch: str | None, switch_states: object) -> object:
    """Show ``value`` only if the caller-supplied state of ``switch`` is exactly ``"on"``.

    ``switch_states`` is the control API's switch state, passed by the caller; it maps a
    switch id (``venue:use``) to its state. Nothing inside ``value`` is consulted, so a
    payload cannot switch itself on. Fails closed: an unknown switch, a missing or non-"on"
    state, or a ``switch_states`` that is not a mapping withholds the whole value except
    its string ``status_code``. ``switch=None`` means the value is not behind a switch.
    """
    if switch is None:
        return value
    if (switch != WITHHELD_NO_SWITCH and isinstance(switch_states, Mapping)
            and type(switch_states.get(switch)) is str and switch_states.get(switch) == "on"):
        return value
    out: dict = {"venue_data": "[withheld: venue data switch %s is not on]" % switch}
    if type(value) is dict and type(value.get("status_code")) is str:
        out["status_code"] = value["status_code"]
    return out


def redact_panel(panel: Mapping, reads: Mapping, switch_states: object) -> dict:
    """Apply a panel's own switch to each of its read results (``command -> response``)."""
    switch = panel_switch(panel)
    return {command: withhold_venue_data(data, switch, switch_states) for command, data in reads.items()}
