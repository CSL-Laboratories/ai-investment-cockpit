"""Coinbase kill (B12/B28, F44): list and cancel each order, OAuth-grant revoke. Mocks only."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from cockpit.paper.coinbase import MAX_LIST_PAGES, CoinbaseKill
from cockpit.paper.gateway import PaperBook, create_book

PAPER_DIR = Path(__file__).resolve().parents[1]
NO_AMBIENT = (r"(?m)os\.environ|getenv|\bopen\(|^\s*(import|from)\s+"
              r"(os|socket|ssl|urllib|http|requests|keyring|subprocess)\b")


class MockCoinbase:
    """A mock venue with state: open orders, per-order cancel, an OAuth grant. No network."""

    def __init__(self, orders=(), page_size=2, log=None):
        self.open = list(orders)
        self.page_size = page_size
        self.granted = True
        self.log = log if log is not None else []
        self.fail_cancel: dict[str, object] = {}  # order_id -> exception or reply
        self.list_error = None
        self.revoke_reply: object = {"accepted": True}
        self.revoke_works = True
        self.probe_reply: object = None  # an exception to raise or a reply, in place of the grant's
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

    def probe_authenticated(self):
        self.log.append("probe")
        if isinstance(self.probe_reply, BaseException):
            raise self.probe_reply
        if self.probe_reply is not None:
            return self.probe_reply
        return "authorized" if self.granted else "unauthorized"


class CoinbaseKillTests(unittest.TestCase):
    def _kill(self, venue):
        return CoinbaseKill(lambda: venue.log.append("halt"), venue)

    def test_halt_then_list_and_cancel_each_then_revoke_confirmed(self) -> None:
        venue = MockCoinbase(orders=["o1", "o2", "o3"])
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "list", "list", "cancel:o1", "cancel:o2", "cancel:o3",
                                     "list", "revoke", "probe"])
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
        cases = ((dict(revoke_works=False), {"revoke_probe": "authorized"}),
                 (dict(revoke_works=False, revoke_reply={"accepted": False}),
                  {"revoke_grant": "rejected", "revoke_probe": "authorized"}),
                 (dict(revoke_works=False, revoke_reply=ConnectionError()),
                  {"revoke_grant": "error:ConnectionError", "revoke_probe": "authorized"}))
        for attrs, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockCoinbase()
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], detail)

    def test_last_probe_is_reported_under_its_own_key(self) -> None:
        """The probe result never mixes into the revoke attempt's detail."""
        cases = ((dict(revoke_works=False), "pending", {"revoke_probe": "authorized"}),
                 (dict(revoke_reply={"accepted": False}), "pending",
                  {"revoke_grant": "rejected", "revoke_probe": "unauthorized"}),
                 (dict(probe_reply=TimeoutError()), "pending", {"revoke_probe": "error:TimeoutError"}),
                 (dict(probe_reply={"unauthorized": True}), "pending", {"revoke_probe": "unreadable_probe"}),
                 (dict(probe_reply="x" * 40), "pending", {"revoke_probe": "x" * 32}),
                 (dict(), "confirmed", {"revoke_probe": "unauthorized"}))
        for attrs, state, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockCoinbase(orders=["o1"])
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                                 (state, "confirmed", state))
                self.assertEqual(out["detail"], detail)

    def test_each_probe_overwrites_the_last_probe_result(self) -> None:
        venue = MockCoinbase()
        venue.revoke_works = False  # the grant stays, so each kill can list
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
        class ProbeUnauthorized(MockCoinbase):
            def probe_authenticated(self):
                self.log.append("probe")
                return "unauthorized"
        for reply, detail in (({"accepted": False}, "rejected"),
                              (ConnectionError(), "error:ConnectionError"),
                              ({}, "unreadable_response"), ({"accepted": 1}, "unreadable_response"),
                              (None, "unreadable_response")):
            with self.subTest(reply=reply):
                venue = ProbeUnauthorized(orders=["o1"])
                venue.revoke_works = False  # the grant still lists and cancels; only the probe says no
                venue.revoke_reply = reply
                out = self._kill(venue).kill()
                self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "list", "revoke", "probe"])
                self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], {"revoke_grant": detail, "revoke_probe": "unauthorized"})

    def test_cancel_timeout_still_revokes_after_it_and_stays_pending(self) -> None:
        """O70: halt, cancel, revoke in one kill; a confirmed revoke never confirms a timed-out cancel."""
        venue = MockCoinbase(orders=["o1", "o2"])
        venue.fail_cancel["o2"] = TimeoutError()
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "cancel:o2", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "confirmed", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "unconfirmed:1", "revoke_probe": "unauthorized"})
        self.assertEqual((out["cancelled"], venue.open), (["o1"], ["o2"]))
        # The same when the listing itself times out: nothing cancelled, the revoke still runs.
        venue = MockCoinbase(orders=["o1"])
        venue.list_error = TimeoutError()
        out = self._kill(venue).kill()
        self.assertEqual(venue.log, ["halt", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "list_error:TimeoutError", "revoke_probe": "unauthorized"})

    def test_retry_only_what_is_not_confirmed(self) -> None:
        venue = MockCoinbase(orders=["o1", "o2"])
        venue.fail_cancel["o2"] = TimeoutError()
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "pending")
        del venue.fail_cancel["o2"]
        # The first pass confirmed the revoke, so a retry needs the host to re-authorise
        # the mock for cleanup (a gap listed in the PR); it must not re-cancel o1 or re-revoke.
        venue.granted = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(out["status"], "confirmed")
        self.assertEqual(venue.log, ["halt", "list", "cancel:o2", "list"])
        self.assertEqual(out["cancelled"], ["o1", "o2"])

    def test_success_reply_but_still_listed_is_retried(self) -> None:
        """Red-team round 2 finding 2: a venue that still lists the order wins over its reply."""
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
        self.assertEqual(venue.log, ["list", "cancel:o1", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"], out["detail"]["halt"]), ("pending", "pending", "error:OSError"))

    def _failing_once_halt(self, venue):
        errors = [OSError("disk")]
        def halt():
            venue.log.append("halt")
            if errors:
                raise errors.pop()
        return halt

    def test_cancel_confirm_is_not_kept_when_the_halt_call_failed(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        kill = CoinbaseKill(self._failing_once_halt(venue), venue)
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:o1", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"halt": "error:OSError", "cancel_orders": "halt_failed",
                                         "revoke_probe": "unauthorized"})
        # An order placed while the halt was not set: the next kill lists and cancels again.
        venue.open = ["c-new"]
        venue.fail_cancel["c-new"] = {"order_id": "c-new", "success": True}  # says yes, stays open
        # The first pass confirmed the revoke, so the retry needs the host to re-authorise
        # the mock (a gap listed in the PR).
        venue.granted = True
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt", "list", "cancel:c-new", "list"])
        self.assertEqual((out["status"], out["halt"], out["cancel_orders"]), ("pending", "confirmed", "pending"))
        self.assertEqual(out["detail"], {"cancel_orders": "remaining:1", "revoke_probe": "unauthorized"})
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
        self.assertEqual(venue.log, ["halt", "list", "cancel:late", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_orders": "remaining:1", "revoke_probe": "unauthorized"})

    def test_confirmed_cancel_and_revoke_are_not_retried(self) -> None:
        venue = MockCoinbase(orders=["o1"])
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "confirmed")
        # The revoked grant can no longer list; that must not demote the cancel.
        with self.assertRaises(PermissionError):
            venue.list_open_orders(None)
        venue.log.clear()
        out = kill.kill()
        self.assertEqual(venue.log, ["halt"])
        self.assertEqual((out["status"], out["cancel_orders"], out["revoke_grant"]),
                         ("confirmed", "confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})

    def test_successful_halt_drops_the_stored_halt_error(self) -> None:
        venue = MockCoinbase()
        venue.revoke_works = False  # the grant stays, so the second kill can list
        kill = CoinbaseKill(self._failing_once_halt(venue), venue)
        out = kill.kill()
        self.assertEqual((out["halt"], out["detail"]["halt"]), ("pending", "error:OSError"))
        venue.revoke_works = True
        out = kill.kill()
        self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})

    def test_kill_halts_the_paper_book(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-cb-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        create_book(path, cash_minor={"paper-acct": 10**15})
        book = PaperBook(path)
        self.addCleanup(book.close)
        out = CoinbaseKill(book.halt, MockCoinbase(orders=["o1"])).kill()
        self.assertEqual(out["status"], "confirmed")
        self.assertTrue(book.halted())


if __name__ == "__main__":
    unittest.main()
