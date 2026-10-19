# llm audit

An append-only log of model judgments and a scorecard that refuses look-ahead.

Log a judgment when it is made, with `as_of` set to the information time, not the scoring time. Fill `outcome_up`, `outcome_return`, and `outcome_at` only once the horizon has elapsed. Scoring drops:

- rows with no outcome
- rows whose `outcome_at` is not strictly after `as_of`
- rows whose `as_of` is not strictly after `model_training_cutoff`, when that cutoff is present
- bad timestamps or labels

The scorecard reports Brier, log loss, a 10-bin reliability table, and Spearman IC of probability against `outcome_return` when at least three scored rows have a return. The null forecast is probability 0.5. An optional per-row `baseline_probability` is scored separately when present. `beats_null` does not promote a route. `threshold_status` is `not_frozen`.

`anonymize_text(text, symbols)` replaces tickers with stable `ENT_` tokens before a historical prompt is shown to a model. The token is a hash of the ticker. It is not a privacy control against someone who has the symbol list.

## Showdown

`ShowdownLog` appends a decision before any outcome. The decision carries the thesis, its hash, the expected direction and magnitude, the horizon end, the confidence, and the driver class. It is rejected if it includes a realized return or a catalyst flag. The outcome is a later row for the same id. A second outcome, an outcome logged at or before the decision, and an outcome before the horizon are refused.

`score_showdown` partitions scored rows:

- right for the right reasons: direction hit, the stated driver happened, and the attribution says that driver is why the price moved
- right for the wrong reasons: direction hit, but the driver did not happen or was not why it moved
- wrong for the right reasons: the driver happened and the direction missed
- wrong for the wrong reasons: neither

A market driver happened when `beta * market_return` is strictly on the called side. A catalyst happened when the news or sentiment log marks that kind as having occurred and not already priced. A process driver happened when the decision-time evidence bit is true. That bit is how the trend stand-in and the Figgie bench record a sound procedure. The outcome row cannot change it.

The scorecard reports Brier and a 10-bin reliability curve of confidence against the direction hit, hit rates by driver class, and a weekly grouping by the ISO week of `as_of`. `render_weekly` writes that as markdown. `threshold_status` stays `not_frozen`.

`adapters.log_standin_closes` logs the combined stand-in rule id `combined.deterministic_stand_in.v1` and the sign of the trailing 60-session return. It does not invent thesis prose. `adapters.log_news_catalog` logs each synthetic-news kind from the lexicon sign, then joins the catalog truth flag and the tape's realized return. Headlines are not copied into the log. No model is called. The dollar cost of that step is zero.

## Retrofit

Tap `agent_runner` results and schema-validated model outputs into `JudgmentLog`. Do not give the log a path to `OrderGateway`. Whether a route has earned a role is a later human decision against a freeze, using only rows that survived the filters above.
