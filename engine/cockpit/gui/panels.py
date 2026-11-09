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

Kalshi panels are defined in ``KALSHI_PANELS`` but are not exported by ``registry()``:
the shared renderer does not yet apply a venue-data switch keyed to the contract's
``kalshi.model_input`` (PRODUCT.md 5.3), so they stay off the daemon page until a
switch-aware consumer exists. ``REQUIRED`` still names the Kalshi venue classes so the
shared GUI reports them as beta gaps; that is route status, not venue data.

``redact_panel`` is Cockpit's fail-closed view of a panel's reads: it keeps only rows
labelled with the panel's ``venue`` (anything unlabelled or labelled otherwise is dropped),
then, for a venue behind a switch, withholds everything unless the caller-supplied switch
state is exactly ``on``. The switch state never comes from the read payload.

Every panel is read-only: no intervention, no Stop and no order verb. Data is plain
Python, so nothing here imports Recursion; the composition root passes it across.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["DEFAULT_SWITCH_STATES", "KALSHI_PANELS", "PANELS", "REQUIRED", "VENUE_SWITCHES",
           "keep_venue_rows", "panel_switch", "redact_panel", "registry", "withhold_venue_data"]

# Default switch state: every switch off (Kalshi's model-fed switch is off by default, 5.3).
DEFAULT_SWITCH_STATES = MappingProxyType({})
# The contract's switch per venue (PRODUCT.md 5.3). Which uses each switch covers is the
# per-use map for Lucas and counsel; both Kalshi panels sit behind the one contract switch.
VENUE_SWITCHES = MappingProxyType({"kalshi": "kalshi.model_input"})
NO_SWITCH = None
WITHHELD_NO_SWITCH = "[no switch declared]"
OWN_TRADING_VENUES = frozenset({"paper"})

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
)

# Not exported by registry() until the shared renderer applies kalshi.model_input.
KALSHI_PANELS = (
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

    Only an own-trading venue panel that sets ``withhold_venue_data`` exactly ``False`` is
    outside a switch. Any other panel uses its venue's contract switch (``kalshi`` ->
    ``kalshi.model_input``) and fails closed when its venue has none.
    """
    venue = panel.get("venue")
    if panel.get("withhold_venue_data", True) is False and venue in OWN_TRADING_VENUES:
        return NO_SWITCH
    return VENUE_SWITCHES.get(venue, WITHHELD_NO_SWITCH) if type(venue) is str else WITHHELD_NO_SWITCH


def withhold_venue_data(value: object, switch: str | None, switch_states: object) -> object:
    """Show ``value`` only if the caller-supplied state of ``switch`` is exactly ``"on"``.

    ``switch_states`` is the control API's switch state, passed by the caller; it maps a
    switch id (``kalshi.model_input``) to its state. Nothing inside ``value`` is consulted,
    so a payload cannot switch itself on. Fails closed: an unknown switch, a missing or
    non-"on" state, or a ``switch_states`` that is not a mapping withholds the whole value
    except its string ``status_code``. ``switch=None`` means the value is not behind a switch.
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


_DROP = object()


def keep_venue_rows(value: object, venue: object) -> object:
    """Keep only data inside dicts labelled ``venue`` == ``venue``; fail closed on the rest.

    A dict labelled with another venue is dropped; unlabelled scalars outside a matching
    dict are dropped; unlabelled containers stay only as the path to kept data. A string
    top-level ``status_code`` is kept. A non-string ``venue`` keeps nothing.
    """
    kept = _rows(value, venue, False) if type(venue) is str else _DROP
    out = {} if kept is _DROP or type(kept) is not dict else kept
    if kept is not _DROP and type(kept) is not dict:
        out = {"rows": kept}
    if type(value) is dict and type(value.get("status_code")) is str:
        out["status_code"] = value["status_code"]
    return out


def _rows(value: object, venue: str, scoped: bool) -> object:
    if type(value) is dict:
        if "venue" in value:
            if value["venue"] != venue:
                return _DROP
            scoped = True
        out = {k: v for k, v in ((k, _rows(v, venue, scoped)) for k, v in value.items()) if v is not _DROP}
    elif type(value) is list:
        out = [v for v in (_rows(v, venue, scoped) for v in value) if v is not _DROP]
    else:
        return value if scoped else _DROP
    return out if out or scoped else _DROP


def redact_panel(panel: Mapping, reads: Mapping, switch_states: object) -> dict:
    """A panel's fail-closed view of its reads (``command -> response``).

    Rows not labelled with the panel's ``venue`` are dropped first, then the panel's switch
    (``panel_switch``) is applied with the caller-supplied ``switch_states``.
    """
    switch = panel_switch(panel)
    return {command: withhold_venue_data(keep_venue_rows(data, panel.get("venue")), switch, switch_states)
            for command, data in reads.items()}
