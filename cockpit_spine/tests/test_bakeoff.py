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
from successor.route_consumer.profile import canonical_digest

SOURCE = Path(bakeoff.__file__)


ALLOWED_IMPORT_MODULES = frozenset({
    "__future__", "collections.abc", "dataclasses", "typing",
    "successor.route_consumer", "successor.route_consumer.profile",
})
FORBIDDEN_NAMES = frozenset({
    "open", "print", "input", "exec", "eval", "compile", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "breakpoint", "builtins",
})
WRITE_ATTRIBUTES = frozenset({
    "write", "writelines", "write_text", "write_bytes", "dump", "mkdir", "replace", "rename",
    "unlink", "remove", "touch", "open", "system", "popen", "run",
})


def write_capability_problems(source: str) -> list[str]:
    """Why a module could write, or reach something that can. Allow-list, not deny-list."""

    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += [f"import {a.name}" for a in node.names
                         if a.name not in ALLOWED_IMPORT_MODULES]
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            if node.level or module not in ALLOWED_IMPORT_MODULES:
                problems.append(f"from {module} import ...")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in WRITE_ATTRIBUTES | FORBIDDEN_NAMES:
            problems.append(f"attribute {node.attr}")
        elif isinstance(node, ast.Constant) and node.value in FORBIDDEN_NAMES - {"open", "print"}:
            problems.append(f"string {node.value!r}")
    return problems


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
                     bakeoff.Plan(trials=bakeoff.MAX_TRIALS + 1), bakeoff.Plan(trials=True),
                     bakeoff.Plan(trials=2.0), bakeoff.Plan(trials="2")):
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


class TrialValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = bakeoff.run_trials(bakeoff.Plan(trials=2), StubConsumer)
        self.by_arm = {
            arm: [t for t in self.batch["trials"] if t["arm"] == arm] for arm in ("claude", "codex")
        }

    def test_the_accepted_arm_carries_the_output_digest_and_reason(self) -> None:
        for trial in self.by_arm["claude"]:
            self.assertEqual(ACCEPTED, trial["disposition"])
            self.assertEqual("sha256:" + "2" * 64, trial["output_digest"])
            self.assertEqual("ok", trial["reason"])

    def test_the_fail_closed_arm_has_no_output_digest_and_names_the_mode_reason(self) -> None:
        for trial in self.by_arm["codex"]:
            self.assertIsNone(trial["output_digest"])
            self.assertEqual("engine_not_permitted_in_mode", trial["reason"])

    def test_the_request_digest_is_the_digest_of_the_request_the_route_saw(self) -> None:
        for trial in self.batch["trials"]:
            self.assertEqual(canonical_digest({"id": "r"}), trial["request_digest"])

    def test_a_missing_request_record_gives_a_null_request_digest(self) -> None:
        class NoRequest(StubConsumer):
            def decide(self, **kwargs):
                return DecisionOutcome(FAIL_CLOSED, "gone", None, None, None, "watch_only")

        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), NoRequest)
        self.assertEqual((None, None), (batch["trials"][0]["request_digest"],
                                         batch["trials"][0]["output_digest"]))


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
        self.assertEqual([], write_capability_problems(SOURCE.read_text(encoding="utf-8")))

    def test_the_write_guard_refuses_each_known_bypass(self) -> None:
        for snippet in (
            "from os import remove", "from os import remove as _rm", "from shutil import copyfile",
            "import builtins", "import os", "import os.path as p", "from . import sibling",
            "from pathlib import Path", "import subprocess", "from io import open",
            "open('x', 'w')", "__import__('os')", "exec('x')", "eval('x')",
            "getattr(object, 'x')", "x.write_text('y')", "x.unlink()",
            "import successor.other",
        ):
            self.assertTrue(write_capability_problems(snippet), snippet)

    def test_the_write_guard_accepts_what_the_module_needs(self) -> None:
        for snippet in (
            "from __future__ import annotations",
            "from collections.abc import Callable",
            "from dataclasses import dataclass",
            "from typing import Any, Protocol",
            "from successor.route_consumer import DECISION_PROFILE",
            "from successor.route_consumer.profile import canonical_digest",
        ):
            self.assertEqual([], write_capability_problems(snippet), snippet)

    def test_no_result_record_exists_in_the_tree(self) -> None:
        """A tripwire only: file names under ``cockpit_spine/`` that say result, verdict or cp3.

        It does not look at other directories (``successor/route_consumer/records/
        x2-fake-driver-bakeoff.json`` is plumbing output and sits outside its scope) and
        it cannot see a record saved under another name.
        """

        root = Path(bakeoff.__file__).resolve().parent
        found = [p for p in root.rglob("*.json")
                 if re.search(r"result|verdict|cp3", p.name, re.IGNORECASE)]
        self.assertEqual([], found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
