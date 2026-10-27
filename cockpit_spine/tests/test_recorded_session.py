"""The committed Linux operator session record.

The committed file cites the synthetic projection stand-in from
``tests/support/vault.py``, built in ``setUpModule`` and discarded in
``tearDownModule``. It does not call ``load_registry()`` with no vault root, and
it does not read ``CSL_RESEARCH_CHECKOUT``. A different registry, including the
packaged csl-research registry, fails the digest check instead of passing.
Real-vault checks live in ``test_projections.RealVaultCheckoutTests`` and skip
when that checkout is unset.

Whole-record checks run anywhere. Regeneration needs the read-only Recursion
checkout (``RECURSION_ROUTE_CONTRACT_CHECKOUT``) and fails closed without it,
exactly like WP12's conformance tests; ``ROUTE_CONSUMER_ALLOW_NO_RECURSION=1``
skips them and proves nothing. The human Linux operator session is still unmet;
Lucas records that one himself.
"""

from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from cockpit_spine import recorded
from cockpit_spine.projections import VAULT_CHECKOUT_ENV, Registry
from cockpit_spine.tests.support import record_session
from cockpit_spine.tests.support.vault import (
    build_synthetic_registry, discard_synthetic_registry,
)
from successor.route_consumer.tests.support.recursion_bridge import require_checkout

_VAULT: Path | None = None
_REGISTRY_PATH: Path | None = None
REGISTRY: Registry


def setUpModule() -> None:
    global REGISTRY, _VAULT, _REGISTRY_PATH
    _VAULT, _REGISTRY_PATH, REGISTRY = build_synthetic_registry()


def tearDownModule() -> None:
    if _VAULT is not None and _REGISTRY_PATH is not None:
        discard_synthetic_registry(_VAULT, _REGISTRY_PATH)


def _vault() -> tuple[Path, Path]:
    if _VAULT is None or _REGISTRY_PATH is None:
        raise AssertionError("synthetic registry was not built")
    return _VAULT, _REGISTRY_PATH


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

    def _retitle(self, record: dict, argv0: str, code: str, label: str, value: str) -> None:
        step = self.step(record, argv0, code)
        for line in step["response"]["lines"]:
            if line.get("label") == label:
                line["value"] = value
                return
        raise AssertionError(f"no {label} line on {argv0}/{code}")

    def test_edited_operator_lines_are_reported(self) -> None:
        cases = (
            ("approve", "approved", "authority", "live"),
            ("approve", "approved", "result", "vetoed"),
            ("approve", "digest_mismatch", "result", "something-else"),
            ("stop", "stopped", "state", "running"),
            ("open", "status", "mode", "read_only"),
        )
        for argv0, code, label, value in cases:
            found = self.damaged(
                lambda r, a=argv0, c=code, lab=label, val=value: self._retitle(r, a, c, lab, val)
            )
            self.assertTrue(
                any("response differs" in item for item in found), (argv0, label, found))

    def test_an_edited_response_envelope_is_reported(self) -> None:
        def withheld(r: dict) -> None:
            self.step(r, "approve", "approved")["response"]["withheld"] = 9

        def api(r: dict) -> None:
            self.step(r, "open", "status")["response"]["api"] = "operator-api/9"

        def extra(r: dict) -> None:
            self.step(r, "stop", "stopped")["response"]["live"] = True

        for mutate in (withheld, api, extra):
            found = self.damaged(mutate)
            self.assertTrue(any("response differs" in item for item in found), found)

    def test_a_changed_prompt_is_reported(self) -> None:
        def prompt(r: dict) -> None:
            step = self.step(r, "propose", "timeout")
            step["argv"][step["argv"].index("--prompt") + 1] = "not-fail-closed"

        def added(r: dict) -> None:
            step = self.step(r, "propose", "proposal_received")
            step["argv"] += ["--prompt", "other"]

        for mutate in (prompt, added):
            found = self.damaged(mutate)
            self.assertTrue(any("prompt differs" in item for item in found), found)

    def test_a_refusal_whose_arguments_would_succeed_is_reported(self) -> None:
        def swap(r: dict) -> None:
            refused = self.step(r, "approve", "digest_mismatch")
            accepted = self.step(r, "approve", "approved")
            refused["argv"][refused["argv"].index("--confirm-digest") + 1] = (
                accepted["argv"][accepted["argv"].index("--confirm-digest") + 1])

        found = self.damaged(swap)
        self.assertTrue(any("would not be refused" in item for item in found), found)

    def test_swapping_the_refused_proposal_id_is_reported(self) -> None:
        """prop-r2 also rejects the zero digest, so ok and code still match (Codex D17)."""

        def swap(r: dict) -> None:
            refused = self.step(r, "approve", "digest_mismatch")
            refused["argv"][refused["argv"].index("--proposal-id") + 1] = "prop-r2"

        found = self.damaged(swap)
        self.assertTrue(any("refusal record differs" in item for item in found), found)

    def test_surrounding_whitespace_matches_what_the_session_stores(self) -> None:
        def pad(r: dict) -> None:
            veto = self.step(r, "veto", "vetoed")
            veto["argv"][veto["argv"].index("--operator") + 1] = " operator-1 "
            stop = self.step(r, "stop", "stopped")
            stop["argv"][stop["argv"].index("--reason") + 1] = " end of demo session "

        self.assertEqual([], self.damaged(pad))

    def test_a_repeated_flag_is_read_the_way_the_cli_reads_it(self) -> None:
        """argparse keeps the last copy of a flag; the checker must too (Codex 4383053753)."""

        def appended(r: dict) -> None:
            self.step(r, "approve", "approved")["argv"] += [
                "--confirm-digest", "sha256:" + "0" * 64]

        found = self.damaged(appended)
        self.assertTrue(any("confirm digest differs" in item for item in found), found)

    def test_argv_the_cli_would_refuse_is_reported(self) -> None:
        def unknown(r: dict) -> None:
            self.step(r, "stop", "stopped")["argv"] += ["--live"]

        def missing(r: dict) -> None:
            argv = self.step(r, "veto", "vetoed")["argv"]
            del argv[argv.index("--reason"):argv.index("--reason") + 2]

        for mutate in (unknown, missing):
            found = self.damaged(mutate)
            self.assertTrue(any("not a command the CLI accepts" in item for item in found),
                            found)

    def test_a_changed_driver_or_contract_identity_is_reported(self) -> None:
        def top(r: dict) -> None:
            r["adapter_kind"] = "live"

        def opened(r: dict) -> None:
            r["chain"][0]["data"]["adapter_kind"] = "live"

        def head(r: dict) -> None:
            r["contract"]["head_sha"] = "f" * 40

        def schema(r: dict) -> None:
            r["contract"]["schema_sha256"] = "0" * 64

        def extra(r: dict) -> None:
            r["contract"]["live"] = True

        for mutate, words in ((top, "adapter kind"), (opened, "adapter kind"),
                              (head, "contract identity"), (schema, "contract identity"),
                              (extra, "contract identity")):
            found = self.damaged(mutate)
            self.assertTrue(any(words in item for item in found), (mutate.__name__, found))

    def test_a_boolean_exit_code_is_reported(self) -> None:
        self.assertTrue(self.damaged(lambda r: r["steps"][0].update(exit_code=False)))

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
        vault, registry_path = _vault()
        fresh = record_session.render(record_session.run(
            vault_root=vault, registry_path=registry_path))
        self.assertEqual(fresh, record_session.RECORD_PATH.read_text(encoding="utf-8"))

    def test_two_runs_are_identical(self) -> None:
        require_checkout()
        vault, registry_path = _vault()
        self.assertEqual(
            record_session.run(vault_root=vault, registry_path=registry_path),
            record_session.run(vault_root=vault, registry_path=registry_path),
        )

    def test_an_ambient_checkout_does_not_replace_the_explicit_vault(self) -> None:
        """``CSL_RESEARCH_CHECKOUT`` must not change the recorded digest."""

        require_checkout()
        vault, registry_path = _vault()
        absent = Path("/absent-csl-research-checkout")
        with mock.patch.dict(os.environ, {VAULT_CHECKOUT_ENV: str(absent)}):
            fresh = record_session.run(vault_root=vault, registry_path=registry_path)
        self.assertEqual(REGISTRY.digest, fresh["projection_registry_digest"])

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


class RecorderArgumentTests(unittest.TestCase):
    def test_the_recorder_requires_an_explicit_vault_root(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            record_session.main([])
        self.assertEqual(2, caught.exception.code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
