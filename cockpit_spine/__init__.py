"""Cockpit Nov 11 spine (WP17): operator surfaces, linked figures, bakeoff plumbing.

Draft, provisional tree (WP7 sets the final one). It sits beside ``successor/``
rather than inside it, because WP12's tree-wide guard forbids session and queue
words, sockets, http and argparse under ``successor/``; that guard is left
unchanged. The operator session here is a human-approval transcript, not route
machinery: it never opens, holds, leases or selects a route. It reaches the
route only through ``successor.route_consumer``. Nothing here is an order path:
no broker, risk, audit-chain, mode or approval module of the legacy ``app/``
tree is imported, and a test forbids it.
"""
