"""QT — the quantitative engine: limit order book, strategies, replay, execution simulation.

WHAT THIS PACKAGE IS FOR
------------------------
The cockpit knows *whether* a trade is a good idea. It has never known what the trade
COSTS. There are no bars, no quotes, no ticks, no book, and no fill model anywhere else in
the tree — ``FakeAlpaca`` returns ``accepted, filled_qty=0`` and never fills. So the system
can say "this headline is material" but not "and you would be 4,000 shares back in the
queue, so this costs 9bps."

``quant/`` is that missing half. It is consumed at three escalating authority tiers:

  Tier A (``verify.py``)            advisory scoring of an existing proposal. No authority.
  Tier B (``risk/engine.py``)       that score as a real pre-trade check.
  Tier C (``execution/autoloop``)   an autonomous high-frequency loop, SIMULATED VENUE ONLY.

THE CONTAINMENT RULE — READ THIS BEFORE ADDING AN IMPORT
--------------------------------------------------------
    NOTHING under ``quant/`` may import ``brokers/``. Ever.

Invariant 1 says an order reaches a broker ONLY via risk-pass -> mode -> approval ->
``OrderGateway``, and that includes human-initiated trades. Tier C — a strategy loop placing
its own orders at market-making frequency — cannot live behind a human approval click. The
two are irreconcilable, so they are SEPARATED rather than compromised: Tier C drives
``quant.execution.sim_venue.SimVenue``, an in-process matching engine that is not a broker
and cannot become one.

That separation is enforced by an AST gate in ``tests/test_invariants.py``, not by this
docstring. The gate exists because "we agreed not to" is not a control — the same reasoning
that put the tracer on the real order path. If you find yourself wanting a broker handle in
here, the answer is that Tier C is sim-only by construction; pointing it at a live or paper
BROKER is a different project requiring a change to ``_ORDER_AUTHORITY_MODES`` and an
adversarial-review round.

TWO NAMING RULES THAT ARE LOAD-BEARING, NOT STYLISTIC
------------------------------------------------------
1. No method here may be named ``submit_order`` / ``place_order`` / ``place_equity_order`` /
   ``place_crypto_order``. The Invariant-1 gate is a plain REGEX over source text, not an
   import graph — the literal string outside ``brokers/`` and ``tests/`` fails CI even in a
   pure simulator. Use ``accept_order``, ``cancel_order``, ``route``.
2. There is no ``quant/risk.py`` — it is ``quant/guards.py``. Top-level ``risk`` is the real
   deterministic risk engine; a second module of that name is a trap for the next reader
   even though the imports happen to resolve.

CONVENTIONS SETTLED ONCE (see events.py for the reasoning)
-----------------------------------------------------------
* Event time is ``int`` NANOSECONDS, and every event carries THREE of them.
* Wall clock is ``core.clock.Clock`` and returns a tz-aware datetime. The two never mix.
* Prices are INTEGER TICKS inside the book; ``Decimal`` only at the cockpit boundary.
* numpy/pandas are analytics-only. The book and matcher are pure stdlib, for determinism.
"""
