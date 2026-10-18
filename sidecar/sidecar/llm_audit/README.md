# llm audit

An append-only log of model judgments and a scorecard that refuses look-ahead.

Log a judgment when it is made, with `as_of` set to the information time, not the scoring time. Fill `outcome_up`, `outcome_return`, and `outcome_at` only once the horizon has elapsed. Scoring drops:

- rows with no outcome
- rows whose `outcome_at` is not strictly after `as_of`
- rows whose `as_of` is not strictly after `model_training_cutoff`, when that cutoff is present
- bad timestamps or labels

The scorecard reports Brier, log loss, a 10-bin reliability table, and Spearman IC of probability against `outcome_return` when at least three scored rows have a return. The null forecast is probability 0.5. An optional per-row `baseline_probability` is scored separately when present. `beats_null` does not promote a route. `threshold_status` is `not_frozen`.

`anonymize_text(text, symbols)` replaces tickers with stable `ENT_` tokens before a historical prompt is shown to a model. The token is a hash of the ticker. It is not a privacy control against someone who has the symbol list.

## Retrofit

Tap `agent_runner` results and schema-validated model outputs into `JudgmentLog`. Do not give the log a path to `OrderGateway`. Whether a route has earned a role is a later human decision against a freeze, using only rows that survived the filters above.
