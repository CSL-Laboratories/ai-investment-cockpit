"""QT-09 — the quant engine's control-plane surface, driven from Discord.

WHY A SEPARATE MODULE
----------------------
``orchestrator/app.py`` carries a load-bearing header rule — *no handler places an order or
imports ``brokers/``* — and it is already long. The quant routes are self-contained and have
their own containment story, so they live here and are registered by ``create_app``. The
Invariant-1 CI gate scans this file like any other; it imports nothing from ``brokers``.

WHAT AN OPERATOR CAN DO FROM DISCORD, AND WHAT THEY CANNOT
------------------------------------------------------------
CAN: read the engine's posture, and run a bounded SIMULATION against generated data.

CANNOT: place an order, move a limit, arm a mode, or point the engine at anything real.
There is deliberately no route here that mutates cockpit state. ``/quant sim`` runs
``Runner`` (the research loop) over ``SyntheticFeed`` — not ``AutoLoop``, and not against any
venue that could be confused for one. The strongest thing a compromised operator account can
achieve through this surface is to make the box do some arithmetic.

That asymmetry is the point. The quant engine is the newest and least-proven component in
the tree, and it is being exposed on the operator surface; so it is exposed READ-ONLY and
SIMULATION-ONLY, and the endpoints that would make it dangerous simply do not exist.

THE EVENT BUDGET IS A REAL CONTROL, NOT A PARAMETER
------------------------------------------------------
``MAX_SIM_EVENTS`` caps a run. The orchestrator API is a single loopback process that also
serves ``/status``, ``/mode`` and ``/killswitch``; an unbounded simulation would occupy it
for minutes and make the KILL SWITCH UNREACHABLE while it ran. A cap is therefore a safety
property of the control plane, not a nicety about response times — and it is enforced
server-side, because a Discord slash command's argument is operator input like any other.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_SIM_EVENTS = 50_000
"""Hard ceiling on one simulated run. See the module docstring — this keeps the loopback
control plane responsive, which is what keeps /killswitch reachable."""

DEFAULT_SIM_EVENTS = 5_000

STRATEGIES = ("market_maker", "momentum")
"""Exposed to the operator surface. ``pairs`` is omitted deliberately: it needs two
correlated symbols and a warm rolling hedge ratio, so a single-symbol synthetic run would
report it as permanently silent — which looks like a broken strategy rather than a
misapplied one. It remains fully available in-process and under test."""


class SimRequest(BaseModel):
    """Every field is bounded, because every field is operator input.

    ``events`` was the only one that was, and the module docstring's argument for it — the
    cap is a safety property of the control plane, not a nicety — applies unchanged to the
    rest: they all reach the same run loop on the same loopback process that serves
    ``/killswitch``. Unbounded, a plausible value came back as an unhandled 500 rather than a
    422 naming the field, which tells the operator nothing and leaves a traceback in the log
    of the process they may be trying to halt.
    """

    strategy: str = "market_maker"
    # NOT bounded here on purpose: ``run_sim`` already rejects it with a message that names
    # the ceiling AND why it exists. A Field bound would pre-empt that with pydantic's generic
    # "less than or equal to 50000", which tells an operator the number but not the reason.
    events: int = DEFAULT_SIM_EVENTS
    seed: int = 3
    symbol: str = Field(default="SIM", min_length=1, max_length=16)
    quote_size: int = Field(default=100, gt=0, le=1_000_000)
    max_inventory: int = Field(default=1_000, ge=0, le=100_000_000)
    requote_threshold_ticks: int = Field(default=2, ge=0, le=100_000)
    no_fees: bool = False


def quant_status(state) -> dict:
    """The engine's posture. Read-only; touches no market and no broker.

    Reports the CONTAINMENT state explicitly rather than assuming it. An operator asking
    "what is the quant engine allowed to do right now?" should get an answer from the
    running process, not from a document that may have drifted from it.
    """
    from risk.engine import RiskContext

    defaults = RiskContext.__dataclass_fields__
    return {
        "engine": "available",
        "strategies": list(STRATEGIES),
        "sim": {"max_events": MAX_SIM_EVENTS, "default_events": DEFAULT_SIM_EVENTS},
        # None today: no book feed exists in the tree, so Tiers A/B are dormant and every
        # proposal is evaluated with no verdict. Stated plainly so nobody reads the presence
        # of the checks as evidence that they are firing.
        "book_feed": "not_wired",
        "verification": {
            "tier_a": "available (advisory scoring)",
            "tier_b": "wired, dormant until a book feed exists",
            "require_quant_verdict": bool(defaults["require_quant_verdict"].default),
            "max_execution_cost_bps": str(defaults["max_execution_cost_bps"].default),
        },
        "containment": {
            "tier_c_venue": "SimVenue (in-process matching engine; not a broker)",
            "quant_may_import_brokers": False,
            "order_authority": "none — no route in this module mutates cockpit state",
        },
    }


def run_sim(body: SimRequest) -> dict:
    """Run one bounded synthetic simulation and return a tearsheet.

    Pure compute against generated data. No I/O, no market, no persistence — a second call
    with the same body returns the same numbers, which is what makes a result an operator
    can paste into a channel worth anything.
    """
    from quant.costs import ZERO_FEES, FeeSchedule
    from quant.events import PriceScale
    from quant.feed import validate_ordering
    from quant.feed.synthetic import SyntheticFeed
    from quant.runner import Runner
    from quant.strategies.market_maker import MarketMaker, MarketMakerParams
    from quant.strategies.momentum import Momentum, MomentumParams

    if body.strategy not in STRATEGIES:
        raise HTTPException(status_code=422,
                            detail=f"unknown strategy {body.strategy!r}; expected one of "
                                   f"{list(STRATEGIES)}")
    if body.events < 1 or body.events > MAX_SIM_EVENTS:
        # 422 rather than a silent clamp: an operator who asked for 500,000 events should be
        # told the ceiling, not handed a 50,000-event result labelled as what they requested.
        raise HTTPException(status_code=422,
                            detail=f"events must be between 1 and {MAX_SIM_EVENTS} "
                                   f"(got {body.events}); the cap keeps the loopback control "
                                   "plane responsive so /killswitch stays reachable")

    scale = PriceScale(tick_size=Decimal("0.01"))
    schedule = ZERO_FEES if body.no_fees else FeeSchedule()
    feed = SyntheticFeed(symbols=(body.symbol,), n_events=body.events, seed=body.seed)

    if body.strategy == "momentum":
        strategy = Momentum(name="momentum", symbols=(body.symbol,), params=MomentumParams())
    else:
        strategy = MarketMaker(
            name="mm", symbols=(body.symbol,),
            params=MarketMakerParams(quote_size=body.quote_size,
                                     max_inventory=body.max_inventory,
                                     requote_threshold_ticks=body.requote_threshold_ticks),
        )

    runner = Runner(feed=validate_ordering(iter(feed)), strategies=[strategy], scale=scale,
                    timer_interval_ns=5_000_000, schedule=schedule)
    result = runner.run()
    attribution = runner.pnl_for(body.symbol).snapshot()
    book = runner.books.get(body.symbol)

    return {
        "request": body.model_dump(),
        "events": result.events,
        "market_trades": result.market_trades,
        "orders_sent": result.intents_submitted,
        "desync_cancels": result.desync_cancels,
        "attribution": attribution.as_dict(),
        "book": {
            "best_bid_ticks": book.best_bid() if book else None,
            "best_ask_ticks": book.best_ask() if book else None,
            "resting_orders": len(book) if book else 0,
            "crossed": bool(book.is_crossed) if book else False,
        },
        # Printed with every result, deliberately. A tearsheet pasted into a Discord channel
        # outlives the context in which it was run, and a plausible-looking PnL number with no
        # caveat attached is exactly how a synthetic result gets quoted as evidence later.
        "caveat": (
            "SYNTHETIC DATA. This measures whether the strategy RUNS, not whether it works. "
            "The generator has no adverse selection, no informed flow and no news beyond what "
            "it was told to create, and it cannot surprise you."
        ),
    }


def register_quant_routes(app, state) -> None:
    """Attach the read-only quant routes. Called by ``create_app``.

    Both routes inherit the app-level ``Depends(ApiKeyAuth())``, so they are key-gated like
    every other route — unlike the WebSocket, which must check the handshake itself.
    """

    @app.get("/quant/status")
    def get_quant_status():
        return quant_status(state)

    @app.post("/quant/sim")
    def post_quant_sim(body: SimRequest):
        return run_sim(body)
