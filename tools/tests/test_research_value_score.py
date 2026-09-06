"""Synthetic calculator checks; never prospective study observations."""
import copy
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "research_value_score", Path(__file__).resolve().parents[1] / "research_value_score.py",
)
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)
PROTOCOL = {"study_id": "synthetic-test-only", "protocol_sha256": "a" * 64}


class ScoreTests(unittest.TestCase):
    def pair(self, predicted=None, truth="present", target="105.00"):
        common = {"id": "x", "field": "cash_offer", "source_sha256": "b" * 64}
        return (
            {**PROTOCOL, "parser_revision": "test-revision", "rows": [{**common, "value": predicted}]},
            {**PROTOCOL, "reviewer": "synthetic-independent-labeler",
             "independence_procedure": "unit fixture only",
             "rows": [{**common, "truth": truth, "value": target}]},
        )

    def test_missing_data_never_becomes_a_pass(self):
        self.assertEqual(score.score_time(None)["status"], "unmeasured")
        self.assertEqual(score.score_strategy(None)["status"], "unmeasured")
        self.assertEqual(score.score_extraction(None, None)["status"], "unmeasured")

    def test_refused_extractable_field_is_a_false_negative(self):
        field = score.score_extraction(*self.pair())["fields"]["cash_offer"]
        self.assertEqual(field["fn"], 1)
        self.assertIsNone(field["precision"])
        self.assertEqual(field["recall"], "0")

    def test_wrong_value_counts_both_false_emission_and_miss(self):
        field = score.score_extraction(*self.pair(predicted="106.00"))["fields"]["cash_offer"]
        self.assertEqual((field["tp"], field["fp"], field["fn"]), (0, 1, 1))

    def test_correct_value_and_ambiguous_truth_are_distinct(self):
        field = score.score_extraction(*self.pair(predicted="105.00"))["fields"]["cash_offer"]
        self.assertEqual((field["precision"], field["recall"]), ("1", "1"))
        ambiguous = score.score_extraction(
            *self.pair(predicted="105.00", truth="ambiguous", target=None),
        )["fields"]["cash_offer"]
        self.assertEqual(ambiguous["ambiguous"], 1)
        self.assertIsNone(ambiguous["precision"])
        wrong_type = score.score_extraction(*self.pair(predicted=1, target=True))["fields"]["cash_offer"]
        self.assertEqual((wrong_type["fp"], wrong_type["fn"]), (1, 1))

    def test_daily_returns_are_compounded(self):
        common = {"strategy_gross_return": ".1", "execution_cost_return": 0,
                  "operating_cost_return": 0, "cash_net_return": 0,
                  "broad_equity_net_return": 0, "simple_merger_net_return": 0}
        result = score.score_strategy({**PROTOCOL, "rows": [
            {**common, "date": "2026-09-01"}, {**common, "date": "2026-09-02"},
        ]})
        self.assertEqual(result["net_cumulative_return"]["strategy"], "0.21")

    def test_true_absence_is_not_perfect_recall(self):
        field = score.score_extraction(*self.pair(truth="absent", target=None))["fields"]["cash_offer"]
        self.assertEqual(field["tn"], 1)
        self.assertIsNone(field["recall"])

    def test_missing_label_or_changed_source_refuses(self):
        predictions, labels = self.pair()
        wrong = copy.deepcopy(labels)
        wrong["rows"][0]["source_sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            score.score_extraction(predictions, wrong)
        labels["rows"] = []
        with self.assertRaises(ValueError):
            score.score_extraction(predictions, labels)

    def test_maintenance_and_allocated_cost_can_erase_savings(self):
        result = score.score_time({**PROTOCOL, "hourly_value_usd": "60", "rows": [{
            "pair_id": "1", "manual_minutes": 30, "assisted_minutes": 10,
            "maintenance_minutes": 15, "allocated_cost_usd": 6,
        }]})
        self.assertEqual(result["median_minutes_saved"], "5")
        self.assertEqual(Decimal(result["net_value_usd"]), Decimal(-1))

    def test_costs_reverse_gross_strategy_outperformance(self):
        row = {"date": "2026-09-01", "strategy_gross_return": ".02",
               "execution_cost_return": ".01", "operating_cost_return": ".015",
               "cash_net_return": 0, "broad_equity_net_return": ".01",
               "simple_merger_net_return": ".005"}
        result = score.score_strategy({**PROTOCOL, "rows": [row]})
        self.assertEqual(result["net_cumulative_return"]["strategy"], "-0.005")
        self.assertEqual(result["cost_adjusted_excess_return"]["cash"], "-0.005")
        self.assertEqual(result["max_drawdown"]["strategy"], "0.005")
        with self.assertRaises(ValueError):
            score.score_strategy({**PROTOCOL, "rows": [row, row]})

    def test_nonfinite_and_negative_costs_refuse(self):
        for value in ("NaN", "Infinity", "-1", True):
            with self.assertRaises((TypeError, ValueError)):
                score._number(value, nonnegative=True)


if __name__ == "__main__":
    unittest.main()
