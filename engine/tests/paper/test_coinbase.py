"""Coinbase kill (B12/B28, F44): list and cancel each order, OAuth-grant revoke. Mocks only."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from cockpit.paper.coinbase import MAX_LIST_PAGES, CoinbaseKill
from cockpit.paper.gateway import PaperBook, create_book
from cockpit.paper.revoke import RevokeReply

PAPER_DIR = Path(__file__).resolve().parents[2] / "cockpit" / "paper"
NO_AMBIENT = (r"(?m)os\.environ|getenv|\bopen\(|^\s*(import|from)\s+"
              r"(os|socket|ssl|urllib|http|requests|keyring|subprocess)\b")
UNAUTH = {"revoke_probe_api": "unauthorized", "revoke_probe_refresh": "unauthorized"}
AUTH = {"revoke_probe_api": "authorized", "revoke_probe_refresh": "authorized"}
PROBES = (("api_reply", "revoke_probe_api", "revoke_probe_refresh"),
          ("refresh_reply", "revoke_probe_refresh", "revoke_probe_api"))


class MockCoinbase:
    """A mock venue with state: open orders, per-order cancel, an OAuth grant. No network."""

    def __init__(self, orders=(), page_size=2, log=None):
        self.open = list(orders)
        self.page_size = page_size
        self.granted = True
        self.log = log if log is not None else []
        self.fail_cancel: dict[str, object] = {}  # order_id -> exception or reply
        self.list_error = None
        self.revoke_reply: object = RevokeReply(200)
        self.revoke_works = True
        self.refresh_live = False  # True: the refresh token stays live after API access is revoked
        self.api_reply: object = None      # an exception to raise or a reply, in place of the grant's
        self.refresh_reply: object = None  # the same, for the refresh-token exchange
        self.on_list = None

    def _auth(self):
        if not self.granted:
            raise PermissionError("unauthorized")

    def list_open_orders(self, cursor):
        self.log.append("list")
        self._auth()
        if self.on_list:
            self.on_list()
        if self.list_error is not None:
            raise self.list_error
        start = int(cursor or 0)
        page = self.open[start:start + self.page_size]
        nxt = start + self.page_size
        return {"order_ids": list(page), "cursor": str(nxt) if nxt < len(self.open) else None}

    def cancel_order(self, order_id):
        self.log.append("cancel:" + order_id)
        self._auth()
        outcome = self.fail_cancel.get(order_id)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            return outcome
        self.open.remove(order_id)
        return {"order_id": order_id, "success": True}

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


def _halting(log, errors=()):
    """The test's own halt flag: a halt callable that logs, raises each given error once, then
    sets the flag; and the read-back of that flag."""
    errors, flag = list(errors), []
    def halt():
        log.append("halt")
        if errors:
            raise errors.pop()
        flag.append(True)
    return halt, lambda: bool(flag)


class CoinbaseKillTests(unittest.TestCase):
    def _kill(self, venue, errors=()):
        halt, halted = _halting(venue.log, errors)
        return CoinbaseKill(halt, venue, halted=halted)

    def test_halt_then_list_and_cancel_each_then_revoke_confirmed(self) -> None:
        venue = MockCoinbase(orders=["o1", "o2", "o3"])
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "list", "list", "cancel:o1", "cancel:o2", "cancel:o3",
                                     "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["venue"], out["status"], out["halt"], out["cancel_orders"], out["revoke_grant"]),
                         ("coinbase", "confirmed", "confirmed", "confirmed", "confirmed"))
        self.assertEqual(out["cancelled"], ["o1", "o2", "o3"])
        self.assertEqual(venue.open, [])
        # Post-kill: an authenticated call fails.
        with self.assertRaises(PermissionError):
            venue.list_open_orders(None)

    def test_no_cancel_all_operation_is_used(self) -> None:
        source = PAPER_DIR.joinpath("coinbase.py").read_text(encoding="utf-8")
        self.assertNotIn("cancel_all", source)
        self.assertNotRegex(source, NO_AMBIENT)

    def test_cancelled_only_on_venue_confirm_and_one_failure_never_skips_others(self) -> None:
        for reply in (TimeoutError(), {"order_id": "o2", "success": False}, {"order_id": "zz", "success": True},
                      {"success": "true", "order_id": "o2"}, "garbage"):
            with self.subTest(reply=reply):
                venue = MockCoinbase(orders=["o1", "o2", "o3"])
                venue.fail_cancel["o2"] = reply
                out = self._kill(venue).kill()
                self.assertIn("cancel:o3", venue.log)
                self.assertEqual(out["cancelled"], ["o1", "o3"])
                self.assertEqual((out["status"], out["cancel_orders"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_orders"], "unconfirmed:1")
                self.assertIn("revoke", venue.log)  # the revoke still runs

    def test_order_appearing_after_listing_keeps_cancel_pending(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        calls = []
        def sneak():
            calls.append(1)
            if len(calls) == 2:
                venue.open.append("late")
        venue.on_list = sneak
        out = self._kill(venue).kill()
        self.assertEqual((out["status"], out["cancel_orders"]), ("pending", "pending"))
        self.assertEqual(out["detail"]["cancel_orders"], "remaining:1")

    def test_list_failures_are_pending_and_still_revoke(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        venue.list_error = ConnectionError()
        out = self._kill(venue).kill()
        self.assertEqual(out["detail"]["cancel_orders"], "list_error:ConnectionError")
        self.assertEqual((out["status"], out["revoke_grant"]), ("pending", "confirmed"))

        class Endless(MockCoinbase):
            def list_open_orders(self, cursor):
                return {"order_ids": ["o"], "cursor": "more"}
        out = self._kill(Endless()).kill()
        self.assertEqual(out["detail"]["cancel_orders"], "list_page_cap")
        self.assertGreater(MAX_LIST_PAGES, 1)

        class Garbled(MockCoinbase):
            def list_open_orders(self, cursor):
                return {"order_ids": "o1"}
        self.assertEqual(self._kill(Garbled()).kill()["detail"]["cancel_orders"], "unreadable_list")

    def test_revoke_confirmed_only_when_post_kill_call_fails(self) -> None:
        cases = ((dict(revoke_works=False), AUTH),
                 (dict(revoke_works=False, revoke_reply=RevokeReply(400)), {"revoke_grant": "rejected", **AUTH}),
                 (dict(revoke_works=False, revoke_reply=ConnectionError()),
                  {"revoke_grant": "error:ConnectionError", **AUTH}))
        for attrs, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockCoinbase()
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
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
                venue = MockCoinbase(orders=["o1"])
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual(venue.revoke_reply, RevokeReply(200))
                self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "list", "revoke",
                                             "probe_api", "probe_refresh"])
                self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
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
                    venue = MockCoinbase(orders=["o1"])
                    setattr(venue, attr, reply)
                    out = self._kill(venue).kill()
                    # One probe raising or unreadable never skips the other.
                    self.assertEqual(venue.log[-2:], ["probe_api", "probe_refresh"])
                    self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                                     ("pending", "confirmed", "pending"))
                    self.assertEqual(out["detail"], {key: stored, other: "unauthorized"})

    def test_each_probe_overwrites_the_last_probe_result(self) -> None:
        venue = MockCoinbase()
        venue.revoke_works = False  # the grant stays, so each kill can list
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
        self.assertEqual(venue.log, ["halt", "list", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["revoke_grant"], out["detail"]), ("confirmed", "confirmed", UNAUTH))

    def test_retry_after_a_live_refresh_token_confirms_the_revoke(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        venue.refresh_live = True
        kill = self._kill(venue)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending"))
        venue.refresh_live = False  # the venue has now dropped the refresh token too
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual(out["revoke_grant"], "confirmed")
        # The revoke was pending, so the listing ran again, and API access was already gone:
        # the cancel cannot be confirmed without the host re-authorising (a gap listed in the PR).
        self.assertEqual((out["status"], out["cancel_orders"]), ("pending", "pending"))
        self.assertEqual(out["detail"], {"cancel_orders": "list_error:PermissionError", **UNAUTH})

    def test_unauthorized_probes_after_a_failed_revoke_confirm_nothing(self) -> None:
        """Finding 3: confirm only when the revoke answered 200 and both probes are unauthorized."""
        class ProbesUnauthorized(MockCoinbase):
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
                venue = ProbesUnauthorized(orders=["o1"])
                venue.revoke_works = False  # the grant still lists and cancels; only the probes say no
                venue.revoke_reply = reply
                out = self._kill(venue).kill()
                self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "list", "revoke",
                                             "probe_api", "probe_refresh"])
                self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], {"revoke_grant": detail, **UNAUTH})

    def test_a_200_drops_an_earlier_revoke_detail(self) -> None:
        venue = MockCoinbase()
        venue.revoke_works = False
        venue.revoke_reply = RevokeReply(503)
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["detail"], {"revoke_grant": "rejected", **AUTH})
        venue.revoke_reply = RevokeReply(200)
        self.assertEqual(kill.kill()["detail"], AUTH)

    def test_cancel_timeout_still_revokes_after_it_and_stays_pending(self) -> None:
        """O70: halt, cancel, revoke in one kill; a confirmed revoke never confirms a timed-out cancel."""
        venue = MockCoinbase(orders=["o1", "o2"])
        venue.fail_cancel["o2"] = TimeoutError()
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "cancel:o2", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["halt"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "unconfirmed:1", **UNAUTH})
        self.assertEqual((out["cancelled"], venue.open), (["o1"], ["o2"]))
        # The same when the listing itself times out: nothing cancelled, the revoke still runs.
        venue = MockCoinbase(orders=["o1"])
        venue.list_error = TimeoutError()
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "list_error:TimeoutError", **UNAUTH})

    def test_retry_cancels_what_is_left_and_revokes_and_probes_again(self) -> None:
        venue = MockCoinbase(orders=["o1", "o2"])
        venue.fail_cancel["o2"] = TimeoutError()
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "pending")
        del venue.fail_cancel["o2"]
        # The first pass confirmed the revoke, so a retry needs the host to re-authorise
        # the mock for cleanup (a gap listed in the PR); it must not re-cancel o1.
        venue.granted = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(out["status"], "confirmed")
        # The earlier revoke confirm is not kept: this kill revokes and probes for itself.
        self.assertEqual(venue.log, ["halt", "list", "cancel:o2", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual(out["cancelled"], ["o1", "o2"])
        self.assertEqual(out["revoke_evidence"], {"http_status": 200, "probe_api": "unauthorized",
                                                  "probe_refresh": "unauthorized"})

    def test_success_reply_but_still_listed_is_retried(self) -> None:
        """A venue that still lists the order wins over its reply."""
        venue = MockCoinbase(orders=["o1", "o2"])
        venue.fail_cancel["o2"] = {"order_id": "o2", "success": True}  # says yes, stays open
        kill = self._kill(venue)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_orders"]), ("pending", "pending"))
        self.assertEqual(out["detail"]["cancel_orders"], "remaining:1")
        self.assertEqual(out["cancelled"], ["o1"])  # never names an order the venue lists open
        self.assertEqual(venue.open, ["o2"])
        # Next kill cancels o2 again rather than skipping it.
        del venue.fail_cancel["o2"]
        venue.granted = True  # grant still live, so this retry can list and cancel
        venue.log.clear()
        out = kill.kill()
        self.assertIn("cancel:o2", venue.log)
        self.assertEqual((out["status"], out["cancel_orders"], out["cancelled"]), ("confirmed", "confirmed", ["o1", "o2"]))
        self.assertEqual(venue.open, [])

    def test_still_listed_every_time_never_confirms(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        venue.fail_cancel["o1"] = {"order_id": "o1", "success": True}
        kill = self._kill(venue)
        for _ in range(3):
            venue.granted = True
            venue.log.clear()
            out = kill.kill()
            self.assertIn("cancel:o1", venue.log)
            self.assertEqual((out["status"], out["cancelled"], out["detail"]["cancel_orders"]),
                             ("pending", [], "remaining:1"))

    def test_halt_failure_still_attempts_risk_reducing_calls(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        def broken_halt():
            raise OSError("disk")
        out = CoinbaseKill(broken_halt, venue).kill()
        self.assertEqual(venue.log, ["list", "cancel:o1", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["halt"], out["detail"]["halt"]), ("pending", "pending", "error:OSError"))

    def test_cancel_confirm_is_not_kept_when_the_halt_call_failed(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        kill = self._kill(venue, errors=[OSError("disk")])
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["halt"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"halt": "error:OSError", "cancel_orders": "halt_failed",
                                         **UNAUTH})
        # An order placed while the halt was not set: the next kill lists and cancels again.
        venue.open = ["c-new"]
        venue.fail_cancel["c-new"] = {"order_id": "c-new", "success": True}  # says yes, stays open
        # The first pass confirmed the revoke, so the retry needs the host to re-authorise
        # the mock (a gap listed in the PR).
        venue.granted = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:c-new", "list", "revoke", "probe_api", "probe_refresh"])
        # The revoke was confirmed before this cancel: still pending, with its own typed reason.
        self.assertEqual((out["status"], out["halt"], out["cancel_orders"]),
                         ("pending", "confirmed", "pending_credential_revoked"))
        self.assertEqual(out["detail"], {"cancel_orders": "remaining:1", **UNAUTH})
        self.assertEqual(venue.open, ["c-new"])

    def test_pending_revoke_lists_and_cancels_again(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        venue.revoke_works = False
        kill = self._kill(venue)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending"))
        # The grant still worked, so an order could be placed after the first listing.
        venue.open = ["late"]
        venue.fail_cancel["late"] = {"order_id": "late", "success": True}  # says yes, stays open
        venue.revoke_works = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:late", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "remaining:1", **UNAUTH})

    def test_second_kill_revokes_and_probes_again_and_keeps_the_cancel_only_on_a_dead_grant(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "confirmed")
        # The revoked grant can no longer list; that must not demote the cancel.
        with self.assertRaises(PermissionError):
            venue.list_open_orders(None)
        venue.log.clear()
        out = kill.kill()
        # Every leg is tried again: the halt, the listing, and a fresh revoke with both probes.
        self.assertEqual(venue.log, ["halt", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("confirmed", "confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "kept:list_error:PermissionError", **UNAUTH})
        self.assertEqual(out["revoke_evidence"], {"http_status": 200, "probe_api": "unauthorized",
                                                  "probe_refresh": "unauthorized"})

    def test_second_kill_does_not_confirm_on_the_first_kills_probes(self) -> None:
        """A confirmed kill, then the grant works again: the next kill must see it and say pending."""
        for attr, key in PROBES[0][:2], PROBES[1][:2]:
            with self.subTest(probe=key):
                venue = MockCoinbase(orders=["o1"])
                kill = self._kill(venue)
                self.assertEqual(kill.kill()["status"], "confirmed")
                setattr(venue, attr, "authorized")
                venue.log.clear()
                out = kill.kill()
                self.assertEqual(venue.log[-3:], ["revoke", "probe_api", "probe_refresh"])
                self.assertEqual((out["status"], out["revoke_grant"], out["detail"][key]),
                                 ("pending", "pending", "authorized"))
                # The earlier cancel confirm does not stand on a grant that is not proven dead.
                self.assertEqual((out["cancel_orders"], out["detail"]["cancel_orders"]),
                                 ("pending_credential_revoked", "list_error:PermissionError"))
                self.assertEqual(out["revoke_evidence"][key.replace("revoke_", "")], "authorized")
        # The grant works again and lists a new order: cancelled in that kill, and not kept after it.
        venue = MockCoinbase(orders=["o1"])
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "confirmed")
        venue.granted, venue.revoke_works, venue.open = True, False, ["late-1"]
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:late-1", "list", "revoke", "probe_api", "probe_refresh"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending"))
        self.assertEqual(out["detail"], AUTH)
        # A revoke that answers anything but 200 on a later kill is pending too, probes unauthorized or not.
        venue = MockCoinbase(orders=["o1"])
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "confirmed")
        venue.revoke_reply = RevokeReply(503)
        out = kill.kill()
        self.assertEqual((out["status"], out["revoke_grant"], out["detail"]["revoke_grant"]),
                         ("pending", "pending", "rejected"))
        self.assertEqual(out["revoke_evidence"], {"http_status": None, "probe_api": "unauthorized",
                                                  "probe_refresh": "unauthorized"})

    def test_kill_is_not_confirmed_when_its_latest_halt_read_fails(self) -> None:
        """The halt leg is read again on every kill; an earlier confirmed read is not kept."""
        def raising() -> bool:
            raise OSError("disk")
        for label, read, detail in (("not_halted", lambda: False, "not_halted"),
                                    ("read_error", raising, "read_error:OSError")):
            with self.subTest(read=label):
                venue = MockCoinbase(orders=["o1"])
                reads = [lambda: True, read, lambda: True]
                kill = CoinbaseKill(lambda: None, venue, halted=lambda: reads.pop(0)())
                self.assertEqual(kill.kill()["status"], "confirmed")
                out = kill.kill()
                self.assertEqual((out["status"], out["halt"], out["detail"]["halt"]), ("pending", "pending", detail))
                self.assertEqual((out["cancel_orders"], out["revoke_grant"]), ("confirmed", "confirmed"))
                out = kill.kill()
                self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
                self.assertNotIn("halt", out["detail"])

    def test_successful_halt_drops_the_stored_halt_error(self) -> None:
        venue = MockCoinbase()
        venue.revoke_works = False  # the grant stays, so the second kill can list
        kill = self._kill(venue, errors=[OSError("disk")])
        out = kill.kill()
        self.assertEqual((out["halt"], out["detail"]["halt"]), ("pending", "error:OSError"))
        venue.revoke_works = True
        out = kill.kill()
        self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
        self.assertEqual(out["detail"], UNAUTH)

    def test_kill_halts_the_paper_book(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-cb-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        create_book(path)
        book = PaperBook(path)
        self.addCleanup(book.close)
        out = CoinbaseKill(book.halt, MockCoinbase(orders=["o1"]), halted=book.halted).kill()
        self.assertEqual(out["status"], "confirmed")
        self.assertTrue(book.halted())


if __name__ == "__main__":
    unittest.main()
