# kronos_signal

Walk-forward forecasts from Kronos, an open-source candlestick foundation model (MIT, AAAI 2026, arXiv 2508.02739). This package does not place orders. The paper runner can size a book from a finished report. It does not load the weights.

## Pin

Inference sources are copied under `vendor/kronos/` from `https://github.com/shiyu-coder/Kronos` at commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a` (2026-04-13). `vendor/kronos/PIN.json` lists the files. The MIT notice in `vendor/kronos/LICENSE` applies. Training code, the web UI, and figures are not vendored. Weights stay on Hugging Face and are fetched by `from_pretrained`:

| Size | Model | Tokenizer | Context |
|---|---|---|---|
| mini | `NeoQuasar/Kronos-mini` | `NeoQuasar/Kronos-Tokenizer-2k` | 2048 |
| small | `NeoQuasar/Kronos-small` | `NeoQuasar/Kronos-Tokenizer-base` | 512 |

`Kronos-base` is not used. Device is CPU. `KronosPredictor.predict_batch` averages its samples before returning, so this package calls `sample_count=1` once per path and computes the mean and the sample dispersion here.

Install the optional dependencies (the vendored April 2026 sources were run against `huggingface_hub==0.33.1`):

```bash
python -m pip install -e ".[kronos]"
```

If that resolves a CUDA torch wheel, install the CPU wheel first:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[kronos]"
```

## Cutoff

The paper's appendix says the pre-training data extends up to June 2024, and that the test period for every task begins in July 2024. This module uses that sentence: scored decisions are on or after **2024-07-01**, and a label that ends on or before **2024-06-30** is refused.

A repository comment (shiyu-coder/Kronos#117, 2025-09-27) describes a finer split: training through February 2024, validation March–May 2024, out-of-sample from June 2024. The NeurIPS 2025 slides put train/validation through about June 2024 and test from July 2024. July 2024 is after all of those windows. Context bars may be older. They are the history available on the decision date, not labels. There is no fine-tune.

Future timestamps passed to the model are calendar features (minute, hour, weekday, day, month). They are not future prices. The scored label is the close `pred_len` sessions after the decision close.

## Signal

For each allowlisted symbol the forecast is the mean of several sample paths of the next-N-bar close return, plus the sample standard deviation. That mean is the edge and that dispersion is the volatility passed to `suggest_notional`. They share the horizon. The horizon vol target is `0.01 * sqrt(pred_len)`. A non-positive edge sizes to zero. The book is long-only.

`amount` is `volume * close`. Yahoo bars do not carry a dollar amount. The fixture is not a point-in-time tape.

## Commands

Smoke (what the unit tests call: Kronos-mini, lookback 32, temperatures 1.0 and 0.8, one-bar horizon, eight decisions, SPY only):

```bash
python -m sidecar.kronos_signal.evaluate --profile smoke
```

Longer CPU run. The checked-in `examples/kronos_eval.json` is one execution of this command. Re-running it replaces that file. Quote the committed file when citing the pull request.

```bash
python -m sidecar.kronos_signal.evaluate --profile report --out examples/kronos_eval.json --ledger examples/kronos_eval.ledger.jsonl
```

The report grid is mini and small, lookbacks 64 and 128, temperatures 1.0 and 0.7 (eight configs). Primary, fixed before the run, is mini / lookback 64 / temperature 1.0. Horizon and stride are 5 sessions, 36 decisions, 4 sample paths, symbols SPY, QQQ, and IWM. Decisions start at the first session on or after 2024-07-01. The book stops at the open five sessions after the last fill. It does not keep the residual hold through the end of the fixture.

## Committed report

`examples/kronos_eval.json` is one report-profile run. The scored book runs from the 2024-07-02 fill through the 2025-03-21 mark. `orders_submitted` is 0. The primary config did **not** beat zero-return, trailing mean, or buy-and-hold. `beats_baselines` is false. `threshold_status` is `not_frozen`.

Primary (mini, lookback 64, temperature 1.0), pooled across SPY, QQQ, and IWM, 36 decisions:

| Item | Primary Kronos | Zero-return | Trailing mean | Buy-and-hold |
|---|---|---|---|---|
| Ending equity | 91945.01467356542211066211403 | 100000 | 103025.6929640805959608712531 | 101418.4563917799362885658572 |
| Mean step return | -0.0020492933604 | 0 | 0.00110747036551 | 0.000684357460998 |
| PSR | 0.270841980117 | unavailable (zero variance) | 0.613422389804 | 0.565582151057 |
| DSR | 0.0192597888218 | unavailable | 0.120846851722 | 0.0978540411666 |
| Annualized Sharpe | -0.71741828213 | unavailable | 0.348681864839 | 0.198877769022 |
| Max drawdown | 0.121297714523 | n/a | 0.0775648025825 | 0.11318155771 |
| Information coefficient | 0.16113576 | undefined | -0.13759115 | n/a |
| Rank IC | 0.11814672 | undefined | -0.25302445 | n/a |
| Directional hit rate | 0.52777778 | undefined | 0.61111111 | n/a |
| Brier on direction | 0.27854938 | 0.25000000 | 0.38888889 | n/a |

PBO across the eight configs is **0.333333333333** (4 slices, 3 splits). That PBO ranks forecast-weighted realized returns, not the long-only equity curve. PSR, DSR, and drawdown are the long-only book. Annualized Sharpe uses 50 periods per year because the stride is 5 sessions.

Per symbol on the primary config: SPY IC 0.24815804, hit 0.61111111, Brier 0.29687500. QQQ IC -0.04151459, hit 0.41666667, Brier 0.40451389. IWM IC 0.12103610, hit 0.55555556, Brier 0.23611111.

The constant-0.5 Brier is 0.25. The primary Brier is worse than that null. The positive IC did not survive costs and the long-only sizer: ending equity is below the flat book. Other grid rows ended higher (the best committed row is mini, lookback 128, temperature 1.0, ending equity 114446.8329072878574543375526). Those rows were not eligible to replace the primary after the result was known.

The unscored forecast on the last fixture bar (2026-10-16) is negative for SPY, QQQ, and IWM, so the sizer target from this file is zero until a later report says otherwise. Yahoo bars are not a point-in-time tape. This is not a qualify decision.

## Paper runner

The default strategy is still `inverse_vol`. Kronos is optional:

```bash
python -m sidecar.harness.runner --strategy kronos --when close
python -m sidecar.harness.runner --strategy kronos --when close --execute --kronos-report examples/kronos_eval.json
```

`--execute` still requires the sidecar paper credentials and the paper host. See `sidecar/harness/README.md`. The runner reads the JSON report only. It does not import torch. It refuses the strategy when the report is missing, when `evaluation_start` is not `2024-07-01`, when `pretraining_through` is not `2024-06-30`, when `live_eligible` is not true, or when `tradable_signals` is empty. On execute, a signal dated more than 10 days before the New York session, or after that session, is refused before a trade client is constructed.

`live_eligible` means the post-cutoff protocol finished (`n` step returns at least 8, decisions on or after 2024-07-01, `threshold_status` `not_frozen`). It does not mean the forecast beat the nulls. `beats_baselines` is a separate flag and does not gate the order.

## Evaluation

Nulls, all on the same decision dates:

- zero-return: edge 0, flat book, probability 0.5
- trailing mean: N-bar returns that end on or before the decision, then the same sizer
- buy-and-hold: equal weight at the first fill open, 1 bp, then hold

Signal quality is Pearson IC, Spearman rank IC, directional hit rate, and Brier score on `p_up` against the sign of the realized N-bar return. The cost-aware book fills the next adjusted open and charges 1 bp, the same convention as the harness. PSR and DSR are on that book's step returns. Annualized Sharpe uses `252/stride` periods per year.

PBO is CSCV on the forecast-weighted series (pooled mean forecast times pooled realized return), across the predeclared grid. A long-only cash book has too many exact zeros to split. The ranking series is not the equity curve. CSCV is not purged. `threshold_status` stays `not_frozen`.

## Retrofit

Do not call this module from the cockpit gateway, and do not give it broker credentials. A later integration would keep the forecast here and would still have to pass risk, mode, and approval before any order. The experimental runner is a separate paper path. It has to be reconciled at integration time. It is not a second cockpit order path.
