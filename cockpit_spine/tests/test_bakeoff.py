"""Plumbing runs; the result-record step stays blocked on WP8."""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from cockpit_spine import bakeoff
from successor.route_consumer import ACCEPTED, FAIL_CLOSED, DecisionOutcome

SOURCE = Path(bakeoff.__file__)


def _outcome(disposition: str, reason: str) -> DecisionOutcome:
    request = {"id": "r"}
    if disposition == ACCEPTED:
        reply = {"output_digest": "sha256:" + "2" * 64}
        return DecisionOutcome(ACCEPTED, reason, {"proposal_id": "p"}, request, reply, None)
    return DecisionOutcome(FAIL_CLOSED, reason, None, request, None, "watch_only")


class StubConsumer:
    def __init__(self, engine: str) -> None:
        self.engine, self.calls = engine, []

    def decide(self, *, request_id, profile, mode, prompt, output_schema=None):
        self.calls.append((request_id, profile.workload_id, mode, prompt))
        if self.engine == "codex":
            return _outcome(FAIL_CLOSED, "engine_not_permitted_in_mode")
        return _outcome(ACCEPTED, "ok")


class PlanTests(unittest.TestCase):
    def test_bad_plans_are_refused_before_any_call(self) -> None:
        for plan in (bakeoff.Plan(engines=()), bakeoff.Plan(engines=("gpt",)),
                     bakeoff.Plan(engines=("claude", "claude")), bakeoff.Plan(trials=0),
                     bakeoff.Plan(trials=bakeoff.MAX_TRIALS + 1)):
            calls: list = []
            with self.assertRaises(ValueError):
                bakeoff.run_trials(plan, calls.append)
            self.assertEqual([], calls)


class RunTests(unittest.TestCase):
    def test_every_planned_trial_goes_through_the_consumer_once(self) -> None:
        consumers: dict[str, StubConsumer] = {}

        def factory(engine: str) -> StubConsumer:
            consumers[engine] = StubConsumer(engine)
            return consumers[engine]

        batch = bakeoff.run_trials(bakeoff.Plan(trials=3), factory)
        self.assertEqual(6, len(batch["trials"]))
        self.assertEqual(
            [("bk-claude-1", "workload-d04", "paper", "synthetic prompt"),
             ("bk-claude-2", "workload-d04", "paper", "synthetic prompt"),
             ("bk-claude-3", "workload-d04", "paper", "synthetic prompt")],
            consumers["claude"].calls)
        self.assertEqual(3, len(consumers["codex"].calls))
        self.assertEqual({FAIL_CLOSED}, {t["disposition"] for t in batch["trials"]
                                         if t["arm"] == "codex"})

    def test_the_batch_is_raw_unsealed_and_carries_no_result_vocabulary(self) -> None:
        batch = bakeoff.run_trials(bakeoff.Plan(), StubConsumer)
        self.assertEqual((False, None), (batch["sealed"], batch["protocol_ref"]))
        self.assertIn("not a result record", batch["kind"])
        blob = json.dumps(batch).lower()
        for word in ("score", "winner", "rank", "return", "pnl", "sharpe", "beats", "verdict:"):
            self.assertNotIn(word, blob.replace("no verdict", ""))
        self.assertEqual({"arm", "trial", "disposition", "reason", "request_digest",
                          "output_digest"}, set(batch["trials"][0]))


class BlockedOnWp8Tests(unittest.TestCase):
    def test_status_says_blocked_and_names_wp8(self) -> None:
        status = bakeoff.record_step_status()
        self.assertEqual(("blocked", 0), (status["state"], status["result_records_written"]))
        self.assertIn("WP8", status["blocked_on"])
        self.assertIn("TODO(WP8)", status["todo"])

    def test_sealing_is_refused_for_any_batch(self) -> None:
        batch = bakeoff.run_trials(bakeoff.Plan(), StubConsumer)
        for candidate in (batch, {}, {"sealed": True, "protocol_ref": "x"}):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "WP8"):
                bakeoff.seal_result_record(candidate)

    def test_pinning_a_protocol_does_not_unlock_a_writer(self) -> None:
        with mock.patch.object(bakeoff, "WP8_PROTOCOL", {"digest": "sha256:" + "a" * 64}):
            self.assertIn("writer not implemented", bakeoff.record_step_status()["state"])
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "not implemented"):
                bakeoff.seal_result_record({})

    def test_the_protocol_pin_is_committed_as_none(self) -> None:
        self.assertIsNone(bakeoff.WP8_PROTOCOL)
        self.assertRegex(SOURCE.read_text(), r"TODO\(WP8\)")

    def test_the_module_has_no_way_to_write_anything(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse({"open", "print"} & names)
        self.assertFalse({"write", "write_text", "write_bytes", "dump", "mkdir", "replace"} & attrs)
        imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                   for a in n.names}
        self.assertFalse({"os", "shutil", "pathlib", "json", "tempfile"} & imports)

    def test_no_result_record_exists_in_the_tree(self) -> None:
        root = Path(bakeoff.__file__).resolve().parent
        found = [p for p in root.rglob("*.json")
                 if re.search(r"result|verdict|cp3", p.name, re.IGNORECASE)]
        self.assertEqual([], found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
