"""Kalshi: model-fed switches default off (B13) and the Kalshi-only kill (B12), mocks only."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from cockpit.paper.gateway import Refused
from cockpit.paper.kalshi import KalshiKill, KalshiModelFeed, KalshiUseSwitches

PAPER_DIR = Path(__file__).resolve().parents[1]


class SwitchTests(unittest.TestCase):
    def test_default_off_refuses_before_calling_the_fake(self) -> None:
        called = []
        feed = KalshiModelFeed(KalshiUseSwitches())
        with self.assertRaises(Refused) as ctx:
            feed.model_read(lambda: called.append("read"))
        self.assertEqual(ctx.exception.code, "kalshi_model_fed_read_off_cx144")
        with self.assertRaises(Refused) as ctx:
            feed.model_propose(lambda: called.append("propose"))
        self.assertEqual(ctx.exception.code, "kalshi_model_fed_propose_off_cx144")
        self.assertEqual(called, [])

    def test_no_ambient_override(self) -> None:
        names = {"KALSHI_MODEL_FED": "1", "KALSHI_MODEL_FED_READ": "true", "COCKPIT_KALSHI_MODEL_FED": "on",
                 "CX144_CLEARED": "1"}
        with mock.patch.dict(os.environ, names):
            switches = KalshiUseSwitches()
            self.assertEqual((switches.model_fed_read, switches.model_fed_propose), (False, False))
            with self.assertRaises(Refused):
                KalshiModelFeed(switches).model_read(lambda: "data")
        source = PAPER_DIR.joinpath("kalshi.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"os\.environ|getenv|open\(|import os")

    def test_only_an_exact_true_turns_a_switch_on(self) -> None:
        for value in (1, "true", "1", None, [True]):
            with self.subTest(value=value), self.assertRaises(Refused):
                KalshiUseSwitches(model_fed_read=value)
        self.assertRaises(Refused, KalshiModelFeed, {"model_fed_read": True})
        self.assertEqual(KalshiModelFeed(KalshiUseSwitches(model_fed_read=True)).model_read(lambda: "ok"), "ok")

    def test_no_venue_order_code_and_kalshi_kill_is_kalshi_only(self) -> None:
        # Coinbase and Gemini have their own kill modules (F44); none of them places orders.
        for path in PAPER_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"def (place|submit|create)_?order.*kalshi|kalshi.*place_order")
            if path.name in ("kalshi.py", "coinbase.py", "gemini.py", "killswitch.py"):
                with self.subTest(path=path.name, check="no order placement"):
                    self.assertNotRegex(text, r"(place|submit|create)_?order")
        kalshi = PAPER_DIR.joinpath("kalshi.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("coinbase", kalshi)
        self.assertNotIn("gemini", kalshi)


_DEFAULT = object()


class FakeTransport:
    def __init__(self, cancel=_DEFAULT, revoke=_DEFAULT, probe="unauthorized",
                 orders=_DEFAULT, log=None):
        self.cancel, self.revoke, self.probe = cancel, revoke, probe
        self.orders = [] if orders is _DEFAULT else orders
        self.log = log if log is not None else []

    def _do(self, name, outcome):
        self.log.append(name)
        if isinstance(outcome, BaseException) or (isinstance(outcome, type) and issubclass(outcome, BaseException)):
            raise outcome
        return outcome

    def cancel_all(self):
        return self._do("cancel_all", {"remaining": 0} if self.cancel is _DEFAULT else self.cancel)

    def list_open_orders(self):
        outcome = self.orders
        if type(outcome) is list:
            outcome = {"order_ids": list(outcome)}
        return self._do("list_open_orders", outcome)

    def revoke_api_key(self):
        return self._do("revoke_api_key", {"accepted": True} if self.revoke is _DEFAULT else self.revoke)

    def probe_authenticated(self):
        return self._do("probe", self.probe)


class KillTests(unittest.TestCase):
    def _kill(self, **transport):
        log = []
        kill = KalshiKill(lambda: log.append("halt"), FakeTransport(log=log, **transport))
        return kill, log

    def test_halt_first_then_one_cancel_and_one_revoke(self) -> None:
        kill, log = self._kill()
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("confirmed", "confirmed", "confirmed"))

    def test_cancel_failure_still_revokes_and_reports_pending(self) -> None:
        kill, log = self._kill(cancel=TimeoutError())
        out = kill.kill()
        self.assertIn("revoke_api_key", log)
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"]["cancel_all"], "error:TimeoutError")

    def test_revoke_failure_after_cancel_reports_pending(self) -> None:
        for transport, detail in ((dict(revoke=ConnectionError(), probe="authorized"), "error:ConnectionError"),
                                  (dict(revoke={"accepted": False}, probe="authorized"), "rejected"),
                                  (dict(probe="authorized"), "probe:authorized"),
                                  (dict(probe=TimeoutError()), "probe_error:TimeoutError")):
            with self.subTest(transport=transport):
                out = self._kill(**transport)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"]["revoke_key"], detail)

    def test_capped_or_unreadable_cancel_is_pending_not_confirmed(self) -> None:
        for response, detail in (({"remaining": 7}, "remaining:7"), ({}, "unreadable_response"),
                                 ({"remaining": "0"}, "unreadable_response"), (None, "unreadable_response")):
            with self.subTest(response=response):
                out = self._kill(cancel=response)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_all"], detail)

    def test_retry_only_what_is_not_confirmed(self) -> None:
        log = []
        transport = FakeTransport(cancel={"remaining": 3}, log=log)
        kill = KalshiKill(lambda: log.append("halt"), transport)
        self.assertEqual(kill.kill()["status"], "pending")
        transport.cancel = {"remaining": 0}
        log.clear()
        self.assertEqual(kill.kill()["status"], "confirmed")
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders"])

    def test_zero_remaining_reply_is_not_confirmation(self) -> None:
        """A cancel-all reply of remaining 0 confirms nothing until the re-list is empty."""
        log = []
        transport = FakeTransport(cancel={"remaining": 0}, orders=["k1"], log=log)
        kill = KalshiKill(lambda: log.append("halt"), transport)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
        self.assertEqual(out["detail"]["cancel_all"], "remaining:1")
        self.assertIn("list_open_orders", log)
        transport.orders = []
        log.clear()
        self.assertEqual(kill.kill()["status"], "confirmed")
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders"])

    def test_relist_failure_keeps_cancel_pending(self) -> None:
        for orders, detail in ((TimeoutError(), "list_error:TimeoutError"),
                               ({"order_ids": [1]}, "unreadable_list"),
                               ({}, "unreadable_list"),
                               (None, "unreadable_list")):
            with self.subTest(detail=detail):
                out = self._kill(orders=orders)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_all"], detail)

    def test_halt_failure_still_attempts_risk_reducing_calls(self) -> None:
        log = []
        def broken_halt():
            raise OSError("disk")
        out = KalshiKill(broken_halt, FakeTransport(log=log)).kill()
        self.assertEqual(log, ["cancel_all", "list_open_orders", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["halt"]), ("pending", "pending"))


if __name__ == "__main__":
    unittest.main()
