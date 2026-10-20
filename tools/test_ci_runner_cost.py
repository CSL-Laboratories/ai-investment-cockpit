"""Cost accounting regressions: all starts, not just successful candidates."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_runner_cost import estimate


def job(**changes):
    value = {"repository": "CSL-Laboratories/ai-investment-cockpit", "run_id": 100,
                 "run_attempt": 1, "job_id": 1, "event": "pull_request", "workflow": "ci.yml",
                 "name": "linux (3.12)", "head_sha": "a" * 40,
                 "labels": ["blacksmith-2vcpu-ubuntu-2404"],
                 "started_at": "2026-10-17T00:00:00Z",
                 "completed_at": "2026-10-17T00:01:01Z", "conclusion": "success"}
    value.update(changes)
    value.setdefault("runner_id", 123)
    value.setdefault("steps", [{"name": "test", "conclusion": "success"}])
    return value


class RunnerCostTests(unittest.TestCase):
    def test_all_events_and_failed_cancelled_starts_count(self):
        rows = [job(job_id=i + 1, event=event, conclusion=outcome)
                for i, (event, outcome) in enumerate([
                    ("pull_request", "success"), ("push", "failure"),
                    ("workflow_dispatch", "cancelled"), ("merge_group", "success"),
                    ("schedule", "timed_out"), ("pull_request_review", "success")])]
        result = estimate(rows)
        self.assertEqual(result["supplied_jobs_total_usd"], "0.048")
        self.assertEqual(len(result["jobs"]), 6)

    def test_reruns_distinct_repeated_exports_deduplicated(self):
        first = job()
        retry = job(job_id=2, run_attempt=2)
        self.assertEqual(estimate([first, copy.deepcopy(first), retry])[
            "supplied_jobs_total_usd"], "0.016")
        with self.assertRaises(ValueError):
            estimate([first, job(run_attempt=2)])

    def test_each_cell_rounded_independently(self):
        rows = [job(job_id=1, completed_at="2026-10-17T00:00:01Z"),
                job(job_id=2, completed_at="2026-10-17T00:00:01Z")]
        self.assertEqual(estimate(rows)["supplied_jobs_total_usd"], "0.008")
        for end, expected in [("00:00:00", "0.004"), ("00:01:00", "0.004"),
                              ("00:01:00.000001", "0.008")]:
            self.assertEqual(estimate([job(completed_at=f"2026-10-17T{end}Z")])[
                "supplied_jobs_total_usd"], expected)

    def test_public_core_free_does_not_make_paid_mac_free(self):
        rows = [job(job_id=1, labels=["ubuntu-latest"]),
                job(job_id=2, labels=["macos-latest"]),
                job(job_id=3, labels=["blacksmith-macos-m4"])]
        result = estimate(rows)
        self.assertEqual(result["known_subtotal_usd"], "0.000")
        self.assertIsNone(result["supplied_jobs_total_usd"])
        self.assertEqual(result["unknown_jobs"], 1)
        for labels in ([], ["self-hosted", "macos-latest"], ["ubuntu-8core"]):
            self.assertEqual(estimate([job(labels=labels)])["unknown_jobs"], 1)

    def test_windows_and_other_repo(self):
        result = estimate([job(repository="CSL-Laboratories/recursion",
                               labels=["blacksmith-2vcpu-windows-2025"])])
        self.assertEqual(result["supplied_jobs_total_usd"], "0.016")

    def test_missing_timing_never_assumed_zero_except_unstarted_skip(self):
        for conclusion in ("cancelled", "failure", None, "success"):
            self.assertEqual(estimate([job(started_at=None, completed_at=None,
                                           conclusion=conclusion)])["unknown_jobs"], 1)
        self.assertEqual(estimate([job(started_at=None, completed_at=None,
                                       conclusion="skipped", runner_id=None, steps=[])])[
            "supplied_jobs_total_usd"], "0.000")
        self.assertEqual(estimate([job(completed_at=None)])["unknown_jobs"], 1)
        self.assertEqual(estimate([job(conclusion="skipped")])[
            "supplied_jobs_total_usd"], "0.008")

    def test_invalid_input_rejected(self):
        for change in [{"run_attempt": True}, {"job_id": 0}, {"head_sha": "bad"},
                       {"repository": "private/repo"}, {"labels": "macos-latest"},
                       {"completed_at": "2026-10-16T23:59:00Z"},
                       {"started_at": "2026-10-17T00:00:00"}, {"event": ""}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                estimate([job(**change)])
        with self.assertRaises(ValueError):
            estimate([])

    def test_cli_audit_hash_and_exit_codes(self):
        script = Path(__file__).with_name("ci_runner_cost.py")
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            for records, code in [([job()], 0), ([job(labels=["unknown"])], 1), ([], 2)]:
                ledger.write_text(json.dumps(records), encoding="utf-8")
                result = subprocess.run([sys.executable, str(script), str(ledger)],
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, code, result.stderr)
                if code != 2:
                    report = json.loads(result.stdout)
                    self.assertEqual(len(report["input_sha256"]), 64)
                    self.assertIn("not verified", report["export_completeness"])
                    self.assertIn("attempts/1", report["jobs"][0]["url"])

    def test_outcome_and_incomplete_timestamp_schema(self):
        missing = job()
        del missing["conclusion"]
        with self.assertRaises(KeyError):
            estimate([missing])
        for conclusion in ([], {}, 1, "nonsense"):
            with self.subTest(conclusion=conclusion), self.assertRaises(ValueError):
                estimate([job(conclusion=conclusion)])
        for start in (123, "", "not-a-time"):
            with self.subTest(start=start), self.assertRaises(ValueError):
                estimate([job(started_at=start, completed_at=None)])

    def test_api_stamped_skips_require_no_runner_and_no_steps(self):
        skipped = job(conclusion="skipped", runner_id=None, steps=[],
                      completed_at="2026-10-16T23:59:59Z")
        self.assertEqual(estimate([skipped])["supplied_jobs_total_usd"], "0.000")
        self.assertEqual(estimate([job(conclusion="skipped", runner_id=None)])[
            "supplied_jobs_total_usd"], "0.008")
        self.assertEqual(estimate([job(conclusion="cancelled", runner_id=None,
                                       steps=[])])["supplied_jobs_total_usd"], "0.008")


if __name__ == "__main__":
    unittest.main()
