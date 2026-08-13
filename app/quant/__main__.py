"""QT — command-line entry point: run a strategy against a feed and print a tearsheet.

    python -m quant --feed synthetic --strategy market_maker --events 20000
    python -m quant --feed synthetic --strategy market_maker --requote-threshold 0
    python -m quant --feed replay --path data/captures/2026-08-07.parquet --strategy momentum

DELIBERATELY RESEARCH-ONLY
---------------------------
This runs ``Runner``, not ``AutoLoop``: it drives a feed through strategies and reports what
happened. It cannot place a real order, because nothing under ``quant/`` can — see
``quant/__init__.py`` for the containment argument and the CI gate that enforces it.

THE COMPARISON WORTH RUNNING FIRST
-----------------------------------
    python -m quant --strategy market_maker --requote-threshold 2
    python -m quant --strategy market_maker --requote-threshold 0

Same strategy, same data, same seed; the only difference is whether it reprices on every
tick. Against a real FIFO book the aggressive setting sends roughly ninety times the order
traffic and wins FEWER passive fills, because every reprice forfeits queue position. Under
the exp(-k*delta) fill model this engine replaces, that comparison is not merely different —
it is inexpressible, because fill probability there depends only on distance from the mid and
repricing is free.
"""
from __future__ import annotations

import argparse
from decimal import Decimal

from quant.costs import ZERO_FEES, FeeSchedule
from quant.events import PriceScale
from quant.feed import validate_ordering
from quant.feed.synthetic import SyntheticFeed
from quant.runner import Runner
from quant.strategies.market_maker import MarketMaker, MarketMakerParams
from quant.strategies.momentum import Momentum, MomentumParams


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant", description=__doc__.split("\n")[0])
    parser.add_argument("--feed", choices=("synthetic", "replay"), default="synthetic")
    parser.add_argument("--path", help="capture path, required for --feed replay")
    parser.add_argument("--strategy", choices=("market_maker", "momentum"), default="market_maker")
    parser.add_argument("--symbol", default="S")
    parser.add_argument("--events", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--quote-size", type=int, default=100)
    parser.add_argument("--max-inventory", type=int, default=1_000)
    parser.add_argument("--requote-threshold", type=int, default=2,
                        help="ticks the target must move before repricing; 0 destroys queue priority")
    parser.add_argument("--timer-ms", type=float, default=5.0)
    parser.add_argument("--no-fees", action="store_true",
                        help="frictionless venue — for measuring how much of a result is a fee assumption")
    return parser


def build_feed(args):
    if args.feed == "replay":
        if not args.path:
            raise SystemExit("--feed replay requires --path")
        from quant.feed.replay import ReplayFeed

        return ReplayFeed(path=args.path)
    return SyntheticFeed(symbols=(args.symbol,), n_events=args.events, seed=args.seed)


def build_strategy(args):
    if args.strategy == "momentum":
        return Momentum(name="momentum", symbols=(args.symbol,), params=MomentumParams())
    return MarketMaker(
        name="mm", symbols=(args.symbol,),
        params=MarketMakerParams(quote_size=args.quote_size,
                                 max_inventory=args.max_inventory,
                                 requote_threshold_ticks=args.requote_threshold),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scale = PriceScale(tick_size=Decimal(args.tick_size))
    schedule = ZERO_FEES if args.no_fees else FeeSchedule()

    feed = build_feed(args)
    strategy = build_strategy(args)
    runner = Runner(feed=validate_ordering(iter(feed)), strategies=[strategy], scale=scale,
                    timer_interval_ns=int(args.timer_ms * 1_000_000), schedule=schedule)
    result = runner.run()
    book = runner.books.get(args.symbol)
    attribution = runner.pnl_for(args.symbol).snapshot()

    provenance = getattr(feed, "provenance", None)

    print(f"\n  feed            {args.feed}  ({result.events} events, {result.market_trades} market trades)")
    print(f"  strategy        {args.strategy}")
    if args.strategy == "market_maker":
        print(f"  requote thresh  {args.requote_threshold} ticks")
    print(f"  fees            {'NONE (frictionless)' if args.no_fees else 'realistic'}")
    print()
    print(f"  orders sent     {result.intents_submitted}")
    print(f"  our fills       {attribution.fills}  "
          f"(maker {attribution.maker_fills} / taker {attribution.taker_fills})")
    print(f"  maker ratio     {attribution.maker_ratio:.1%}")
    print(f"  desync cancels  {result.desync_cancels}")
    print(f"  final position  {result.final_positions}")
    print()
    print(f"  spread capture  {attribution.spread_capture}")
    print(f"  inventory drift {attribution.inventory_drift}")
    print(f"  fees            {attribution.fees}  ({attribution.fee_bps}bps)")
    print("  ---------------")
    print(f"  net             {attribution.total}")
    if book is not None:
        print(f"\n  book            bid {book.best_bid()} / ask {book.best_ask()}  "
              f"({len(book)} orders, crossed={book.is_crossed})")
    if provenance is not None and provenance.caveat():
        print(f"\n  CAVEAT: {provenance.caveat()}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
