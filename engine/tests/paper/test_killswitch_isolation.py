"""The kill switch confirms only on revoke evidence, and no venue can skip or stall another.

* ``confirmed`` needs, from this ``kill()`` call, each venue's revoke answering exactly 200
  with both probes exactly ``"unauthorized"`` (Kalshi: the accepted revoke and its one
  probe), plus its cancel leg and halt read. A bare ``{"status": "confirmed"}`` is not it.
* With no venue composed, a completed kill is ``paper_only``, never ``confirmed``.
* A venue raising any ``BaseException`` (``asyncio.CancelledError``, say) or hanging leaves
  the others reached; a hung venue is ``timeout``, then ``in_flight`` on later kills, and
  never blocks them.

Mocks only: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
import time
import unittest
from dataclasses import replace

from cockpit.paper import plugin
from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.gateway import Caps, PaperBook, create_book
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.kalshi import KalshiKill
from cockpit.paper.killswitch import DEFAULT_VENUE_TIMEOUT_S, KillSwitch
from tests.paper.fakecore import AUTHORITY, FakeCore
from tests.paper.test_coinbase import MockCoinbase
from tests.paper.test_gateway import INTENT
from tests.paper.test_gemini import MockGemini
from tests.paper.test_kalshi import FakeTransport

HANG_TIMEOUT_S = 1.0  # long enough for the mock venues on a loaded box, short enough for a test
PROMPT_S = 15.0       # "did not wait for the hung venue": far below the hang, far above the bound
OAUTH_OK = {"venue": "gemini", "status": "confirmed", "halt": "confirmed", "cancel_all": "confirmed",
            "revoke_grant": "confirmed", "detail": {},
            "revoke_evidence": {"http_status": 200, "probe_api": "unauthorized", "probe_refresh": "unauthorized"}}
KEY_OK = {"venue": "kalshi", "status": "confirmed", "halt": "confirmed", "cancel_all": "confirmed",
          "revoke_key": "confirmed", "detail": {}, "revoke_evidence": {"accepted": True, "probe": "unauthorized"}}


class StrSub(str):
    """Equal to its text for every ``str`` comparison, but not exactly a ``str``."""


class IntSub(int):
    """Equal to its value for every ``int`` comparison, but not exactly an ``int``."""


class DictSub(dict):
    """Reads like the dict it copies, but is not exactly a ``dict``."""


class AlwaysEqual:
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


class Says:
    """A venue kill that returns what it was given, and counts its calls."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def kill(self) -> object:
        self.calls += 1
        return self.result


class Raises:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def kill(self) -> dict:
        raise self.exc


class Hangs:
    """A venue kill that does not return until released; then it reports full evidence."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0
        self.running = 0
        self.most_running = 0
        self._lock = threading.Lock()

    def kill(self) -> dict:
        with self._lock:
            self.calls += 1
            self.running += 1
            self.most_running = max(self.most_running, self.running)
        self.entered.set()
        try:
            self.release.wait(120)
            return dict(OAUTH_OK, venue="hangs")
        finally:
            with self._lock:
                self.running -= 1


def _with(base: dict, **changes: object) -> dict:
    out = dict(base)
    for key, value in changes.items():
        if key.startswith("evidence_"):
            out["revoke_evidence"] = dict(out["revoke_evidence"], **{key[len("evidence_"):]: value})
        else:
            out[key] = value
    return out


def _without(base: dict, key: str) -> dict:
    out = dict(base)
    if key.startswith("evidence_"):
        out["revoke_evidence"] = {k: v for k, v in out["revoke_evidence"].items() if k != key[len("evidence_"):]}
    else:
        del out[key]
    return out


class Base(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-kill-iso-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)

    def _switch(self, venues: list, timeout: float = 30.0) -> KillSwitch:
        return KillSwitch(self.book.halt, venues, paper_book=self.book, venue_timeout_s=timeout)

    def _gemini(self, orders=("gm-1",)) -> tuple[MockGemini, GeminiKill]:
        mock = MockGemini(orders=list(orders))
        return mock, GeminiKill(self.book.halt, mock, halted=self.book.halted)

    def _hang(self) -> Hangs:
        venue = Hangs()
        self.addCleanup(venue.release.set)
        return venue


class ConfirmedNeedsRevokeEvidenceTests(Base):
    def test_default_composition_is_paper_only_never_confirmed(self) -> None:
        """No venue composed (the Nov 11 default): halted and paper cancelled is ``paper_only``."""
        core = FakeCore(budget_minor=10**9)
        composed = plugin.compose(core, self.path, Caps(10**9, 10**6))
        for i in range(4):
            intent = replace(INTENT, intent_id=f"w-{i}")
            approval = composed.gateway.request_approval(intent)["approval_id"]
            core.operator_approves(approval)
            composed.gateway.submit(intent, approval, AUTHORITY)
        for cancelled in (4, 0):  # a second kill says the same
            out = composed.kill_switch.kill()
            self.assertIs(type(out["status"]), str)
            self.assertEqual(out, {"status": "paper_only", "halt": "confirmed", "venues": [
                {"venue": "paper", "status": "confirmed", "cancelled": cancelled, "detail": {}}]})
        self.assertEqual((self.book.halted(), self.book.working()), (True, 0))

    def test_paper_only_needs_the_halt_and_the_paper_cancel_confirmed(self) -> None:
        book = self.book

        class NotHalted:
            cancel_all, working = book.cancel_all, book.working
            def halted(self):
                return False

        class StillWorking:
            cancel_all, halted = book.cancel_all, book.halted
            def working(self):
                return 2
        for label, paper, halt, paper_status in (("halt", NotHalted(), "not_halted", "confirmed"),
                                                 ("cancel", StillWorking(), "confirmed", "pending")):
            with self.subTest(unconfirmed=label):
                out = KillSwitch(book.halt, [], paper_book=paper).kill()
                self.assertEqual((out["status"], out["halt"], out["venues"][0]["status"]),
                                 ("pending", halt, paper_status))

    def test_a_bare_confirmed_status_is_not_evidence(self) -> None:
        for label, result in (("status_only", {"venue": "odd", "status": "confirmed"}),
                              ("legs_no_evidence", _without(OAUTH_OK, "revoke_evidence")),
                              ("detail_probes_only", _with(_without(OAUTH_OK, "revoke_evidence"), detail={
                                  "revoke_probe_api": "unauthorized", "revoke_probe_refresh": "unauthorized"}))):
            with self.subTest(result=label):
                venue = Says(result)
                out = self._switch([venue]).kill()
                self.assertEqual((out["status"], out["halt"]), ("pending", "confirmed"))
                self.assertEqual(out["venues"][0]["status"], "confirmed")  # the paper cancel is done
                reported = out["venues"][1]
                self.assertIs(type(reported["status"]), str)
                self.assertEqual((reported["status"], reported["evidence"]), ("pending", "missing"))
                self.assertEqual(result["status"], "confirmed")  # the venue's own dict is not rewritten

    def test_full_evidence_confirms(self) -> None:
        """Control: the same shapes with their evidence do confirm, so the cases below fail on the change."""
        for label, result in (("oauth", OAUTH_OK), ("oauth_cancel_orders", _with(
                _without(OAUTH_OK, "cancel_all"), venue="coinbase", cancel_orders="confirmed")), ("api_key", KEY_OK)):
            with self.subTest(result=label):
                out = self._switch([Says(dict(result))]).kill()
                self.assertEqual((out["status"], out["venues"][1]), ("confirmed", result))

    def test_evidence_is_checked_with_exact_types_and_values(self) -> None:
        oauth = {
            "status_str_subclass": _with(OAUTH_OK, status=StrSub("confirmed")),
            "status_rigged_eq": _with(OAUTH_OK, status=AlwaysEqual()),
            "result_dict_subclass": DictSub(OAUTH_OK),
            "halt_pending": _with(OAUTH_OK, halt="pending"),
            "halt_missing": _without(OAUTH_OK, "halt"),
            "halt_str_subclass": _with(OAUTH_OK, halt=StrSub("confirmed")),
            "cancel_pending": _with(OAUTH_OK, cancel_all="pending"),
            "cancel_credential_revoked": _with(OAUTH_OK, cancel_all="pending_credential_revoked"),
            "cancel_missing": _without(OAUTH_OK, "cancel_all"),
            "cancel_two_legs_one_pending": _with(OAUTH_OK, cancel_orders="pending"),
            "cancel_two_legs": _with(OAUTH_OK, cancel_orders="confirmed"),
            "cancel_rigged_eq": _with(OAUTH_OK, cancel_all=AlwaysEqual()),
            "revoke_pending": _with(OAUTH_OK, revoke_grant="pending"),
            "revoke_missing": _without(OAUTH_OK, "revoke_grant"),
            "revoke_str_subclass": _with(OAUTH_OK, revoke_grant=StrSub("confirmed")),
            "evidence_none": _with(OAUTH_OK, revoke_evidence=None),
            "evidence_dict_subclass": _with(OAUTH_OK, revoke_evidence=DictSub(OAUTH_OK["revoke_evidence"])),
            "evidence_says_confirmed": _with(OAUTH_OK, revoke_evidence="confirmed"),
            "http_none": _with(OAUTH_OK, evidence_http_status=None),
            "http_missing": _without(OAUTH_OK, "evidence_http_status"),
            "http_204": _with(OAUTH_OK, evidence_http_status=204),
            "http_float_200": _with(OAUTH_OK, evidence_http_status=200.0),
            "http_str_200": _with(OAUTH_OK, evidence_http_status="200"),
            "http_true": _with(OAUTH_OK, evidence_http_status=True),
            "http_int_subclass": _with(OAUTH_OK, evidence_http_status=IntSub(200)),
            "http_rigged_eq": _with(OAUTH_OK, evidence_http_status=AlwaysEqual()),
            "both_revoke_legs": _with(OAUTH_OK, revoke_key="confirmed"),
        }
        for probe in ("probe_api", "probe_refresh"):
            for label, value in (("authorized", "authorized"), ("none", None), ("true", True), ("int_401", 401),
                                 ("str_401", "401"), ("upper", "UNAUTHORIZED"), ("spaced", "unauthorized "),
                                 ("bytes", b"unauthorized"), ("str_subclass", StrSub("unauthorized")),
                                 ("rigged_eq", AlwaysEqual()), ("error", "error:TimeoutError")):
                oauth[f"{probe}_{label}"] = _with(OAUTH_OK, **{"evidence_" + probe: value})
            oauth[f"{probe}_missing"] = _without(OAUTH_OK, "evidence_" + probe)
        api_key = {
            "accepted_false": _with(KEY_OK, evidence_accepted=False),
            "accepted_one": _with(KEY_OK, evidence_accepted=1),
            "accepted_str": _with(KEY_OK, evidence_accepted="true"),
            "accepted_missing": _without(KEY_OK, "evidence_accepted"),
            "probe_authorized": _with(KEY_OK, evidence_probe="authorized"),
            "probe_none": _with(KEY_OK, evidence_probe=None),
            "probe_str_subclass": _with(KEY_OK, evidence_probe=StrSub("unauthorized")),
            "probe_missing": _without(KEY_OK, "evidence_probe"),
            "revoke_key_pending": _with(KEY_OK, revoke_key="pending"),
            "halt_pending": _with(KEY_OK, halt="pending"),
            "cancel_pending": _with(KEY_OK, cancel_all="pending"),
            # One probe is the API-key rule; an OAuth venue cannot confirm on it.
            "not_kalshi": _with(KEY_OK, venue="gemini"),
            "oauth_evidence_on_a_key_leg": _with(KEY_OK, revoke_evidence=OAUTH_OK["revoke_evidence"]),
        }
        for family, cases in (("oauth", oauth), ("api_key", api_key)):
            for label, result in cases.items():
                with self.subTest(family=family, case=label):
                    # A fully evidenced venue beside it does not lend its evidence.
                    out = self._switch([Says(dict(OAUTH_OK)), Says(result)]).kill()
                    self.assertIs(type(out["status"]), str)
                    self.assertEqual((out["status"], out["halt"]), ("pending", "confirmed"))
                    self.assertEqual([v["status"] for v in out["venues"]], ["confirmed", "confirmed", "pending"])
                    self.assertIs(type(out["venues"][2]["status"]), str)

    def test_real_venue_kills_carry_the_evidence_the_switch_needs(self) -> None:
        log: list = []
        kalshi = KalshiKill(self.book.halt, FakeTransport(log=log), halted=self.book.halted)
        coinbase = CoinbaseKill(self.book.halt, MockCoinbase(orders=["c1"]), halted=self.book.halted)
        _, gemini = self._gemini()
        out = self._switch([kalshi, coinbase, gemini]).kill()
        self.assertEqual((out["status"], [v["status"] for v in out["venues"]]), ("confirmed", ["confirmed"] * 4))
        self.assertEqual(out["venues"][1]["revoke_evidence"], {"accepted": True, "probe": "unauthorized"})
        for venue in out["venues"][2:]:
            self.assertEqual(venue["revoke_evidence"], {"http_status": 200, "probe_api": "unauthorized",
                                                        "probe_refresh": "unauthorized"})

    def test_every_kill_calls_every_venue_again(self) -> None:
        """Nothing is kept from an earlier kill: a venue that confirmed is asked again, and believed anew."""
        venue = Says(dict(OAUTH_OK))
        mock, gemini = self._gemini()
        switch = self._switch([venue, gemini])
        self.assertEqual(switch.kill()["status"], "confirmed")
        venue.result = _with(OAUTH_OK, status="pending", revoke_grant="pending", evidence_probe_api="authorized")
        mock.api_reply = "authorized"
        out = switch.kill()
        self.assertEqual((venue.calls, mock.log.count("revoke")), (2, 2))
        self.assertEqual((out["status"], [v["status"] for v in out["venues"]]),
                         ("pending", ["confirmed", "pending", "pending"]))
        venue.result = dict(OAUTH_OK)
        # The grant looked live in that kill, so its earlier cancel confirm is gone: the cancel
        # must list again, which needs the host to re-authorise the mock.
        mock.api_reply, mock.granted = None, True
        out = switch.kill()
        self.assertEqual((venue.calls, mock.log.count("revoke")), (3, 3))
        self.assertEqual(out["status"], "confirmed")


class OneVenueNeverSkipsAnotherTests(Base):
    def test_a_cancelled_venue_call_does_not_skip_the_other_venues(self) -> None:
        """``asyncio.CancelledError`` is a ``BaseException``: it must stay inside its own venue."""
        cancelled = MockCoinbase(orders=["cb-1"])
        cancelled.api_reply = asyncio.CancelledError()
        first = CoinbaseKill(self.book.halt, cancelled, halted=self.book.halted)
        gemini_mock, gemini = self._gemini()
        log: list = []
        kalshi = KalshiKill(self.book.halt, FakeTransport(log=log), halted=self.book.halted)
        out = self._switch([first, gemini, kalshi]).kill()
        self.assertEqual((out["status"], out["halt"]), ("pending", "confirmed"))
        self.assertEqual(out["venues"][1], {"venue": "CoinbaseKill", "status": "pending",
                                            "detail": {"kill": "error:CancelledError"}})
        self.assertEqual([v["status"] for v in out["venues"]], ["confirmed", "pending", "confirmed", "confirmed"])
        self.assertEqual((gemini_mock.open, gemini_mock.log.count("revoke")), ([], 1))
        self.assertIn("revoke_api_key", log)

    def test_any_base_exception_from_a_venue_is_pending_and_the_rest_still_run(self) -> None:
        class Odd(BaseException):
            pass
        for exc in (asyncio.CancelledError(), GeneratorExit(), Odd(), RuntimeError("down")):
            with self.subTest(exc=type(exc).__name__):
                mock, gemini = self._gemini()
                last = Says(dict(OAUTH_OK))
                out = self._switch([Raises(exc), gemini, Raises(exc), last]).kill()
                self.assertEqual(out["status"], "pending")
                self.assertEqual([v["status"] for v in out["venues"]],
                                 ["confirmed", "pending", "confirmed", "pending", "confirmed"])
                for index in (1, 3):
                    self.assertEqual(out["venues"][index], {"venue": "Raises", "status": "pending", "detail": {
                        "kill": "error:" + type(exc).__name__}})
                self.assertEqual((mock.log.count("revoke"), last.calls), (1, 1))

    def test_keyboard_interrupt_and_system_exit_are_raised_only_after_every_venue_was_attempted(self) -> None:
        for exc_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc=exc_type.__name__):
                mock, gemini = self._gemini()
                last = Says(dict(OAUTH_OK))
                exc = exc_type()
                with self.assertRaises(exc_type) as ctx:
                    self._switch([Raises(exc), gemini, last]).kill()
                self.assertIs(ctx.exception, exc)
                self.assertEqual((mock.open, mock.log.count("revoke"), last.calls), ([], 1, 1))
                self.assertIs(self.book.halted(), True)

    def test_an_interrupt_from_the_halt_still_reaches_the_paper_cancel_and_every_venue(self) -> None:
        mock, gemini = self._gemini()

        def interrupted() -> None:
            self.book.halt()
            raise KeyboardInterrupt
        switch = KillSwitch(interrupted, [gemini], paper_book=self.book)
        with self.assertRaises(KeyboardInterrupt):
            switch.kill()
        self.assertEqual((self.book.halted(), mock.open, mock.log.count("revoke")), (True, [], 1))

    def test_the_halt_and_the_paper_cancel_run_first_in_the_calling_thread(self) -> None:
        seen = {}
        book = self.book

        def halt() -> None:
            seen["halt_thread"] = threading.get_ident()
            book.halt()

        class Looks:
            def kill(self) -> dict:
                seen["venue_thread"] = threading.get_ident()
                seen["halted_at_venue"] = PaperBook.halted(book)
                seen["daemon"] = threading.current_thread().daemon
                return dict(OAUTH_OK)
        out = KillSwitch(halt, [Looks()], paper_book=book).kill()
        self.assertEqual(out["status"], "confirmed")
        self.assertEqual(seen["halt_thread"], threading.get_ident())
        self.assertNotEqual(seen["venue_thread"], threading.get_ident())
        self.assertEqual((seen["halted_at_venue"], seen["daemon"]), (True, True))


class HangingVenueTests(Base):
    def test_a_venue_that_hangs_times_out_and_does_not_stall_the_other_venues(self) -> None:
        hung = self._hang()
        mock, gemini = self._gemini()
        before = Says(dict(OAUTH_OK))
        switch = self._switch([before, hung, gemini], timeout=HANG_TIMEOUT_S)
        started = time.monotonic()
        out = switch.kill()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, PROMPT_S)
        self.assertGreaterEqual(elapsed, HANG_TIMEOUT_S * 0.9)  # it did wait the bound for the hung venue
        self.assertEqual((out["status"], out["halt"]), ("pending", "confirmed"))
        self.assertEqual(out["venues"][2], {"venue": "Hangs", "status": "pending", "detail": {"kill": "timeout"}})
        # The venue after the hung one was cancelled, revoked and probed, and confirms.
        self.assertEqual([v["status"] for v in out["venues"]], ["confirmed", "confirmed", "pending", "confirmed"])
        self.assertEqual((mock.open, mock.log[-3:]), ([], ["revoke", "probe_api", "probe_refresh"]))
        self.assertIs(hung.entered.is_set(), True)

    def test_a_later_kill_reports_in_flight_and_never_starts_a_second_call_on_the_hung_venue(self) -> None:
        hung = self._hang()
        mock, gemini = self._gemini()
        switch = self._switch([hung, gemini], timeout=HANG_TIMEOUT_S)
        self.assertEqual(switch.kill()["venues"][1]["detail"], {"kill": "timeout"})
        for revokes in (2, 3):
            started = time.monotonic()
            out = switch.kill()
            # The hung venue is not waited for again: nothing else is in flight.
            self.assertLess(time.monotonic() - started, HANG_TIMEOUT_S * 0.9)
            self.assertEqual(out["status"], "pending")
            self.assertEqual(out["venues"][1], {"venue": "Hangs", "status": "pending", "detail": {"kill": "in_flight"}})
            # Every other leg still ran afresh.
            self.assertEqual((out["halt"], out["venues"][0]["status"], out["venues"][2]["status"]),
                             ("confirmed",) * 3)
            self.assertEqual(mock.log.count("revoke"), revokes)
        self.assertEqual((hung.calls, hung.most_running), (1, 1))
        # Once the hung call returns, its late result is discarded and the next kill calls it anew.
        hung.release.set()
        switch._in_flight[0].join(PROMPT_S)
        out = switch.kill()
        self.assertEqual((hung.calls, hung.most_running), (2, 1))
        self.assertEqual((out["status"], [v["status"] for v in out["venues"]]), ("confirmed", ["confirmed"] * 3))

    def test_concurrent_kills_with_a_hung_venue_all_return_and_call_it_once(self) -> None:
        hung = self._hang()
        mock, gemini = self._gemini()
        switch = self._switch([hung, gemini], timeout=HANG_TIMEOUT_S)
        results: list = []

        def run() -> None:
            results.append(switch.kill())
        threads = [threading.Thread(target=run, daemon=True) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(PROMPT_S)
        self.assertEqual([thread.is_alive() for thread in threads], [False] * 3)
        self.assertEqual(sorted(r["venues"][1]["detail"]["kill"] for r in results),
                         ["in_flight", "in_flight", "timeout"])
        self.assertEqual({r["status"] for r in results}, {"pending"})
        self.assertEqual((hung.calls, hung.most_running, mock.log.count("revoke")), (1, 1, 3))

    def test_a_slow_venue_inside_the_bound_is_waited_for(self) -> None:
        class Slow(Says):
            def kill(self) -> object:
                time.sleep(0.2)
                return super().kill()
        out = self._switch([Slow(dict(OAUTH_OK)), Slow(dict(OAUTH_OK))], timeout=30.0).kill()
        self.assertEqual((out["status"], [v["status"] for v in out["venues"]]), ("confirmed", ["confirmed"] * 3))

    def test_the_bound_is_a_positive_finite_number_and_defaults_to_ten_seconds(self) -> None:
        self.assertEqual(DEFAULT_VENUE_TIMEOUT_S, 10.0)
        self.assertEqual(KillSwitch(self.book.halt, [], paper_book=self.book)._venue_timeout_s, 10.0)
        for bad in (0, -1, 0.0, float("inf"), float("nan"), None, "10", True):
            with self.subTest(timeout=bad), self.assertRaises(ValueError):
                KillSwitch(self.book.halt, [], paper_book=self.book, venue_timeout_s=bad)
        with self.assertRaises(ValueError):
            plugin.compose(FakeCore(), self.path, Caps(10**9, 10**6), venue_timeout_s=0)
        composed = plugin.compose(FakeCore(), self.path, Caps(10**9, 10**6), venue_timeout_s=2)
        self.assertEqual(composed.kill_switch._venue_timeout_s, 2)


if __name__ == "__main__":
    unittest.main()
