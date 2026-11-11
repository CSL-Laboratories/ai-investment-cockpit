"""Cockpit paper plugin entry: the artifact the Recursion loader signs, checks and runs.

Loaded in the daemon's process by the Recursion core's loader (G2). It requests
``read``, ``propose`` and ``order``, and no network, credential or process grant. The
core hands it a transport bound to its plugin id; the plugin builds the one paper
``OrderGateway`` on it. Absolute imports only: the loader runs this file as a fresh
module, not as part of the ``cockpit.paper`` package.

``compose`` is the composition: one ``PaperBook``, the gateway on it, and the
``KillSwitch`` on that same book instance with the gateway's halt. It returns both as a
``PaperPlugin`` and also sets the switch as ``gateway.kill_switch``. ``build`` is
``compose(...).gateway`` for callers that only place orders; the switch stays reachable
from that gateway, so such a caller can still kill. ``venue_kills`` are the venue kill
objects (each with a ``kill()`` returning the ``killswitch.VenueKill`` result) that the
credential-owning host hands in; the default is none, so the switch reaches the paper
book only and a completed kill reports ``paper_only``, never ``confirmed``: there is no
credential to revoke. ``venue_timeout_s`` bounds how long one ``kill`` waits for the
venues. Nothing here builds a venue kill, a transport or a credential.
"""

from dataclasses import dataclass
from typing import Callable, Sequence

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook
from cockpit.paper.killswitch import DEFAULT_VENUE_TIMEOUT_S, KillSwitch, VenueKill

PLUGIN_ID = "csl.cockpit.paper"
PERMISSIONS = ("read", "propose", "order")


@dataclass(frozen=True)
class PaperPlugin:
    """What ``compose`` built: the single order gateway and the kill switch over its book."""

    gateway: OrderGateway
    kill_switch: KillSwitch


def compose(core_transport: Callable[[bytes], bytes], book_path: str, caps: Caps, *,
            venue_kills: Sequence[VenueKill] = (),
            venue_timeout_s: float = DEFAULT_VENUE_TIMEOUT_S) -> PaperPlugin:
    if type(caps) is not Caps:
        raise TypeError("caps must be cockpit.paper.gateway.Caps")
    if type(venue_kills) not in (tuple, list):
        raise TypeError("venue_kills must be a tuple or list of venue kills")
    book = PaperBook(book_path)
    gateway = OrderGateway(plugin_id=PLUGIN_ID, book=book, core=CoreClient(core_transport), caps=caps)
    try:
        kill_switch = KillSwitch(gateway.halt, tuple(venue_kills), paper_book=book,
                                 venue_timeout_s=venue_timeout_s)
    except BaseException:
        book.close()
        raise
    gateway.kill_switch = kill_switch  # reachable from the gateway alone (``build``)
    return PaperPlugin(gateway, kill_switch)


def build(core_transport: Callable[[bytes], bytes], book_path: str, caps: Caps, *,
          venue_kills: Sequence[VenueKill] = (),
          venue_timeout_s: float = DEFAULT_VENUE_TIMEOUT_S) -> OrderGateway:
    return compose(core_transport, book_path, caps, venue_kills=venue_kills,
                   venue_timeout_s=venue_timeout_s).gateway
