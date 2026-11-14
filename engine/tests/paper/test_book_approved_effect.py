"""The booking writes the approved effect, never a later read of the intent (review finding F4).

``PaperBook._book`` re-runs the risk pass on the intent and compares it with the granted
effect. Everything written after that (the order row, the cash or share reservation, the
audit row, the reply) comes from the grant's approved effect, the one the operator approved
and the core charged. A change to the intent object after the re-check therefore cannot
change what is booked.

Deterministic, no sleeps: the gateway's run check is called inside the booking transaction,
after the re-check, so a run check that mutates the intent is a pause at exactly that point.
A mutation before the re-check (during the core call) is refused ``leftover_charge``.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest

from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import AUTHORITY, FakeCore

ACCT, OTHER_ACCT = "paper-acct", "other-acct"
CASH = 10**9
# Every booked field, changed to a value that would itself be bookable on this book.
MUTATIONS = {"quantity": 99, "limit_price_minor": 1, "intent_id": "swapped", "account": OTHER_ACCT,
             "instrument": "MSFT", "side": "sell", "run_id": "run-ok", "connection": "conn-ok"}


class BookApprovedEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-f4-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: CASH, OTHER_ACCT: CASH},
                    holdings={(ACCT, "AAPL"): 500, (ACCT, "MSFT"): 500, (OTHER_ACCT, "AAPL"): 500})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**12)
        self.inside_booking = None  # set by a test: called inside the booking transaction
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                               caps=Caps(1_000_000, 100), allowlist=frozenset({"AAPL", "MSFT"}),
                               run_check=self._run_check)

    def _run_check(self, run_id: str, connection: str, plugin_id: str) -> dict:
        if self.inside_booking is not None:
            hook, self.inside_booking = self.inside_booking, None
            hook()
        return self.core.check(run_id, connection, plugin_id)

    def _approved(self, intent_id: str) -> tuple:
        intent = self.gw.size_buy(intent_id=intent_id, account=ACCT, instrument="AAPL", limit_price_minor=10_000,
                                  conviction_bps=5_000, run_id="run-1", connection="conn-a")
        self.assertEqual(intent.quantity, 50)
        effect = self.gw.risk_pass(intent)
        approval = self.gw.request_approval(intent)
        self.core.operator_approves(approval["approval_id"])
        self.assertEqual(approval["digest"], hashlib.sha256(canonical(effect)).hexdigest())
        return intent, effect, approval

    def _rows(self) -> list:
        db = sqlite3.connect(self.path)
        try:
            return db.execute("SELECT intent_id, account, instrument, side, quantity, limit_price_minor, status"
                              " FROM orders ORDER BY seq").fetchall()
        finally:
            db.close()

    def _funds(self) -> tuple:
        return (self.book.available_cash_minor(ACCT), self.book.available_cash_minor(OTHER_ACCT),
                self.book.held_shares(ACCT, "AAPL"), self.book.held_shares(ACCT, "MSFT"),
                self.book.held_shares(OTHER_ACCT, "AAPL"))

    def test_intent_changed_inside_the_booking_books_the_approved_effect(self) -> None:
        booked = []
        cases = [{field: value} for field, value in MUTATIONS.items()] + [dict(MUTATIONS)]
        for n, changes in enumerate(cases):
            with self.subTest(changed=sorted(changes)):
                intent_id = "f4-%d" % n
                intent, effect, approval = self._approved(intent_id)
                funds = self._funds()
                fired = []

                def mutate() -> None:
                    for field, value in changes.items():
                        object.__setattr__(intent, field, value)
                    fired.append(True)
                self.inside_booking = mutate
                out = self.gw.submit(intent, approval["approval_id"], AUTHORITY)
                self.assertEqual(fired, [True])  # the change landed after the re-check, inside the booking
                self.assertEqual({field: getattr(intent, field) for field in changes}, changes)
                # Reply, order row, reservation and audit row are the approved effect's.
                payload = effect["payload"]
                self.assertEqual((payload["quantity"], payload["limit_price_minor"], effect["amount_minor"]),
                                 (50, 10_000, 500_000))
                self.assertEqual(out, {"status": "paper_working", "intent_id": intent_id, "seq": out["seq"],
                                       "notional_minor": 500_000, "digest": approval["digest"]})
                booked.append((intent_id, ACCT, "AAPL", "buy", 50, 10_000, "working"))
                self.assertEqual(self._rows(), booked)
                self.assertEqual(self._funds(), (funds[0] - 500_000,) + funds[1:])
                row = self.book.audit()[-1]
                self.assertEqual((row["event"], row["stage"], row["intent_id"], row["run_id"], row["connection"],
                                  row["effect_sha256"]),
                                 ("booked", "book", intent_id, "run-1", "conn-a", approval["digest"]))
        self.assertEqual(self.core.spent, 500_000 * len(cases))  # each charge matches its booking

    def test_intent_changed_before_the_recheck_is_a_leftover_charge(self) -> None:
        for n, (field, value) in enumerate(MUTATIONS.items()):
            with self.subTest(changed=field):
                intent, effect, approval = self._approved("pre-%d" % n)
                funds, core = self._funds(), self.core

                def mutate_during_core_call(raw: bytes) -> bytes:
                    out = core(raw)
                    object.__setattr__(intent, field, value)
                    return out
                self.gw._core = CoreClient(mutate_during_core_call)
                with self.assertRaises(Refused) as ctx:
                    self.gw.submit(intent, approval["approval_id"], AUTHORITY)
                self.gw._core = CoreClient(core)
                self.assertEqual((ctx.exception.code, ctx.exception.cause), ("leftover_charge", "book_direct_refused"))
                row = self.book.audit()[-1]
                self.assertEqual((row["stage"], row["code"], row["intent_id"], row["effect_sha256"]),
                                 ("book", "leftover_charge:book_direct_refused", "pre-%d" % n, approval["digest"]))
                self.assertEqual((self._rows(), self._funds()), ([], funds))

    def test_sell_changed_inside_the_booking_books_the_approved_sell(self) -> None:
        # 700 bps of the 1_000_000 notional cap at 10_000 is 7 shares, under the 500 held.
        sell = self.gw.size_sell(intent_id="f4-sell", account=ACCT, instrument="AAPL", limit_price_minor=10_000,
                                 conviction_bps=700, run_id="run-1", connection="conn-a")
        self.assertEqual(sell.quantity, 7)
        approval = self.gw.request_approval(sell)
        self.core.operator_approves(approval["approval_id"])

        def mutate() -> None:
            for field, value in (("quantity", 100), ("instrument", "MSFT"), ("account", OTHER_ACCT)):
                object.__setattr__(sell, field, value)
        self.inside_booking = mutate
        out = self.gw.submit(sell, approval["approval_id"], AUTHORITY)
        self.assertEqual((out["intent_id"], out["notional_minor"]), ("f4-sell", 70_000))
        self.assertEqual(self._rows(), [("f4-sell", ACCT, "AAPL", "sell", 7, 10_000, "working")])
        self.assertEqual(self._funds(), (CASH, CASH, 493, 500, 500))


if __name__ == "__main__":
    unittest.main()
