from __future__ import annotations

import re
import unittest

from cockpit_spine import projections as proj
from cockpit_spine import service as svc
from cockpit_spine import session as sess
from cockpit_spine import views
from cockpit_spine.tests.support.scripted import (
    ScriptedSource, accepted, counter_clock, fail_closed, proposal_payload,
)

REGISTRY = proj.load_registry()
OP = "operator-1"


def make(*outcomes, mode: str = "paper") -> svc.OperatorService:
    session = sess.OperatorSession.open(
        session_id="s-1", mode=mode, source=ScriptedSource(*outcomes), clock=counter_clock())
    return svc.OperatorService(session, REGISTRY)


def call(service: svc.OperatorService, command: str, **args: str) -> dict:
    return service.handle({"api": svc.API_VERSION, "command": command, "args": args})


def value(response: dict, label: str) -> str:
    return next(item["value"] for item in response["lines"] if item["label"] == label)


class RequestShapeTests(unittest.TestCase):
    def test_malformed_requests_are_refused_and_change_nothing(self) -> None:
        service = make()
        head = service.session.head
        good = {"api": svc.API_VERSION, "command": "status", "args": {}}
        bad = [
            None, [], "status", {}, {**good, "api": "operator-api/9"},
            {**good, "command": "nope"}, {**good, "command": 5},
            {**good, "args": {"x": "y"}}, {**good, "args": []},
            {"api": svc.API_VERSION, "command": "approve", "args": {"proposal_id": "p"}},
            {"api": svc.API_VERSION, "command": "stop",
             "args": {"operator": OP, "reason": "r", "force": "yes"}},
            {"api": svc.API_VERSION, "command": "stop", "args": {"operator": OP, "reason": 5}},
            {"api": svc.API_VERSION, "command": "propose", "args": {"prompt": "p"}},
        ]
        for request in bad:
            response = service.handle(request)
            self.assertFalse(response["ok"], request)
            self.assertIn(response["code"], (svc.BAD_REQUEST, svc.UNKNOWN_COMMAND))
        self.assertEqual(head, service.session.head)
        self.assertTrue(service.handle(good)["ok"])

    def test_the_prompt_is_optional_on_propose(self) -> None:
        service = make(accepted(proposal_payload()))
        self.assertTrue(call(service, "propose", request_id="req-1")["ok"])

    def test_a_proposal_missing_an_optional_size_still_renders(self) -> None:
        for missing in ("quantity", "notional"):
            payload = proposal_payload("prop-1")
            del payload[missing]
            service = make(accepted(payload))
            proposed = call(service, "propose", request_id="req-1", prompt="p")
            self.assertTrue(proposed["ok"], missing)
            self.assertEqual("none", value(proposed, missing))
            status = call(service, "status")
            self.assertTrue(status["ok"], missing)
            self.assertEqual("none", value(status, missing))
            self.assertEqual("pending", value(status, "proposal state"))


class FlowTests(unittest.TestCase):
    def flow(self) -> list[dict]:
        service = make(
            accepted(proposal_payload("prop-1")), accepted(proposal_payload("prop-2")),
            fail_closed("http_429"))
        responses = [call(service, "propose", request_id=f"req-{n}", prompt="p") for n in (1, 2, 3)]
        digest = value(responses[1], "confirm digest")
        responses.append(call(service, "approve", proposal_id="prop-1", operator=OP,
                              confirm_digest=value(responses[0], "confirm digest")))
        responses.append(call(service, "approve", proposal_id="prop-2", operator=OP,
                              confirm_digest="sha256:" + "0" * 64))
        responses.append(call(service, "veto", proposal_id="prop-2", operator=OP, reason="too big"))
        responses.append(call(service, "stop", operator=OP, reason="done"))
        responses.append(call(service, "propose", request_id="req-9"))
        responses.append(call(service, "status"))
        responses.append(call(service, "audit"))
        self.assertEqual(digest, digest)
        return responses

    def test_every_step_does_what_the_session_does(self) -> None:
        r = self.flow()
        self.assertEqual([True, True, False], [r[0]["ok"], r[1]["ok"], r[2]["ok"]])
        self.assertEqual("unrecognized_code", r[2]["code"])
        self.assertEqual((True, "approved"), (r[3]["ok"], r[3]["code"]))
        self.assertIn("none, inert record", value(r[3], "authority"))
        self.assertEqual((False, "digest_mismatch"), (r[4]["ok"], r[4]["code"]))
        self.assertEqual((True, "vetoed"), (r[5]["ok"], r[5]["code"]))
        self.assertEqual((True, "stopped"), (r[6]["ok"], r[6]["code"]))
        self.assertEqual((False, "session_stopped"), (r[7]["ok"], r[7]["code"]))
        self.assertEqual("stopped, watch_only", value(r[8], "state"))

    def test_every_response_passes_view_validation_and_has_linked_figures(self) -> None:
        for response in self.flow():
            lines = [views.Line(**item) for item in response["lines"]]
            views.validate_lines(lines, REGISTRY)
            for item in lines:
                if item.kind == "figure":
                    self.assertTrue(
                        item.value.startswith(("15 cells [projection:", "20 cells [projection:")),
                        item.value)

    def test_digits_appear_only_in_lines_allowed_to_carry_them(self) -> None:
        for response in self.flow():
            for item in response["lines"]:
                if item["kind"] in ("text", "quoted", "code"):
                    self.assertNotRegex(item["value"], r"[0-9]", item)
                if item["kind"] == "figure":
                    self.assertRegex(
                        item["value"], r"\[projection:[a-z0-9._-]+@sha256:[0-9a-f]{64}\]$")

    def test_model_prose_numerals_are_masked_in_the_view_but_kept_in_the_record(self) -> None:
        payload = proposal_payload(thesis="Up 12 percent by 2027.", invalidation="Below 3.5.")
        service = make(accepted(payload))
        response = call(service, "propose", request_id="req-1")
        self.assertEqual(
            "Up [n] percent by [n].", value(response, "thesis (model text, unverified)"))
        self.assertEqual(payload, service.session.proposal("prop-1")["payload"])

    def test_a_line_that_would_show_an_unlinked_number_is_withheld_not_the_truth(self) -> None:
        service = make()
        response = service._reply(True, "x", [views.Line("text", "state", "up 12 percent"),
                                              views.Line("id", "session", "s-1")])
        self.assertEqual((True, "x", 1), (response["ok"], response["code"], response["withheld"]))
        self.assertEqual([views.WITHHELD_LINE.as_dict(),
                          views.Line("id", "session", "s-1").as_dict()], response["lines"])
        self.assertNotIn("12", str(response["lines"]))

    def test_audit_view_lists_every_record_with_time_and_hash(self) -> None:
        r = self.flow()[-1]
        kinds = [i["value"] for i in r["lines"] if i["label"] == "record"]
        self.assertEqual("1:session_opened", kinds[0])
        self.assertTrue(all(re.match(r"^\d+:[a-z_]+$", k) for k in kinds))
        self.assertEqual(len(kinds), len([i for i in r["lines"] if i["label"] == "hash"]))

    def test_read_only_mode_refuses_approval_through_the_service_too(self) -> None:
        service = make(accepted(proposal_payload()), mode="read_only")
        digest = value(call(service, "propose", request_id="req-1"), "confirm digest")
        response = call(
            service, "approve", proposal_id="prop-1", operator=OP, confirm_digest=digest)
        self.assertEqual((False, "mode_read_only"), (response["ok"], response["code"]))


class AppliedChangeIsNeverHiddenTests(unittest.TestCase):
    """Samir's reproductions: a recorded change must be reported as it happened."""

    def labels(self, response: dict) -> list[str]:
        return [item["label"] for item in response["lines"]]

    def test_an_operator_name_with_a_space_is_refused_before_anything_is_recorded(self) -> None:
        service = make(accepted(proposal_payload()))
        digest = value(call(service, "propose", request_id="r1"), "confirm digest")
        before = [r["kind"] for r in service.session.records()]
        for command, args in (
            ("veto", {"reason": "no"}),
            ("approve", {"confirm_digest": digest}),
            ("stop", {"reason": "halt"}),
        ):
            extra = {} if command == "stop" else {"proposal_id": "prop-1"}
            response = call(service, command, operator="Samir Shah", **extra, **args)
            self.assertEqual((False, sess.OPERATOR_INVALID), (response["ok"], response["code"]))
        kinds = [r["kind"] for r in service.session.records()]
        self.assertEqual(before + ["command_refused"] * 3, kinds)
        self.assertEqual("pending", service.session.proposal("prop-1")["state"])
        status = call(service, "status")
        self.assertTrue(status["ok"])
        self.assertEqual(0, status["withheld"])
        self.assertGreater(len(status["lines"]), 5)

    def test_two_source_refs_each_show_and_the_confirm_digest_is_visible(self) -> None:
        refs = ["parquet://2026-10-26/events-1#0", "parquet://2026-10-26/events-1#1"]
        service = make(accepted(proposal_payload(source_refs=refs)))
        response = call(service, "propose", request_id="r1")
        self.assertEqual((True, "proposal_received", 0), (
            response["ok"], response["code"], response["withheld"]))
        shown = [i["value"] for i in response["lines"] if i["label"] == "source ref"]
        self.assertEqual(refs, shown)
        self.assertRegex(value(response, "confirm digest"), r"^sha256:[0-9a-f]{64}$")

    def test_a_stop_that_took_effect_reports_ok_even_when_a_line_is_withheld(self) -> None:
        service = make(accepted(proposal_payload("123456")))
        response = call(service, "propose", request_id="r1")
        self.assertEqual((True, "proposal_received"), (response["ok"], response["code"]))
        self.assertEqual(1, response["withheld"])
        self.assertIn("withheld line", self.labels(response))
        self.assertRegex(value(response, "confirm digest"), r"^sha256:")
        stopped = call(service, "stop", operator=OP, reason="halt")
        self.assertEqual((True, "stopped"), (stopped["ok"], stopped["code"]))
        self.assertTrue(service.session.stopped)
        self.assertEqual(1, stopped["withheld"])

    def test_after_a_withheld_line_every_later_status_still_works(self) -> None:
        service = make(accepted(proposal_payload("123456")))
        call(service, "propose", request_id="r1")
        for _ in range(2):
            status = call(service, "status")
            self.assertTrue(status["ok"])
            self.assertEqual("status", status["code"])
            self.assertGreater(len(status["lines"]), 5)
        self.assertEqual("stopped, watch_only" if service.session.stopped else "running",
                         value(status, "state"))

    def test_a_numeric_only_source_ref_is_withheld_alone(self) -> None:
        service = make(accepted(proposal_payload(source_refs=["20261026", "ref-ok"])))
        response = call(service, "propose", request_id="r1")
        self.assertEqual(1, response["withheld"])
        self.assertIn("ref-ok", [i["value"] for i in response["lines"]])
        self.assertNotIn("20261026", str(response["lines"]))

    def test_an_invalid_operator_id_never_reaches_the_transcript_via_replay(self) -> None:
        from cockpit_spine.audit import AuditChain, AuditError

        chain = AuditChain()
        chain.append("session_opened", {"session_id": "s", "mode": "paper",
                                        "adapter_kind": "fake", "at": "t"})
        chain.append("session_stopped", {"operator": "Samir Shah", "reason": "r",
                                         "abandoned": [], "fail_closed_to": "watch_only",
                                         "at": "t"})
        with self.assertRaises(AuditError):
            sess.OperatorSession.replay(chain.records(), source=ScriptedSource())


if __name__ == "__main__":
    unittest.main(verbosity=2)
