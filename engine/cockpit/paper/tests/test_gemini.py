"""Gemini kill (B12/B28, F44): cancel working orders, OAuth-grant revoke. Mocks only."""

from __future__ import annotations

import unittest
from pathlib import Path

from cockpit.paper.gemini import GeminiKill

PAPER_DIR = Path(__file__).resolve().parents[1]
NO_AMBIENT = (r"(?m)os\.environ|getenv|\bopen\(|^\s*(import|from)\s+"
              r"(os|socket|ssl|urllib|http|requests|keyring|subprocess)\b")


class MockGemini:
    """A mock venue with state: open orders, a cancel-all, an OAuth grant. No network."""

    def __init__(self, orders=(), log=None):
        self.open = list(orders)
        self.granted = True
        self.log = log if log is not None else []
        self.cancel_error = None
        self.cancel_leaves = 0
        self.list_reply = None
        self.revoke_reply: object = {"accepted": True}
        self.revoke_works = True
        self.probe_reply: object = None  # an exception to raise or a reply, in place of the grant's

    def _auth(self):
        if not self.granted:
            raise PermissionError("unauthorized")

    def cancel_all(self):
        self.log.append("cancel_all")
        self._auth()
        if self.cancel_error is not None:
            raise self.cancel_error
        done = self.open[self.cancel_leaves:]
        self.open = self.open[:self.cancel_leaves]
        return {"cancelled": done}

    def list_open_orders(self):
        self.log.append("list")
        self._auth()
        return {"order_ids": list(self.open)} if self.list_reply is None else self.list_reply

    def revoke_oauth_grant(self):
        self.log.append("revoke")
        if isinstance(self.revoke_reply, BaseException):
            raise self.revoke_reply
        if self.revoke_works:
            self.granted = False
        return self.revoke_reply

    def probe_authenticated(self):
        self.log.append("probe")
        if isinstance(self.probe_reply, BaseException):
            raise self.probe_reply
        if self.probe_reply is not None:
            return self.probe_reply
        return "authorized" if self.granted else "unauthorized"


class GeminiKillTests(unittest.TestCase):
    def _kill(self, venue):
        return GeminiKill(lambda: venue.log.append("halt"), venue)

    def test_halt_then_cancel_then_confirm_by_listing_then_revoke(self) -> None:
        venue = MockGemini(orders=["g1", "g2"])
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe"])
        self.assertEqual((out["venue"], out["status"], out["halt"], out["cancel_all"], out["revoke_grant"]),
                         ("gemini", "confirmed", "confirmed", "confirmed", "confirmed"))
        self.assertEqual(venue.open, [])
        with self.assertRaises(PermissionError):
            venue.list_open_orders()

    def test_cancel_reply_alone_is_not_confirmation(self) -> None:
        cases = ((dict(cancel_leaves=1), "remaining:1"),
                 (dict(list_reply={}), "unreadable_list"),
                 (dict(list_reply={"order_ids": [3]}), "unreadable_list"),
                 (dict(cancel_error=TimeoutError()), "error:TimeoutError"))
        for attrs, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockGemini(orders=["g1", "g2"])
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_all"], detail)
                self.assertIn("revoke", venue.log)  # a cancel failure never skips the revoke

    def test_revoke_confirmed_only_when_post_kill_call_fails(self) -> None:
        cases = ((dict(revoke_works=False), {"revoke_probe": "authorized"}),
                 (dict(revoke_works=False, revoke_reply={"accepted": False}),
                  {"revoke_grant": "rejected", "revoke_probe": "authorized"}),
                 (dict(revoke_works=False, revoke_reply=ConnectionError()),
                  {"revoke_grant": "error:ConnectionError", "revoke_probe": "authorized"}))
        for attrs, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockGemini()
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], detail)

    def test_last_probe_is_reported_under_its_own_key(self) -> None:
        """The probe result never mixes into the revoke attempt's detail."""
        cases = ((dict(revoke_works=False), "pending", {"revoke_probe": "authorized"}),
                 (dict(revoke_reply={"accepted": False}), "pending",
                  {"revoke_grant": "rejected", "revoke_probe": "unauthorized"}),
                 (dict(probe_reply=TimeoutError()), "pending", {"revoke_probe": "error:TimeoutError"}),
                 (dict(probe_reply={"unauthorized": True}), "pending", {"revoke_probe": "unreadable_probe"}),
                 # Any undeclared string is unreadable; no prefix of it is stored.
                 *((dict(probe_reply=reply), "pending", {"revoke_probe": "unreadable_probe"})
                   for reply in ("x" * 40, "confirmed", "cancelled", "UNAUTHORIZED", "unauthorized ")),
                 (dict(), "confirmed", {"revoke_probe": "unauthorized"}))
        for attrs, state, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockGemini(orders=["g1"])
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 (state, "confirmed", state))
                self.assertEqual(out["detail"], detail)

    def test_each_probe_overwrites_the_last_probe_result(self) -> None:
        venue = MockGemini()
        venue.revoke_works = False  # the grant stays, so each kill can cancel and list
        venue.probe_reply = TimeoutError()
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["detail"], {"revoke_probe": "error:TimeoutError"})
        venue.probe_reply = None
        self.assertEqual(kill.kill()["detail"], {"revoke_probe": "authorized"})
        venue.revoke_works = True
        out = kill.kill()
        self.assertEqual((out["status"], out["detail"]), ("confirmed", {"revoke_probe": "unauthorized"}))

    def test_unauthorized_probe_after_a_failed_revoke_confirms_nothing(self) -> None:
        """Finding 3: confirm only when the revoke was accepted and the probe is unauthorized."""
        class ProbeUnauthorized(MockGemini):
            def probe_authenticated(self):
                self.log.append("probe")
                return "unauthorized"
        for reply, detail in (({"accepted": False}, "rejected"),
                              (ConnectionError(), "error:ConnectionError"),
                              ({}, "unreadable_response"), ({"accepted": 1}, "unreadable_response"),
                              (None, "unreadable_response")):
            with self.subTest(reply=reply):
                venue = ProbeUnauthorized(orders=["g1"])
                venue.revoke_works = False  # the grant still lists and cancels; only the probe says no
                venue.revoke_reply = reply
                out = self._kill(venue).kill()
                self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe"])
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], {"revoke_grant": detail, "revoke_probe": "unauthorized"})

    def test_cancel_timeout_still_revokes_after_it_and_stays_pending(self) -> None:
        """O70: halt, cancel, revoke in one kill; a confirmed revoke never confirms a timed-out cancel."""
        venue = MockGemini(orders=["g1"])
        venue.cancel_error = TimeoutError()
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "error:TimeoutError", "revoke_probe": "unauthorized"})
        self.assertEqual(venue.open, ["g1"])

    def test_retry_only_what_is_not_confirmed(self) -> None:
        venue = MockGemini(orders=["g1"])
        venue.revoke_works = False
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "pending")
        venue.revoke_works = True
        venue.log.clear()
        self.assertEqual(kill.kill()["status"], "confirmed")
        # The revoke was pending, so the cancel and the list run again before it.
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe"])

    def test_halt_failure_still_attempts_risk_reducing_calls(self) -> None:
        venue = MockGemini(orders=["g1"])
        def broken_halt():
            raise OSError("disk")
        out = GeminiKill(broken_halt, venue).kill()
        self.assertEqual(venue.log, ["cancel_all", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"]), ("pending", "pending"))

    def test_empty_list_that_is_not_finished_keeps_cancel_pending(self) -> None:
        """An empty page that says more may follow confirms nothing."""
        for extra in ({"cursor": "page-2"}, {"cursor": ""}, {"cursor": 0}, {"truncated": True},
                      {"truncated": None}, {"truncated": 1}, {"remaining": 2}, {"remaining": "0"},
                      {"remaining": None}, {"remaining": False}):
            with self.subTest(extra=extra):
                venue = MockGemini(orders=["g1"])
                venue.list_reply = {"order_ids": [], **extra}
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_all"], "unfinished_list")
                self.assertIn("revoke", venue.log)  # an unfinished list never skips the revoke

    def test_finished_empty_list_confirms(self) -> None:
        for payload in ({"order_ids": []}, {"order_ids": [], "cursor": None},
                        {"order_ids": [], "truncated": False}, {"order_ids": [], "remaining": 0},
                        {"order_ids": [], "cursor": None, "truncated": False, "remaining": 0}):
            with self.subTest(payload=payload):
                venue = MockGemini(orders=["g1"])
                venue.list_reply = payload
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("confirmed", "confirmed"))
                self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})

    def _failing_once_halt(self, venue):
        errors = [OSError("disk")]
        def halt():
            venue.log.append("halt")
            if errors:
                raise errors.pop()
        return halt

    def test_cancel_confirm_is_not_kept_when_the_halt_call_failed(self) -> None:
        venue = MockGemini(orders=["g1"])
        kill = GeminiKill(self._failing_once_halt(venue), venue)
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"], out["revoke_grant"]),
                         ("pending", "pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"halt": "error:OSError", "cancel_all": "halt_failed",
                                         "revoke_probe": "unauthorized"})
        # An order placed while the halt was not set: the next kill cancels and lists again.
        venue.open = ["g-new"]
        venue.cancel_leaves = 1  # the venue still lists it after the cancel
        # The first pass confirmed the revoke, so the retry needs the host to re-authorise
        # the mock (a gap listed in the PR).
        venue.granted = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "list"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"]), ("pending", "confirmed", "pending"))
        self.assertEqual(out["detail"], {"cancel_all": "remaining:1", "revoke_probe": "unauthorized"})
        self.assertEqual(venue.open, ["g-new"])

    def test_pending_revoke_cancels_and_lists_again(self) -> None:
        venue = MockGemini(orders=["g1"])
        venue.revoke_works = False
        kill = self._kill(venue)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]), ("pending", "confirmed", "pending"))
        # The grant still worked, so an order could be placed after the first listing.
        venue.open = ["late"]
        venue.cancel_leaves = 1  # the venue still lists it after the cancel
        venue.revoke_works = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]), ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "remaining:1", "revoke_probe": "unauthorized"})

    def test_confirmed_cancel_and_revoke_are_not_retried(self) -> None:
        venue = MockGemini(orders=["g1"])
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "confirmed")
        # The revoked grant can no longer cancel or list; that must not demote the cancel.
        with self.assertRaises(PermissionError):
            venue.list_open_orders()
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                         ("confirmed", "confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})

    def test_successful_halt_drops_the_stored_halt_error(self) -> None:
        venue = MockGemini()
        venue.revoke_works = False  # the grant stays, so the second kill can cancel and list
        kill = GeminiKill(self._failing_once_halt(venue), venue)
        out = kill.kill()
        self.assertEqual((out["halt"], out["detail"]["halt"]), ("pending", "error:OSError"))
        venue.revoke_works = True
        out = kill.kill()
        self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})

    def test_no_ambient_or_network_code(self) -> None:
        source = PAPER_DIR.joinpath("gemini.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, NO_AMBIENT)


if __name__ == "__main__":
    unittest.main()
