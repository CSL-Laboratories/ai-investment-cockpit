"""Gemini kill (B12/B28, F44): cancel working orders, OAuth-grant revoke. Mocks only."""

from __future__ import annotations

import unittest
from pathlib import Path

from cockpit.paper.gemini import REVOKE_ENDPOINT, GeminiKill
from cockpit.paper.revoke import RevokeReply

PAPER_DIR = Path(__file__).resolve().parents[1]
NO_AMBIENT = (r"(?m)os\.environ|getenv|\bopen\(|^\s*(import|from)\s+"
              r"(os|socket|ssl|urllib|http|requests|keyring|subprocess)\b")
UNAUTH = {"revoke_probe_api": "unauthorized", "revoke_probe_refresh": "unauthorized"}
AUTH = {"revoke_probe_api": "authorized", "revoke_probe_refresh": "authorized"}
PROBES = (("api_reply", "revoke_probe_api", "revoke_probe_refresh"),
          ("refresh_reply", "revoke_probe_refresh", "revoke_probe_api"))


class MockGemini:
    """A mock venue with state: open orders, a cancel-all, an OAuth grant. No network."""

    def __init__(self, orders=(), log=None):
        self.open = list(orders)
        self.granted = True
        self.log = log if log is not None else []
        self.cancel_error = None
        self.cancel_leaves = 0
        self.list_reply = None
        self.revoke_reply: object = RevokeReply(200)
        self.revoke_works = True
        self.refresh_live = False  # True: the refresh token stays live after API access is revoked
        self.api_reply: object = None      # an exception to raise or a reply, in place of the grant's
        self.refresh_reply: object = None  # the same, for the refresh-token exchange

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

    def probe_api_call(self):
        self.log.append("probe_api")
        if isinstance(self.api_reply, BaseException):
            raise self.api_reply
        if self.api_reply is not None:
            return self.api_reply
        return "authorized" if self.granted else "unauthorized"

    def probe_refresh_token_exchange(self):
        self.log.append("probe_refresh")
        if isinstance(self.refresh_reply, BaseException):
            raise self.refresh_reply
        if self.refresh_reply is not None:
            return self.refresh_reply
        return "authorized" if self.granted or self.refresh_live else "unauthorized"


class GeminiKillTests(unittest.TestCase):
    def _kill(self, venue):
        return GeminiKill(lambda: venue.log.append("halt"), venue)

    def test_halt_then_cancel_then_confirm_by_listing_then_revoke(self) -> None:
        venue = MockGemini(orders=["g1", "g2"])
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
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
        cases = ((dict(revoke_works=False), AUTH),
                 (dict(revoke_works=False, revoke_reply=RevokeReply(400)), {"revoke_grant": "rejected", **AUTH}),
                 (dict(revoke_works=False, revoke_reply=ConnectionError()),
                  {"revoke_grant": "error:ConnectionError", **AUTH}))
        for attrs, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockGemini()
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], detail)

    def test_a_200_confirms_only_with_both_probes_unauthorized(self) -> None:
        """Option A: the 200 is ignored; the API call and the refresh exchange must both be refused."""
        cases = (
            # The venue revoked API access but kept the refresh token live.
            (dict(refresh_live=True), "pending",
             {"revoke_probe_api": "unauthorized", "revoke_probe_refresh": "authorized"}),
            (dict(api_reply="authorized"), "pending",
             {"revoke_probe_api": "authorized", "revoke_probe_refresh": "unauthorized"}),
            # A 200 for a revoke that changed nothing: the 200 alone is not proof.
            (dict(revoke_works=False), "pending", AUTH),
            (dict(), "confirmed", UNAUTH),
        )
        for attrs, state, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockGemini(orders=["g1"])
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual(venue.revoke_reply, RevokeReply(200))
                self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 (state, "confirmed", state))
                self.assertEqual(out["detail"], detail)

    def test_each_probe_key_uses_the_closed_set_on_its_own(self) -> None:
        """A probe result never mixes into the revoke detail or the other probe's key."""
        replies = (*((reply, "unreadable_probe") for reply in
                     # Any undeclared reply is unreadable; no prefix of it is stored.
                     ("x" * 40, "confirmed", "cancelled", "UNAUTHORIZED", "unauthorized ",
                      {"unauthorized": True}, 401)),
                   (TimeoutError(), "error:TimeoutError"), (ValueError("unauthorized"), "error:ValueError"))
        for attr, key, other in PROBES:
            for reply, stored in replies:
                with self.subTest(attr=attr, reply=reply):
                    venue = MockGemini(orders=["g1"])
                    setattr(venue, attr, reply)
                    out = self._kill(venue).kill()
                    # One probe raising or unreadable never skips the other.
                    self.assertEqual(venue.log[-2:], ["probe_api", "probe_refresh"])
                    self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                     ("pending", "confirmed", "pending"))
                    self.assertEqual(out["detail"], {key: stored, other: "unauthorized"})

    def test_each_probe_overwrites_the_last_probe_result(self) -> None:
        venue = MockGemini()
        venue.revoke_works = False  # the grant stays, so each kill can cancel and list
        venue.api_reply = TimeoutError()
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["detail"],
                         {"revoke_probe_api": "error:TimeoutError", "revoke_probe_refresh": "authorized"})
        venue.api_reply = None
        self.assertEqual(kill.kill()["detail"], AUTH)
        venue.revoke_works = True
        venue.log.clear()
        out = kill.kill()
        # The retry runs both probes again and confirms once both are unauthorized.
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["revoke_grant"], out["detail"]), ("confirmed", "confirmed", UNAUTH))

    def test_retry_after_a_live_refresh_token_confirms_the_revoke(self) -> None:
        venue = MockGemini(orders=["g1"])
        venue.refresh_live = True
        kill = self._kill(venue)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]), ("pending", "confirmed", "pending"))
        venue.refresh_live = False  # the venue has now dropped the refresh token too
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual(out["revoke_grant"], "confirmed")
        # The revoke was pending, so the cancel ran again, and API access was already gone:
        # the cancel cannot be confirmed without the host re-authorising (a gap listed in the PR).
        self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
        self.assertEqual(out["detail"], {"cancel_all": "error:PermissionError", **UNAUTH})

    def test_unauthorized_probes_after_a_failed_revoke_confirm_nothing(self) -> None:
        """Finding 3: confirm only when the revoke answered 200 and both probes are unauthorized."""
        class ProbesUnauthorized(MockGemini):
            def probe_api_call(self):
                self.log.append("probe_api")
                return "unauthorized"

            def probe_refresh_token_exchange(self):
                self.log.append("probe_refresh")
                return "unauthorized"
        for reply, detail in ((RevokeReply(400), "rejected"), (RevokeReply(503), "rejected"),
                              (RevokeReply(204), "rejected"), (RevokeReply(True), "rejected"),
                              (RevokeReply("200"), "rejected"),
                              (ConnectionError(), "error:ConnectionError"),
                              # The old dict contract, a bare status and no reply are not a RevokeReply.
                              ({"accepted": True}, "unreadable_response"), (200, "unreadable_response"),
                              (None, "unreadable_response")):
            with self.subTest(reply=reply):
                venue = ProbesUnauthorized(orders=["g1"])
                venue.revoke_works = False  # the grant still lists and cancels; only the probes say no
                venue.revoke_reply = reply
                out = self._kill(venue).kill()
                self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], {"revoke_grant": detail, **UNAUTH})

    def test_a_200_drops_an_earlier_revoke_detail(self) -> None:
        venue = MockGemini()
        venue.revoke_works = False
        venue.revoke_reply = RevokeReply(503)
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["detail"], {"revoke_grant": "rejected", **AUTH})
        venue.revoke_reply = RevokeReply(200)
        self.assertEqual(kill.kill()["detail"], AUTH)

    def test_revoke_endpoint_is_the_rfc_7009_one(self) -> None:
        self.assertEqual(REVOKE_ENDPOINT, "https://exchange.gemini.com/auth/token/revoke")
        source = PAPER_DIR.joinpath("gemini.py").read_text(encoding="utf-8")
        self.assertNotIn("revokeByToken", source)
        self.assertNotIn("probe_authenticated", source)

    def test_cancel_timeout_still_revokes_after_it_and_stays_pending(self) -> None:
        """O70: halt, cancel, revoke in one kill; a confirmed revoke never confirms a timed-out cancel."""
        venue = MockGemini(orders=["g1"])
        venue.cancel_error = TimeoutError()
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "cancel_all", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "error:TimeoutError", **UNAUTH})
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
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])

    def test_halt_failure_still_attempts_risk_reducing_calls(self) -> None:
        venue = MockGemini(orders=["g1"])
        def broken_halt():
            raise OSError("disk")
        out = GeminiKill(broken_halt, venue).kill()
        self.assertEqual(venue.log, ["cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
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
                self.assertEqual(out["detail"], UNAUTH)

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
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"], out["revoke_grant"]),
                         ("pending", "pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"halt": "error:OSError", "cancel_all": "halt_failed",
                                         **UNAUTH})
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
        self.assertEqual(out["detail"], {"cancel_all": "remaining:1", **UNAUTH})
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
        self.assertEqual(venue.log, ["halt", "cancel_all", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]), ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "remaining:1", **UNAUTH})

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
        self.assertEqual(out["detail"], UNAUTH)

    def test_successful_halt_drops_the_stored_halt_error(self) -> None:
        venue = MockGemini()
        venue.revoke_works = False  # the grant stays, so the second kill can cancel and list
        kill = GeminiKill(self._failing_once_halt(venue), venue)
        out = kill.kill()
        self.assertEqual((out["halt"], out["detail"]["halt"]), ("pending", "error:OSError"))
        venue.revoke_works = True
        out = kill.kill()
        self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
        self.assertEqual(out["detail"], UNAUTH)

    def test_no_ambient_or_network_code(self) -> None:
        source = PAPER_DIR.joinpath("gemini.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, NO_AMBIENT)


if __name__ == "__main__":
    unittest.main()
