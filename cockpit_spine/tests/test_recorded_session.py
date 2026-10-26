"""The committed Linux operator session record.

Whole-record checks run anywhere. Regeneration needs the read-only Recursion
checkout (``RECURSION_ROUTE_CONTRACT_CHECKOUT``) and fails closed without it,
exactly like WP12's conformance tests; ``ROUTE_CONSUMER_ALLOW_NO_RECURSION=1``
skips them and proves nothing.
"""

from __future__ import annotations

import copy
import json
import unittest

from cockpit_spine import projections as proj
from cockpit_spine import recorded
from cockpit_spine.tests.support import record_session
from successor.route_consumer.tests.support.recursion_bridge import require_checkout

REGISTRY = proj.load_registry()


def committed() -> dict:
    return json.loads(record_session.RECORD_PATH.read_text(encoding="utf-8"))


class CommittedRecordTests(unittest.TestCase):
    def test_the_record_is_whole(self) -> None:
        self.assertEqual([], recorded.problems(committed(), REGISTRY))

    def test_the_record_shows_approve_veto_stop_and_the_refusals(self) -> None:
        record = committed()
        codes = [(s["argv"][0], s["response"]["code"]) for s in record["steps"]]
        for expected in (("approve", "approved"), ("veto", "vetoed"), ("stop", "stopped"),
                         ("approve", "digest_mismatch"), ("propose", "timeout"),
                         ("propose", "session_stopped"), ("approve", "session_stopped")):
            self.assertIn(expected, codes)
        self.assertEqual("recursion-fake-driver", record["adapter_kind"])
        self.assertFalse(record["live_calls"])

    def test_no_secret_prompt_or_verdict_text_is_in_the_record(self) -> None:
        blob = record_session.RECORD_PATH.read_text(encoding="utf-8").lower()
        for word in ("canary", "secret", "api_key", "password", "sk-"):
            self.assertNotIn(word, blob)
        self.assertNotIn("verdict:", blob)

    def test_every_kind_of_damage_is_reported(self) -> None:
        base = committed()

        def edited(mutate) -> dict:
            record = copy.deepcopy(base)
            mutate(record)
            return record

        cases = {
            "chain edit": lambda r: r["chain"][2]["data"].update(at="x"),
            "wrong head": lambda r: r.update(head="sha256:" + "1" * 64),
            "live": lambda r: r.update(live_calls=True),
            "platform": lambda r: r.update(platform="windows"),
            "registry": lambda r: r.update(projection_registry_digest="sha256:" + "2" * 64),
            "unlinked number": lambda r: r["steps"][0]["response"]["lines"].append(
                {"kind": "figure", "label": "x", "value": "99 cells"}),
            "no stop": lambda r: r.update(steps=[
                s for s in r["steps"] if s["response"]["code"] != "stopped"]),
            "exit code": lambda r: r["steps"][1].update(exit_code=2),
            "verdict claim": lambda r: r.update(kind="verdict"),
            "verdict hidden in the kind": lambda r: r.update(
                kind="no verdict; verdict: model A wins"),
            "label is not exact": lambda r: r.update(label="deferred, and more"),
            "steps reversed": lambda r: r["steps"].reverse(),
            "every head at the first record": lambda r: [
                s["response"].update(head=r["chain"][0]["hash"]) for s in r["steps"]],
            "digest_mismatch flipped to ok": lambda r: [
                (s["response"].update(ok=True), s.update(exit_code=0))
                for s in r["steps"] if s["response"]["code"] == "digest_mismatch"],
            "steps null": lambda r: r.update(steps=None),
            "response null": lambda r: r["steps"][3].update(response=None),
        }
        for name, mutate in cases.items():
            self.assertTrue(recorded.problems(edited(mutate), REGISTRY), name)


class StepsAreBoundToTheChainTests(unittest.TestCase):
    """Samir's mutations: the transcript half must not be freely editable."""

    def damaged(self, mutate) -> list[str]:
        record = copy.deepcopy(committed())
        mutate(record)
        return recorded.problems(record, REGISTRY)

    def step(self, record: dict, argv0: str, code: str) -> dict:
        return next(s for s in record["steps"]
                    if s["argv"][0] == argv0 and s["response"]["code"] == code)

    def test_reversing_the_steps_is_reported(self) -> None:
        self.assertTrue(self.damaged(lambda r: r["steps"].reverse()))

    def test_swapping_two_steps_is_reported(self) -> None:
        def swap(r: dict) -> None:
            r["steps"][1], r["steps"][2] = r["steps"][2], r["steps"][1]

        self.assertTrue(self.damaged(swap))

    def test_pointing_every_head_at_the_first_chain_record_is_reported(self) -> None:
        def point(r: dict) -> None:
            for step in r["steps"]:
                step["response"]["head"] = r["chain"][0]["hash"]

        self.assertTrue(self.damaged(point))

    def test_flipping_the_digest_mismatch_refusal_to_success_is_reported(self) -> None:
        def flip(r: dict) -> None:
            step = self.step(r, "approve", "digest_mismatch")
            step["response"]["ok"] = True
            step["exit_code"] = 0

        found = self.damaged(flip)
        self.assertTrue(any("digest_mismatch" in m or "claims ok" in m for m in found), found)

    def test_changing_only_a_code_or_only_an_ok_is_reported(self) -> None:
        self.assertTrue(self.damaged(
            lambda r: self.step(r, "approve", "approved")["response"].update(code="vetoed")))
        self.assertTrue(self.damaged(
            lambda r: self.step(r, "propose", "timeout")["response"].update(code="other")))
        self.assertTrue(self.damaged(
            lambda r: self.step(r, "veto", "vetoed")["response"].update(ok=False)))

    def test_changing_an_argument_the_chain_recorded_is_reported(self) -> None:
        def operator(r: dict) -> None:
            step = self.step(r, "veto", "vetoed")
            step["argv"][step["argv"].index("--operator") + 1] = "someone-else"

        def reason(r: dict) -> None:
            step = self.step(r, "stop", "stopped")
            step["argv"][step["argv"].index("--reason") + 1] = "different"

        def request(r: dict) -> None:
            step = self.step(r, "propose", "timeout")
            step["argv"][step["argv"].index("--request-id") + 1] = "r9"

        for mutate in (operator, reason, request):
            self.assertTrue(self.damaged(mutate))

    def test_a_step_that_claims_the_wrong_command_is_reported(self) -> None:
        self.assertTrue(self.damaged(
            lambda r: self.step(r, "veto", "vetoed")["argv"].__setitem__(0, "approve")))

    def test_dropping_or_duplicating_a_step_is_reported(self) -> None:
        self.assertTrue(self.damaged(lambda r: r["steps"].pop(4)))
        self.assertTrue(self.damaged(lambda r: r["steps"].insert(5, copy.deepcopy(r["steps"][4]))))
        self.assertTrue(self.damaged(lambda r: r["steps"].pop(11 - 2)))

    def test_a_read_step_that_claims_to_have_written_is_reported(self) -> None:
        def write(r: dict) -> None:
            audit = self.step(r, "audit", "audit")
            audit["response"]["head"] = r["chain"][3]["hash"]

        self.assertTrue(self.damaged(write))

    def test_a_chain_head_line_that_differs_from_the_response_head_is_reported(self) -> None:
        def line(r: dict) -> None:
            status = r["steps"][0]["response"]
            for item in status["lines"]:
                if item["label"] == "chain head":
                    item["value"] = "sha256:" + "3" * 64

        self.assertTrue(self.damaged(line))

    def test_malformed_input_is_a_problem_not_an_exception(self) -> None:
        for name, mutate in {
            "steps null": lambda r: r.update(steps=None),
            "steps empty": lambda r: r.update(steps=[]),
            "steps a string": lambda r: r.update(steps="x"),
            "step null": lambda r: r["steps"].__setitem__(2, None),
            "response null": lambda r: r["steps"][2].update(response=None),
            "response a list": lambda r: r["steps"][2].update(response=[]),
            "response empty": lambda r: r["steps"][2].update(response={}),
            "ok missing": lambda r: r["steps"][2]["response"].pop("ok"),
            "head a list": lambda r: r["steps"][2]["response"].update(head=["x"]),
            "lines null": lambda r: r["steps"][2]["response"].update(lines=None),
            "argv empty": lambda r: r["steps"][2].update(argv=[]),
            "argv ints": lambda r: r["steps"][2].update(argv=[1, 2]),
            "chain null": lambda r: r.update(chain=None),
            "chain empty": lambda r: r.update(chain=[]),
            "chain of nulls": lambda r: r.update(chain=[None]),
        }.items():
            record = copy.deepcopy(committed())
            mutate(record)
            found = recorded.problems(record, REGISTRY)
            self.assertTrue(found, name)
        self.assertEqual(["record is not an object"], recorded.problems([], REGISTRY))
        self.assertEqual(["record is not an object"], recorded.problems(None, REGISTRY))

    def test_the_committed_steps_each_map_to_their_chain_events(self) -> None:
        record = committed()
        index_of = {r["hash"]: i for i, r in enumerate(record["chain"])}
        ends = [index_of[s["response"]["head"]] for s in record["steps"]]
        self.assertEqual(sorted(ends), ends)
        self.assertEqual(len(record["chain"]) - 1, ends[-1])


class RegenerationTests(unittest.TestCase):
    def test_committed_record_equals_a_fresh_run_byte_for_byte(self) -> None:
        require_checkout()
        fresh = record_session.render(record_session.run())
        self.assertEqual(fresh, record_session.RECORD_PATH.read_text(encoding="utf-8"))

    def test_two_runs_are_identical(self) -> None:
        require_checkout()
        self.assertEqual(record_session.run(), record_session.run())

    def test_every_propose_went_through_the_consumer_and_the_fake_driver(self) -> None:
        require_checkout()
        record = committed()
        received = [r for r in record["chain"] if r["kind"] == "proposal_received"]
        self.assertEqual(3, len(received))
        for item in received:
            self.assertRegex(item["data"]["reply_output_digest"], r"^sha256:[0-9a-f]{64}$")
        refused = [r for r in record["chain"] if r["kind"] == "proposal_refused"]
        self.assertEqual(("timeout", "watch_only"),
                         (refused[0]["data"]["reason"], refused[0]["data"]["fail_closed_to"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
