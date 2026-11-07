"""Cockpit paper plugin entry: the artifact the Recursion loader signs, checks and runs.

Loaded in the daemon's process by the Recursion core's loader (G2). It requests
``read``, ``propose`` and ``order``, and no network, credential or process grant. The
core hands it a transport bound to its plugin id; the plugin builds the one paper
``OrderGateway`` on it. Absolute imports only: the loader runs this file as a fresh
module, not as part of the ``cockpit.paper`` package.
"""

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook

PLUGIN_ID = "csl.cockpit.paper"
PERMISSIONS = ("read", "propose", "order")


def build(core_transport, book_path, caps):
    if type(caps) is not Caps:
        raise TypeError("caps must be cockpit.paper.gateway.Caps")
    return OrderGateway(plugin_id=PLUGIN_ID, book=PaperBook(book_path), core=CoreClient(core_transport),
                        caps=caps)
