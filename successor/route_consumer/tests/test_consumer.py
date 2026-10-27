"""Consumer behavior with scripted ports. Needs no Recursion checkout."""

from __future__ import annotations

import copy
import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from successor.route_consumer import pin
from successor.route_consumer.consumer import (
    ACCEPTED,
    FAIL_CLOSED,
    NEEDS_HUMAN_REVIEW,
    RouteConsumer,
)
from successor.route_consumer.profile import (
    DECISION_PROFILE,
    FAIL_CLOSED_TO,
    WorkloadProfile,
    load_output_schema,
)
from successor.route_consumer.records import RouteBinding

PROFILE = DECISION_PROFILE["workload-d03"]
SCHEMA = load_output_schema(PROFILE)
GOOD = {
    "proposal_id": "prop-1", "grounded": True, "injection_suspected": False,
    "unsupported_claims": [], "source_refs_checked": ["parquet://x#0"],
}
DIGEST = "sha256:" + "2" * 64


def binding(engine: str = "claude") -> RouteBinding:
    return RouteBinding("route-a", engine, DIGEST, "proc-req-a", "model-x", "tenant-a", "fake", "ref-a")


def support(engine: str = "claude", *, kind: str = "fake") -> dict[str, Any]:
    if kind == "live":
        return {"type": "AdapterSupport@0", "engine_id": engine, "adapter_kind": "live",
                "state": "unsupported", "reason": "awaiting-csl-460", "live_calls": False}
    return {"type": "AdapterSupport@0", "engine_id": engine, "adapter_kind": "fake",
            "state": "supported", "reason": "fake-driver", "live_calls": False}


def result(request_id: str = "req-1", **overrides: Any) -> dict[str, Any]:
    record = {
        "type": "RouteExecutionResult@0", "request_id": request_id, "status": "ok", "error": None,
        "dispatched": True, "repaired": False, "usage_state": "ok", "output_digest": DIGEST,
        "validated": copy.deepcopy(GOOD), "health_event": None, "termination": None,
    }
    record.update(overrides)
    return record


def refusal(code: str, request_id: str = "req-1") -> dict[str, Any]:
    return {"type": "RouteRefusal@0", "request_id": request_id, "code": code, "dispatched": False}


class FixedPort:
    def __init__(self, reply: Any = None, *, sup: Any = None, boom: bool = False,
                 sup_boom: bool = False) -> None:
        self.reply = reply
        self.sup = sup if sup is not None else support()
        self.boom = boom
        self.sup_boom = sup_boom
        self.calls: list[dict[str, Any]] = []

    def support(self) -> Any:
        if self.sup_boom:
            raise RuntimeError("secret detail")
        return self.sup

    def submit(self, request, *, prompt, output_schema):
        self.calls.append({"request": request, "prompt": prompt})
        if self.boom:
            raise RuntimeError("secret detail")
        return self.reply


def decide(port: FixedPort, *, engine: str = "claude", mode: str = "paper", **kwargs: Any):
    return RouteConsumer(port, binding=binding(engine)).decide(
        request_id="req-1", profile=PROFILE, mode=mode, prompt="PROMPT", **kwargs
    )


class DispositionTests(unittest.TestCase):
    def test_ok_is_accepted_after_revalidation(self) -> None:
        outcome = decide(FixedPort(result()))
        self.assertEqual((ACCEPTED, "ok", GOOD, None),
                         (outcome.disposition, outcome.reason, outcome.payload, outcome.fail_closed_to))
        self.assertTrue(outcome.usable)

    def test_payload_is_a_copy(self) -> None:
        reply = result()
        outcome = decide(FixedPort(reply))
        outcome.payload["grounded"] = False
        self.assertTrue(reply["validated"]["grounded"])

    def test_ok_payload_failing_the_consumers_own_schema_is_quarantined(self) -> None:
        bad = dict(GOOD, grounded="yes")
        outcome = decide(FixedPort(result(validated=bad)))
        self.assertEqual((NEEDS_HUMAN_REVIEW, "consumer_revalidation_failed", None),
                         (outcome.disposition, outcome.reason, outcome.payload))

    def test_needs_human_review_is_quarantined_not_failed_closed(self) -> None:
        reply = result(status="needs_human_review", error="repair_still_invalid", validated=None)
        outcome = decide(FixedPort(reply))
        self.assertEqual((NEEDS_HUMAN_REVIEW, "repair_still_invalid", None, None),
                         (outcome.disposition, outcome.reason, outcome.payload, outcome.fail_closed_to))

    def test_limit_and_timeout_and_breaker_fail_closed_to_watch_only(self) -> None:
        event = {"type": "RouteHealthEvent@0", "engine_id": "claude", "state": "exhausted",
                 "error": "limit_or_error"}
        cases = {
            "limit_or_error": result(status="limit_hit", error="limit_or_error", validated=None,
                                     usage_state="exhausted", health_event=event),
            "breaker_open": result(status="limit_hit", error="breaker_open", validated=None,
                                   dispatched=False, usage_state="exhausted"),
        }
        for reason, reply in cases.items():
            outcome = decide(FixedPort(reply))
            with self.subTest(reason):
                self.assertEqual((FAIL_CLOSED, reason, FAIL_CLOSED_TO, None),
                                 (outcome.disposition, outcome.reason, outcome.fail_closed_to,
                                  outcome.payload))

    def test_output_size_reason_codes_fail_closed_to_watch_only(self) -> None:
        # Landed with the master pin: non-ok size failures, same path as other limit hits.
        for reason, repaired in (("output_too_large", False), ("repair_output_too_large", True)):
            event = {"type": "RouteHealthEvent@0", "engine_id": "claude", "state": "exhausted",
                     "error": reason}
            reply = result(status="limit_hit", error=reason, validated=None, repaired=repaired,
                           usage_state="exhausted", health_event=event)
            outcome = decide(FixedPort(reply))
            with self.subTest(reason):
                self.assertEqual((FAIL_CLOSED, reason, FAIL_CLOSED_TO, None),
                                 (outcome.disposition, outcome.reason, outcome.fail_closed_to,
                                  outcome.payload))

    def test_every_refusal_code_fails_closed_and_is_never_retried(self) -> None:
        codes = ("busy", "custody_denied", "cwd_refused", "secret_refused",
                 "tool_policy_refused", "unsupported")
        for code in codes:
            port = FixedPort(refusal(code))
            outcome = decide(port)
            with self.subTest(code):
                self.assertEqual((FAIL_CLOSED, code, FAIL_CLOSED_TO),
                                 (outcome.disposition, outcome.reason, outcome.fail_closed_to))
                self.assertEqual(1, len(port.calls), "no retry, no re-queue")


class ContractDriftTests(unittest.TestCase):
    def test_a_one_byte_schema_drift_fails_closed_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(pin.PACKAGE_DIR / "contract", root / "contract")
            target = root / pin.SCHEMA_KEY
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 1
            target.write_bytes(bytes(data))
            pin.load_schema.cache_clear()
            pin._validator.cache_clear()
            try:
                with mock.patch.object(pin, "PACKAGE_DIR", root):
                    port = FixedPort(result())
                    outcome = decide(port)
            finally:
                pin.load_schema.cache_clear()
                pin._validator.cache_clear()
        self.assertEqual((FAIL_CLOSED, "contract_drift", FAIL_CLOSED_TO, None),
                         (outcome.disposition, outcome.reason, outcome.fail_closed_to,
                          outcome.payload))
        self.assertFalse(outcome.usable)
        self.assertNotIn("sha256", repr(outcome))
        self.assertEqual([], port.calls)
        self.assertEqual(ACCEPTED, decide(FixedPort(result())).disposition)


class InterrogationTests(unittest.TestCase):
    PROFILE = DECISION_PROFILE["workload-d05"]
    FRESH = {
        "question": "why?", "answer": "because", "pattern": "fresh",
        "session_id": None, "generated_at": "2026-10-26T00:00:00Z",
    }

    def _decide(self, payload: dict):
        port = FixedPort(result(validated=payload))
        outcome = RouteConsumer(port, binding=binding()).decide(
            request_id="req-1", profile=self.PROFILE, mode="paper", prompt="PROMPT")
        return port, outcome

    def test_a_fresh_interrogation_is_accepted(self) -> None:
        _port, outcome = self._decide(self.FRESH)
        self.assertEqual((ACCEPTED, "ok", self.FRESH),
                         (outcome.disposition, outcome.reason, outcome.payload))
        self.assertTrue(outcome.usable)

    def test_a_resume_interrogation_fails_closed(self) -> None:
        resumed = dict(self.FRESH, pattern="resume", session_id="previous-session")
        port, outcome = self._decide(resumed)
        self.assertEqual((FAIL_CLOSED, "interrogation_not_fresh", FAIL_CLOSED_TO, None),
                         (outcome.disposition, outcome.reason, outcome.fail_closed_to,
                          outcome.payload))
        self.assertFalse(outcome.usable)
        self.assertEqual(1, len(port.calls))


class MalformedReplyTests(unittest.TestCase):
    def test_invalid_or_mismatched_replies_fail_closed(self) -> None:
        cases = {
            "not a dict": "ok",
            "none": None,
            "unknown type": {"type": "Other@0"},
            "extra field": result(prompt="leak"),
            "ok without payload": result(validated=None),
            "ok with error": result(error="timeout"),
            "wrong request id": result(request_id="req-other"),
            "refusal wrong id": refusal("busy", "req-other"),
            "unknown refusal code": refusal("please-retry"),
            "timeout without tree": result(status="timeout", error="timeout", validated=None,
                                           usage_state="exhausted"),
        }
        for label, reply in cases.items():
            outcome = decide(FixedPort(reply))
            with self.subTest(label):
                self.assertEqual((FAIL_CLOSED, "invalid_route_reply", None),
                                 (outcome.disposition, outcome.reason, outcome.payload))

    def test_a_raising_port_fails_closed_without_leaking_its_message(self) -> None:
        outcome = decide(FixedPort(boom=True))
        self.assertEqual((FAIL_CLOSED, "route_port_error"), (outcome.disposition, outcome.reason))
        self.assertNotIn("secret detail", repr(outcome))
        outcome = decide(FixedPort(sup_boom=True))
        self.assertEqual("route_port_error", outcome.reason)
        self.assertNotIn("secret detail", repr(outcome))


class SupportGateTests(unittest.TestCase):
    def test_unsupported_live_adapter_is_refused_before_submit(self) -> None:
        port = FixedPort(result(), sup=support(kind="live"))
        outcome = decide(port)
        self.assertEqual((FAIL_CLOSED, "unsupported"), (outcome.disposition, outcome.reason))
        self.assertEqual([], port.calls)

    def test_malformed_or_mismatched_support_is_refused_before_submit(self) -> None:
        bad = {
            "wrong engine": support("codex"),
            "live_calls true": dict(support(), live_calls=True),
            "supported live": dict(support(kind="live"), state="supported"),
            "not a dict": "supported",
        }
        for label, sup in bad.items():
            port = FixedPort(result(), sup=sup)
            outcome = decide(port)
            with self.subTest(label):
                self.assertEqual(FAIL_CLOSED, outcome.disposition)
                self.assertIn(outcome.reason, ("invalid_route_reply", "unsupported"))
                self.assertEqual([], port.calls)


class ProfileGateTests(unittest.TestCase):
    def test_codex_outside_its_boundary_never_contacts_the_route(self) -> None:
        for mode in ("review_live", "auto_veto", "auto_full"):
            port = FixedPort(result(), sup=support("codex"))
            outcome = decide(port, engine="codex", mode=mode)
            with self.subTest(mode):
                self.assertEqual((FAIL_CLOSED, "engine_not_permitted_in_mode"),
                                 (outcome.disposition, outcome.reason))
                self.assertEqual([], port.calls)

    def test_codex_in_read_only_and_paper_is_allowed(self) -> None:
        for mode in ("read_only", "paper"):
            port = FixedPort(result(), sup=support("codex"))
            self.assertEqual(ACCEPTED, decide(port, engine="codex", mode=mode).disposition, mode)

    def test_unknown_mode_fails_closed(self) -> None:
        port = FixedPort(result())
        outcome = decide(port, mode="live")
        self.assertEqual((FAIL_CLOSED, "unknown_mode"), (outcome.disposition, outcome.reason))
        self.assertEqual([], port.calls)

    def test_engine_unknown_to_the_profile_fails_closed(self) -> None:
        port = FixedPort(result(), sup=support("grok"))
        outcome = decide(port, engine="grok", mode="read_only")
        self.assertEqual((FAIL_CLOSED, "engine_not_permitted_in_mode"),
                         (outcome.disposition, outcome.reason))
        self.assertEqual([], port.calls)

    def test_supplied_schema_must_match_the_profile_digest(self) -> None:
        port = FixedPort(result())
        bent = copy.deepcopy(SCHEMA)
        bent["required"] = []
        outcome = decide(port, output_schema=bent)
        self.assertEqual((FAIL_CLOSED, "output_schema_digest_mismatch"),
                         (outcome.disposition, outcome.reason))
        self.assertEqual([], port.calls)
        self.assertEqual(ACCEPTED, decide(FixedPort(result()), output_schema=SCHEMA).disposition)

    def test_unreadable_vendored_schema_fails_closed(self) -> None:
        ghost = WorkloadProfile("workload-d99", "x", "NoSuchSchema", "sha256:" + "0" * 64)
        port = FixedPort(result())
        outcome = RouteConsumer(port, binding=binding()).decide(
            request_id="req-1", profile=ghost, mode="paper", prompt="PROMPT")
        self.assertEqual((FAIL_CLOSED, "unknown_workload"),
                         (outcome.disposition, outcome.reason))
        self.assertEqual([], port.calls)

    def test_a_caller_built_profile_fails_closed_before_the_route(self) -> None:
        # Same id as a decision workload, but not the registered profile.
        custom = dataclasses.replace(PROFILE, cockpit_job="caller-built")
        port = FixedPort(result(), sup=support("codex"))
        outcome = RouteConsumer(port, binding=binding("codex")).decide(
            request_id="req-1", profile=custom, mode="paper", prompt="PROMPT")
        self.assertEqual((FAIL_CLOSED, "unknown_workload", FAIL_CLOSED_TO, None),
                         (outcome.disposition, outcome.reason, outcome.fail_closed_to,
                          outcome.payload))
        self.assertEqual([], port.calls)
        self.assertFalse(outcome.usable)

    def test_the_consumer_sends_the_request_and_prompt_once_and_only_to_its_port(self) -> None:
        port = FixedPort(result())
        decide(port)
        self.assertEqual(1, len(port.calls))
        self.assertEqual("PROMPT", port.calls[0]["prompt"])
        self.assertNotIn("PROMPT", repr(port.calls[0]["request"]))

    def test_port_cannot_mutate_what_the_consumer_keeps(self) -> None:
        class Mutating(FixedPort):
            def submit(self, request, *, prompt, output_schema):
                request["allowed_tools"].append("Bash")
                output_schema["required"] = []
                return super().submit(request, prompt=prompt, output_schema=output_schema)

        outcome = RouteConsumer(Mutating(result()), binding=binding()).decide(
            request_id="req-1", profile=PROFILE, mode="paper", prompt="PROMPT")
        self.assertEqual([], outcome.request_record["allowed_tools"])
        self.assertEqual(ACCEPTED, outcome.disposition)


if __name__ == "__main__":
    unittest.main(verbosity=2)
