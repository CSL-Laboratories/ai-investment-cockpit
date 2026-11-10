"""Plumbing runs, and a batch seals inconclusive against a synthetic protocol.

The synthetic vault is built in ``setUpModule``. Tests patch ``PROTOCOL_PIN``.
A real csl-research checkout is optional and used only by the skip-unless test.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cockpit_spine import bakeoff, projections
from successor.route_consumer import ACCEPTED, FAIL_CLOSED, DecisionOutcome
from successor.route_consumer.profile import canonical_digest

SOURCE = Path(bakeoff.__file__)
SYNTHETIC_RELATIVE = "submissions/synthetic-cp7/result.md"
_SYNTHETIC_TEXT = (
    "synthetic CP7 stand-in for unit tests\n"
    "\"verdict_class\": {\"enum\": [\"inconclusive\"]}\n"
    "\"entry_type\": {\"enum\": [\"observation_seal\"]}\n"
    "\"role\": {\"enum\": [\"pre_contract\"]}\n"
    "\"venue_class\": {\"enum\": [\"VC-EQ\"]}\n"
    "\"status\": {\"enum\": [\"registered\", \"running\", \"completed\", "
    "\"aborted\", \"failed\", \"invalid\"]}\n"
    "This file is not the csl-research protocol.\n"
)
# Section 6.5 entry schema, as Samir read it at the pinned commit: objects for
# contract_ref and authored_by, and the status enum. The checkout test extracts
# the schema from the pinned bytes and is the check against that file.
_LEDGER_ENTRY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "seq", "entry_id", "entry_type", "recorded_at_utc",
        "prev_entry_sha256", "entry_sha256", "contract_ref", "trial_id", "role",
        "venue_class", "venue_cell", "status", "authored_by",
    ],
    "properties": {
        "schema_version": {"type": "integer"},
        "seq": {"type": "integer", "minimum": 0},
        "entry_id": {"type": "string", "minLength": 1},
        "entry_type": {"enum": [
            "registration", "amendment", "run_start", "run_end", "observation_seal",
            "class_assignment", "invalidation", "note",
        ]},
        "recorded_at_utc": {"type": "string", "minLength": 1},
        "prev_entry_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        "entry_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "contract_ref": {
            "type": "object",
            "additionalProperties": False,
            "required": ["repository", "commit", "path"],
            "properties": {
                "repository": {"type": "string", "minLength": 1},
                "commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "path": {"type": "string", "minLength": 1},
                "result_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        },
        "trial_id": {"type": "string", "minLength": 1},
        "role": {"type": "string", "minLength": 1},
        "venue_class": {"type": "string", "minLength": 1},
        "venue_cell": {"type": "string", "minLength": 1},
        "status": {"enum": [
            "registered", "running", "completed", "aborted", "failed", "invalid",
        ]},
        "authored_by": {
            "type": "object",
            "additionalProperties": False,
            "required": ["actor_label", "tool"],
            "properties": {
                "actor_label": {"type": "string", "minLength": 1},
                "tool": {"type": "string", "minLength": 1},
                "human_owner": {"type": "string"},
            },
        },
    },
}
_CLOCK = "2026-10-27T00:00:00Z"
_ENTRY_KEYS = {
    "schema_version", "seq", "entry_id", "entry_type", "recorded_at_utc",
    "prev_entry_sha256", "entry_sha256", "contract_ref", "trial_id", "role",
    "venue_class", "venue_cell", "status", "authored_by",
}

SYNTHETIC_VAULT: Path
SYNTHETIC_PIN: dict[str, str]


def _install(
    vault: Path, relative: str, data: bytes, *,
    status: str = "draft_candidate_not_accepted",
    record_sha: str | None = None,
    record_id: str | None = None,
) -> dict[str, str]:
    """Write a result.md and its sibling record.json. Return the pin for those bytes."""

    target = vault / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    parts = relative.split("/")
    record = {
        "record_id": parts[-2] if record_id is None else record_id,
        "contract_status": status,
        "result": {"sha256": digest if record_sha is None else record_sha},
    }
    (target.parent / "record.json").write_text(json.dumps(record), encoding="utf-8")
    return {
        "repository": projections.VAULT_REPOSITORY,
        "commit": "d" * 40,
        "path": relative,
        "sha256": digest,
    }


def setUpModule() -> None:
    """Build a synthetic vault. No real checkout, and no registry round-trip."""

    global SYNTHETIC_VAULT, SYNTHETIC_PIN
    SYNTHETIC_VAULT = Path(tempfile.mkdtemp(prefix="cp7-vault-"))
    SYNTHETIC_PIN = _install(
        SYNTHETIC_VAULT, SYNTHETIC_RELATIVE, _SYNTHETIC_TEXT.encode("utf-8"),
    )


def tearDownModule() -> None:
    shutil.rmtree(SYNTHETIC_VAULT, ignore_errors=True)


def _bare(body: dict) -> str:
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_defs(node: dict, root: dict) -> dict:
    if node is root or "$defs" in node or "$defs" not in root:
        return node
    merged = dict(node)
    merged["$defs"] = root["$defs"]
    return merged


def _entry_schemas(node: object, root: dict) -> list[dict]:
    """Schema objects that describe a section 6.5 ledger entry."""

    found: list[dict] = []
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and {"entry_sha256", "authored_by", "status"} <= set(props):
            found.append(_with_defs(node, root))
        for value in node.values():
            found.extend(_entry_schemas(value, root))
    elif isinstance(node, list):
        for item in node:
            found.extend(_entry_schemas(item, root))
    return found


def ledger_schemas(text: str) -> list[dict]:
    """Pull section 6.5 entry schemas out of fenced JSON in a protocol file."""

    found: list[dict] = []
    for block in re.findall(r"```(?:json)?[^\n]*\n(.*?)```", text, re.DOTALL):
        try:
            document = json.loads(block)
        except ValueError:
            continue
        if isinstance(document, dict):
            found.extend(_entry_schemas(document, document))
    return found


def _validate(entries: list[dict], schema: dict) -> None:
    import jsonschema

    validator = jsonschema.Draft202012Validator(schema)
    for entry in entries:
        validator.validate(entry)


ALLOWED_IMPORT_MODULES = frozenset({
    "__future__", "collections.abc", "dataclasses", "datetime", "hashlib", "json", "typing",
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
    "datetime": frozenset({"datetime", "timezone"}),
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
    "Exception", "TypeError", "UnicodeError", "ValueError", "bool", "bytes", "dict", "enumerate",
    "int", "isinstance", "len", "list", "range", "set", "str", "tuple",
})
# Exactly the attributes bakeoff.py uses, including list.append. Anything else,
# including frame, code and globals attributes, is a write-capability problem.
# hashlib.sha256().hexdigest() and str.startswith / bytes.decode are reads.
ALLOWED_ATTRIBUTES = frozenset({
    "append", "check", "decide", "decode", "disposition", "dumps", "encode", "engines",
    "get", "hexdigest", "join", "loads", "now", "prompt", "reason", "reply_record",
    "request_record", "sha256", "split", "startswith", "strftime", "trials", "utc",
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
    def test_the_committed_pin_has_citation_shape(self) -> None:
        pin = bakeoff.PROTOCOL_PIN
        self.assertEqual(projections.VAULT_REPOSITORY, pin["repository"])
        self.assertEqual(40, len(pin["commit"]))
        self.assertEqual(64, len(pin["sha256"]))
        self.assertTrue(pin["path"].endswith("/result.md"))
        self.assertNotIn("..", pin["path"].split("/"))
        self.assertRegex(SOURCE.read_text(encoding="utf-8"), r"TODO\(WP8\)")

    def test_acceptance_is_a_lucas_receipt_and_samir_only_reviews(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        for phrase in (
            "Samir accepts", "accepted by Samir", "Samir has not accepted", "Samir's acceptance",
        ):
            self.assertNotIn(phrase, text)
        self.assertIn("Samir reviews", text)
        self.assertIn("096ad9d8", text)
        self.assertNotIn("receipt absent", text)
        status = bakeoff.record_step_status()
        self.assertEqual(0, status["result_records_written"])
        self.assertIn("inconclusive", status["state"])
        self.assertIn("durable write blocked", status["state"])
        self.assertIn("096ad9d8", status["acceptance"])
        self.assertIn("Samir reviews", status["acceptance"])
        self.assertNotIn("accepted by Samir", status["acceptance"])
        self.assertEqual(bakeoff.PROTOCOL_PIN, status["protocol"])
        self.assertIn("TODO(WP8)", status["todo"])
        self.assertIn("PENDING_OWNER: ledger location", status["open_gaps"])
        self.assertIn("PENDING_OWNER: SESOI", status["open_gaps"])
        self.assertIn("PENDING_DATA: sigma_a, rho, mbar, rho_c", status["open_gaps"])
        self.assertIn("NOT_DECIDED: non-default verdict class wording", status["open_gaps"])
        self.assertIn("Hard-coded block", bakeoff.power_established.__doc__)
        self.assertFalse(bakeoff.power_established())

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
            with mock.patch.object(bakeoff, "PROTOCOL_PIN", SYNTHETIC_PIN):
                sealed = bakeoff.seal_result_record(batch)
        self.assertEqual("inconclusive", sealed["verdict_class"])

    def test_vault_root_wins_over_the_checkout_env(self) -> None:
        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), StubConsumer)
        empty = Path(tempfile.mkdtemp(prefix="cp7-empty-"))
        env = os.environ.copy()
        env[projections.VAULT_CHECKOUT_ENV] = str(empty)
        try:
            with mock.patch.object(bakeoff, "PROTOCOL_PIN", SYNTHETIC_PIN):
                with mock.patch.dict(os.environ, env, clear=True):
                    sealed = bakeoff.seal_result_record(batch, vault_root=SYNTHETIC_VAULT)
                self.assertEqual("inconclusive", sealed["verdict_class"])
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "unreadable"):
                        bakeoff.seal_result_record(batch, vault_root=empty)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class SealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = bakeoff.run_trials(bakeoff.Plan(trials=2), StubConsumer)
        self.before = sorted(p.relative_to(SYNTHETIC_VAULT) for p in SYNTHETIC_VAULT.rglob("*"))
        self._pin = mock.patch.object(bakeoff, "PROTOCOL_PIN", SYNTHETIC_PIN)
        self._clock = mock.patch.object(bakeoff, "_recorded_at_utc", return_value=_CLOCK)
        self._pin.start()
        self._clock.start()
        self.addCleanup(self._pin.stop)
        self.addCleanup(self._clock.stop)

    def _seal(self, batch: dict | None = None, vault_root: Path | None = None) -> dict:
        return bakeoff.seal_result_record(
            self.batch if batch is None else batch,
            vault_root=SYNTHETIC_VAULT if vault_root is None else vault_root,
        )

    def test_the_default_class_is_inconclusive_and_nothing_is_called(self) -> None:
        sealed = self._seal()
        self.assertEqual("inconclusive", sealed["verdict_class"])
        self.assertIn("no estimand measured", sealed["reason"])
        self.assertNotIn("under-powered", sealed["reason"])
        self.assertEqual("provisional", sealed["publication_label"])
        self.assertEqual("Provisional: inconclusive by default", sealed["display"])
        self.assertFalse(sealed["persisted"])
        self.assertTrue(sealed["sealed"])
        self.assertNotIn("verdict", sealed)
        self.assertNotIn("called_verdict", sealed)
        self.assertNotIn("label", sealed)
        self.assertNotIn("protocol_ref", sealed)
        self.assertNotIn("ledger", sealed)
        self.assertNotIn("ledger_head", sealed)
        self.assertNotIn("result_sha256", sealed)
        self.assertEqual(bakeoff.OPEN_GAPS, tuple(sealed["open_gaps"]))
        self.assertIn("not a CP7 section 7 result record", sealed["kind"])

    def test_the_citation_matches_section_64(self) -> None:
        sealed = self._seal()
        contract = sealed["preregistration_contract"]
        self.assertEqual(
            {"repository", "commit", "path", "result_sha256", "wording_status"}, set(contract),
        )
        self.assertEqual(SYNTHETIC_PIN["sha256"], contract["result_sha256"])
        self.assertNotIn("sha256:", contract["result_sha256"])
        self.assertEqual("provisional, inconclusive by default", contract["wording_status"])
        ledger = sealed["trial_ledger"]
        self.assertEqual(
            {"ledger_head_sha256", "first_seq", "last_seq", "n_trials_declared"}, set(ledger),
        )
        self.assertEqual(0, ledger["first_seq"])
        self.assertEqual(3, ledger["last_seq"])
        self.assertEqual(4, ledger["n_trials_declared"])
        self.assertEqual(sealed["entries"][-1]["entry_sha256"], ledger["ledger_head_sha256"])

    def test_the_ledger_matches_section_65_and_recomputes(self) -> None:
        sealed = self._seal()
        entries = sealed["entries"]
        self.assertEqual(4, len(entries))
        previous = None
        for seq, entry in enumerate(entries):
            self.assertEqual(_ENTRY_KEYS, set(entry))
            self.assertEqual(seq, entry["seq"])
            self.assertEqual(previous, entry["prev_entry_sha256"])
            self.assertEqual("observation_seal", entry["entry_type"])
            self.assertEqual("pre_contract", entry["role"])
            self.assertEqual("VC-EQ", entry["venue_class"])
            self.assertEqual("workload-d04", entry["venue_cell"])
            self.assertEqual(
                "completed" if entry["trial_id"].startswith("bk-claude-") else "failed",
                entry["status"],
            )
            self.assertEqual(
                {
                    "repository": SYNTHETIC_PIN["repository"],
                    "commit": SYNTHETIC_PIN["commit"],
                    "path": SYNTHETIC_PIN["path"],
                    "result_sha256": SYNTHETIC_PIN["sha256"],
                },
                entry["contract_ref"],
            )
            self.assertEqual(
                {"actor_label": "cockpit_spine.bakeoff", "tool": "cockpit_spine"},
                entry["authored_by"],
            )
            self.assertEqual(_CLOCK, entry["recorded_at_utc"])
            body = {key: entry[key] for key in entry if key != "entry_sha256"}
            self.assertEqual(_bare(body), entry["entry_sha256"])
            self.assertEqual(64, len(entry["entry_sha256"]))
            self.assertNotIn("sha256:", entry["entry_sha256"])
            if previous is not None:
                self.assertNotIn("sha256:", previous)
            previous = entry["entry_sha256"]
        self.assertIsNone(entries[0]["prev_entry_sha256"])
        self.assertEqual(
            ["bk-claude-1", "bk-claude-2", "bk-codex-1", "bk-codex-2"],
            [entry["trial_id"] for entry in entries],
        )
        self.assertEqual("engine_not_permitted_in_mode", sealed["trials"][-1]["reason"])

    def test_entries_validate_against_the_synthetic_section_65_schema(self) -> None:
        sealed = self._seal()
        _validate(sealed["entries"], _LEDGER_ENTRY_SCHEMA)
        broken = json.loads(json.dumps(sealed["entries"][0]))
        broken["contract_ref"] = broken["contract_ref"]["result_sha256"]
        import jsonschema
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(_LEDGER_ENTRY_SCHEMA).validate(broken)
        broken = json.loads(json.dumps(sealed["entries"][0]))
        broken["status"] = "recorded"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(_LEDGER_ENTRY_SCHEMA).validate(broken)
        broken = json.loads(json.dumps(sealed["entries"][0]))
        broken["authored_by"] = "cockpit_spine.bakeoff"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(_LEDGER_ENTRY_SCHEMA).validate(broken)

    def test_a_fenced_schema_extracts_and_validates_the_entries(self) -> None:
        text = "section 6.5\n```json\n" + json.dumps(_LEDGER_ENTRY_SCHEMA) + "\n```\n"
        schemas = ledger_schemas(text)
        self.assertEqual(1, len(schemas))
        _validate(self._seal()["entries"], schemas[0])

    def test_a_different_outcome_changes_the_ledger_head(self) -> None:
        sealed = self._seal()
        changed = json.loads(json.dumps(self.batch))
        changed["trials"][0]["disposition"] = "fail_closed"
        changed["trials"][0]["reason"] = "timeout"
        changed["trials"][0]["output_digest"] = None
        again = self._seal(changed)
        self.assertEqual("aborted", again["entries"][0]["status"])
        self.assertEqual("completed", sealed["entries"][0]["status"])
        self.assertNotEqual(
            sealed["trial_ledger"]["ledger_head_sha256"],
            again["trial_ledger"]["ledger_head_sha256"],
        )

    def test_an_unknown_disposition_cannot_seal(self) -> None:
        changed = json.loads(json.dumps(self.batch))
        changed["trials"][0]["disposition"] = "mystery"
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "status"):
            self._seal(changed)

    def test_the_seal_digest_covers_the_record_except_itself(self) -> None:
        sealed = self._seal()
        material = {key: value for key, value in sealed.items() if key != "seal_digest"}
        self.assertEqual(_bare(material), sealed["seal_digest"])
        self.assertNotIn("sha256:", sealed["seal_digest"])
        again = self._seal()
        self.assertEqual(sealed["seal_digest"], again["seal_digest"])

    def test_the_raw_batch_is_left_unsealed(self) -> None:
        self._seal()
        self.assertEqual((False, None), (self.batch["sealed"], self.batch["protocol_ref"]))

    def test_sealing_does_not_write_a_record(self) -> None:
        self._seal()
        after = sorted(p.relative_to(SYNTHETIC_VAULT) for p in SYNTHETIC_VAULT.rglob("*"))
        self.assertEqual(self.before, after)

    def test_a_digest_mismatch_is_refused(self) -> None:
        forged = dict(SYNTHETIC_PIN)
        forged["sha256"] = "a" * 64
        with mock.patch.object(bakeoff, "PROTOCOL_PIN", forged):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "pinned sha256"):
                bakeoff.seal_result_record(self.batch, vault_root=SYNTHETIC_VAULT)

    def test_a_protocol_that_does_not_name_the_verdict_class_is_refused(self) -> None:
        vault = Path(tempfile.mkdtemp(prefix="cp7-class-"))
        self.addCleanup(shutil.rmtree, vault, True)
        data = b"synthetic protocol with no default class, only inconclusiveness\n"
        pin = _install(vault, "submissions/other-protocol/result.md", data)
        with mock.patch.object(bakeoff, "PROTOCOL_PIN", pin):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "verdict class"):
                bakeoff.seal_result_record(self.batch, vault_root=vault)

    def test_a_non_utf8_protocol_is_refused(self) -> None:
        vault = Path(tempfile.mkdtemp(prefix="cp7-bin-"))
        self.addCleanup(shutil.rmtree, vault, True)
        pin = _install(vault, "submissions/binary-protocol/result.md", b"\xff\xfe")
        with mock.patch.object(bakeoff, "PROTOCOL_PIN", pin):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "utf-8"):
                bakeoff.seal_result_record(self.batch, vault_root=vault)

    def test_record_json_must_describe_the_unaccepted_candidate(self) -> None:
        text = _SYNTHETIC_TEXT.encode("utf-8")
        cases = (
            ("status", {"status": "accepted"}),
            ("sha", {"record_sha": "0" * 64}),
            ("record_id", {"record_id": "someone-else"}),
        )
        for name, kwargs in cases:
            with self.subTest(name):
                vault = Path(tempfile.mkdtemp(prefix="cp7-record-"))
                self.addCleanup(shutil.rmtree, vault, True)
                pin = _install(vault, "submissions/synthetic-cp7/result.md", text, **kwargs)
                with mock.patch.object(bakeoff, "PROTOCOL_PIN", pin):
                    with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "record.json"):
                        bakeoff.seal_result_record(self.batch, vault_root=vault)

    def test_a_malformed_pin_cannot_seal(self) -> None:
        base = dict(SYNTHETIC_PIN)
        cases = {
            "empty": {},
            "extra": {**base, "extra": "x"},
            "short commit": {**base, "commit": "abc"},
            "bad sha": {**base, "sha256": "nope"},
            "absolute": {**base, "path": "/abs/result.md"},
            "dotdot": {**base, "path": "submissions/../result.md"},
            "empty part": {**base, "path": "submissions//result.md"},
            "other repo": {**base, "repository": "other/repo"},
            "bare file": {**base, "path": "result.md"},
            "missing sha": {key: base[key] for key in base if key != "sha256"},
        }
        for name, pin in cases.items():
            with self.subTest(name):
                with mock.patch.object(bakeoff, "PROTOCOL_PIN", pin):
                    with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "protocol"):
                        bakeoff.seal_result_record(self.batch, vault_root=SYNTHETIC_VAULT)

    def test_a_path_with_a_double_dot_name_can_still_seal(self) -> None:
        vault = Path(tempfile.mkdtemp(prefix="cp7-dots-"))
        self.addCleanup(shutil.rmtree, vault, True)
        pin = _install(
            vault, "submissions/a..b/result.md", _SYNTHETIC_TEXT.encode("utf-8"),
        )
        with mock.patch.object(bakeoff, "PROTOCOL_PIN", pin):
            sealed = bakeoff.seal_result_record(self.batch, vault_root=vault)
        self.assertEqual("inconclusive", sealed["verdict_class"])
        self.assertEqual("submissions/a..b/result.md", sealed["preregistration_contract"]["path"])

    def test_a_non_committed_pin_cannot_seal(self) -> None:
        vault = Path(tempfile.mkdtemp(prefix="cp7-forged-"))
        self.addCleanup(shutil.rmtree, vault, True)
        # A complete stand-in, with an all-zero commit, that the old pin argument sealed.
        pin = _install(vault, "submissions/forged/result.md", _SYNTHETIC_TEXT.encode("utf-8"))
        pin = dict(pin)
        pin["commit"] = "0" * 40
        with self.assertRaises(TypeError):
            bakeoff.seal_result_record(self.batch, vault_root=vault, pin=pin)
        with self.assertRaises(TypeError):
            bakeoff.load_protocol_text(vault, pin)
        self._pin.stop()
        try:
            with self.assertRaises(bakeoff.BlockedOnProtocol):
                bakeoff.seal_result_record(self.batch, vault_root=vault)
        finally:
            self._pin.start()

    def test_a_missing_vault_fails_before_a_bad_batch(self) -> None:
        env = os.environ.copy()
        env.pop(projections.VAULT_CHECKOUT_ENV, None)
        self._pin.stop()
        try:
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(bakeoff.BlockedOnProtocol):
                    bakeoff.seal_result_record({})
        finally:
            self._pin.start()

    def test_an_empty_or_non_directory_vault_is_refused(self) -> None:
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "empty"):
            self._seal(vault_root=Path(""))
        file_root = Path(tempfile.mkdtemp(prefix="cp7-file-"))
        self.addCleanup(shutil.rmtree, file_root, True)
        not_dir = file_root / "not-a-checkout"
        not_dir.write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "absent"):
            self._seal(vault_root=not_dir)

    def test_a_symlink_that_leaves_the_vault_is_refused(self) -> None:
        vault = Path(tempfile.mkdtemp(prefix="cp7-link-"))
        outside = Path(tempfile.mkdtemp(prefix="cp7-out-"))
        self.addCleanup(shutil.rmtree, vault, True)
        self.addCleanup(shutil.rmtree, outside, True)
        data = _SYNTHETIC_TEXT.encode("utf-8")
        (outside / "secret.md").write_bytes(data)
        pin = _install(vault, "submissions/synthetic-cp7/result.md", data)
        link = vault / "submissions" / "synthetic-cp7" / "result.md"
        link.unlink()
        link.symlink_to(outside / "secret.md")
        with mock.patch.object(bakeoff, "PROTOCOL_PIN", pin):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "leaves the repository"):
                bakeoff.seal_result_record(self.batch, vault_root=vault)

    def test_a_malformed_batch_is_refused_after_the_protocol_loads(self) -> None:
        for candidate in ({}, {"sealed": True, "protocol_ref": "x"}):
            with self.assertRaises(ValueError):
                self._seal(candidate)

    def test_trials_out_of_order_or_with_extra_keys_are_refused(self) -> None:
        swapped = json.loads(json.dumps(self.batch))
        swapped["trials"][0], swapped["trials"][1] = swapped["trials"][1], swapped["trials"][0]
        with self.assertRaisesRegex(ValueError, "do not follow the plan"):
            self._seal(swapped)
        extra = json.loads(json.dumps(self.batch))
        extra["trials"][0]["extra"] = 1
        with self.assertRaisesRegex(ValueError, "extra"):
            self._seal(extra)

    def test_a_plan_outside_the_bakeoff_is_refused_before_it_is_expanded(self) -> None:
        unknown = json.loads(json.dumps(self.batch))
        unknown["plan"]["engines"] = ["zz"]
        with self.assertRaises(ValueError):
            self._seal(unknown)
        huge = json.loads(json.dumps(self.batch))
        huge["plan"]["trials"] = 10**9
        with self.assertRaises(ValueError):
            self._seal(huge)

    def test_a_powered_claim_cannot_be_sealed(self) -> None:
        with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "NOT_DECIDED"):
            bakeoff.verdict_for(power_established=True)
        sealed_class, reason = bakeoff.verdict_for(power_established=False)
        self.assertEqual("inconclusive", sealed_class)
        self.assertIn("no estimand measured", reason)
        with mock.patch.object(bakeoff, "power_established", return_value=True):
            with self.assertRaisesRegex(bakeoff.BlockedOnProtocol, "NOT_DECIDED"):
                self._seal()


class CommittedBlobTests(unittest.TestCase):
    def test_the_committed_pin_matches_the_blob_at_its_commit(self) -> None:
        raw = os.environ.get(projections.VAULT_CHECKOUT_ENV, "").strip()
        if not raw:
            self.skipTest("CSL_RESEARCH_CHECKOUT is unset")
        root = Path(raw)
        if not root.is_dir() or not (root / ".git").exists():
            self.skipTest("CSL_RESEARCH_CHECKOUT is not a git checkout")
        from cockpit_spine.tests.gitpin import blob_sha256_at

        pin = bakeoff.PROTOCOL_PIN
        self.assertEqual(pin["sha256"], blob_sha256_at(root, pin["commit"], pin["path"]))
        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), StubConsumer)
        sealed = bakeoff.seal_result_record(batch, vault_root=root)
        self.assertEqual("inconclusive", sealed["verdict_class"])
        self.assertFalse(sealed["persisted"])

    def test_entries_validate_against_the_schema_in_the_pinned_file(self) -> None:
        raw = os.environ.get(projections.VAULT_CHECKOUT_ENV, "").strip()
        if not raw:
            self.skipTest("CSL_RESEARCH_CHECKOUT is unset")
        root = Path(raw)
        if not root.is_dir() or not (root / ".git").exists():
            self.skipTest("CSL_RESEARCH_CHECKOUT is not a git checkout")
        pin = bakeoff.PROTOCOL_PIN
        shown = subprocess.run(
            ["git", "show", f"{pin['commit']}:{pin['path']}"],
            cwd=root, capture_output=True, check=False,
        )
        self.assertEqual(0, shown.returncode)
        self.assertEqual(pin["sha256"], hashlib.sha256(shown.stdout).hexdigest())
        schemas = ledger_schemas(shown.stdout.decode("utf-8"))
        self.assertTrue(schemas, "pinned result.md has no section 6.5 entry schema")
        batch = bakeoff.run_trials(bakeoff.Plan(engines=("claude",), trials=1), StubConsumer)
        sealed = bakeoff.seal_result_record(batch, vault_root=root)
        failures = []
        for schema in schemas:
            try:
                _validate(sealed["entries"], schema)
            except Exception as exc:
                failures.append(exc)
            else:
                return
        self.fail(f"no extracted section 6.5 schema accepted the entries: {failures[0]}")


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
            "import json",
            "from datetime import datetime, timezone",
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
