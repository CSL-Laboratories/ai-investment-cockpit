"""The operator session: approve, veto and stop, all fail closed and all audited."""

from __future__ import annotations

import copy
import threading
import time
import unittest

from cockpit_spine import session as sess
from cockpit_spine.audit import AuditChain, AuditError, verify_chain
from cockpit_spine.tests.support.scripted import (
    ScriptedSource, accepted, counter_clock, fail_closed, proposal_payload, quarantined,
)
from successor.route_consumer import ACCEPTED, FAIL_CLOSED, DecisionOutcome
from successor.route_consumer.profile import canonical_digest

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

    def test_open_refuses_a_non_identifier_session_id(self) -> None:
        for bad in ("Samir Shah", "12345", "has space"):
            with self.assertRaises(ValueError, msg=bad):
                sess.OperatorSession.open(session_id=bad, mode="paper", source=ScriptedSource())

    def test_a_padded_session_id_is_stored_clean(self) -> None:
        session = sess.OperatorSession.open(
            session_id=" s-1 ", mode="paper", source=ScriptedSource(), clock=counter_clock())
        self.assertEqual("s-1", session.session_id)
        self.assertEqual("s-1", session.records()[0]["data"]["session_id"])
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())


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
        self.assertEqual(
            ("vetoed", OP, "too big"), (item["state"], item["operator"], item["reason"]))

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


def _forge(*events) -> list:
    chain = AuditChain()
    for kind, data in events:
        chain.append(kind, {"at": "t", **data})
    return chain.records()


def _opened(mode: str = "paper"):
    return ("session_opened", {"session_id": "s", "mode": mode, "adapter_kind": "fake"})


def _requested(request_id: str = "r1"):
    return ("proposal_requested", {"request_id": request_id, "prompt_digest": "sha256:x"})


def _received(request_id: str = "r1", payload: dict | None = None, **over):
    payload = payload if payload is not None else proposal_payload("prop-1")
    data = {"request_id": request_id, "proposal_id": payload.get("proposal_id"),
            "payload": payload, "payload_digest": canonical_digest(payload),
            "reply_output_digest": None}
    data.update(over)
    return ("proposal_received", data)


def _refused_record(request_id: str = "r1", reason: str = "timeout"):
    return ("proposal_refused", {"request_id": request_id, "reason": reason,
                                 "quarantined": False, "fail_closed_to": "watch_only"})


def _approved(proposal_id: str = "prop-1", digest: str | None = None):
    digest = digest or canonical_digest(proposal_payload(proposal_id))
    return ("proposal_approved", {"proposal_id": proposal_id, "operator": OP,
                                  "payload_digest": digest})


def _stopped(abandoned: list):
    return ("session_stopped", {"operator": OP, "reason": "halt", "abandoned": abandoned,
                                "fail_closed_to": "watch_only"})


class ReplayInvariantTests(unittest.TestCase):
    """A chain only links records; the rules the live commands enforce must hold on replay."""

    def refuses(self, name: str, *events) -> None:
        with self.assertRaises(AuditError, msg=name):
            sess.OperatorSession.replay(_forge(*events), source=ScriptedSource())

    def test_a_well_formed_forged_chain_replays(self) -> None:
        session = sess.OperatorSession.replay(
            _forge(_opened(), _requested(), _received(), _approved(),
                   _requested("r2"), _refused_record("r2"), _stopped([])),
            source=ScriptedSource())
        self.assertEqual("approved", session.proposal("prop-1")["state"])
        self.assertTrue(session.stopped)

    def test_read_only_sessions_never_replay_an_approval(self) -> None:
        self.refuses("read-only approve", _opened("read_only"), _requested(), _received(),
                     _approved())

    def test_an_approval_with_the_wrong_digest_does_not_replay(self) -> None:
        self.refuses("digest mismatch", _opened(), _requested(), _received(),
                     _approved(digest="sha256:" + "0" * 64))

    def test_a_second_proposal_with_the_same_id_does_not_replay(self) -> None:
        self.refuses("duplicate proposal_id", _opened(), _requested(), _received(), _approved(),
                     _requested("r2"), _received("r2"))

    def test_a_payload_that_fails_the_schema_does_not_replay(self) -> None:
        bad = proposal_payload("prop-1", side="short-the-world")
        self.refuses("schema", _opened(), _requested(), _received(payload=bad))
        extra = proposal_payload("prop-1", execute_now=True)
        self.refuses("extra field", _opened(), _requested(), _received(payload=extra))

    def test_a_payload_digest_that_is_not_recomputed_does_not_replay(self) -> None:
        self.refuses("digest not recomputed", _opened(), _requested(),
                     _received(payload_digest="sha256:" + "0" * 64))

    def test_proposal_id_must_be_the_payloads(self) -> None:
        self.refuses("id differs", _opened(), _requested(), _received(proposal_id="prop-9"))

    def test_a_request_gets_one_outcome(self) -> None:
        second = proposal_payload("prop-2")
        self.refuses("received twice", _opened(), _requested(), _received(),
                     _received(payload=second))
        self.refuses("refused twice", _opened(), _requested(), _refused_record(),
                     _refused_record())
        self.refuses("refused then received", _opened(), _requested(), _refused_record(),
                     _received())
        self.refuses("received then refused", _opened(), _requested(), _received(),
                     _refused_record())

    def test_an_outcome_needs_a_request_and_a_request_cannot_repeat(self) -> None:
        self.refuses("received without request", _opened(), _received())
        self.refuses("refused without request", _opened(), _refused_record())
        self.refuses("request twice", _opened(), _requested(), _requested())

    def test_stop_must_abandon_exactly_the_pending_proposals(self) -> None:
        self.refuses("omits pending", _opened(), _requested(), _received(), _stopped([]))
        self.refuses("names a decided one", _opened(), _requested(), _received(), _approved(),
                     _stopped(["prop-1"]))
        self.refuses("names an unknown one", _opened(), _stopped(["ghost"]))
        self.refuses("duplicates", _opened(), _requested(), _received(),
                     _stopped(["prop-1", "prop-1"]))

    def test_refusal_records_must_state_the_fail_closed_target_consistently(self) -> None:
        bad = ("proposal_refused", {"request_id": "r1", "reason": "x", "quarantined": False,
                                    "fail_closed_to": None})
        self.refuses("refused without watch_only", _opened(), _requested(), bad)
        bad = ("session_stopped", {"operator": OP, "reason": "r", "abandoned": [],
                                   "fail_closed_to": "trade"})
        self.refuses("stop to something else", _opened(), bad)

    def test_a_null_operator_or_reason_does_not_replay(self) -> None:
        digest = canonical_digest(proposal_payload("prop-1"))
        self.refuses("null approve operator", _opened(), _requested(), _received(),
                     ("proposal_approved", {"proposal_id": "prop-1", "operator": None,
                                            "payload_digest": digest}))
        self.refuses("null veto operator", _opened(), _requested(), _received(),
                     ("proposal_vetoed", {"proposal_id": "prop-1", "operator": None,
                                          "reason": "no"}))
        self.refuses("null veto reason", _opened(), _requested(), _received(),
                     ("proposal_vetoed", {"proposal_id": "prop-1", "operator": OP,
                                          "reason": None}))
        self.refuses("null stop operator", _opened(),
                     ("session_stopped", {"operator": None, "reason": "halt", "abandoned": [],
                                          "fail_closed_to": "watch_only"}))
        self.refuses("null stop reason", _opened(),
                     ("session_stopped", {"operator": OP, "reason": None, "abandoned": [],
                                          "fail_closed_to": "watch_only"}))
        self.refuses("null session id",
                     ("session_opened", {"session_id": None, "mode": "paper",
                                         "adapter_kind": "fake"}))

    def test_a_reply_digest_that_is_not_a_digest_does_not_replay(self) -> None:
        self.refuses("not a digest", _opened(), _requested(),
                     _received(reply_output_digest="not-a-digest"))

    def test_mutating_the_callers_payload_after_replay_does_not_change_the_session(self) -> None:
        records = _forge(_opened(), _requested(), _received())
        session = sess.OperatorSession.replay(records, source=ScriptedSource())
        records[2]["data"]["payload"]["symbol"] = "BBB"
        self.assertEqual("AAA", session.proposal("prop-1")["payload"]["symbol"])
        self.assertEqual("AAA", session.records()[2]["data"]["payload"]["symbol"])
        self.assertEqual(records[2]["hash"], session.head)

    def test_malformed_records_are_audit_errors_not_crashes(self) -> None:
        self.refuses("missing keys", _opened(), ("proposal_requested", {}))
        self.refuses("not a dict payload", _opened(), _requested(),
                     ("proposal_received", {"request_id": "r1", "proposal_id": "x",
                                            "payload": None, "payload_digest": "d",
                                            "reply_output_digest": None}))
        self.refuses("bad mode", ("session_opened", {"session_id": "s", "mode": "live",
                                                     "adapter_kind": "fake"}))
        self.refuses("padded request id", _opened(), _requested(" r1"))
        self.refuses("dirty operator", _opened(), _requested(), _received(),
                     ("proposal_vetoed", {"proposal_id": "prop-1", "operator": " ",
                                          "reason": "no"}))

    def test_whitespace_variants_of_a_request_id_are_one_request(self) -> None:
        session, source = open_session(accepted(proposal_payload("prop-1")),
                                       accepted(proposal_payload("prop-2")))
        self.assertTrue(session.request_proposal(request_id=" r1 ", prompt="p").ok)
        again = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.DUPLICATE_REQUEST), (again.ok, again.code))
        self.assertEqual(1, len(source.calls))
        self.assertEqual("r1", session.records()[1]["data"]["request_id"])
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())


class RaisingSource:
    adapter_kind = "fake"

    def fetch(self, *, request_id: str, mode: str, prompt: str):
        raise RuntimeError("boom")


class SourceFailureTests(unittest.TestCase):
    def test_a_source_that_raises_fails_closed_with_a_recorded_refusal(self) -> None:
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=RaisingSource(), clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        last = session.records()[-1]
        self.assertEqual(("proposal_refused", "watch_only"),
                         (last["kind"], last["data"]["fail_closed_to"]))
        self.assertNotIn("boom", str(session.records()))
        self.assertEqual([], session.proposals())
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())
        self.assertTrue(session.stop(operator=OP, reason="halt").ok)

    def test_a_non_text_reason_is_refused_instead_of_dangling(self) -> None:
        source = ScriptedSource(DecisionOutcome(
            FAIL_CLOSED, None, None, {"id": "r"}, None, "watch_only"))  # type: ignore[arg-type]
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source, clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        self.assertEqual(
            ["session_opened", "proposal_requested", "proposal_refused"], kinds(session))
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_a_non_text_reply_digest_is_refused_instead_of_dangling(self) -> None:
        source = ScriptedSource(DecisionOutcome(
            ACCEPTED, "ok", proposal_payload(), {"id": "r"}, {"output_digest": 1}, None))
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source, clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        self.assertEqual(
            ["session_opened", "proposal_requested", "proposal_refused"], kinds(session))
        self.assertEqual([], session.proposals())
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_a_source_that_returns_none_fails_closed(self) -> None:
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=_Returns(None), clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        self.assertEqual(
            ["session_opened", "proposal_requested", "proposal_refused"], kinds(session))
        self.assertEqual([], session.proposals())
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_a_raising_disposition_fails_closed(self) -> None:
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=_Returns(_RaisingDisposition()),
            clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        self.assertEqual(
            ["session_opened", "proposal_requested", "proposal_refused"], kinds(session))
        self.assertNotIn("disposition blew up", str(session.records()))
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_a_dirty_source_reason_is_stored_as_source_error(self) -> None:
        dirty = "line\nbreak" + ("x" * 500)
        source = ScriptedSource(DecisionOutcome(
            FAIL_CLOSED, dirty, None, {"id": "r"}, None, "watch_only"))
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source, clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        self.assertEqual(sess.SOURCE_ERROR, session.records()[-1]["data"]["reason"])
        self.assertNotIn(dirty, str(session.records()))
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_a_string_that_is_not_a_digest_is_refused(self) -> None:
        source = ScriptedSource(DecisionOutcome(
            ACCEPTED, "ok", proposal_payload(), {"id": "r"},
            {"output_digest": "not-a-digest"}, None))
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source, clock=counter_clock())
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_ERROR), (result.ok, result.code))
        self.assertEqual([], session.proposals())
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_a_hung_fetch_fails_closed_to_watch_only_and_a_late_result_is_not_applied(self) -> None:
        source = _HungThenAccepted()
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source, clock=counter_clock(),
            fetch_deadline_seconds=0.2)
        began = time.monotonic()
        result = session.request_proposal(request_id="r1", prompt="p")
        self.assertLess(time.monotonic() - began, 2.0)
        self.assertEqual((False, sess.SOURCE_TIMEOUT), (result.ok, result.code))
        refusal = session.records()[-1]
        self.assertEqual(
            ("proposal_refused", "watch_only", False, sess.SOURCE_TIMEOUT),
            (refusal["kind"], refusal["data"]["fail_closed_to"],
             refusal["data"]["quarantined"], refusal["data"]["reason"]))
        self.assertEqual([], session.proposals())
        source.release.set()
        self.assertTrue(source.returned.wait(2))
        self.assertEqual([], session.proposals())
        self.assertNotIn("proposal_received", kinds(session))
        self.assertNotIn("proposal_approved", kinds(session))
        refused = session.approve(proposal_id="prop-1", operator=OP, confirm_digest="x")
        self.assertEqual(sess.UNKNOWN_PROPOSAL, refused.code)
        self.assertFalse(session.stopped)
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_repeated_hangs_do_not_grow_the_live_thread_count(self) -> None:
        """A hung source leaves one worker. Later proposes refuse and start none.

        The operator service is long-lived, so each timed-out fetch must not add
        another thread. The refusal is the same fail-closed source_timeout.
        """

        source = _HungThenAccepted()
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source, clock=counter_clock(),
            fetch_deadline_seconds=0.2)
        self.addCleanup(source.release.set)
        already = _live_fetchers()
        first = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((False, sess.SOURCE_TIMEOUT), (first.ok, first.code))
        running = threading.active_count()
        self.assertEqual(already + 1, _live_fetchers())
        worker = session._fetch_worker
        self.assertIsNotNone(worker)
        began = time.monotonic()
        for number in range(2, 6):
            result = session.request_proposal(request_id=f"r{number}", prompt="p")
            self.assertEqual((False, sess.SOURCE_TIMEOUT), (result.ok, result.code))
            refusal = session.records()[-1]
            self.assertEqual(
                ("proposal_refused", "watch_only", False, sess.SOURCE_TIMEOUT),
                (refusal["kind"], refusal["data"]["fail_closed_to"],
                 refusal["data"]["quarantined"], refusal["data"]["reason"]))
        self.assertEqual(running, threading.active_count())
        self.assertEqual(already + 1, _live_fetchers())
        self.assertIs(worker, session._fetch_worker)
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual([], session.proposals())
        source.release.set()
        self.assertTrue(source.returned.wait(2))
        worker = session._fetch_worker
        assert worker is not None
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("proposal_received", kinds(session))
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

    def test_back_to_back_fetches_succeed_and_a_hung_fetch_still_refuses_the_next(self) -> None:
        """Gate on the in-flight flag, not on whether the worker thread is alive.

        The worker clears the flag before the thread exits. Two proposes with no
        join in between both get the source's real outcome, even while the first
        thread is still alive. A hung fetch leaves the flag set, so the next
        propose is the same fail-closed source_timeout and starts no thread.
        """

        hold = threading.Event()
        original_run = sess.threading.Thread.run

        def lingering_run(worker: threading.Thread) -> None:
            try:
                original_run(worker)
            finally:
                if worker.name == "proposal-fetch":
                    hold.wait(5)

        sess.threading.Thread.run = lingering_run  # type: ignore[method-assign]
        hung = _HungThenAccepted()

        def release() -> None:
            hold.set()
            hung.release.set()
            sess.threading.Thread.run = original_run  # type: ignore[method-assign]
            for item in threading.enumerate():
                if item.name == "proposal-fetch":
                    item.join(2)

        self.addCleanup(release)
        session, source = open_session(
            accepted(proposal_payload("prop-1")), accepted(proposal_payload("prop-2")))
        first = session.request_proposal(request_id="r1", prompt="p")
        self.assertEqual((True, "proposal_received"), (first.ok, first.code))
        worker = session._fetch_worker
        assert worker is not None
        self.assertTrue(worker.is_alive())
        self.assertFalse(session._fetch_in_flight)
        second = session.request_proposal(request_id="r2", prompt="p")
        self.assertTrue(worker.is_alive())
        self.assertEqual((True, "proposal_received"), (second.ok, second.code))
        self.assertEqual(["r1", "r2"], [call["request_id"] for call in source.calls])
        self.assertEqual("pending", session.proposal("prop-1")["state"])
        self.assertEqual("pending", session.proposal("prop-2")["state"])
        self.assertNotIn(sess.SOURCE_TIMEOUT, [r["data"].get("reason") for r in session.records()])
        sess.OperatorSession.replay(session.records(), source=ScriptedSource())

        hung_session = sess.OperatorSession.open(
            session_id="s-2", mode="paper", source=hung, clock=counter_clock(),
            fetch_deadline_seconds=0.2)
        timed_out = hung_session.request_proposal(request_id="h1", prompt="p")
        self.assertEqual((False, sess.SOURCE_TIMEOUT), (timed_out.ok, timed_out.code))
        self.assertTrue(hung_session._fetch_in_flight)
        hung_worker = hung_session._fetch_worker
        running = threading.active_count()
        fetchers = _live_fetchers()
        began = time.monotonic()
        refused = hung_session.request_proposal(request_id="h2", prompt="p")
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual((False, sess.SOURCE_TIMEOUT), (refused.ok, refused.code))
        refusal = hung_session.records()[-1]
        self.assertEqual(
            ("proposal_refused", "watch_only", False, sess.SOURCE_TIMEOUT),
            (refusal["kind"], refusal["data"]["fail_closed_to"],
             refusal["data"]["quarantined"], refusal["data"]["reason"]))
        self.assertEqual(running, threading.active_count())
        self.assertEqual(fetchers, _live_fetchers())
        self.assertIs(hung_worker, hung_session._fetch_worker)
        self.assertEqual([], hung_session.proposals())
        self.assertNotIn("proposal_received", kinds(hung_session))
        sess.OperatorSession.replay(hung_session.records(), source=ScriptedSource())


def _live_fetchers() -> int:
    return sum(
        1 for item in threading.enumerate()
        if item.name == "proposal-fetch" and item.is_alive()
    )


class _HungThenAccepted:
    """Blocks in fetch, then returns an accepted proposal once released."""

    adapter_kind = "fake"

    def __init__(self) -> None:
        self.release = threading.Event()
        self.returned = threading.Event()

    def fetch(self, *, request_id: str, mode: str, prompt: str):
        self.release.wait()
        try:
            return accepted(proposal_payload())
        finally:
            self.returned.set()


class _Returns:
    adapter_kind = "fake"

    def __init__(self, outcome: object) -> None:
        self._outcome = outcome

    def fetch(self, *, request_id: str, mode: str, prompt: str) -> object:
        return self._outcome


class _RaisingDisposition:
    reason = "ok"

    @property
    def disposition(self) -> str:
        raise RuntimeError("disposition blew up")


if __name__ == "__main__":
    unittest.main(verbosity=2)
