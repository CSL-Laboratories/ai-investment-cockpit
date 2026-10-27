"""Plumbing runs, and an under-powered batch seals inconclusive against a synthetic protocol.

The synthetic vault and its registry are built in ``setUpModule``. Nothing here
reads ``CSL_RESEARCH_CHECKOUT`` or a real csl-research checkout.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cockpit_spine import bakeoff, projections
from successor.route_consumer import ACCEPTED, FAIL_CLOSED, DecisionOutcome
from successor.route_consumer.profile import canonical_digest

SOURCE = Path(bakeoff.__file__)
SYNTHETIC_RELATIVE = "synthetic/cp7-rev2.md"
SYNTHETIC_COMMIT = "d" * 40
_SYNTHETIC_TEXT = (
    "synthetic CP7 stand-in for unit tests\n"
    "verdict class default: inconclusive\n"
    "This file is not the csl-research protocol.\n"
)

SYNTHETIC_VAULT: Path
SYNTHETIC_REGISTRY: Path


def setUpModule() -> None:
    """Build a synthetic vault and the registry that cites it. No real checkout."""

    global SYNTHETIC_VAULT, SYNTHETIC_REGISTRY
    data = _SYNTHETIC_TEXT.encode("utf-8")
    SYNTHETIC_VAULT = Path(tempfile.mkdtemp(prefix="cp7-vault-"))
    target = SYNTHETIC_VAULT / SYNTHETIC_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    pin = {
        "repository": projections.VAULT_REPOSITORY,
        "commit": SYNTHETIC_COMMIT,
        "path": SYNTHETIC_RELATIVE,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    directory = Path(tempfile.mkdtemp(prefix="cp7-registry-"))
    SYNTHETIC_REGISTRY = directory / "registry.json"
    SYNTHETIC_REGISTRY.write_text(
        json.dumps({"registry_version": 1, "protocol": pin}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def tearDownModule() -> None:
    shutil.rmtree(SYNTHETIC_VAULT, ignore_errors=True)
    shutil.rmtree(SYNTHETIC_REGISTRY.parent, ignore_errors=True)


def synthetic_pin() -> dict[str, str]:
    """The pin from the registry built in ``setUpModule``, not a hand-written dict."""

    document = json.loads(SYNTHETIC_REGISTRY.read_text(encoding="utf-8"))
    protocol = document["protocol"]
    if not isinstance(protocol, dict):
        raise AssertionError("synthetic registry has no protocol pin")
    return protocol


ALLOWED_IMPORT_MODULES = frozenset({
    "__future__", "collections.abc", "dataclasses", "hashlib", "typing",
    "cockpit_spine.projections",
    "successor.route_consumer", "successor.route_consumer.profile",
})
# Exactly the names bakeoff.py imports from each module. A dunder or any other
# name is refused even when the module itself is allowed, so
# ``from typing import __builtins__ as b`` cannot bind the builtins dict.
ALLOWED_IMPORT_NAMES = {
    "__future__": frozenset({"annotations"}),
    "collections.abc": frozenset({"Callable"}),
    "dataclasses": frozenset({"dataclass"}),
    "typing": frozenset({"Any", "Protocol"}),
    "cockpit_spine.projections": frozenset({
        "REPO_ROOT", "VAULT_REPOSITORY", "ProjectionError", "read_cited_bytes",
    }),
    "successor.route_consumer": frozenset({"DECISION_PROFILE", "DecisionOutcome"}),
    "successor.route_consumer.profile": frozenset({"canonical_digest"}),
}
# Exactly the builtins bakeoff.py calls or names. Every other builtin, including
# open, print, getattr, vars, globals, eval, exec, __import__, help and memoryview,
# is refused by name, so a new dynamic accessor cannot slip past a short deny-list.
ALLOWED_BUILTINS = frozenset({
    "Exception", "UnicodeError", "ValueError", "any", "bool", "dict", "enumerate", "int",
    "isinstance",
    "len", "list", "range", "set", "str", "tuple",
})
# Exactly the attributes bakeoff.py uses, including list.append. Anything else,
# including frame, code and globals attributes, is a write-capability problem.
# hashlib.sha256().hexdigest() and str.startswith / bytes.decode are reads.
ALLOWED_ATTRIBUTES = frozenset({
    "append", "check", "decide", "decode", "disposition", "engines", "hexdigest",
    "prompt", "reason", "reply_record", "request_record", "sha256", "startswith",
    "trials",
})
_BUILTIN_NAMES = frozenset(vars(builtins))


def _is_dunder(text: object) -> bool:
    return isinstance(text, str) and len(text) > 4 and text[:2] == text[-2:] == "__"


def _import_from_problems(node: ast.ImportFrom) -> list[str]:
    """Refuse a ``from`` import whose module, name or alias is not allow-listed.

    The name and alias are checked for every source module, including modules
    on ``ALLOWED_IMPORT_MODULES``. A dunder (``__builtins__``, ``__import__``,
    ``__loader__``) is refused even if it were listed.
    """

    module = ("." * node.level) + (node.module or "")
    problems: list[str] = []
    if node.level or module not in ALLOWED_IMPORT_MODULES:
        problems.append(f"from {module} import ...")
    allowed = ALLOWED_IMPORT_NAMES.get(module, frozenset())
    for alias in node.names:
        if _is_dunder(alias.name) or alias.name not in allowed:
            problems.append(f"from {module} import {alias.name}")
        if alias.asname is not None and (
            _is_dunder(alias.asname) or alias.asname not in allowed
        ):
            problems.append(f"from {module} import {alias.name} as {alias.asname}")
    return problems


def write_capability_problems(source: str) -> list[str]:
    """Why a module could write, or reach something that can. Allow-lists, not deny-lists.

    Imports, the names an ``ImportFrom`` binds, builtins and attribute reads are
    each limited to what bakeoff.py uses. Imported names are checked, not only
    the source module, so a dunder or any name bakeoff.py does not import is
    refused from every module. Every dunder name or string is refused, and so
    is a ``match`` class pattern, because it reads attributes without an
    ``ast.Attribute`` node (``case object(gi_frame=f)``).
    """

    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += [f"import {a.name}" for a in node.names
                         if a.name not in ALLOWED_IMPORT_MODULES]
        elif isinstance(node, ast.ImportFrom):
            problems += _import_from_problems(node)
        elif isinstance(node, ast.Name) and (
            _is_dunder(node.id)
            or (node.id in _BUILTIN_NAMES and node.id not in ALLOWED_BUILTINS)
        ):
            problems.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr not in ALLOWED_ATTRIBUTES:
            problems.append(f"attribute {node.attr}")
        elif isinstance(node, ast.MatchClass):
            problems.append("match class pattern")
        elif isinstance(node, ast.Constant) and _is_dunder(node.value):
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

    def test_a_refusal_reply_without_an_output_digest_is_omitted(self) -> None:
        class Refusal(StubConsumer):
            def decide(self, **kwargs):
                reply = {"type": "RouteRefusal@0", "request_id": "r", "code": "timeout",
                         "dispatched": False}
                return DecisionOutcome(
                    FAIL_CLOSED, "timeout", None, {"id": "r"}, reply, "watch_only")

        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), Refusal)
        self.assertIsNone(batch["trials"][0]["output_digest"])
        self.assertEqual("timeout", batch["trials"][0]["reason"])

    def test_a_missing_request_record_gives_a_null_request_digest(self) -> None:
        class NoRequest(StubConsumer):
            def decide(self, **kwargs):
                return DecisionOutcome(FAIL_CLOSED, "gone", None, None, None, "watch_only")

        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), NoRequest)
        self.assertEqual((None, None), (batch["trials"][0]["request_digest"],
                                         batch["trials"][0]["output_digest"]))


class ProtocolPinTests(unittest.TestCase):
    def test_the_committed_pin_cites_the_candidate_rev2_record(self) -> None:
        pin = bakeoff.PROTOCOL_PIN
        self.assertEqual(bakeoff.WP8_PROTOCOL, pin)
        self.assertEqual(projections.VAULT_REPOSITORY, pin["repository"])
        self.assertEqual("cfefc77b1c43a941ae986dca877f02c251be42e1", pin["commit"])
        self.assertEqual(
            "submissions/20261026-csl518-cp7-prereg-power-contract-02/result.md", pin["path"],
        )
        self.assertEqual(
            "fa3e486cf256f485b5cf73849757df825685b56633505d842f37bc6c5bbcf2d1", pin["sha256"],
        )
        self.assertRegex(SOURCE.read_text(encoding="utf-8"), r"TODO\(WP8\)")

    def test_status_names_the_candidate_pin_and_writes_nothing(self) -> None:
        status = bakeoff.record_step_status()
        self.assertEqual(0, status["result_records_written"])
        self.assertIn("inconclusive", status["state"])
        self.assertIn("durable write blocked", status["state"])
        self.assertEqual("not accepted by Samir; pin may change", status["acceptance"])
        self.assertEqual(bakeoff.PROTOCOL_PIN, status["protocol"])
        self.assertIn("TODO(WP8)", status["todo"])
        self.assertIn("PENDING_OWNER: ledger location", status["open_gaps"])
        self.assertIn("NOT_DECIDED: non-default verdict class wording", status["open_gaps"])

    def test_a_missing_checkout_fails_closed(self) -> None:
        batch = bakeoff.run_trials(bakeoff.Plan(), StubConsumer)
        env = os.environ.copy()
        env.pop(projections.VAULT_CHECKOUT_ENV, None)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "checkout"):
                bakeoff.seal_result_record(batch)

    def test_the_checkout_env_is_used_when_vault_root_is_omitted(self) -> None:
        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), StubConsumer)
        env = os.environ.copy()
        env[projections.VAULT_CHECKOUT_ENV] = str(SYNTHETIC_VAULT)
        with mock.patch.dict(os.environ, env, clear=True):
            sealed = bakeoff.seal_result_record(batch, pin=synthetic_pin())
        self.assertEqual("inconclusive", sealed["verdict"])

    def test_vault_root_wins_over_the_checkout_env(self) -> None:
        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), StubConsumer)
        empty = Path(tempfile.mkdtemp(prefix="cp7-empty-"))
        env = os.environ.copy()
        env[projections.VAULT_CHECKOUT_ENV] = str(empty)
        try:
            with mock.patch.dict(os.environ, env, clear=True):
                sealed = bakeoff.seal_result_record(
                    batch, vault_root=SYNTHETIC_VAULT, pin=synthetic_pin(),
                )
            self.assertEqual("inconclusive", sealed["verdict"])
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "unreadable"):
                    bakeoff.seal_result_record(batch, vault_root=empty, pin=synthetic_pin())
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class SealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pin = synthetic_pin()
        self.batch = bakeoff.run_trials(bakeoff.Plan(trials=2), StubConsumer)
        self.before = sorted(p.relative_to(SYNTHETIC_VAULT) for p in SYNTHETIC_VAULT.rglob("*"))

    def _seal(self, batch: dict | None = None, **kwargs: object) -> dict:
        return bakeoff.seal_result_record(
            self.batch if batch is None else batch,
            vault_root=kwargs.get("vault_root", SYNTHETIC_VAULT),
            pin=kwargs.get("pin", self.pin),
        )

    def test_an_under_powered_run_seals_inconclusive_and_does_not_call_a_verdict(self) -> None:
        sealed = self._seal()
        self.assertEqual("inconclusive", sealed["verdict"])
        self.assertEqual("under-powered", sealed["reason"])
        self.assertEqual((False, False, "Provisional:"), (
            sealed["called_verdict"], sealed["persisted"], sealed["label"],
        ))
        self.assertTrue(sealed["sealed"])
        self.assertEqual(self.pin, sealed["protocol_ref"])
        self.assertEqual(4, sealed["trial_count"])
        self.assertEqual(bakeoff.OPEN_GAPS, tuple(sealed["open_gaps"]))
        self.assertFalse(bakeoff.power_established())

    def test_the_raw_batch_is_left_unsealed(self) -> None:
        self._seal()
        self.assertEqual((False, None), (self.batch["sealed"], self.batch["protocol_ref"]))

    def test_every_trial_is_on_the_ledger_including_a_refusal(self) -> None:
        sealed = self._seal()
        self.assertEqual(4, len(sealed["ledger"]))
        previous = "sha256:" + "0" * 64
        for index, record in enumerate(sealed["ledger"], start=1):
            self.assertEqual(index, record["seq"])
            self.assertEqual(previous, record["prev"])
            body = {key: record[key] for key in ("seq", "kind", "data", "prev")}
            self.assertEqual(canonical_digest(body), record["hash"])
            previous = record["hash"]
        self.assertEqual(previous, sealed["ledger_head"])
        arms = [record["data"]["arm"] for record in sealed["ledger"]]
        self.assertEqual(["claude", "claude", "codex", "codex"], arms)
        self.assertEqual(
            "engine_not_permitted_in_mode", sealed["ledger"][-1]["data"]["reason"],
        )

    def test_the_result_digest_covers_the_record_except_itself(self) -> None:
        sealed = self._seal()
        material = {key: value for key, value in sealed.items() if key != "result_sha256"}
        self.assertEqual(canonical_digest(material), sealed["result_sha256"])
        again = self._seal()
        self.assertEqual(sealed["result_sha256"], again["result_sha256"])

    def test_sealing_does_not_write_a_record(self) -> None:
        self._seal()
        after = sorted(p.relative_to(SYNTHETIC_VAULT) for p in SYNTHETIC_VAULT.rglob("*"))
        self.assertEqual(self.before, after)

    def test_a_digest_mismatch_is_refused(self) -> None:
        forged = dict(self.pin)
        forged["sha256"] = "a" * 64
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "pinned sha256"):
            self._seal(pin=forged)

    def test_a_protocol_that_does_not_name_inconclusive_is_refused(self) -> None:
        data = b"synthetic protocol with no default class\n"
        path = SYNTHETIC_VAULT / "synthetic" / "other.md"
        path.write_bytes(data)
        pin = dict(self.pin)
        pin["path"] = "synthetic/other.md"
        pin["sha256"] = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "inconclusive"):
            self._seal(pin=pin)

    def test_a_malformed_batch_is_refused_after_the_protocol_loads(self) -> None:
        for candidate in ({}, {"sealed": True, "protocol_ref": "x"}):
            with self.assertRaises(ValueError):
                self._seal(candidate)

    def test_a_powered_claim_cannot_be_sealed(self) -> None:
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "NOT_DECIDED"):
            bakeoff.verdict_for(power_established=True)
        self.assertEqual(
            ("inconclusive", "under-powered"),
            bakeoff.verdict_for(power_established=False),
        )


class WriteGuardTests(unittest.TestCase):
    def test_the_module_has_no_way_to_write_anything(self) -> None:
        self.assertEqual([], write_capability_problems(SOURCE.read_text(encoding="utf-8")))

    def test_the_write_guard_refuses_each_known_bypass(self) -> None:
        for snippet in (
            "from os import remove", "from os import remove as _rm", "from shutil import copyfile",
            "from typing import __builtins__ as b", "from dataclasses import __builtins__ as b",
            "from collections.abc import __loader__", "from typing import __import__",
            "from typing import Any as __builtins__", "from typing import cast",
            "from successor.route_consumer import __builtins__", "from typing import *",
            "import builtins", "import os", "import os.path as p", "from . import sibling",
            "from pathlib import Path", "import subprocess", "from io import open",
            "open('x', 'w')", "__import__('os')", "exec('x')", "eval('x')",
            "getattr(object, 'x')", "x.write_text('y')", "x.unlink()",
            "import successor.other",
            "__builtins__['open']", "canonical_digest.__globals__",
            "type(x).__subclasses__()", "x.__dict__", "x.__class__",
            "g = (i for i in ()); g.gi_frame.f_globals['__builtins__']['open']",
            "'__builtins__'", "x = '__globals__'",
            "match g:\n    case object(gi_frame=f): pass",
            "match canonical_digest:\n    case Callable(__globals__=g): pass",
            "__loader__", "__spec__", "help()", "memoryview(b'')", "vars()", "globals()",
            "type(x)", "object",
        ):
            self.assertTrue(write_capability_problems(snippet), snippet)
        for name in (
            "gi_frame", "cr_frame", "ag_frame", "tb_frame", "f_back", "f_globals",
            "f_builtins", "f_locals", "gi_code", "co_code", "co_names",
        ):
            self.assertTrue(write_capability_problems(f"x.{name}"), name)

    def test_the_refused_hops_really_reach_the_builtins(self) -> None:
        """Positive controls: each hop the guard refuses does reach ``open`` at runtime."""

        hops = {
            "attribute": "g = (i for i in ())\nfound = g.gi_frame.f_globals['__builtins__']",
            "from-import": "from typing import __builtins__ as found",
            # Only names and attributes the module may use: the read hides in the pattern.
            "match": (
                "from collections.abc import Callable\n"
                "from successor.route_consumer.profile import canonical_digest\n"
                "match canonical_digest:\n"
                "    case Callable(__globals__=found): pass"
            ),
        }
        for label, snippet in hops.items():
            with self.subTest(label):
                space: dict = {}
                exec(snippet, space)
                found = space["found"]
                if "__builtins__" in found:
                    found = found["__builtins__"]
                found = found if isinstance(found, dict) else vars(found)
                self.assertIs(open, found["open"])
                self.assertTrue(write_capability_problems(snippet))

    def test_the_write_guard_accepts_what_the_module_needs(self) -> None:
        for snippet in (
            "from __future__ import annotations",
            "from collections.abc import Callable",
            "from dataclasses import dataclass",
            "from typing import Any, Protocol",
            "from successor.route_consumer import DECISION_PROFILE",
            "from successor.route_consumer.profile import canonical_digest",
            "import hashlib",
            "from cockpit_spine.projections import REPO_ROOT, VAULT_REPOSITORY",
            "from cockpit_spine.projections import ProjectionError, read_cited_bytes",
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
