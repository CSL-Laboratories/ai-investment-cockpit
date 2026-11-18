"""Lucas, Nov 11 (2:15am PST): "Yes, a Kalshi kill confirms on accepted revoke plus its one
unauthorized probe". Every other venue keeps its rule: revoke answered exactly 200 AND both
probes (``probe_api`` and ``probe_refresh``) ``unauthorized``.

Pinned two ways: through ``KillSwitch`` with the real venue kills on their in-memory mocks
(``KalshiKill`` on ``FakeTransport``, ``CoinbaseKill`` on ``MockCoinbase``, ``GeminiKill``
on ``MockGemini``), and with stub venues returning hand-made results, so the rule itself is
checked apart from any one adapter: the Kalshi-shaped evidence confirms only for the venue
``"kalshi"``, and an OAuth venue with only one probe unauthorized stays ``pending``.
Paper and mock only: no network, credential or keyring is touched.
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
from tests.paper.test_coinbase import MockCoinbase
from tests.paper.test_gemini import MockGemini
from tests.paper.test_kalshi import FakeTransport

KALSHI_EVIDENCE = {"accepted": True, "probe": "unauthorized"}
OAUTH_EVIDENCE = {"http_status": 200, "probe_api": "unauthorized", "probe_refresh": "unauthorized"}


def _new_book(path: str) -> None:
    if "cash_minor" in inspect.signature(create_book).parameters:
        create_book(path, cash_minor={"paper-acct": 0})
    else:
        create_book(path)


class _Stub:
    """A venue kill returning a fixed result: the rule is checked apart from any adapter."""

    def __init__(self, result: dict) -> None:
        self.result = result

    def kill(self) -> dict:
        return dict(self.result)


def _kalshi_result(**over) -> dict:
    out = {"venue": "kalshi", "status": "confirmed", "halt": "confirmed", "cancel_all": "confirmed",
           "revoke_key": "confirmed", "revoke_evidence": dict(KALSHI_EVIDENCE)}
    out.update(over)
    return out


def _oauth_result(venue: str, **evidence) -> dict:
    return {"venue": venue, "status": "confirmed", "halt": "confirmed", "cancel_orders": "confirmed",
            "revoke_grant": "confirmed", "revoke_evidence": dict(OAUTH_EVIDENCE, **evidence)}


class KalshiKillRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-kill-kalshi-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        _new_book(path)
        self.book = PaperBook(path)
        self.addCleanup(self.book.close)

    def _kill(self, venues) -> dict:
        return KillSwitch(self.book.halt, venues, paper_book=self.book, venue_timeout_s=5.0).kill()

    # --- Kalshi: accepted revoke plus its one unauthorized probe ---

    def test_kalshi_confirms_on_accepted_revoke_and_its_one_unauthorized_probe(self) -> None:
        transport = FakeTransport()
        out = self._kill([KalshiKill(self.book.halt, transport, halted=self.book.halted)])
        ks = out["venues"][1]
        self.assertEqual((out["status"], ks["status"], ks["revoke_key"]), ("confirmed", "confirmed", "confirmed"))
        self.assertEqual(ks["revoke_evidence"], KALSHI_EVIDENCE)  # one probe, no refresh probe exists
        self.assertNotIn("probe_refresh", ks["revoke_evidence"])
        self.assertEqual(transport.log.count("revoke_api_key"), 1)
        self.assertEqual(transport.log.count("probe"), 1)
        self.assertGreater(transport.log.index("probe"), transport.log.index("revoke_api_key"))

    def test_kalshi_without_an_accepted_revoke_or_an_unauthorized_probe_is_pending(self) -> None:
        cases = {
            "revoke_rejected": FakeTransport(revoke={"accepted": False}),
            "revoke_raised": FakeTransport(revoke=PermissionError()),
            "probe_authorized": FakeTransport(probe="authorized"),
            "probe_unreadable": FakeTransport(probe="maybe"),
            "probe_raised": FakeTransport(probe=TimeoutError()),
        }
        for name, transport in cases.items():
            with self.subTest(case=name):
                out = self._kill([KalshiKill(self.book.halt, transport, halted=self.book.halted)])
                self.assertEqual((out["status"], out["venues"][1]["status"]), ("pending", "pending"))

    def test_the_kalshi_rule_at_the_switch(self) -> None:
        self.assertEqual(self._kill([_Stub(_kalshi_result())])["status"], "confirmed")
        broken = {
            "accepted_not_true": _kalshi_result(revoke_evidence={"accepted": 1, "probe": "unauthorized"}),
            "probe_authorized": _kalshi_result(revoke_evidence={"accepted": True, "probe": "authorized"}),
            "probe_missing": _kalshi_result(revoke_evidence={"accepted": True}),
            "revoke_key_pending": _kalshi_result(revoke_key="pending"),
        }
        for name, result in broken.items():
            with self.subTest(case=name):
                out = self._kill([_Stub(result)])
                self.assertEqual((out["status"], out["venues"][1]["status"]), ("pending", "pending"))

    # --- every other venue: revoke 200 AND both probes unauthorized ---

    def test_a_non_kalshi_venue_never_confirms_on_the_kalshi_rule(self) -> None:
        for venue in ("coinbase", "gemini", "kalshi-demo", "Kalshi"):
            with self.subTest(venue=venue):
                out = self._kill([_Stub(_kalshi_result(venue=venue))])
                self.assertEqual((out["status"], out["venues"][1]["status"]), ("pending", "pending"))
                self.assertEqual(out["venues"][1]["evidence"], "missing")

    def test_a_non_kalshi_venue_still_needs_revoke_200_and_both_probes(self) -> None:
        for venue in ("coinbase", "gemini"):
            with self.subTest(venue=venue, case="all_three"):
                self.assertEqual(self._kill([_Stub(_oauth_result(venue))])["status"], "confirmed")
            one_short = {
                "only_probe_api": {"probe_refresh": "authorized"},
                "only_probe_refresh": {"probe_api": "authorized"},
                "refresh_probe_missing": {"probe_refresh": None},
                "revoke_not_200": {"http_status": 204},
            }
            for name, evidence in one_short.items():
                with self.subTest(venue=venue, case=name):
                    result = _oauth_result(venue, **evidence)
                    if evidence.get("probe_refresh", "") is None:
                        del result["revoke_evidence"]["probe_refresh"]
                    out = self._kill([_Stub(result)])
                    self.assertEqual((out["status"], out["venues"][1]["status"]), ("pending", "pending"))

    def test_real_oauth_venues_need_both_probes(self) -> None:
        for name, make, kill_cls in (("coinbase", lambda: MockCoinbase(orders=["c1"]), CoinbaseKill),
                                     ("gemini", lambda: MockGemini(orders=["g1"]), GeminiKill)):
            for broken in ("api_reply", "refresh_reply"):
                with self.subTest(venue=name, authorized=broken):
                    mock_venue = make()
                    setattr(mock_venue, broken, "authorized")  # one probe still authorized
                    out = self._kill([kill_cls(self.book.halt, mock_venue, halted=self.book.halted)])
                    self.assertEqual((out["status"], out["venues"][1]["status"]), ("pending", "pending"))
            with self.subTest(venue=name, authorized=None):
                out = self._kill([kill_cls(self.book.halt, make(), halted=self.book.halted)])
                self.assertEqual(out["status"], "confirmed")
                self.assertEqual(out["venues"][1]["revoke_evidence"], OAUTH_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
