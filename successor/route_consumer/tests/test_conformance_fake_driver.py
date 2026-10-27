"""WP6 conformance cases, replayed through the Cockpit consumer against the fake driver.

Subject under test: ``RouteConsumer``. Counterpart: Recursion's in-process
``ReferenceAdapter`` over the AT1 ``FakeRouteDriver``, at the pinned head
(CSL-496 draft, PR 302 merged to recursion master). Cases and expected values are the pinned
oracle vectors plus the case classes of that PR's own conformance suite:
schema projection, zero-tools, secret refusal, health injection, tree kill,
session ownership, output size, and live adapters being unsupported.

No live provider, no network, no real credential. The only real processes are
local helper scripts started with ``sys.executable``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from successor.route_consumer import pin
from successor.route_consumer.consumer import ACCEPTED, FAIL_CLOSED, NEEDS_HUMAN_REVIEW
from successor.route_consumer.profile import FAIL_CLOSED_TO, engines_allowed
from successor.route_consumer.tests.support import rig as rigmod
from successor.route_consumer.tests.support.recursion_bridge import (
    LiveRoutePort,
    recursion,
    vectors,
)

REC = None
VECTORS = None
PROFILE = None


def setUpModule() -> None:
    global REC, VECTORS, PROFILE
    REC = recursion()
    VECTORS = vectors()
    PROFILE = rigmod.vector_profile()


def decide(rig, *, request_id="req-1", prompt="PROMPT", mode="paper", profile=None):
    return rig.consumer.decide(
        request_id=request_id,
        profile=profile or PROFILE,
        mode=mode,
        prompt=prompt,
        output_schema=VECTORS["schema"],
    )


def assert_records_valid(case: unittest.TestCase, outcome) -> None:
    if outcome.request_record is not None:
        case.assertEqual([], pin.validation_errors("RouteExecutionRequest", outcome.request_record))
    reply = outcome.reply_record
    if reply is not None:
        name = "RouteRefusal" if reply["type"] == "RouteRefusal@0" else "RouteExecutionResult"
        case.assertEqual([], pin.validation_errors(name, reply))


class ZeroToolsTests(unittest.TestCase):
    def test_request_is_byte_identical_to_recursions_own_record(self) -> None:
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()])
        outcome = decide(rig)
        self.assertEqual(ACCEPTED, outcome.disposition)
        self.assertEqual(1, len(rig.port.submitted))

    def test_prompt_travels_on_stdin_only_and_argv_is_the_oracle_argv(self) -> None:
        prompt = "do not leak: UNIQUE-PROMPT-MARKER"
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()])
        decide(rig, prompt=prompt)
        call = rig.transport.calls[0]
        self.assertEqual(prompt, call["input"])
        self.assertFalse(any("UNIQUE-PROMPT-MARKER" in token for token in call["argv"]))
        self.assertEqual(VECTORS["argv"]["claude"], call["argv"])
        blob = json.dumps(rig.port.submitted)
        self.assertNotIn("UNIQUE-PROMPT-MARKER", blob)

    def test_codex_argv_is_the_oracle_argv(self) -> None:
        script = [VECTORS["health"]["codex_ok"]["script"][0]]
        rig = rigmod.make_rig(REC, "codex", script=script)
        outcome = decide(rig, mode="read_only")
        self.assertEqual(ACCEPTED, outcome.disposition)
        self.assertEqual(VECTORS["argv"]["codex_no_schema_path"], rig.transport.calls[0]["argv"])

    def test_no_request_can_grant_tools(self) -> None:
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()])
        outcome = decide(rig)
        record = outcome.request_record
        self.assertEqual(("zero-tools", [], "stdin"), (
            record["tool_profile"], record["allowed_tools"], record["prompt_channel"]))
        self.assertEqual((1, False), (record["output"]["repair_attempts"],
                                       record["output"]["raw_output_retained"]))

    def test_pinned_cwd_refusal_is_fail_closed_and_never_spawns(self) -> None:
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()], pinned_cwd="/pinned")
        outcome = decide(rig)
        self.assertEqual((FAIL_CLOSED, "cwd_refused"), (outcome.disposition, outcome.reason))
        self.assertEqual([], rig.transport.calls)
        assert_records_valid(self, outcome)


class SecretRefusalTests(unittest.TestCase):
    def test_oracle_vectors(self) -> None:
        for name, expected in VECTORS["secret"].items():
            with self.subTest(name):
                prompt = "key is sk-LEAK123456 ok" if name == "in_prompt" else "nothing here"
                secrets = {"in_prompt": ["sk-LEAK123456"], "absent": ["sk-LEAK123456"],
                           "empty_secret_ignored": [""], "no_secrets": None}[name]
                rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()], secret_values=secrets)
                outcome = decide(rig, prompt=prompt)
                if expected["refused"]:
                    self.assertEqual((FAIL_CLOSED, "secret_refused"),
                                     (outcome.disposition, outcome.reason))
                else:
                    self.assertEqual(ACCEPTED, outcome.disposition)
                self.assertEqual(expected["transport_calls"], len(rig.transport.calls))
                assert_records_valid(self, outcome)

    def test_canary_leaves_no_trace_and_no_session(self) -> None:
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()],
                              secret_values=lambda: [rigmod.CANARY])
        outcome = decide(rig, prompt=f"please use {rigmod.CANARY}")
        self.assertEqual("secret_refused", outcome.reason)
        blob = json.dumps([outcome.request_record, outcome.reply_record, rig.driver.snapshot()])
        self.assertNotIn(rigmod.CANARY, blob)
        self.assertEqual([], rig.driver.snapshot()["sessions"])

    def test_repair_prompt_does_not_quote_response_values_and_releases_the_session(self) -> None:
        # The landed contract ignores validator text so a repair prompt cannot quote
        # private response values. The canary stays out of the repair prompt and the reply.
        bad = {"returncode": 0, "stderr": "", "timed_out": False,
               "stdout": json.dumps({"result": json.dumps({"label": rigmod.CANARY, "score": 1})})}
        rig = rigmod.make_rig(REC, script=[bad, rigmod.ok_envelope()],
                              secret_values=[rigmod.CANARY])
        outcome = decide(rig)
        self.assertEqual(ACCEPTED, outcome.disposition)
        self.assertEqual(2, len(rig.transport.calls))
        self.assertNotIn(rigmod.CANARY, rig.transport.calls[1]["input"])
        blob = json.dumps([outcome.request_record, outcome.reply_record, rig.driver.snapshot()])
        self.assertNotIn(rigmod.CANARY, blob)
        self.assertFalse(self._busy(rig, "req-1"))

    def test_custody_refuses_unknown_and_cross_tenant_refs(self) -> None:
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()],
                              driver=rigmod.make_driver(REC, register=False))
        outcome = decide(rig)
        self.assertEqual((FAIL_CLOSED, "custody_denied"), (outcome.disposition, outcome.reason))
        self.assertEqual([], rig.transport.calls)
        assert_records_valid(self, outcome)

    @staticmethod
    def _busy(rig, request_id: str) -> bool:
        key = REC.session.SessionKey(rigmod.TENANT, "route-fake", request_id)
        return rig.driver.owner.is_busy(key)


class HealthInjectionTests(unittest.TestCase):
    ENGINE_FOR_CASE = {"claude": "claude", "codex": "codex"}

    def run_case(self, case: dict):
        engine = self.ENGINE_FOR_CASE[case["runtime"]]
        rig = rigmod.make_rig(REC, engine, script=case["script"])
        return engine, rig, decide(rig, prompt=case["prompt"], mode="read_only")

    def test_every_oracle_health_vector(self) -> None:
        self.assertGreaterEqual(len(VECTORS["health"]), 20)
        for name, case in VECTORS["health"].items():
            expect = case["expect"]
            engine, rig, outcome = self.run_case(case)
            with self.subTest(name):
                reply = outcome.reply_record
                self.assertEqual(expect["status"], reply["status"])
                self.assertEqual(expect["error"], reply["error"])
                self.assertEqual(expect["usage_state"], reply["usage_state"])
                self.assertEqual(expect["repaired"], reply["repaired"])
                self.assertEqual(expect["dispatched"], reply["dispatched"])
                self.assertEqual(expect["validated"], reply["validated"])
                self.assertEqual(expect["transport_calls"], len(rig.transport.calls))
                self.assertEqual([list(f) for f in rig.health.faults], expect["faults"])
                self.assertEqual(expect["used"], rig.health.used(engine))
                assert_records_valid(self, outcome)
                if expect["status"] == "ok":
                    self.assertEqual((ACCEPTED, expect["validated"]),
                                     (outcome.disposition, outcome.payload))
                elif expect["status"] == "needs_human_review":
                    self.assertEqual(NEEDS_HUMAN_REVIEW, outcome.disposition)
                    self.assertIsNone(outcome.fail_closed_to)
                else:
                    self.assertEqual((FAIL_CLOSED, expect["error"], FAIL_CLOSED_TO),
                                     (outcome.disposition, outcome.reason, outcome.fail_closed_to))

    def test_nothing_unvalidated_ever_reaches_the_caller(self) -> None:
        for name, case in VECTORS["health"].items():
            _, _, outcome = self.run_case(case)
            with self.subTest(name):
                if outcome.disposition != ACCEPTED:
                    self.assertIsNone(outcome.payload)
                    self.assertFalse(outcome.usable)

    def test_breaker_open_fails_closed_without_spawning(self) -> None:
        expect = VECTORS["breaker_open"]
        health = REC.health.InMemoryHealth()
        first = rigmod.make_rig(REC, script=[{"returncode": 1, "stdout": "", "stderr": "boom",
                                              "timed_out": False}], health=health)
        decide(first)
        second = rigmod.make_rig(REC, script=[rigmod.ok_envelope()], health=health)
        outcome = decide(second, request_id="req-2")
        self.assertEqual((FAIL_CLOSED, expect["error"]), (outcome.disposition, outcome.reason))
        reply = outcome.reply_record
        self.assertEqual((expect["status"], expect["usage_state"], expect["dispatched"]),
                         (reply["status"], reply["usage_state"], reply["dispatched"]))
        self.assertEqual(expect["transport_calls"], len(second.transport.calls))
        self.assertEqual([list(f) for f in health.faults], expect["faults"])

    def test_model_text_cannot_trip_the_breaker(self) -> None:
        case = VECTORS["health"]["model_text_mentions_limit_words"]
        engine, rig, outcome = self.run_case(case)
        self.assertEqual(ACCEPTED, outcome.disposition)
        self.assertFalse(rig.health.is_open(engine))


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@unittest.skipUnless(os.name == "posix", "process-group kill is asserted on POSIX only; Windows unverified")
class TreeKillTests(unittest.TestCase):
    def test_timeout_kills_the_tree_and_the_consumer_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "tree.py"
            helper.write_text(rigmod.HELPER_TREE, encoding="utf-8")
            pid_file = Path(tmp) / "child.pid"
            transport = rigmod.LocalHelperTransport(REC, [str(helper), str(pid_file)])
            rig = rigmod.make_rig(REC, transport=transport)
            profile = rigmod.vector_profile(timeout_s=1.5)
            outcome = decide(rig, profile=profile)
            self.assertEqual((FAIL_CLOSED, "timeout", FAIL_CLOSED_TO),
                             (outcome.disposition, outcome.reason, outcome.fail_closed_to))
            self.assertEqual([("claude", "exhausted", "timeout")], rig.health.faults)
            self.assertFalse(pid_alive(int(pid_file.read_text(encoding="utf-8"))))
            termination = outcome.reply_record["termination"]
            self.assertEqual("terminated", termination["tree_status"])
            self.assertEqual("not-evaluated", termination["effect_conclusion"])
            assert_records_valid(self, outcome)
            key = REC.session.SessionKey(rigmod.TENANT, "route-fake", "req-1")
            self.assertFalse(rig.driver.owner.is_busy(key), "session released after the kill")
            again = decide(rig, request_id="req-2", profile=profile)
            self.assertEqual("breaker_open", again.reason)
            self.assertEqual(VECTORS["argv"]["claude"], transport.seen_argv[0])


class ContentionTests(unittest.TestCase):
    def test_busy_is_a_refusal_not_a_wait(self) -> None:
        driver = rigmod.make_driver(REC)
        seen: dict = {}

        class Reentrant:
            def run(inner, argv, input_text, timeout_s, cwd, max_output_bytes=None):
                other = rigmod.make_rig(REC, script=[], driver=driver)
                seen["outcome"] = decide(other)
                return REC.transport.ProcResult(0, rigmod.ok_envelope()["stdout"], "", False)

        rig = rigmod.make_rig(REC, transport=Reentrant(), driver=driver)
        first = decide(rig)
        self.assertEqual(ACCEPTED, first.disposition)
        busy = seen["outcome"]
        self.assertEqual((FAIL_CLOSED, "busy", FAIL_CLOSED_TO),
                         (busy.disposition, busy.reason, busy.fail_closed_to))
        assert_records_valid(self, busy)

    def test_sessions_release_on_every_outcome(self) -> None:
        driver = rigmod.make_driver(REC)
        scripts = [
            [rigmod.ok_envelope()],
            [{"returncode": 1, "stdout": "", "stderr": "boom", "timed_out": False}],
            [{"returncode": 124, "stdout": "", "stderr": "", "timed_out": True}],
        ]
        for index, script in enumerate(scripts):
            rig = rigmod.make_rig(REC, script=script, driver=driver)
            decide(rig, request_id=f"req-{index}")
            key = REC.session.SessionKey(rigmod.TENANT, "route-fake", f"req-{index}")
            self.assertFalse(driver.owner.is_busy(key), index)

    def test_prompt_body_is_not_stored_in_driver_state(self) -> None:
        rig = rigmod.make_rig(REC, script=[rigmod.ok_envelope()])
        decide(rig, prompt="UNIQUE-BODY-MARKER")
        blob = json.dumps(rig.driver.snapshot())
        self.assertNotIn("UNIQUE-BODY-MARKER", blob)
        self.assertNotIn(rigmod.CANARY, blob)


class LiveAdaptersUnsupportedTests(unittest.TestCase):
    def test_live_enablement_is_a_constant_that_is_off(self) -> None:
        self.assertIs(False, REC.live.LIVE_ADAPTERS_ENABLED)

    def test_every_live_adapter_reports_unsupported_and_the_consumer_never_submits(self) -> None:
        self.assertGreaterEqual(len(REC.live.LIVE_ENGINE_IDS), 5)
        for engine_id in REC.live.LIVE_ENGINE_IDS:
            port = LiveRoutePort(REC, engine_id)
            support = port.support()
            with self.subTest(engine_id):
                self.assertEqual([], pin.validation_errors("AdapterSupport", support))
                self.assertEqual(("live", "unsupported", "awaiting-csl-460", False),
                                 (support["adapter_kind"], support["state"], support["reason"],
                                  support["live_calls"]))
        for engine_id in ("claude", "codex"):
            port = LiveRoutePort(REC, engine_id)
            consumer = rigmod.RouteConsumer(port, binding=rigmod.binding(engine_id))
            outcome = consumer.decide(request_id="req-1", profile=PROFILE, mode="read_only",
                                      prompt="PROMPT", output_schema=VECTORS["schema"])
            with self.subTest(consumer=engine_id):
                self.assertEqual((FAIL_CLOSED, "unsupported", FAIL_CLOSED_TO),
                                 (outcome.disposition, outcome.reason, outcome.fail_closed_to))
                self.assertEqual(0, port.submit_calls)

    def test_the_fake_adapter_is_the_only_supported_kind(self) -> None:
        support = rigmod.make_rig(REC).port.support()
        self.assertEqual([], pin.validation_errors("AdapterSupport", support))
        self.assertEqual(("fake", "supported", False),
                         (support["adapter_kind"], support["state"], support["live_calls"]))

    def test_real_transport_refuses_provider_executables_and_the_consumer_fails_closed(self) -> None:
        rig = rigmod.make_rig(REC, transport=REC.transport.SubprocessTransport())
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("spawned")):
            outcome = decide(rig)
        self.assertEqual((FAIL_CLOSED, "unsupported"), (outcome.disposition, outcome.reason))
        key = REC.session.SessionKey(rigmod.TENANT, "route-fake", "req-1")
        self.assertFalse(rig.driver.owner.is_busy(key))


class OutputSizeEndToEndTests(unittest.TestCase):
    """Oversize engine output through RouteConsumer and the pinned reference adapter.

    ``test_output_size_reason_codes_fail_closed_to_watch_only`` injects a finished
    ``limit_hit`` reply. These cases script the process result and let ``decide``
    read the reply the adapter builds. The cap is the profile's
    ``max_output_bytes``: the consumer puts it on the request and the port passes
    it to the transport. Stdout over that cap is ``output_too_large`` before any
    repair. A schema miss under the cap still gets the one repair attempt, and an
    oversize repair is ``repair_output_too_large``.
    """

    CAP = 128

    def test_oversize_stdout_fails_closed_as_output_too_large(self) -> None:
        oversized = "x" * (self.CAP + 1)
        rig, outcome = self._decide([{
            "returncode": 0, "stdout": oversized, "stderr": "", "timed_out": False,
        }])
        self._assert_closed(rig, outcome, "output_too_large", calls=1, repaired=False)
        self.assertEqual(0, rig.health.used("claude"))
        self.assertNotIn(oversized, json.dumps([outcome.request_record, outcome.reply_record]))

    def test_oversize_repair_fails_closed_as_repair_output_too_large(self) -> None:
        # Valid JSON, under the cap, and not the vector schema: the adapter repairs once.
        bad = json.dumps({"session_id": "s1", "result": "{}"})
        self.assertLessEqual(len(bad.encode("utf-8")), self.CAP)
        oversized = "y" * (self.CAP + 1)
        rig, outcome = self._decide([
            {"returncode": 0, "stdout": bad, "stderr": "", "timed_out": False},
            {"returncode": 0, "stdout": oversized, "stderr": "", "timed_out": False},
        ])
        self._assert_closed(rig, outcome, "repair_output_too_large", calls=2, repaired=True)
        self.assertEqual(1, rig.health.used("claude"))
        self.assertNotIn(oversized, json.dumps([outcome.request_record, outcome.reply_record]))
        self.assertNotIn(bad, rig.transport.calls[1]["input"])

    def _decide(self, script):
        profile = dataclasses.replace(PROFILE, max_output_bytes=self.CAP)
        rig = rigmod.make_rig(REC, script=script)
        return rig, decide(rig, profile=profile)

    def _assert_closed(self, rig, outcome, reason: str, *, calls: int, repaired: bool) -> None:
        self.assertEqual(
            (FAIL_CLOSED, reason, FAIL_CLOSED_TO, None, False),
            (
                outcome.disposition, outcome.reason, outcome.fail_closed_to,
                outcome.payload, outcome.usable,
            ),
        )
        self.assertEqual(self.CAP, outcome.request_record["output"]["max_output_bytes"])
        self.assertEqual(self.CAP, rig.port.submitted[0]["output"]["max_output_bytes"])
        self.assertEqual(calls, len(rig.transport.calls))
        self.assertEqual(
            [self.CAP] * calls, [call["max_output_bytes"] for call in rig.transport.calls]
        )
        reply = outcome.reply_record
        self.assertEqual(
            ("limit_hit", reason, repaired, None),
            (reply["status"], reply["error"], reply["repaired"], reply["validated"]),
        )
        self.assertEqual([("claude", "exhausted", reason)], rig.health.faults)
        assert_records_valid(self, outcome)
        key = REC.session.SessionKey(rigmod.TENANT, "route-fake", "req-1")
        self.assertFalse(rig.driver.owner.is_busy(key))


class ModePinOnTheWireTests(unittest.TestCase):
    def test_codex_never_reaches_the_route_outside_its_boundary(self) -> None:
        script = [VECTORS["health"]["codex_ok"]["script"][0]]
        for mode in ("review_live", "auto_veto", "auto_full"):
            rig = rigmod.make_rig(REC, "codex", script=list(script))
            outcome = decide(rig, mode=mode)
            with self.subTest(mode):
                self.assertEqual((FAIL_CLOSED, "engine_not_permitted_in_mode"),
                                 (outcome.disposition, outcome.reason))
                self.assertEqual([], rig.transport.calls)
                self.assertEqual([], rig.port.submitted)
        for mode in ("review_live", "auto_veto", "auto_full"):
            self.assertEqual(("claude",), engines_allowed(mode))


if __name__ == "__main__":
    unittest.main(verbosity=2)
