"""Pins Lucas's Nov 11 kill decisions (relayed 12:52am PST) through ``KillSwitch`` on a real book.

* Decision 3, "Keep paper_only": a kill with no venue composed, once the halt reads back
  and the paper cancel is confirmed, reports ``paper_only`` and never ``confirmed``.
* Decision 4, "Accept keeping the earlier cancel leg marked kept:<error>": after a confirmed
  kill, a later kill whose cancel fails on the dead credential keeps that venue's cancel leg
  confirmed, with detail ``kept:<this attempt's error>``, only when the same kill's fresh
  revoke and probes confirm again. The kill confirm rule is unchanged: an OAuth venue needs
  its revoke answering exactly 200 AND both probes ``unauthorized``; Kalshi (an API key, no
  refresh token) needs the accepted revoke and its one probe ``unauthorized``. Anything less
  leaves the venue, and the switch, ``pending``.

Paper and mock only: the venues are the in-memory mocks of ``test_coinbase.py``,
``test_gemini.py`` and ``test_kalshi.py``. No network, credential or keyring is touched.
"""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import unittest

from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.gateway import PaperBook, create_book
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.kalshi import KalshiKill
from cockpit.paper.killswitch import KillSwitch
from cockpit.paper.revoke import RevokeReply
from tests.paper.test_coinbase import MockCoinbase
from tests.paper.test_gemini import MockGemini
from tests.paper.test_kalshi import FakeTransport

UNAUTH = {"revoke_probe_api": "unauthorized", "revoke_probe_refresh": "unauthorized"}


def _new_book(path: str) -> None:
    # The book gained per-account cash later in the stack; this file runs on either book.
    if "cash_minor" in inspect.signature(create_book).parameters:
        create_book(path, cash_minor={"paper-acct": 0})
    else:
        create_book(path)


class KillDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-kill-nov11-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        _new_book(path)
        self.book = PaperBook(path)
        self.addCleanup(self.book.close)

    def _switch(self, venues=()) -> KillSwitch:
        return KillSwitch(self.book.halt, venues, paper_book=self.book, venue_timeout_s=5.0)

    def _venues(self):
        coinbase, gemini, kalshi = MockCoinbase(orders=["c1"]), MockGemini(orders=["g1"]), FakeTransport()
        kills = [CoinbaseKill(self.book.halt, coinbase, halted=self.book.halted),
                 GeminiKill(self.book.halt, gemini, halted=self.book.halted),
                 KalshiKill(self.book.halt, kalshi, halted=self.book.halted)]
        return (coinbase, gemini, kalshi), kills

    def test_decision3_zero_venues_is_paper_only_never_confirmed(self) -> None:
        switch = self._switch()
        for _ in range(2):  # every kill re-checks; still paper_only, never upgraded
            out = switch.kill()
            self.assertEqual((out["status"], out["halt"]), ("paper_only", "confirmed"))
            self.assertEqual([v["venue"] for v in out["venues"]], ["paper"])
            self.assertNotEqual(out["status"], "confirmed")
        self.assertTrue(self.book.halted())

    def test_decision3_paper_only_needs_the_halt_read_back(self) -> None:
        switch = KillSwitch(lambda: None, (), paper_book=self.book)  # a halt that never lands
        self.assertEqual(switch.kill()["status"], "pending")

    def test_decision4_kept_cancel_leg_on_this_kills_fresh_revoke_and_probes(self) -> None:
        (coinbase, gemini, kalshi), kills = self._venues()
        switch = self._switch(kills)
        self.assertEqual(switch.kill()["status"], "confirmed")
        # The credentials are dead now: listing or cancelling raises on every venue.
        kalshi.cancel = kalshi.orders = PermissionError()
        coinbase.log.clear(), gemini.log.clear()
        out = switch.kill()
        self.assertEqual(out["status"], "confirmed")
        cb, gm, ks = out["venues"][1:]
        self.assertEqual((cb["cancel_orders"], cb["detail"]),
                         ("confirmed", {"cancel_orders": "kept:list_error:PermissionError", **UNAUTH}))
        self.assertEqual((gm["cancel_all"], gm["detail"]),
                         ("confirmed", {"cancel_all": "kept:error:PermissionError", **UNAUTH}))
        self.assertEqual((ks["cancel_all"], ks["detail"]),
                         ("confirmed", {"cancel_all": "kept:error:PermissionError", "revoke_probe": "unauthorized"}))
        # The keep rests on this kill's own fresh revoke and probes, never the first kill's.
        self.assertEqual(coinbase.log[-3:], ["revoke", "probe_api", "probe_refresh"])
        self.assertEqual(gemini.log[-3:], ["revoke", "probe_api", "probe_refresh"])
        for venue in (cb, gm):
            self.assertEqual(venue["revoke_evidence"],
                             {"http_status": 200, "probe_api": "unauthorized", "probe_refresh": "unauthorized"})
        self.assertEqual(ks["revoke_evidence"], {"accepted": True, "probe": "unauthorized"})

    def test_decision4_no_keep_without_revoke_200_and_both_probes_unauthorized(self) -> None:
        breaks = {
            "revoke_503": lambda cb: setattr(cb, "revoke_reply", RevokeReply(503)),
            "probe_api_authorized": lambda cb: setattr(cb, "api_reply", "authorized"),
            "probe_refresh_authorized": lambda cb: setattr(cb, "refresh_reply", "authorized"),
        }
        for name, broken in breaks.items():
            with self.subTest(broken=name):
                (coinbase, _, _), kills = self._venues()
                switch = self._switch(kills)
                self.assertEqual(switch.kill()["status"], "confirmed")
                broken(coinbase)  # the grant still cannot list, but this kill cannot prove it dead
                out = switch.kill()
                cb = out["venues"][1]
                self.assertEqual((out["status"], cb["status"]), ("pending", "pending"))
                self.assertNotEqual(cb["cancel_orders"], "confirmed")
                self.assertFalse(cb["detail"]["cancel_orders"].startswith("kept:"))

    def test_decision4_no_keep_when_kalshi_probe_is_authorized(self) -> None:
        (_, _, kalshi), kills = self._venues()
        switch = self._switch(kills)
        self.assertEqual(switch.kill()["status"], "confirmed")
        kalshi.cancel, kalshi.probe = PermissionError(), "authorized"
        out = switch.kill()
        ks = out["venues"][3]
        self.assertEqual((out["status"], ks["status"]), ("pending", "pending"))
        self.assertNotEqual(ks["cancel_all"], "confirmed")

    def test_decision4_unconfirmed_halt_never_latches_kept_cancel(self) -> None:
        (coinbase, gemini, kalshi), _ = self._venues()
        unhalted_kills = [
            CoinbaseKill(lambda: None, coinbase, halted=self.book.halted),
            GeminiKill(lambda: None, gemini, halted=self.book.halted),
            KalshiKill(lambda: None, kalshi, halted=self.book.halted),
        ]
        # Kill #1: halt never lands on the book, while each venue cancels and revokes.
        first = KillSwitch(lambda: None, unhalted_kills, paper_book=self.book, venue_timeout_s=5.0).kill()
        self.assertEqual(first["status"], "pending")
        for venue in first["venues"][1:]:
            self.assertEqual(venue["halt"], "pending")
            self.assertEqual(venue.get("cancel_orders", venue.get("cancel_all")), "confirmed")
            self.assertEqual(venue.get("revoke_grant", venue.get("revoke_key")), "confirmed")
        # Orders placed before revoke while unhalted cannot be listed now that credentials are dead.
        coinbase.open.append("cb-after-cancel")
        gemini.open.append("gm-after-cancel")
        coinbase.list_error = PermissionError("revoked")
        gemini.cancel_error = PermissionError("revoked")
        kalshi.cancel = kalshi.orders = PermissionError("revoked")
        for vk in unhalted_kills:
            vk._halt = self.book.halt
        second = self._switch(unhalted_kills).kill()
        self.assertEqual(second["status"], "pending")
        for venue in second["venues"][1:]:
            self.assertEqual(venue["status"], "pending")

    def test_retry_does_not_reuse_cancel_after_missing_or_failed_halt_readback(self):
        def unreadable():
            raise RuntimeError("fixture read failure")

        for readback in (None, lambda: False, unreadable):
            for adapter, transport in ((CoinbaseKill, MockCoinbase()),
                                       (GeminiKill, MockGemini()),
                                       (KalshiKill, FakeTransport())):
                with self.subTest(adapter=adapter.__name__, readback=readback):
                    kill = adapter(lambda: None, transport, halted=readback)
                    first = kill.kill()
                    self.assertEqual(first["status"], "pending")
                    self.assertEqual(first["halt"], "pending")
                    cancel = "cancel_orders" if adapter is CoinbaseKill else "cancel_all"
                    revoke = "revoke_key" if adapter is KalshiKill else "revoke_grant"
                    self.assertEqual(first[cancel], "confirmed")
                    self.assertEqual(first[revoke], "confirmed")
                    if adapter is KalshiKill:
                        transport.cancel = transport.orders = PermissionError("revoked")
                    elif adapter is CoinbaseKill:
                        transport.list_error = PermissionError("revoked")
                    else:
                        transport.cancel_error = PermissionError("revoked")
                    kill._halted = lambda: True
                    second = kill.kill()
                    self.assertEqual(second["status"], "pending")
                    self.assertEqual(second[cancel], "pending_credential_revoked")
                    self.assertFalse(second["detail"][cancel].startswith("kept:"))


if __name__ == "__main__":
    unittest.main()
