"""The booking writes the approved effect, never a later read of the intent (#317 alone).

Lucas, Nov 11: "Yes, backport the approval-to-booking fix to #317". ``PaperBook._book``
re-runs the risk pass on the intent and compares it with the granted effect. Everything
written after that (the order row and the reply) comes from the grant's approved effect,
the one the operator approved and the core charged, so a change to the intent object after
the re-check cannot change what is booked. #320 carries the same rule with cash, holdings,
sizing and the run check; on a merge its version of this file replaces this one.

Deterministic, no sleeps: the book's booking transaction is wrapped on this test's own book
instance so the change lands after the re-check and before the row is written, which is the
window this closes. A change before the re-check (during the core call) is refused
``leftover_charge``.

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

ACCT = "paper-acct"
# Every booked field, changed to a value that would itself pass the risk pass.
MUTATIONS = {"quantity": 99, "limit_price_minor": 1, "intent_id": "swapped", "account": "other-acct",
             "instrument": "MSFT", "side": "sell", "run_id": "run-ok", "connection": "conn-ok"}


class BookApprovedEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-f4-317-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**12)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                               caps=Caps(1_000_000, 100))

    def _inside_booking(self, hook) -> None:
        """Run ``hook`` once, inside ``_book``, after its re-check and before the booking transaction."""
        tx = self.book._tx

        def wrapped(fn):
            del self.book._tx  # one shot: back to the class method
            hook()
            return tx(fn)
        self.book._tx = wrapped

    def _approved(self, intent_id: str, side: str = "buy") -> tuple:
        intent = Intent(intent_id=intent_id, account=ACCT, instrument="AAPL", side=side, quantity=50,
                        limit_price_minor=10_000, run_id="run-1", connection="conn-a")
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

    def test_intent_changed_inside_the_booking_books_the_approved_effect(self) -> None:
        booked = []
        cases = [{field: value} for field, value in MUTATIONS.items()] + [dict(MUTATIONS)]
        for n, changes in enumerate(cases):
            with self.subTest(changed=sorted(changes)):
                intent_id = "f4-%d" % n
                intent, effect, approval = self._approved(intent_id)
                fired = []

                def mutate() -> None:
                    for field, value in changes.items():
                        object.__setattr__(intent, field, value)
                    fired.append(True)
                self._inside_booking(mutate)
                out = self.gw.submit(intent, approval["approval_id"], AUTHORITY)
                self.assertEqual(fired, [True])  # the change landed after the re-check, inside the booking
                self.assertEqual({field: getattr(intent, field) for field in changes}, changes)
                self.assertEqual(out, {"status": "paper_working", "intent_id": intent_id, "seq": out["seq"],
                                       "notional_minor": 500_000, "digest": approval["digest"]})
                booked.append((intent_id, ACCT, "AAPL", "buy", 50, 10_000, "working"))
                self.assertEqual(self._rows(), booked)
        self.assertEqual(self.core.spent, 500_000 * len(cases))  # each charge matches its booking

    def test_sell_changed_inside_the_booking_books_the_approved_sell(self) -> None:
        intent, _, approval = self._approved("f4-sell", side="sell")

        def mutate() -> None:
            for field, value in (("quantity", 100), ("instrument", "MSFT"), ("side", "buy")):
                object.__setattr__(intent, field, value)
        self._inside_booking(mutate)
        out = self.gw.submit(intent, approval["approval_id"], AUTHORITY)
        self.assertEqual((out["intent_id"], out["notional_minor"]), ("f4-sell", 500_000))
        self.assertEqual(self._rows(), [("f4-sell", ACCT, "AAPL", "sell", 50, 10_000, "working")])

    def test_intent_changed_before_the_recheck_is_a_leftover_charge(self) -> None:
        for n, (field, value) in enumerate(MUTATIONS.items()):
            with self.subTest(changed=field):
                intent, _, approval = self._approved("pre-%d" % n)
                core = self.core

                def mutate_during_core_call(raw: bytes) -> bytes:
                    out = core(raw)
                    object.__setattr__(intent, field, value)
                    return out
                self.gw._core = CoreClient(mutate_during_core_call)
                try:
                    with self.assertRaises(Refused) as ctx:
                        self.gw.submit(intent, approval["approval_id"], AUTHORITY)
                finally:
                    self.gw._core = CoreClient(core)
                self.assertEqual((ctx.exception.code, ctx.exception.cause), ("leftover_charge", "book_direct_refused"))
                self.assertEqual(self._rows(), [])

    def test_duplicate_check_reads_the_approved_intent_id(self) -> None:
        """The pre-charge duplicate check uses the effect's intent id, the one that is booked."""
        first, _, approval = self._approved("dup-1")
        self.gw.submit(first, approval["approval_id"], AUTHORITY)
        second, _, approval = self._approved("dup-2")
        object.__setattr__(second, "intent_id", "dup-1")  # risk pass now says dup-1: same as booked
        with self.assertRaises(Refused) as ctx:
            self.gw.submit(second, approval["approval_id"], AUTHORITY)
        self.assertEqual(ctx.exception.code, "intent_duplicate")
        self.assertEqual(self.core.spent, 500_000)  # refused before the core charged


if __name__ == "__main__":
    unittest.main()
