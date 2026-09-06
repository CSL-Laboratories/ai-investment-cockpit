# Offline quality, time and cost scoring

`tools/research_value_score.py` performs arithmetic on separately collected research records.
It reads files and prints JSON. It has no application, audit, mode, model, network or broker
capability. Every output is marked `research_only_no_gate_change`; it never emits an empirical
PASS. Use the [prospective protocol](../research/gate-feasibility-and-value-2026-09-06.md) to collect
and independently review the input population first.

From the repository root:

```powershell
.\.venv\Scripts\python.exe tools/research_value_score.py
.\.venv\Scripts\python.exe tools/research_value_score.py --predictions predictions.json --labels labels.json --time time.json --strategy strategy.json
.\.venv\Scripts\python.exe -m unittest discover -s tools/tests -p test_research_value_score.py
```

Each supplied file has `study_id`, a lowercase SHA-256 `protocol_sha256`, and a `rows` list.
The hash identifies the predeclared protocol; human review must establish its authenticity and
the population's completeness. The calculator records the exact input-byte and calculator hashes.
Omitted inputs and zero denominators are unmeasured. The no-input
[status record](../evidence/research-value-status-2026-09-06.json) contains no invented observations.

| Input | Required additional fields |
|---|---|
| Predictions | Top-level `parser_revision`. Each row: unique `id`, `field`, `source_sha256`, `value` (exact normalized value, or null for refusal). Include every predeclared field, not only emissions. |
| Independent labels | Top-level `reviewer`, `independence_procedure`. Exactly the same row IDs, fields and raw hashes; each row also has `truth` = `present`, `absent` or `ambiguous`, and `value` (exact normalized truth only for present, otherwise null). |
| Time pairs | Optional top-level `hourly_value_usd` (null leaves dollar value unmeasured). Each row: unique `pair_id`, `manual_minutes`, `assisted_minutes`, `maintenance_minutes`, `allocated_cost_usd`. Record full subscription allocation, incremental cash and contention separately in collection notes to avoid double counting. |
| Strategy days | Strictly ordered unique ISO `date`; `strategy_gross_return`, `execution_cost_return`, `operating_cost_return`, `cash_net_return`, `broad_equity_net_return`, `simple_merger_net_return`. All are daily fractions on aligned capital, already adjusted for external cash flows. Benchmark returns must include their own costs. Preserve the complete observation calendar; the calculator cannot discover omitted days. |

Extraction reports per-field TP/FP/FN/TN and ambiguous counts, precision and recall. A wrong
non-null value counts as both a false emission and a missed correct value; refusing a present
fact counts as a false negative. Null truth is not permission to call an ambiguous field correct.
Use the same independently chosen value normalization in labels and predictions (for example,
decimal cash strings at the declared precision). Declaring a reviewer name does not prove reviewer
independence; the output explicitly says the calculator did not verify it.

Time results subtract assisted review and maintenance from manual time, and subtract allocated
cash cost from any chosen value of saved hours. Strategy results compound net daily returns and
report cumulative returns, maximum drawdown and cost-adjusted excess versus each benchmark.
They do not source prices, authenticate broker fills, estimate capacity, handle leverage, perform
statistical inference or promote paper results to live evidence. Keep cost stress tests and
uncertainty assessment in the study review. Negative savings and underperformance remain visible.

The ten unit tests use only synthetic arithmetic cases, including refusal recall, wrong values,
source/population mismatch, maintenance erasing savings, and costs reversing gross outperformance.
They do not constitute an independent label or investment-result population.
