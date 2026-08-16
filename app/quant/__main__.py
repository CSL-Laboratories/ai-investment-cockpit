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
    parser.add_argument("--equity-sample-ms", type=float, default=10.0,
                        help="event-time grid for the equity curve the statistics are computed on")
    parser.add_argument("--trials", type=int, default=1,
                        help="how many variants you tried before reporting this one. Used to "
                             "deflate the Sharpe; 1 means 'this is the only thing I ran', "
                             "which is almost never true and is not assumed on your behalf")
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


def _print_statistics(equity: list[float], *, trials: int) -> None:
    """QT-10 — report the Sharpe WITH its uncertainty, or say why there isn't one.

    Printed after the attribution rather than beside it, because it qualifies every number
    above: an attribution that reconciles exactly is still an attribution of noise if the
    sample cannot support a conclusion.
    """
    from quant.stats import MIN_OBSERVATIONS, min_track_record_length, pnl_increments, sharpe_with_se

    returns = pnl_increments(equity)
    if len(returns) < MIN_OBSERVATIONS:
        print(f"\n  statistics      NOT COMPUTED — {len(returns)} usable equity samples, "
              f"need {MIN_OBSERVATIONS}. A Sharpe from fewer is noise with a decimal point.")
        return

    # periods_per_year=1 — DELIBERATELY UNANNUALIZED.
    #
    # A synthetic run spans a few seconds of event time. Multiplying its Sharpe by sqrt(252)
    # would assert that the sampling grid maps onto trading days, which is a fiction, and
    # would produce a large impressive number out of a few seconds of made-up data. The
    # t-statistic is invariant to that scaling anyway, so nothing diagnostic is lost.
    estimate = sharpe_with_se(returns, periods_per_year=1)
    if estimate is None:
        print("\n  statistics      NOT COMPUTED — degenerate return series (no variance).")
        return

    low, high = estimate.confidence_interval()
    print()
    print(f"  sharpe/sample   {estimate.sharpe:.3f}  (95% CI {low:.3f} to {high:.3f})"
          f"  [{len(returns)} samples, NOT annualized]")
    print(f"  t-statistic     {estimate.t_statistic:.2f}   "
          f"{'clears' if estimate.is_significant_at_harvey_hurdle else 'BELOW'} the "
          f"Harvey t>3 hurdle")
    print(f"  skew / kurtosis {estimate.skewness:.2f} / {estimate.kurtosis:.2f}")
    if estimate.autocorrelation_penalty < 0.95:
        print(f"  autocorrelation annualization penalised to "
              f"{estimate.autocorrelation_penalty:.2f}x — returns are serially correlated, "
              "so the naive sqrt(252) Sharpe is overstated")

    needed = min_track_record_length(returns)
    if needed is not None and needed > len(returns):
        print(f"  track record    {len(returns)} samples; ~{needed:.0f} needed for this "
              "Sharpe to be significant at 95%")

    if trials <= 1:
        print("  deflated sharpe NOT COMPUTED — pass --trials with the number of variants you "
              "actually tried.\n                  Reporting the best of N as if N=1 is the "
              "most common way a backtest lies.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scale = PriceScale(tick_size=Decimal(args.tick_size))
    schedule = ZERO_FEES if args.no_fees else FeeSchedule()

    feed = build_feed(args)
    strategy = build_strategy(args)
    runner = Runner(feed=validate_ordering(iter(feed)), strategies=[strategy], scale=scale,
                    timer_interval_ns=int(args.timer_ms * 1_000_000), schedule=schedule,
                    equity_sample_ns=int(args.equity_sample_ms * 1_000_000))
    result = runner.run()
    book = runner.books.get(args.symbol)
    attribution = runner.pnl.snapshot()

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
    _print_statistics(runner.pnl.equity_curve, trials=args.trials)
    if book is not None:
        print(f"\n  book            bid {book.best_bid()} / ask {book.best_ask()}  "
              f"({len(book)} orders, crossed={book.is_crossed})")
    if provenance is not None and provenance.caveat():
        print(f"\n  CAVEAT: {provenance.caveat()}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
