"""QT-07 — Tier C: autonomous execution against a SIMULATED venue.

READ THE CONTAINMENT ARGUMENT BEFORE CHANGING ANYTHING HERE
-------------------------------------------------------------
This subpackage is the only part of the engine with no human in the loop. It places orders at
market-making frequency with no per-order approval, which is irreconcilable with the
cockpit's Invariant 1 ("an order reaches a broker ONLY via risk-pass -> mode -> approval ->
OrderGateway", INCLUDING human-initiated trades).

The conflict is resolved by SEPARATION, not by compromise:

    Tier C drives ``SimVenue``. ``SimVenue`` is an in-process matching engine. It is not a
    broker, has no network, and holds no credential. Nothing under ``quant/`` imports
    ``brokers/`` — enforced by an AST gate in ``tests/test_invariants.py``, not by convention.

So no existing invariant is weakened and the ``brokers`` package is untouched: it still has
exactly the two broker-dispatch call sites the G7 acceptance gate pins, and that gate stays
green without modification.

FOUR INDEPENDENT CONTAINMENTS
------------------------------
1. THE IMPORT GATE. ``quant/`` may not import ``brokers``. This is the structural one — it
   makes reaching a broker impossible rather than merely forbidden, and it cannot be evaded
   by ``getattr``, a duck type, or a string-keyed dispatch table.
2. THE TYPE ASSERTION. ``AutoLoop`` refuses to start unless its venue IS a ``SimVenue``.
   Belt and braces for the case where a venue arrives from somewhere the gate did not scan.
3. HALT AND BREAKER. The loop reads the cockpit's existing halt file and watch-only state
   through injected callables, so an operator's kill switch stops this too. A separate stop
   for the quant loop would be a second control surface nobody would remember in an incident.
4. GUARDS. Position, order-size, gross-exposure, order-rate and session-loss limits, checked
   on every intent BEFORE it reaches the book.

IF YOU WANT THIS POINTED AT A REAL OR PAPER BROKER
----------------------------------------------------
That is a different project. It requires changing ``_ORDER_AUTHORITY_MODES`` in
``brokers/gateway.py``, an adversarial-review round per CLAUDE.md, and a decision about what
"human-gated" means for a strategy that trades faster than a human can read. Do not approach
it by loosening anything here.
"""
from quant.execution.autoloop import AutoLoop, AutoLoopResult, VenueContainmentError
from quant.execution.sim_venue import LatencyModel, SimVenue

__all__ = [
    "AutoLoop",
    "AutoLoopResult",
    "LatencyModel",
    "SimVenue",
    "VenueContainmentError",
]
