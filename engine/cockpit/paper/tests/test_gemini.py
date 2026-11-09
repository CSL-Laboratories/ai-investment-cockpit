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
        cases = ((dict(revoke_works=False), "probe:authorized"),
                 (dict(revoke_works=False, revoke_reply={"accepted": False}), "rejected"),
                 (dict(revoke_works=False, revoke_reply=ConnectionError()), "error:ConnectionError"))
        for attrs, detail in cases:
            with self.subTest(attrs=attrs):
                venue = MockGemini()
                for k, v in attrs.items():
                    setattr(venue, k, v)
                out = self._kill(venue).kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_grant"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"]["revoke_grant"], detail)

    def test_retry_only_what_is_not_confirmed(self) -> None:
        venue = MockGemini(orders=["g1"])
        venue.revoke_works = False
        kill = self._kill(venue)
        self.assertEqual(kill.kill()["status"], "pending")
        venue.revoke_works = True
        venue.log.clear()
        self.assertEqual(kill.kill()["status"], "confirmed")
        self.assertEqual(venue.log, ["halt", "revoke", "probe"])

    def test_halt_failure_still_attempts_risk_reducing_calls(self) -> None:
        venue = MockGemini(orders=["g1"])
        def broken_halt():
            raise OSError("disk")
        out = GeminiKill(broken_halt, venue).kill()
        self.assertEqual(venue.log, ["cancel_all", "list", "revoke", "probe"])
        self.assertEqual((out["status"], out["halt"]), ("pending", "pending"))

    def test_no_ambient_or_network_code(self) -> None:
        source = PAPER_DIR.joinpath("gemini.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, NO_AMBIENT)


if __name__ == "__main__":
    unittest.main()
