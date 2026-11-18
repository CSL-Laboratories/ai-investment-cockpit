"""Cockpit's plugin data for Recursion's GUI: required venue classes and panel registry.

Recursion's GUI is the page its daemon serves (PRODUCT.md O68 bar 4). It owns the
shared surface: the O7 reads, the three interventions, Stop and the route badges,
including the O50 beta tag. Cockpit does not serve a page of its own. It hands the
shared GUI two plain values:

* ``REQUIRED``, the route classes Cockpit needs reported, passed as ``required=``;
  any it does not see the shared GUI lists as a beta gap.
* ``PANELS``, Cockpit-specific screens. Each names the O7 reads it needs and the venue
  use it covers. Its explicit ``venue`` is the post-read row filter, not a label; it is
  the O7 venue id (``O7_VENUE``: paper -> ``stock``, kalshi -> ``kalshi_demo``). The paper
  panel's book is ``PAPER_BOOK``: rows labelled ``stock`` or ``paper``, and ``paper_*`` keys.
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

__all__ = ["DEFAULT_SWITCH_STATES", "KALSHI_PANELS", "O7_VENUE", "PANELS", "PAPER_BOOK", "REQUIRED",
           "VENUE_SWITCHES", "R1_PAPER", "keep_venue_rows", "panel_switch", "redact_panel", "registry",
           "withhold_venue_data"]

# Default switch state: every switch off (Kalshi's model-fed switch is off by default, 5.3).
DEFAULT_SWITCH_STATES = MappingProxyType({})
# The contract's switch per venue (PRODUCT.md 5.3). Which uses each switch covers is the
# per-use map for Lucas and counsel; both Kalshi panels sit behind the one contract switch.
# Cockpit venue class (the ``venue`` half of ``venue_use``) -> the venue id O7 v0 reads put
# on rows. The O7 oracle (docs/replacement/outcomes/O7/fixtures/oracle/responses.json) labels
# paper-book positions ``stock`` and Kalshi demo positions ``kalshi_demo``; a panel's declared
# ``venue`` is matched against row labels (exactly in Recursion #330), so panels declare the
# O7 id. Declaring ``paper`` there would show zero positions on the O7 oracle, hiding them
# (PRODUCT.md 3.5 "Nothing is hidden").
O7_VENUE = MappingProxyType({"paper": "stock", "kalshi": "kalshi_demo"})
# Row labels of the one paper book. O7 v0 labels its positions ``stock``; the paper
# OrderGateway (#320) keeps venue ``paper`` on its orders. The paper panel counts both labels,
# and ``paper_*`` / ``stock_*`` keys, as its own book (owner call, Nov 9). Kalshi ids are not
# paired: ``kalshi`` and ``kalshi_demo`` stay separate labels.
PAPER_BOOK = frozenset({O7_VENUE["paper"], "paper"})
# Keyed by the O7 venue id a panel declares. ``kalshi`` stays listed so a panel declaring the
# bare Kalshi id still sits behind the contract switch rather than failing open.
VENUE_SWITCHES = MappingProxyType({"kalshi_demo": "kalshi.model_input", "kalshi": "kalshi.model_input"})
NO_SWITCH = None
WITHHELD_NO_SWITCH = "[no switch declared]"
# The paper book's O7 venue id. Cockpit has no other own-trading venue (paper first, 5.2).
# Only a panel declaring this id is outside a switch; ``PAPER_BOOK`` widens rows, not this.
OWN_TRADING_VENUES = frozenset({O7_VENUE["paper"]})
# PRODUCT.md 5.4 display rule R1, carried in the paper panel title the shared GUI shows.
R1_PAPER = "PAPER (SIMULATED) \u2014 NOT REAL MONEY"

REQUIRED = MappingProxyType({
    "venue_use": ("paper:own_trading", "kalshi:model_fed_read", "kalshi:model_fed_propose"),
})

PANELS = (
    MappingProxyType({
        "id": "cockpit.paper_own_trading", "title": R1_PAPER + ": paper own trading, positions and collateral",
        "venue": O7_VENUE["paper"], "venue_use": "paper:own_trading",
        "reads": ("operator.positions.inspect", "operator.decisions.inspect", "operator.budgets.inspect"),
        "withhold_venue_data": False,
    }),
)

# Not exported by registry() until the shared renderer applies kalshi.model_input.
KALSHI_PANELS = (
    MappingProxyType({
        "id": "cockpit.kalshi_model_fed_read", "title": "Kalshi model-fed reads: evidence and provenance",
        "venue": O7_VENUE["kalshi"], "venue_use": "kalshi:model_fed_read",
        "reads": ("operator.evidence.inspect", "operator.uncertainty.inspect"),
        "withhold_venue_data": True,
    }),
    MappingProxyType({
        "id": "cockpit.kalshi_model_fed_propose", "title": "Kalshi model-fed proposals (propose only)",
        "venue": O7_VENUE["kalshi"], "venue_use": "kalshi:model_fed_propose",
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
    outside a switch. Any other panel uses its venue's contract switch (``kalshi_demo`` or
    ``kalshi`` -> ``kalshi.model_input``) and fails closed when its venue has none.
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
KNOWN_VENUES = frozenset({"paper", "kalshi", "coinbase", "gemini", "stock", "kalshi_demo"})
# Keys that identify a venue row (a market, instrument or quote). An unlabelled dict that
# carries any of them cannot be attributed to a venue, so it is dropped (fail closed).
VENUE_ROW_KEYS = frozenset({
    "venue_data", "ticker", "market", "market_id", "market_ticker", "event_ticker", "series_ticker",
    "symbol", "instrument", "contract", "price", "yes_price", "no_price", "yes_bid", "yes_ask",
    "no_bid", "no_ask", "bid", "ask", "last_price", "orderbook", "quote", "quotes"})


def keep_venue_rows(value: object, venue: object) -> object:
    """Drop data from other venues and unattributable venue rows; keep everything else.

    The rule, applied recursively (``venue`` is the panel's declared venue; a venue in
    ``PAPER_BOOK`` owns both of its labels, ``stock`` and ``paper``, any other venue only itself):

    * A dict with a ``venue`` key is kept if the label is one the panel's venue owns and
      dropped otherwise. Its children are filtered by the same rule.
    * A dict without a ``venue`` key, at any depth (including inside a kept row), is dropped if
      it carries any ``VENUE_ROW_KEYS`` key (venue-like data with no label); otherwise it is
      kept and its values are filtered by the same rule.
    * At any depth, a key named ``<v>`` or ``<v>_...`` for a known venue ``v`` the panel's
      venue does not own is dropped (e.g. ``kalshi_yes_price`` on a paper panel); ``paper_*``
      and ``stock_*`` keys stay on the paper panel.
    * Everything else passes through unchanged: O7 status, refusal, stale, unreconciled,
      uncertainty and error fields, empty results (``[]``, ``{}``) and ``empty_reason``,
      and non-venue reads such as budgets (PRODUCT.md 3.5 "Nothing is hidden", 5.4).

    A non-string ``venue`` drops every venue-labelled dict and venue row. A response the
    rule drops entirely keeps only its string ``status_code``.
    """
    kept = _rows(value, _owned(venue))
    if kept is not _DROP:
        return kept
    if type(value) is dict and type(value.get("status_code")) is str:
        return {"status_code": value["status_code"]}
    return {}


def _owned(venue: object) -> frozenset:
    """The row labels and key prefixes a declared venue owns; none for a non-string venue."""
    if type(venue) is not str:
        return frozenset()
    return PAPER_BOOK if venue in PAPER_BOOK else frozenset({venue})


def _other_venue_key(key: object, owned: frozenset) -> bool:
    if type(key) is not str or key in owned or any(key.startswith(v + "_") for v in owned):
        return False
    return any(key == v or key.startswith(v + "_") for v in KNOWN_VENUES - owned)


def _rows(value: object, owned: frozenset) -> object:
    # Inside a kept venue dict, a nested dict labelled with another venue, a key named for another
    # venue, and an unlabelled venue-like dict (one carrying ``VENUE_ROW_KEYS``) are all dropped:
    # none can be attributed to the paper book (PRODUCT.md 3.5, 5.3; Lucas, Nov 11 2:30am PST).
    if type(value) is dict:
        if "venue" in value:
            if type(value["venue"]) is not str or value["venue"] not in owned:
                return _DROP
        elif VENUE_ROW_KEYS.intersection(value):
            return _DROP
        out = {}
        for key, item in value.items():
            if _other_venue_key(key, owned):
                continue
            item = _rows(item, owned)
            if item is not _DROP:
                out[key] = item
        return out
    if type(value) is list:
        return [v for v in (_rows(v, owned) for v in value) if v is not _DROP]
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
