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

``redact_panel`` is Cockpit's fail-closed view of a panel's reads: it drops rows labelled
with another venue and venue-like rows with no label, keeps O7 status, refusal, stale,
unreconciled, uncertainty and empty states visible (``keep_venue_rows``), then, for a
venue behind a switch, withholds everything unless the caller-supplied switch
state is exactly ``on``. The switch state never comes from the read payload.

Every panel is read-only: no intervention, no Stop and no order verb. Data is plain
Python, so nothing here imports Recursion; the composition root passes it across.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["DEFAULT_SWITCH_STATES", "KALSHI_PANELS", "PANELS", "REQUIRED", "VENUE_SWITCHES",
           "R1_PAPER", "keep_venue_rows", "panel_switch", "redact_panel", "registry", "withhold_venue_data"]

# Default switch state: every switch off (Kalshi's model-fed switch is off by default, 5.3).
DEFAULT_SWITCH_STATES = MappingProxyType({})
# The contract's switch per venue (PRODUCT.md 5.3). Which uses each switch covers is the
# per-use map for Lucas and counsel; both Kalshi panels sit behind the one contract switch.
VENUE_SWITCHES = MappingProxyType({"kalshi": "kalshi.model_input"})
NO_SWITCH = None
WITHHELD_NO_SWITCH = "[no switch declared]"
OWN_TRADING_VENUES = frozenset({"paper"})
# PRODUCT.md 5.4 display rule R1, carried in the paper panel title the shared GUI shows.
R1_PAPER = "PAPER (SIMULATED) \u2014 NOT REAL MONEY"

REQUIRED = MappingProxyType({
    "venue_use": ("paper:own_trading", "kalshi:model_fed_read", "kalshi:model_fed_propose"),
})

PANELS = (
    MappingProxyType({
        "id": "cockpit.paper_own_trading", "title": R1_PAPER + ": paper own trading, positions and collateral",
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
# Known venue ids. A key named ``<venue>_...`` for another venue is that venue's data.
KNOWN_VENUES = frozenset({"paper", "kalshi", "coinbase", "stock", "kalshi_demo"})
# Keys that identify a venue row (a market, instrument or quote). An unlabelled dict that
# carries any of them cannot be attributed to a venue, so it is dropped (fail closed).
VENUE_ROW_KEYS = frozenset({
    "venue_data", "ticker", "market", "market_id", "market_ticker", "event_ticker", "series_ticker",
    "symbol", "instrument", "contract", "price", "yes_price", "no_price", "yes_bid", "yes_ask",
    "no_bid", "no_ask", "bid", "ask", "last_price", "orderbook", "quote", "quotes"})


def keep_venue_rows(value: object, venue: object) -> object:
    """Drop data from other venues and unattributable venue rows; keep everything else.

    The rule, applied recursively (``venue`` is the panel's declared venue):

    * A dict with a ``venue`` key is kept whole if it equals ``venue`` and dropped otherwise.
    * A dict without a ``venue`` key, outside a kept venue dict, is dropped if it carries any
      ``VENUE_ROW_KEYS`` key (venue-like data with no label); otherwise it is kept and its
      values are filtered by the same rule.
    * Outside a kept venue dict, a key named ``<v>_...`` for a known venue ``v`` other than
      ``venue`` is dropped (e.g. ``kalshi_yes_price`` on a paper panel).
    * Everything else passes through unchanged: O7 status, refusal, stale, unreconciled,
      uncertainty and error fields, empty results (``[]``, ``{}``) and ``empty_reason``,
      and non-venue reads such as budgets (PRODUCT.md 3.5 "Nothing is hidden", 5.4).

    A non-string ``venue`` drops every venue-labelled dict and venue row. A response the
    rule drops entirely keeps only its string ``status_code``.
    """
    venue = venue if type(venue) is str else None
    kept = _rows(value, venue, False)
    if kept is not _DROP:
        return kept
    if type(value) is dict and type(value.get("status_code")) is str:
        return {"status_code": value["status_code"]}
    return {}


def _other_venue_key(key: object, venue: str | None) -> bool:
    if type(key) is not str or (venue is not None and key.startswith(venue + "_")):
        return False
    return any(key.startswith(v + "_") for v in KNOWN_VENUES if v != venue)


def _rows(value: object, venue: str | None, scoped: bool) -> object:
    if scoped:
        return value
    if type(value) is dict:
        if "venue" in value:
            return value if venue is not None and value["venue"] == venue else _DROP
        if VENUE_ROW_KEYS.intersection(value):
            return _DROP
        out = {}
        for key, item in value.items():
            if _other_venue_key(key, venue):
                continue
            item = _rows(item, venue, False)
            if item is not _DROP:
                out[key] = item
        return out
    if type(value) is list:
        return [v for v in (_rows(v, venue, False) for v in value) if v is not _DROP]
    return value


def redact_panel(panel: Mapping, reads: Mapping, switch_states: object) -> dict:
    """A panel's fail-closed view of its reads (``command -> response``).

    Other-venue and unattributable venue rows are dropped first (``keep_venue_rows``), then
    the panel's switch
    (``panel_switch``) is applied with the caller-supplied ``switch_states``.
    """
    switch = panel_switch(panel)
    return {command: withhold_venue_data(keep_venue_rows(data, panel.get("venue")), switch, switch_states)
            for command, data in reads.items()}
