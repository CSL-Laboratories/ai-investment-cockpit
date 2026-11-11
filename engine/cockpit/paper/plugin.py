"""Cockpit paper plugin entry: the artifact the Recursion loader signs, checks and runs.

Loaded in the daemon's process by the Recursion core's loader (G2). It requests
``read``, ``propose`` and ``order``, and no network, credential or process grant. The
core hands it a transport bound to its plugin id; the plugin builds the one paper
``OrderGateway`` on it. Absolute imports only: the loader runs this file as a fresh
module, not as part of the ``cockpit.paper`` package.

``compose`` is the composition: one ``PaperBook``, the gateway on it, and the
``KillSwitch`` on that same book instance with the gateway's halt. It returns both as a
``PaperPlugin``. ``build`` is ``compose(...).gateway`` for callers that only place
orders. ``venue_kills`` are the venue kill objects (each with a ``kill()`` returning the
``killswitch.VenueKill`` result) that the credential-owning host hands in; the default
is none, so the switch reaches the paper book only. Nothing here builds a venue kill,
a transport or a credential.
"""

from dataclasses import dataclass
from typing import Callable, Sequence

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook
from cockpit.paper.killswitch import KillSwitch, VenueKill

PLUGIN_ID = "csl.cockpit.paper"
PERMISSIONS = ("read", "propose", "order")


@dataclass(frozen=True)
class PaperPlugin:
    """What ``compose`` built: the single order gateway and the kill switch over its book."""

    gateway: OrderGateway
    kill_switch: KillSwitch


def compose(core_transport: Callable[[bytes], bytes], book_path: str, caps: Caps, *,
            venue_kills: Sequence[VenueKill] = ()) -> PaperPlugin:
    if type(caps) is not Caps:
        raise TypeError("caps must be cockpit.paper.gateway.Caps")
    if type(venue_kills) not in (tuple, list):
        raise TypeError("venue_kills must be a tuple or list of venue kills")
    book = PaperBook(book_path)
    gateway = OrderGateway(plugin_id=PLUGIN_ID, book=book, core=CoreClient(core_transport), caps=caps)
    return PaperPlugin(gateway, KillSwitch(gateway.halt, tuple(venue_kills), paper_book=book))


def build(core_transport: Callable[[bytes], bytes], book_path: str, caps: Caps, *,
          venue_kills: Sequence[VenueKill] = ()) -> OrderGateway:
    return compose(core_transport, book_path, caps, venue_kills=venue_kills).gateway
