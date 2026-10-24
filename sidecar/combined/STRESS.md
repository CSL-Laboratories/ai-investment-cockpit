# Synthetic stress of the combined book

Synthetic paths test robustness and failure modes. They do not prove an edge. A path that loses less than a fully invested SPY book is mostly describing the $1,000 position cap and the cash balance, not a forecasting ability. Nothing in this file was sent to a broker. `orders_submitted` is 0. No model completion was invented.

## What was fit, and what was not

The requested span was 2016-01-01 through 2026-12-31. The bars actually in the tree run from 2021-10-18 to 2026-10-16, 1255 sessions of SPY, QQQ, IWM. There is no 2016–2021 tape here, and there are no single-name prices. NEXA (Nexa Biolabs) and BRKQ (Brickline Holdings) use assumed betas and idiosyncratic volatility. Those names are fictional on purpose.

ETF betas, regime frequencies, the GARCH grid, the Student-t degrees of freedom, jump frequency, overnight-gap share, and the range proxy are in-sample moments of that fixture. The half-spread schedule and the crisis depth multiplier are structural: the bars contain no quotes. Printed volume is not cut in a crisis. The impact formula's depth input is. At a $1,000 order the participation rate versus ETF volume is dust, so the spread, not the square-root impact, is the liquidity effect that can move a fill.

Dates on a generated path are weekdays from 2024-01-02. They are session indexes. They are not a claim that those historical sessions were replayed. The block bootstrap draws joint length-20 blocks of SPY, QQQ, and IWM from the post-warmup panel, wrapping at the ends. Single-name bootstrap returns are the SPY factor plus fresh noise, not a block of a stock that was never loaded.

GARCH(1,1) grid pick: alpha=0.12, beta=0.85, omega=0.000003. Student-t nu=30.00 from GARCH-residual excess kurtosis -0.37. Raw SPY excess kurtosis is 9.05. Residual kurtosis is not positive, so the Student-t sits at the Gaussian cap. Fat tails in the generator are the jump term and the higher crisis variance. Jump probability=0.0104, jump size=0.0403. SPY gap-variance share=0.358.

Regime means (SPY daily simple return) and counts:

- bull: n=278, mean=0.00169, variance=0.000052, half-spread=5e-05, depth scale=1.0
- bear: n=5, mean=0.00054, variance=0.000117 unconditional fallback, half-spread=0.0002, depth scale=0.6
- sideways: n=667, mean=0.00028, variance=0.000074, half-spread=5e-05, depth scale=1.0
- crisis: n=244, mean=0.00008, variance=0.000316, half-spread=0.0015, depth scale=0.25

On one 4000-session Markov path, corr(NEXA, SPY) is 0.5561 in bull (n=990) and 0.9260 in crisis (n=560). The crisis state shrinks idiosyncratic volatility, which is what pulls single names toward the factor. In the fixture itself, corr(QQQ, IWM) is 0.6749 outside crisis labels and 0.9257 inside them.

## Book under the generators

Each variant is the same stand-in as the historical replay: long-only, limit at the prior close, fill at the next open only if the limit is still marketable, and the shipped risk gate. The stress runner passes the day's half-spread into that fill. Flat-cost variants add that spread on top of the 1 bp fee. The TCA variant puts it in the price and lowers the depth input. The historical study does not pass a spread, and its numbers are unchanged. The combined variant's Kronos and options sleeves are flat on these dates. The recorded Kronos curve is a different market, and it is not applied. The separate variance-swap column is a mark of trailing realized variance against this surface. It is not `simulate_book` and it is not an order.

Paths per generator: 500. Sessions per path: 252. CAGR and Sharpe use the scored window, which starts after 60 closes. Beating SPY means the scored-window growth of the book exceeded SPY's growth over those same sessions. The 10% drawdown probe is not a cockpit control. The cockpit's daily-loss limit is $500 of realized loss. This replay still feeds it the open-to-close mark, and a $1,000 position rarely produces a $500 day.

### markov

| variant | median CAGR | worst 5% CAGR | worst 1% CAGR | median Sharpe | median max DD | worst 5% max DD | P(beat SPY) | mean exposure | 10% probe paths | mtm days under -$500 | daily-loss gate hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cockpit | -0.0139 | -0.0570 | -0.0826 | -0.5173 | 0.0282 | 0.0586 | 0.2300 | 0.1041 | 1 | 1 | 0 |
| sizer | -0.0182 | -0.0692 | -0.0963 | -0.6083 | 0.0323 | 0.0662 | 0.2160 | 0.1061 | 1 | 3 | 0 |
| combined | -0.0160 | -0.0547 | -0.0731 | -0.6208 | 0.0287 | 0.0572 | 0.2180 | 0.1009 | 0 | 1 | 0 |
| tca | -0.0132 | -0.0570 | -0.0803 | -0.4938 | 0.0282 | 0.0584 | 0.2260 | 0.1043 | 1 | 1 | 0 |

SPY-equivalent on the same scored windows: median CAGR 0.1274, median Sharpe 0.7354, median max drawdown 0.1412, worst 5% max drawdown 0.3109.
Variance-swap mark on the cockpit paths, median P&L 30.35 dollars, worst 5% 10.64. The worst 5% is still positive: the structural premium was wider than the variance these paths realized. That is the premium assumption, not a fitted variance risk premium.
Orders submitted: 0.

### block_bootstrap

| variant | median CAGR | worst 5% CAGR | worst 1% CAGR | median Sharpe | median max DD | worst 5% max DD | P(beat SPY) | mean exposure | 10% probe paths | mtm days under -$500 | daily-loss gate hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cockpit | -0.0000 | -0.0407 | -0.0518 | 0.0106 | 0.0232 | 0.0425 | 0.2200 | 0.1153 | 0 | 0 | 0 |
| sizer | -0.0018 | -0.0472 | -0.0621 | -0.0442 | 0.0244 | 0.0437 | 0.2220 | 0.1151 | 0 | 0 | 0 |
| combined | -0.0013 | -0.0406 | -0.0524 | -0.0393 | 0.0227 | 0.0393 | 0.2200 | 0.1099 | 0 | 0 | 0 |
| tca | 0.0005 | -0.0394 | -0.0516 | 0.0358 | 0.0230 | 0.0413 | 0.2200 | 0.1150 | 0 | 0 | 0 |

SPY-equivalent on the same scored windows: median CAGR 0.1453, median Sharpe 0.8748, median max drawdown 0.1199, worst 5% max drawdown 0.2251.
Variance-swap mark on the cockpit paths, median P&L 32.48 dollars, worst 5% 11.68. The worst 5% is still positive: the structural premium was wider than the variance these paths realized. That is the premium assumption, not a fitted variance risk premium.
Orders submitted: 0.

## Scenarios

These are forced shapes. flash_crash, crash_2020, grind_2022, and volmageddon are named for the kind of path, not because those episodes were loaded. The fixture does not contain February 2018 or the spring of 2020. The grind is a constant drift, not the 2022 tape.

| scenario | SPY total | SPY worst day | cockpit ending | cockpit exposure | cockpit max DD | beat SPY | sizer ending | tca ending | variance-swap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| flash_crash | -0.0491 | -0.0900 | 25000.00 | 0.0000 | 0.0000 | true | 25000.00 | 25000.00 | 27.51 |
| crash_2020 | -0.1317 | -0.0250 | 24973.86 | 0.1997 | 0.0010 | true | 24872.47 | 24974.41 | 33.66 |
| grind_2022 | -0.2125 | -0.0012 | 25000.00 | 0.0000 | 0.0000 | true | 25000.00 | 25000.00 | 4.57 |
| volmageddon | -0.0422 | -0.0450 | 25000.00 | 0.0000 | 0.0000 | true | 25000.00 | 25000.00 | 25.01 |
| melt_up | 0.4182 | 0.0022 | 25000.00 | 0.0000 | 0.0000 | false | 25000.00 | 25000.00 | 28.11 |
| sideways_chop | 0.0142 | -0.0151 | 24837.68 | 0.0000 | 0.0139 | true | 24601.41 | 24838.63 | 5.17 |
| rate_shock | -0.0350 | -0.0350 | 25000.00 | 0.0000 | 0.0000 | true | 25000.00 | 25000.00 | 20.82 |
| earnings_gaps | 0.0000 | 0.0000 | 24999.75 | 0.0400 | 0.0000 | false | 24999.50 | 25000.00 | 24.15 |

A path that is flat until the shock never bids, because the 60-session return is not positive, so ending equity stays 25,000 and the drawdown stays 0. A path that rises every day still may not fill: part of the rise is the open gap, and the limit is the prior close, so the open leaves the bid behind. The melt-up is that case. Beating SPY on a down scenario with exposure near 0 is the cash balance. It is not a short and it does not harvest the decline.

## Headlines

Each headline is text. The cockpit normalizer is loaded from `app/ingest/normalize.py` without starting the feed package. The source `synthetic:stress` is not on the allowlist, so `python_to_market_event` refuses it and the item never becomes a MarketEvent. No language-model call is made. Cost is $0. Provider credential variables present and unused: none.

SPY text is scored with `score_session`. NEXA and BRKQ are not on that allowlist, so `score_session` drops them. They are then scored with the same buzz formula, the same lexicon, and the same 20-session mention baseline. The lexicon is long-only in effect: an alert requires positive sentiment. A bearish headline does not create a sell. Flow does not read the headline. One synthetic SPY chain was passed to `score_book` (22 contracts, raw alerts 1, orders 0).

The cockpit-limit sleeve uses the prior close as the limit, which is the replay's rule. A gap up makes that limit unmarketable, so the book does not chase a beat. The chase sleeve sets the limit at the next open. It is a counterfactual, labeled as such, so the P&L of believing the headline can be seen. Bearish headlines, whether the ground truth is real or a hoax, are shown with a probe position that was already long. The sentiment rule does not exit it. The holding loss is the gap the probe kept. On a hoax with no price impact that loss is about zero, which is the same mechanism that fails to sell a real miss.

Zero-noise mechanism, one path per class:

| class | truth | alert | scored by | cockpit filled | cockpit P&L | chase filled | chase P&L | probe holding loss | market event |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| earnings_beat | true | yes | same_formula_outside_allowlist | no | 0.00 | yes | -0.20 | n/a | refused |
| earnings_miss | true | no | same_formula_outside_allowlist | no | 0.00 | no | 0.00 | 60.00 | refused |
| ma_rumor_true | true | yes | same_formula_outside_allowlist | no | 0.00 | yes | 9.90 | n/a | refused |
| ma_rumor_false | false | yes | same_formula_outside_allowlist | no | 0.00 | yes | -10.20 | n/a | refused |
| priced_in_beat | true | yes | same_formula_outside_allowlist | yes | -0.20 | yes | -0.20 | n/a | refused |
| fda_clearance | true | yes | same_formula_outside_allowlist | no | 0.00 | yes | -0.20 | n/a | refused |
| guidance_cut | true | no | same_formula_outside_allowlist | no | 0.00 | no | 0.00 | 50.00 | refused |
| macro_print | true | no | score_session | no | 0.00 | no | 0.00 | 15.00 | refused |
| hoax | false | no | same_formula_outside_allowlist | no | 0.00 | no | 0.00 | 0.00 | refused |

Noisy Monte Carlo: 500 paths, one class drawn per path, 40 bp daily background noise, the declared impact added on top. Orders submitted: 0. Market events accepted: 0.

| class | n | alert rate | cockpit fill rate | mean cockpit P&L | chase fill rate | mean chase P&L | mean probe holding loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| earnings_beat | 54 | 1.00 | 0.00 | 0.00 | 1.00 | -1.25 | n/a |
| earnings_miss | 51 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 29.16 |
| fda_clearance | 61 | 1.00 | 0.00 | 0.00 | 1.00 | -3.68 | n/a |
| guidance_cut | 52 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 34.28 |
| hoax | 54 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | -0.18 |
| ma_rumor_false | 55 | 1.00 | 0.00 | 0.00 | 1.00 | -9.61 | n/a |
| ma_rumor_true | 65 | 1.00 | 0.00 | 0.00 | 1.00 | 9.72 | n/a |
| macro_print | 59 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 7.31 |
| priced_in_beat | 49 | 1.00 | 0.55 | -1.08 | 1.00 | -0.22 | n/a |

Latency of an alert is zero sessions when the text is scored on the session it is timestamped, and the fill is attempted at the next open. A bearish headline has no alert, so it has no reaction latency. That includes true misses, guidance cuts, the macro print, and the bearish hoax. The hoax is escaped by the normalizer (`system:` becomes a role-marker) and still refused as a market event.

## Implied vol

ATM IV is the regime's median 20-day realized vol times one plus a structural premium. The put wing is richer. `bs_price` prices a 95-strike put and a 105-strike call at 30 days.

| regime | realized | premium | ATM IV | 95-put IV | 105-call IV | put price at skew | put price at ATM vol |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bull | 0.1023 | 0.1200 | 0.1146 | 0.1151 | 0.1141 | 0.083 | 0.081 |
| sideways | 0.1313 | 0.1800 | 0.1550 | 0.1559 | 0.1541 | 0.272 | 0.266 |
| bear | 0.1357 | 0.3000 | 0.1765 | 0.1781 | 0.1749 | 0.411 | 0.400 |
| crisis | 0.2531 | 0.7500 | 0.4430 | 0.4503 | 0.4361 | 2.908 | 2.833 |

## Reading this

If the capped book has a smaller drawdown than SPY, that is the cash balance. If it beats SPY in a forced bear scenario, that is the stand-in going flat. If the chase sleeve loses money on a false rumor and the cockpit limit does not, the limit is what prevented the chase, not the lexicon. The lexicon still alerts on the bullish false rumor and on a bullish line that has no price impact. It does not alert on a true miss. None of those statements is a claim about live trading, and none of them is an edge.

- The fit was asked to cover 2016 through 2026. The committed fixture is 2021-10-18 through 2026-10-16 and only SPY, QQQ, IWM.
- 2016-01-01 through the session before the fixture is not in the tree, so it is not in the fit.
- Regime labels use the full-sample 80th percentile of 20-day volatility. That cutoff sees the future. It is a calibration label, not a signal.
- Single-name beta, idiosyncratic vol, start price, and volume are assumptions. No single-name tape was loaded.
- Half-spreads and the crisis depth multiplier are structural. The bars contain no quotes. Printed volume is not reduced in a crisis; the impact formula's depth input is.
- Student-t degrees of freedom come from excess kurtosis of GARCH residuals, clipped to [5, 30]. Jumps are the |return| > 3 sigma days. Continuous variance is scaled so jumps are not added on top of that variance.
- ETF betas are in-sample OLS on the same fixture the generator is judged against. They are not a walk-forward estimate.
- Regimes with fewer than 30 labeled days use the unconditional SPY mean and variance. bear n=5 sample mean -0.01120. That sample mean is not used as a drift.

Generated 2026-10-19T07:26:09Z.
