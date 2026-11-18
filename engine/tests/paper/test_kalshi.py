"""Kalshi: model-fed switches default off (B13) and the Kalshi-only kill (B12), mocks only."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from cockpit.paper.gateway import Refused
from cockpit.paper.kalshi import KalshiKill, KalshiModelFeed, KalshiUseSwitches

PAPER_DIR = Path(__file__).resolve().parents[2] / "cockpit" / "paper"


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


def _kalshi_kill(log, transport, errors=()):
    """A KalshiKill on the test's own halt flag: the halt callable logs, raises each given error
    once, then sets the flag; the read-back reads that flag."""
    errors, flag = list(errors), []
    def halt():
        log.append("halt")
        if errors:
            raise errors.pop()
        flag.append(True)
    return KalshiKill(halt, transport, halted=lambda: bool(flag))


class KillTests(unittest.TestCase):
    def _kill(self, **transport):
        log = []
        kill = _kalshi_kill(log, FakeTransport(log=log, **transport))
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

    def test_cancel_timeout_still_revokes_after_it_and_stays_pending(self) -> None:
        """O70: halt, cancel, revoke in one kill; a confirmed revoke never confirms a timed-out cancel."""
        kill, log = self._kill(cancel=TimeoutError())
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"], out["revoke_key"]),
                         ("pending", "confirmed", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "error:TimeoutError", "revoke_probe": "unauthorized"})
        # Still pending on the next kill while the cancel keeps timing out; the revoke and its
        # probe are made again. The revoke was confirmed before this cancel, so the pending leg
        # carries its typed reason.
        log.clear()
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]),
                         ("pending", "pending_credential_revoked", "confirmed"))

    def test_unauthorized_probe_after_a_failed_revoke_confirms_nothing(self) -> None:
        """Finding 3: confirm only when the revoke was accepted and the probe is unauthorized."""
        for revoke, detail in (({"accepted": False}, "rejected"),
                               (ConnectionError(), "error:ConnectionError"),
                               ({}, "unreadable_response"), ({"accepted": 1}, "unreadable_response"),
                               ({"accepted": "true"}, "unreadable_response"), (None, "unreadable_response")):
            with self.subTest(revoke=revoke):
                kill, log = self._kill(revoke=revoke, probe="unauthorized")
                out = kill.kill()
                self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]),
                                 ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], {"revoke_key": detail, "revoke_probe": "unauthorized"})

    def test_accepted_revoke_after_a_rejected_one_confirms_and_drops_the_detail(self) -> None:
        log = []
        transport = FakeTransport(revoke={"accepted": False}, probe="unauthorized", log=log)
        kill = _kalshi_kill(log, transport)
        self.assertEqual(kill.kill()["detail"], {"revoke_key": "rejected", "revoke_probe": "unauthorized"})
        transport.revoke = {"accepted": True}
        out = kill.kill()
        self.assertEqual((out["status"], out["revoke_key"], out["detail"]),
                         ("confirmed", "confirmed", {"revoke_probe": "unauthorized"}))

    def test_revoke_failure_after_cancel_reports_pending(self) -> None:
        for transport, detail in (
                (dict(revoke=ConnectionError(), probe="authorized"),
                 {"revoke_key": "error:ConnectionError", "revoke_probe": "authorized"}),
                (dict(revoke={"accepted": False}, probe="authorized"),
                 {"revoke_key": "rejected", "revoke_probe": "authorized"}),
                (dict(revoke=ConnectionError(), probe=TimeoutError()),
                 {"revoke_key": "error:ConnectionError", "revoke_probe": "error:TimeoutError"})):
            with self.subTest(transport=transport):
                out = self._kill(**transport)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("pending", "confirmed", "pending"))
                self.assertEqual(out["detail"], detail)

    def test_last_probe_is_reported_under_its_own_key(self) -> None:
        """The probe result never mixes into the revoke attempt's detail."""
        cases = ((dict(probe="authorized"), "pending", {"revoke_probe": "authorized"}),
                 (dict(revoke={"accepted": False}, probe="unauthorized"), "pending",
                  {"revoke_key": "rejected", "revoke_probe": "unauthorized"}),
                 (dict(probe=TimeoutError()), "pending", {"revoke_probe": "error:TimeoutError"}),
                 (dict(probe=None), "pending", {"revoke_probe": "unreadable_probe"}),
                 (dict(probe={"unauthorized": True}), "pending", {"revoke_probe": "unreadable_probe"}),
                 # Any undeclared string is unreadable; no prefix of it is stored.
                 *((dict(probe=reply), "pending", {"revoke_probe": "unreadable_probe"})
                   for reply in ("x" * 40, "confirmed", "cancelled", "UNAUTHORIZED", "unauthorized ")),
                 (dict(probe="unauthorized"), "confirmed", {"revoke_probe": "unauthorized"}))
        for transport, state, detail in cases:
            with self.subTest(transport=transport):
                out = self._kill(**transport)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), (state, "confirmed", state))
                self.assertEqual(out["detail"], detail)

    def test_each_probe_overwrites_the_last_probe_result(self) -> None:
        log = []
        transport = FakeTransport(probe=TimeoutError(), log=log)
        kill = _kalshi_kill(log, transport)
        self.assertEqual(kill.kill()["detail"], {"revoke_probe": "error:TimeoutError"})
        transport.probe = "authorized"
        self.assertEqual(kill.kill()["detail"], {"revoke_probe": "authorized"})
        transport.probe = "unauthorized"
        out = kill.kill()
        self.assertEqual((out["status"], out["detail"]), ("confirmed", {"revoke_probe": "unauthorized"}))

    def test_capped_or_unreadable_cancel_is_pending_not_confirmed(self) -> None:
        for response, detail in (({"remaining": 7}, "remaining:7"), ({}, "unreadable_response"),
                                 ({"remaining": "0"}, "unreadable_response"), (None, "unreadable_response")):
            with self.subTest(response=response):
                out = self._kill(cancel=response)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_all"], detail)

    def test_retry_cancels_again_and_revokes_and_probes_again(self) -> None:
        log = []
        transport = FakeTransport(cancel={"remaining": 3}, log=log)
        kill = _kalshi_kill(log, transport)
        self.assertEqual(kill.kill()["status"], "pending")
        transport.cancel = {"remaining": 0}
        log.clear()
        out = kill.kill()
        self.assertEqual(out["status"], "confirmed")
        # The earlier revoke confirm is not kept: this kill revokes and probes for itself.
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])
        self.assertEqual(out["revoke_evidence"], {"accepted": True, "probe": "unauthorized"})

    def test_zero_remaining_reply_is_not_confirmation(self) -> None:
        """A cancel-all reply of remaining 0 confirms nothing until the re-list is empty."""
        log = []
        transport = FakeTransport(cancel={"remaining": 0}, orders=["k1"], log=log)
        kill = _kalshi_kill(log, transport)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
        self.assertEqual(out["detail"]["cancel_all"], "remaining:1")
        self.assertIn("list_open_orders", log)
        transport.orders = []
        log.clear()
        self.assertEqual(kill.kill()["status"], "confirmed")
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])

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

    def test_empty_list_that_is_not_finished_keeps_cancel_pending(self) -> None:
        """An empty page that says more may follow confirms nothing."""
        for extra in ({"cursor": "page-2"}, {"cursor": ""}, {"cursor": 0}, {"truncated": True},
                      {"truncated": None}, {"truncated": 1}, {"remaining": 2}, {"remaining": "0"},
                      {"remaining": None}, {"remaining": False}):
            with self.subTest(extra=extra):
                kill, log = self._kill(orders={"order_ids": [], **extra})
                out = kill.kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("pending", "pending"))
                self.assertEqual(out["detail"]["cancel_all"], "unfinished_list")
                self.assertIn("revoke_api_key", log)  # an unfinished list never skips the revoke

    def test_finished_empty_list_confirms(self) -> None:
        for payload in ({"order_ids": []}, {"order_ids": [], "cursor": None},
                        {"order_ids": [], "truncated": False}, {"order_ids": [], "remaining": 0},
                        {"order_ids": [], "cursor": None, "truncated": False, "remaining": 0}):
            with self.subTest(payload=payload):
                out = self._kill(orders=payload)[0].kill()
                self.assertEqual((out["status"], out["cancel_all"]), ("confirmed", "confirmed"))
                self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})

    def test_cancel_confirm_is_not_kept_when_the_halt_call_failed(self) -> None:
        log = []
        transport = FakeTransport(log=log)
        kill = _kalshi_kill(log, transport, errors=[OSError("disk")])
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["halt"], out["cancel_all"], out["revoke_key"]),
                         ("pending", "pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"halt": "error:OSError", "cancel_all": "halt_failed",
                                         "revoke_probe": "unauthorized"})
        # An order placed while the halt was not set: the next kill cancels and lists again.
        transport.orders = ["k-new"]
        log.clear()
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])
        # The revoke was confirmed before this cancel: still pending, with its own typed reason.
        self.assertEqual((out["status"], out["halt"], out["cancel_all"]),
                         ("pending", "confirmed", "pending_credential_revoked"))
        self.assertEqual(out["detail"], {"cancel_all": "remaining:1", "revoke_probe": "unauthorized"})
        transport.orders = []
        self.assertEqual(kill.kill()["status"], "confirmed")

    def test_pending_revoke_cancels_and_lists_again(self) -> None:
        log = []
        transport = FakeTransport(revoke={"accepted": False}, probe="authorized", log=log)
        kill = _kalshi_kill(log, transport)
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("pending", "confirmed", "pending"))
        # The key still worked, so an order could be placed after the first listing.
        transport.orders = ["late"]
        transport.revoke, transport.probe = {"accepted": True}, "unauthorized"
        log.clear()
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "list_open_orders", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "remaining:1", "revoke_probe": "unauthorized"})

    def test_pending_revoke_recancel_failure_still_revokes(self) -> None:
        log = []
        transport = FakeTransport(probe="authorized", log=log)
        kill = _kalshi_kill(log, transport)
        self.assertEqual(kill.kill()["cancel_all"], "confirmed")
        transport.cancel, transport.probe = TimeoutError(), "unauthorized"
        log.clear()
        out = kill.kill()
        self.assertEqual(log, ["halt", "cancel_all", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("pending", "pending", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "error:TimeoutError", "revoke_probe": "unauthorized"})

    def test_second_kill_revokes_and_probes_again_and_keeps_the_cancel_only_on_a_dead_key(self) -> None:
        log = []
        transport = FakeTransport(log=log)
        kill = _kalshi_kill(log, transport)
        self.assertEqual(kill.kill()["status"], "confirmed")
        # The revoked key can no longer cancel or list; that must not demote the cancel.
        transport.cancel = transport.orders = PermissionError()
        log.clear()
        out = kill.kill()
        # Every leg is tried again: the halt, the cancel, and a fresh revoke with its probe.
        self.assertEqual(log, ["halt", "cancel_all", "revoke_api_key", "probe"])
        self.assertEqual((out["status"], out["cancel_all"], out["revoke_key"]), ("confirmed", "confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"cancel_all": "kept:error:PermissionError", "revoke_probe": "unauthorized"})
        self.assertEqual(out["revoke_evidence"], {"accepted": True, "probe": "unauthorized"})

    def test_second_kill_does_not_confirm_on_the_first_kills_probe(self) -> None:
        """A confirmed kill, then the key works again or the revoke is refused: the next kill is pending."""
        cases = (("probe_authorized", {"probe": "authorized"}, {"accepted": True, "probe": "authorized"}),
                 ("probe_raises", {"probe": TimeoutError()}, {"accepted": True, "probe": None}),
                 ("revoke_rejected", {"revoke": {"accepted": False}}, {"accepted": False, "probe": "unauthorized"}),
                 ("revoke_raises", {"revoke": ConnectionError()}, {"accepted": False, "probe": "unauthorized"}))
        for label, changes, evidence in cases:
            with self.subTest(case=label):
                log = []
                transport = FakeTransport(log=log)
                kill = _kalshi_kill(log, transport)
                self.assertEqual(kill.kill()["status"], "confirmed")
                transport.cancel = PermissionError()  # the dead key cannot cancel
                for name, value in changes.items():
                    setattr(transport, name, value)
                log.clear()
                out = kill.kill()
                self.assertEqual(log, ["halt", "cancel_all", "revoke_api_key", "probe"])
                self.assertEqual((out["status"], out["revoke_key"]), ("pending", "pending"))
                self.assertEqual(out["revoke_evidence"], evidence)
                # The earlier cancel confirm does not stand on a key that is not proven dead.
                self.assertEqual((out["cancel_all"], out["detail"]["cancel_all"]),
                                 ("pending_credential_revoked", "error:PermissionError"))
        # The key works again and the venue lists a new order: seen in that kill.
        log = []
        transport = FakeTransport(log=log)
        kill = _kalshi_kill(log, transport)
        self.assertEqual(kill.kill()["status"], "confirmed")
        transport.orders = ["late-1"]
        out = kill.kill()
        self.assertEqual((out["status"], out["cancel_all"], out["detail"]["cancel_all"]),
                         ("pending", "pending_credential_revoked", "remaining:1"))

    def test_kill_is_not_confirmed_when_its_latest_halt_read_fails(self) -> None:
        """The halt leg is read again on every kill; an earlier confirmed read is not kept."""
        def raising() -> bool:
            raise OSError("disk")
        for label, read, detail in (("not_halted", lambda: False, "not_halted"),
                                    ("read_error", raising, "read_error:OSError")):
            with self.subTest(read=label):
                reads = [lambda: True, read, lambda: True]
                kill = KalshiKill(lambda: None, FakeTransport(), halted=lambda: reads.pop(0)())
                self.assertEqual(kill.kill()["status"], "confirmed")
                out = kill.kill()
                self.assertEqual((out["status"], out["halt"], out["detail"]["halt"]), ("pending", "pending", detail))
                self.assertEqual((out["cancel_all"], out["revoke_key"]), ("confirmed", "confirmed"))
                out = kill.kill()
                self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
                self.assertNotIn("halt", out["detail"])

    def test_successful_halt_drops_the_stored_halt_error(self) -> None:
        log = []
        kill = _kalshi_kill(log, FakeTransport(log=log), errors=[OSError("disk")])
        out = kill.kill()
        self.assertEqual((out["halt"], out["detail"]["halt"]), ("pending", "error:OSError"))
        out = kill.kill()
        self.assertEqual((out["status"], out["halt"]), ("confirmed", "confirmed"))
        self.assertEqual(out["detail"], {"revoke_probe": "unauthorized"})


if __name__ == "__main__":
    unittest.main()
