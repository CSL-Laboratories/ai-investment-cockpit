"""Integrated mock kill drill through the signed plugin loaded by the real Recursion core.

Paper and mock only. Real here: the core (loader, approval gate, authority ledger,
connection breaker, ``CoreService``), the plugin entry's composition (one ``PaperBook``,
the ``OrderGateway`` and the ``KillSwitch`` on it), and the ``CoinbaseKill`` and
``GeminiKill`` classes. Mock here: the two venues, which are the in-memory transports of
``test_coinbase.py`` and ``test_gemini.py``; no network, credential or keyring is touched.
Kalshi is not part of this drill.

The drill: several paper orders are booked through permission -> operator approval ->
authority profile -> gateway, and one more is approved but not submitted. Then the
composed switch kills. The switch reports ``confirmed`` only when the halt reads back on
the book, every working paper order is cancelled, and each OAuth venue's revoke answered
exactly the int 200 with both probes then reading exactly ``"unauthorized"``. Anything
less is ``pending``. After the kill the host faults the active run at the core
(``run.fault``), so a later attempt is refused there too (``run_faulted``).

The paper book keeps orders and their status only; it has no cash or reservation ledger.
"Released" is therefore asserted as: no order is left working.

Skipped only when the core is absent, with the same rule and reason as
``test_e2e_recursion_core.py``, whose world builder this reuses.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest import mock

from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.core_client import CoreClient, CoreRefused
from cockpit.paper.gateway import OrderGateway, Refused
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.killswitch import KillSwitch
from cockpit.paper.revoke import RevokeReply
from tests.paper import test_e2e_recursion_core as e2e
from tests.paper.test_coinbase import MockCoinbase
from tests.paper.test_gemini import MockGemini

OAUTH_VENUES = ("coinbase", "gemini")
BOOKED = ("drill-1", "drill-2", "drill-3")
QUANTITY = 1  # 10_000 each, inside the profile's 120_000 total


class _RaisingKill:
    """A venue kill that fails outright."""

    def __init__(self) -> None:
        self.calls = 0

    def kill(self) -> dict:
        self.calls += 1
        raise RuntimeError("venue kill failed")


@unittest.skipIf(e2e.CORE_SKIP is not None, e2e.AWAITING_328)
class KillDrillTests(unittest.TestCase):
    def _drill(self, *, first_venues: tuple = (), halt_patch: object = None) -> e2e.World:
        """Build the world with both mock venues composed, book ``BOOKED`` and hold one approval."""
        self.coinbase = MockCoinbase(orders=["cb-1", "cb-2", "cb-3"])
        self.gemini = MockGemini(orders=["gm-1", "gm-2"])
        self.mocks = {"coinbase": self.coinbase, "gemini": self.gemini}

        def venue_kills(book) -> list:
            halt = book.halt if halt_patch is None else halt_patch
            return [*first_venues, CoinbaseKill(halt, self.coinbase, halted=book.halted),
                    GeminiKill(halt, self.gemini, halted=book.halted)]
        w = e2e.build_world(self, venue_kills=venue_kills)
        self.assertIs(type(w.kill_switch), KillSwitch)
        self.assertIs(type(w.gateway), OrderGateway)
        for intent_id in BOOKED:
            intent = e2e.sized_buy(w, intent_id, quantity=QUANTITY)
            approval = w.gateway.request_approval(intent)
            self.assertTrue(w.decide(approval)["ok"])
            self.assertEqual(w.gateway.submit(intent, approval["approval_id"], w.profile)["status"],
                             "paper_working")
        self.held = e2e.sized_buy(w, "drill-held", quantity=QUANTITY)
        self.held_effect = w.gateway.risk_pass(self.held)
        self.held_approval = w.gateway.request_approval(self.held)
        self.assertTrue(w.decide(self.held_approval)["ok"])
        self.assertEqual(w.book.orders(), [(i, n, "working") for n, i in enumerate(BOOKED, 1)])
        self.assertEqual((w.book.working(), w.book.halted(), len(w.drawn)), (3, False, 4))
        self.assertEqual(w.plugin_ops, ["effect.request", "effect.authorize"] * 3 + ["effect.request"])
        return w

    def _refused(self, code: str, call) -> None:
        with self.assertRaises(Refused) as ctx:
            call()
        self.assertEqual((ctx.exception.code, ctx.exception.cause), (code, None))

    def _venues(self, out: dict) -> dict:
        self.assertEqual(set(out), {"status", "halt", "venues"})  # no other confirm input exists
        return {venue["venue"]: venue for venue in out["venues"]}

    def _paper_cancelled(self, w: e2e.World, venues: dict, cancelled: int = 3) -> None:
        self.assertEqual((venues["paper"]["status"], venues["paper"]["cancelled"]), ("confirmed", cancelled))
        self.assertEqual(w.book.orders(), [(i, n, "cancelled") for n, i in enumerate(BOOKED, 1)])
        self.assertEqual(w.book.working(), 0)

    def _revoke_evidence(self, name: str, venue: dict) -> tuple:
        """(revoke answered exactly int 200, api probe, refresh probe) as the venue kill recorded them."""
        reply = self.mocks[name].revoke_reply
        answered = (type(reply) is RevokeReply and type(reply.http_status) is int and reply.http_status == 200
                    and "revoke_grant" not in venue["detail"])
        return answered, venue["detail"]["revoke_probe_api"], venue["detail"]["revoke_probe_refresh"]

    def _pending_never_confirmed(self, w: e2e.World, out: dict) -> dict:
        self.assertIs(type(out["status"]), str)
        self.assertEqual(out["status"], "pending")
        return self._venues(out)

    def test_drill_confirms_then_refuses_new_risk_and_still_allows_cancels(self) -> None:
        w = self._drill()
        ops, drawn = list(w.plugin_ops), len(w.drawn)
        out = w.kill_switch.kill()
        venues = self._venues(out)
        self.assertEqual(list(venues), ["paper", "coinbase", "gemini"])
        # Halt: confirmed on the book read, by a connection other than the gateway's own.
        self.assertEqual(out["halt"], "confirmed")
        self.assertIs(w.book.halted(), True)
        self.assertEqual(w.book.halt_seq(), len(BOOKED) + 1)
        # Paper: every working order cancelled, none left working.
        self._paper_cancelled(w, venues)
        # Each OAuth venue: orders gone, revoke answered exactly int 200, both probes unauthorized.
        for name in OAUTH_VENUES:
            with self.subTest(venue=name):
                venue = venues[name]
                self.assertEqual((venue["status"], venue["halt"], venue["revoke_grant"]),
                                 ("confirmed", "confirmed", "confirmed"))
                self.assertEqual(self._revoke_evidence(name, venue), (True, "unauthorized", "unauthorized"))
                self.assertIs(type(venue["detail"]["revoke_probe_api"]), str)
                self.assertIs(type(venue["detail"]["revoke_probe_refresh"]), str)
                self.assertEqual(self.mocks[name].open, [])
                self.assertEqual(self.mocks[name].log[-3:], ["revoke", "probe_api", "probe_refresh"])
        self.assertEqual((venues["coinbase"]["cancel_orders"], venues["coinbase"]["cancelled"]),
                         ("confirmed", ["cb-1", "cb-2", "cb-3"]))
        self.assertEqual(venues["gemini"]["cancel_all"], "confirmed")
        self.assertIs(type(out["status"]), str)
        self.assertEqual(out["status"], "confirmed")
        # The approved-but-unsubmitted order is refused at submit: no core call, nothing booked.
        self._refused("order_halted",
                      lambda: w.gateway.submit(self.held, self.held_approval["approval_id"], w.profile))
        # New risk draws no approval.
        self._refused("order_halted", lambda: w.gateway.request_approval(replace(self.held, intent_id="drill-new")))
        self.assertEqual((w.plugin_ops, len(w.drawn)), (ops, drawn))
        self._paper_cancelled(w, venues)
        # Cancels stay allowed while halted.
        self.assertEqual(w.gateway.cancel_all(), 0)
        self.assertEqual(w.book.working(), 0)

    def test_after_the_kill_the_host_faults_the_run_and_the_core_refuses_it(self) -> None:
        w = self._drill()
        plugin_core = CoreClient(w.plugin_transport)
        self.assertEqual(w.kill_switch.kill()["status"], "confirmed")
        # Only the host may fault a run; the plugin's own client is refused.
        with self.assertRaises(CoreRefused) as ctx:
            plugin_core.call("run.fault", run_id=e2e.RUN)
        self.assertEqual(ctx.exception.code, "host_only")
        self.assertEqual(w.breaker.check(e2e.RUN, e2e.CONNECTION, e2e.PLUGIN_ID)["provider"], e2e.PROVIDER)
        self.assertEqual(w.host("run.fault", run_id=e2e.RUN), {"ok": True, "protocol": "csl-core/v0", "result": {}})
        # Past the gateway's own halt, the core refuses the run for the held approval and for new risk.
        drawn = len(w.drawn)
        attempts = (lambda: plugin_core.authorize(self.held_effect, self.held_approval["approval_id"], w.profile),
                    lambda: plugin_core.request_approval(self.held_effect),
                    lambda: plugin_core.request_approval(dict(self.held_effect, amount_minor=1)))
        for attempt in attempts:
            with self.assertRaises(CoreRefused) as ctx:
                attempt()
            self.assertEqual(ctx.exception.code, "run_faulted")
        with self.assertRaises(e2e.CoreError) as core:
            w.breaker.check(e2e.RUN, e2e.CONNECTION, e2e.PLUGIN_ID)
        self.assertEqual(core.exception.code, "run_faulted")
        self.assertEqual(len(w.drawn), drawn)
        self.assertEqual([status for _, _, status in w.book.orders()], ["cancelled"] * 3)
        # The gateway still answers with its own halt first.
        self._refused("order_halted",
                      lambda: w.gateway.submit(self.held, self.held_approval["approval_id"], w.profile))

    def test_kill_twice_is_idempotent(self) -> None:
        w = self._drill()
        first = w.kill_switch.kill()
        halt_seq, logs = w.book.halt_seq(), {name: list(m.log) for name, m in self.mocks.items()}
        second = w.kill_switch.kill()
        self.assertEqual((first["status"], second["status"], second["halt"]), ("confirmed",) * 3)
        venues = self._venues(second)
        self._paper_cancelled(w, venues, cancelled=0)
        self.assertEqual(w.book.halt_seq(), halt_seq)
        for name in OAUTH_VENUES:
            with self.subTest(venue=name):
                self.assertEqual(venues[name], self._venues(first)[name])
                self.assertEqual(self.mocks[name].log, logs[name])  # nothing is cancelled or revoked again
                self.assertEqual(self.mocks[name].log.count("revoke"), 1)
        self._refused("order_halted",
                      lambda: w.gateway.submit(self.held, self.held_approval["approval_id"], w.profile))

    def test_revoke_200_with_one_probe_authorized_stays_pending(self) -> None:
        for name in OAUTH_VENUES:
            for attr, authorized, unauthorized in (("api_reply", "revoke_probe_api", "revoke_probe_refresh"),
                                                   ("refresh_reply", "revoke_probe_refresh", "revoke_probe_api")):
                with self.subTest(venue=name, probe=authorized):
                    w = self._drill()
                    setattr(self.mocks[name], attr, "authorized")
                    for _ in range(2):  # a retry does not confirm it either
                        out = w.kill_switch.kill()
                        venues = self._pending_never_confirmed(w, out)
                        venue = venues[name]
                        self.assertEqual((venue["status"], venue["revoke_grant"]), ("pending", "pending"))
                        self.assertNotIn("revoke_grant", venue["detail"])  # the revoke itself answered 200
                        self.assertEqual((venue["detail"][authorized], venue["detail"][unauthorized]),
                                         ("authorized", "unauthorized"))
                    # Everything else is done and says so; only the switch as a whole is not.
                    other = venues[OAUTH_VENUES[1 - OAUTH_VENUES.index(name)]]
                    self.assertEqual((out["halt"], other["status"]), ("confirmed", "confirmed"))
                    self._paper_cancelled(w, venues, cancelled=0)

    def test_revoke_not_exactly_int_200_stays_pending(self) -> None:
        replies = {"500": (RevokeReply(500), "rejected"), "float_200": (RevokeReply(200.0), "rejected"),
                   "str_200": (RevokeReply("200"), "rejected"), "true": (RevokeReply(True), "rejected"),
                   "bare_int_200": (200, "unreadable_response"),
                   "raises": (ConnectionError("down"), "error:ConnectionError")}
        for name in OAUTH_VENUES:
            for label, (reply, detail) in replies.items():
                with self.subTest(venue=name, reply=label):
                    w = self._drill()
                    self.mocks[name].revoke_reply = reply
                    self.mocks[name].api_reply = self.mocks[name].refresh_reply = "unauthorized"
                    out = w.kill_switch.kill()
                    venues = self._pending_never_confirmed(w, out)
                    venue = venues[name]
                    self.assertEqual((venue["status"], venue["revoke_grant"]), ("pending", "pending"))
                    # Both probes unauthorized is still not a confirm without the 200.
                    self.assertEqual(venue["detail"]["revoke_grant"], detail)
                    self.assertEqual(self._revoke_evidence(name, venue), (False, "unauthorized", "unauthorized"))
                    other = venues[OAUTH_VENUES[1 - OAUTH_VENUES.index(name)]]
                    self.assertEqual((out["halt"], other["status"]), ("confirmed", "confirmed"))
                    self._paper_cancelled(w, venues)

    def test_a_venue_kill_that_raises_stays_pending_and_the_others_still_run(self) -> None:
        raising = _RaisingKill()
        w = self._drill(first_venues=(raising,))
        for calls in (1, 2):
            out = w.kill_switch.kill()
            venues = self._pending_never_confirmed(w, out)
            self.assertEqual(list(venues), ["paper", "_RaisingKill", "coinbase", "gemini"])
            self.assertEqual(venues["_RaisingKill"], {"venue": "_RaisingKill", "status": "pending",
                                                      "detail": {"kill": "error:RuntimeError"}})
            self.assertEqual(raising.calls, calls)
            self.assertEqual(out["halt"], "confirmed")
            for name in OAUTH_VENUES:
                self.assertEqual(venues[name]["status"], "confirmed")
                self.assertEqual(self._revoke_evidence(name, venues[name]), (True, "unauthorized", "unauthorized"))
                self.assertEqual(self.mocks[name].open, [])
        self._paper_cancelled(w, venues, cancelled=0)
        self._refused("order_halted",
                      lambda: w.gateway.submit(self.held, self.held_approval["approval_id"], w.profile))

    def test_halt_callable_that_returns_without_halting_the_book_stays_pending(self) -> None:
        calls = []

        def no_halt(*_: object) -> int:
            calls.append("halt")
            return 1
        # The composed switch takes the gateway's halt; swap it for one that only returns.
        with mock.patch.object(OrderGateway, "halt", no_halt):
            w = self._drill(halt_patch=no_halt)
        out = w.kill_switch.kill()
        venues = self._pending_never_confirmed(w, out)
        self.assertEqual(calls, ["halt"] * 3)  # the switch and both venue kills each called it
        self.assertEqual(out["halt"], "not_halted")
        self.assertIs(w.book.halted(), False)
        # The risk-reducing legs still ran, but no venue's halt leg is confirmed either.
        self._paper_cancelled(w, venues)
        for name in OAUTH_VENUES:
            venue = venues[name]
            self.assertEqual((venue["status"], venue["halt"], venue["detail"]["halt"]),
                             ("pending", "pending", "not_halted"))
            self.assertEqual(self._revoke_evidence(name, venue), (True, "unauthorized", "unauthorized"))


if __name__ == "__main__":
    unittest.main()
