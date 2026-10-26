"""The operator session: approve, veto and stop, all fail closed and all audited."""

from __future__ import annotations

import copy
import unittest

from cockpit_spine import session as sess
from cockpit_spine.audit import AuditChain, AuditError, verify_chain
from cockpit_spine.tests.support.scripted import (
    ScriptedSource, accepted, counter_clock, fail_closed, proposal_payload, quarantined,
)

OP = "operator-1"


def open_session(*outcomes, mode: str = "paper") -> tuple[sess.OperatorSession, ScriptedSource]:
    source = ScriptedSource(*outcomes)
    session = sess.OperatorSession.open(
        session_id="s-1", mode=mode, source=source, clock=counter_clock()
    )
    return session, source


def kinds(session: sess.OperatorSession) -> list[str]:
    return [r["kind"] for r in session.records()]


class OpenTests(unittest.TestCase):
    def test_only_read_only_and_paper_can_be_opened(self) -> None:
        for mode in ("review_live", "auto_veto", "auto_full", "live", ""):
            with self.assertRaises(ValueError, msg=mode):
                sess.OperatorSession.open(session_id="s", mode=mode, source=ScriptedSource())
        for mode in ("read_only", "paper"):
            session, _ = open_session(mode=mode)
            self.assertEqual(mode, session.mode)

    def test_session_id_must_be_short_printable(self) -> None:
        for bad in ("", " ", "x" * 65, "a\nb", None):
            with self.assertRaises(ValueError):
                sess.OperatorSession.open(session_id=bad, mode="paper", source=ScriptedSource())


class ProposeTests(unittest.TestCase):
    def test_accepted_proposal_is_pending_and_audited(self) -> None:
        session, source = open_session(accepted(proposal_payload()))
        result = session.request_proposal(request_id="req-1", prompt="p")
        self.assertTrue(result.ok)
        self.assertEqual("pending", session.proposal("prop-1")["state"])
        self.assertEqual(["session_opened", "proposal_requested", "proposal_received"],
                         kinds(session))
        self.assertEqual({"request_id": "req-1", "mode": "paper", "prompt": "p"}, source.calls[0])
        self.assertNotIn('"p"', str(session.records()))
        self.assertEqual(session.head, verify_chain(session.records()))

    def test_fail_closed_outcome_makes_no_proposal_and_says_watch_only(self) -> None:
        session, _ = open_session(fail_closed("route_timeout"))
        result = session.request_proposal(request_id="req-1", prompt="p")
        self.assertEqual((False, "route_timeout"), (result.ok, result.code))
        self.assertEqual([], session.proposals())
        refusal = session.records()[-1]
        self.assertEqual(("proposal_refused", "watch_only", False),
                         (refusal["kind"], refusal["data"]["fail_closed_to"],
                          refusal["data"]["quarantined"]))

    def test_quarantined_outcome_is_never_a_proposal(self) -> None:
        session, _ = open_session(quarantined())
        result = session.request_proposal(request_id="req-1", prompt="p")
        self.assertFalse(result.ok)
        self.assertEqual([], session.proposals())
        self.assertTrue(session.records()[-1]["data"]["quarantined"])

    def test_a_source_that_lies_about_acceptance_is_caught_by_the_schema(self) -> None:
        bad = proposal_payload(side="short-the-world")
        session, _ = open_session(accepted(bad))
        result = session.request_proposal(request_id="req-1", prompt="p")
        self.assertEqual((False, sess.PAYLOAD_INVALID), (result.ok, result.code))
        self.assertEqual([], session.proposals())
        extra = proposal_payload(execute_now=True)
        session, _ = open_session(accepted(extra))
        self.assertEqual(sess.PAYLOAD_INVALID, session.request_proposal(
            request_id="req-1", prompt="p").code)

    def test_duplicate_request_and_proposal_ids_are_refused(self) -> None:
        session, source = open_session(accepted(proposal_payload()), accepted(proposal_payload()))
        self.assertTrue(session.request_proposal(request_id="req-1", prompt="p").ok)
        self.assertEqual(sess.DUPLICATE_REQUEST,
                         session.request_proposal(request_id="req-1", prompt="p").code)
        self.assertEqual(1, len(source.calls))
        self.assertEqual(sess.DUPLICATE_PROPOSAL,
                         session.request_proposal(request_id="req-2", prompt="p").code)
        self.assertEqual(1, len(session.proposals()))

    def test_unsafe_request_ids_are_refused_before_the_source(self) -> None:
        session, source = open_session()
        for bad in ("", "a\x00b", "x" * 65):
            self.assertEqual(sess.UNSAFE_TEXT,
                             session.request_proposal(request_id=bad, prompt="p").code)
        self.assertEqual([], source.calls)


class ApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, _ = open_session(accepted(proposal_payload()))
        self.session.request_proposal(request_id="req-1", prompt="p")
        self.digest = self.session.proposal("prop-1")["payload_digest"]

    def test_approval_binds_the_digest_and_yields_an_inert_intent(self) -> None:
        result = self.session.approve(proposal_id="prop-1", operator=OP, confirm_digest=self.digest)
        self.assertEqual((True, "approved"), (result.ok, result.code))
        self.assertEqual("none", result.record["authority"])
        self.assertEqual(self.digest, result.record["payload_digest"])
        self.assertIn("OrderGateway", result.record["note"])
        self.assertEqual("approved", self.session.proposal("prop-1")["state"])
        self.assertEqual(self.session.records()[-1]["hash"], result.record["audit_hash"])

    def test_a_stale_or_wrong_digest_does_not_approve(self) -> None:
        before = len(self.session.records())
        for wrong in ("", "sha256:" + "0" * 64, None):
            result = self.session.approve(proposal_id="prop-1", operator=OP, confirm_digest=wrong)
            self.assertEqual(sess.DIGEST_MISMATCH, result.code)
        self.assertEqual("pending", self.session.proposal("prop-1")["state"])
        self.assertEqual(before + 3, len(self.session.records()))

    def test_second_approval_and_approve_after_veto_are_refused(self) -> None:
        self.session.approve(proposal_id="prop-1", operator=OP, confirm_digest=self.digest)
        again = self.session.approve(proposal_id="prop-1", operator=OP, confirm_digest=self.digest)
        self.assertEqual(sess.NOT_PENDING, again.code)
        veto = self.session.veto(proposal_id="prop-1", operator=OP, reason="late")
        self.assertEqual(sess.NOT_PENDING, veto.code)

    def test_operator_is_required_and_must_be_printable(self) -> None:
        for bad in ("", "  ", "x" * 65, "a\x1bb", None):
            result = self.session.approve(
                proposal_id="prop-1", operator=bad, confirm_digest=self.digest)
            self.assertEqual(sess.OPERATOR_REQUIRED, result.code)
        self.assertEqual("pending", self.session.proposal("prop-1")["state"])

    def test_unknown_proposal_is_refused(self) -> None:
        for bad in ("nope", None, 5):
            result = self.session.approve(proposal_id=bad, operator=OP, confirm_digest="x")
            self.assertEqual(sess.UNKNOWN_PROPOSAL, result.code)

    def test_read_only_mode_can_never_approve(self) -> None:
        session, _ = open_session(accepted(proposal_payload()), mode="read_only")
        session.request_proposal(request_id="req-1", prompt="p")
        result = session.approve(
            proposal_id="prop-1", operator=OP,
            confirm_digest=session.proposal("prop-1")["payload_digest"])
        self.assertEqual(sess.MODE_READ_ONLY, result.code)
        self.assertEqual("pending", session.proposal("prop-1")["state"])
        self.assertTrue(session.veto(proposal_id="prop-1", operator=OP, reason="no").ok)


class VetoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, _ = open_session(accepted(proposal_payload()))
        self.session.request_proposal(request_id="req-1", prompt="p")

    def test_veto_records_operator_and_reason(self) -> None:
        result = self.session.veto(proposal_id="prop-1", operator=OP, reason="  too big  ")
        self.assertTrue(result.ok)
        item = self.session.proposal("prop-1")
        self.assertEqual(("vetoed", OP, "too big"), (item["state"], item["operator"], item["reason"]))

    def test_veto_needs_a_reason(self) -> None:
        for bad in ("", "   ", "x" * 201, "a\nb", None):
            self.assertEqual(sess.REASON_REQUIRED, self.session.veto(
                proposal_id="prop-1", operator=OP, reason=bad).code)
        self.assertEqual("pending", self.session.proposal("prop-1")["state"])

    def test_vetoed_proposal_cannot_be_approved_even_with_the_right_digest(self) -> None:
        digest = self.session.proposal("prop-1")["payload_digest"]
        self.session.veto(proposal_id="prop-1", operator=OP, reason="no")
        self.assertEqual(sess.NOT_PENDING, self.session.approve(
            proposal_id="prop-1", operator=OP, confirm_digest=digest).code)


class StopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.source = open_session(
            accepted(proposal_payload("prop-1")), accepted(proposal_payload("prop-2")),
            accepted(proposal_payload("prop-3")))
        for n in (1, 2):
            self.session.request_proposal(request_id=f"req-{n}", prompt="p")

    def test_stop_abandons_pending_and_closes_the_session(self) -> None:
        result = self.session.stop(operator=OP, reason="end of demo")
        self.assertTrue(result.ok)
        self.assertEqual(["prop-1", "prop-2"], result.record["data"]["abandoned"])
        self.assertEqual("watch_only", result.record["data"]["fail_closed_to"])
        self.assertTrue(self.session.stopped)
        self.assertEqual({"abandoned"}, {p["state"] for p in self.session.proposals()})

    def test_after_stop_nothing_works_and_the_source_is_never_contacted(self) -> None:
        digest = self.session.proposal("prop-1")["payload_digest"]
        self.session.stop(operator=OP, reason="halt")
        calls = len(self.source.calls)
        refused = [
            self.session.request_proposal(request_id="req-9", prompt="p"),
            self.session.approve(proposal_id="prop-1", operator=OP, confirm_digest=digest),
            self.session.veto(proposal_id="prop-2", operator=OP, reason="x"),
            self.session.stop(operator=OP, reason="again"),
        ]
        self.assertEqual({sess.SESSION_STOPPED}, {r.code for r in refused})
        self.assertFalse(any(r.ok for r in refused))
        self.assertEqual(calls, len(self.source.calls))
        self.assertEqual({"abandoned"}, {p["state"] for p in self.session.proposals()})
        self.assertEqual(["command_refused"] * 4, kinds(self.session)[-4:])

    def test_stop_needs_operator_and_reason_and_leaves_the_session_open_otherwise(self) -> None:
        self.assertEqual(sess.OPERATOR_REQUIRED, self.session.stop(operator="", reason="r").code)
        self.assertEqual(sess.REASON_REQUIRED, self.session.stop(operator=OP, reason="").code)
        self.assertFalse(self.session.stopped)
        self.assertTrue(self.session.request_proposal(request_id="req-3", prompt="p").ok)

    def test_stop_with_nothing_pending_still_stops(self) -> None:
        session, _ = open_session()
        result = session.stop(operator=OP, reason="halt")
        self.assertEqual([], result.record["data"]["abandoned"])
        self.assertTrue(session.stopped)

    def test_decided_proposals_keep_their_state_through_stop(self) -> None:
        digest = self.session.proposal("prop-1")["payload_digest"]
        self.session.approve(proposal_id="prop-1", operator=OP, confirm_digest=digest)
        self.session.stop(operator=OP, reason="halt")
        states = {p["proposal_id"]: p["state"] for p in self.session.proposals()}
        self.assertEqual({"prop-1": "approved", "prop-2": "abandoned"}, states)


class ReplayTests(unittest.TestCase):
    def finished(self) -> sess.OperatorSession:
        session, _ = open_session(
            accepted(proposal_payload("prop-1")), accepted(proposal_payload("prop-2")),
            fail_closed(), accepted(proposal_payload("prop-3")))
        for n in range(1, 5):
            session.request_proposal(request_id=f"req-{n}", prompt="p")
        d1 = session.proposal("prop-1")["payload_digest"]
        session.approve(proposal_id="prop-1", operator=OP, confirm_digest=d1)
        session.veto(proposal_id="prop-2", operator=OP, reason="no")
        session.approve(proposal_id="prop-3", operator=OP, confirm_digest="bad")
        session.stop(operator=OP, reason="halt")
        session.approve(proposal_id="prop-3", operator=OP, confirm_digest="bad")
        return session

    def test_replay_rebuilds_identical_state_and_head(self) -> None:
        original = self.finished()
        rebuilt = sess.OperatorSession.replay(original.records(), source=ScriptedSource())
        self.assertEqual(original.head, rebuilt.head)
        self.assertEqual(original.proposals(), rebuilt.proposals())
        self.assertEqual((original.stopped, original.mode, original.session_id),
                         (rebuilt.stopped, rebuilt.mode, rebuilt.session_id))

    def test_replay_refuses_any_edit(self) -> None:
        records = self.finished().records()
        for index in range(len(records)):
            bad = copy.deepcopy(records)
            bad[index]["data"]["at"] = "tampered"
            with self.assertRaises(AuditError, msg=index):
                sess.OperatorSession.replay(bad, source=ScriptedSource())

    def test_replay_refuses_a_consistent_chain_that_breaks_the_rules(self) -> None:
        def forge(*events) -> list:
            chain = AuditChain()
            for kind, data in events:
                chain.append(kind, data)
            return chain.records()

        opened = ("session_opened", {"session_id": "s", "mode": "paper",
                                     "adapter_kind": "fake", "at": "t"})
        approve_without_proposal = forge(
            opened, ("proposal_approved", {"proposal_id": "p", "operator": "o",
                                           "payload_digest": "d", "at": "t"}))
        activity_after_stop = forge(
            opened, ("session_stopped", {"operator": "o", "reason": "r", "abandoned": [],
                                         "fail_closed_to": "watch_only", "at": "t"}),
            ("proposal_requested", {"request_id": "r", "prompt_digest": "d", "at": "t"}))
        no_open = forge(("proposal_requested", {"request_id": "r", "prompt_digest": "d",
                                                "at": "t"}))
        double_open = forge(opened, opened)
        unknown_kind = forge(opened, ("order_sent", {"at": "t"}))
        for name, bad in {"approve-without-proposal": approve_without_proposal,
                          "activity-after-stop": activity_after_stop, "no-open": no_open,
                          "double-open": double_open, "unknown-kind": unknown_kind}.items():
            with self.assertRaises(AuditError, msg=name):
                sess.OperatorSession.replay(bad, source=ScriptedSource())

    def test_replay_of_an_empty_chain_is_refused(self) -> None:
        with self.assertRaises(AuditError):
            sess.OperatorSession.replay([], source=ScriptedSource())


if __name__ == "__main__":
    unittest.main(verbosity=2)
