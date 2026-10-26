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
        }
        for name, mutate in cases.items():
            self.assertTrue(recorded.problems(edited(mutate), REGISTRY), name)


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
