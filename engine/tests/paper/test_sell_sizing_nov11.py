"""Sell sizing (Lucas, Nov 11, relayed 12:52am PST): "Size sells from conviction, capped at the
position held, no caller-set quantity".

``OrderGateway.size_sell`` sizes a sell from the conviction's share of the notional cap and
the quantity cap, like ``size_buy``, and caps it at the account's held shares available to
sell. It takes no quantity. A sell reaches approval or booking only as the unchanged intent
``size_sell`` returned; a hand-built sell, a sized buy whose side was turned into a sell and
a sized sell turned into a buy are all refused ``quantity_not_sized`` before any core call.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import AUTHORITY, FakeCore

ACCT = "paper-acct"
PRICE = 10_000
CAPS = Caps(max_notional_minor=1_000_000, max_quantity=60)  # 100 shares at PRICE by notional, 60 by count


class SellSizingTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-sell-sizing-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: 10**9},
                    holdings={(ACCT, "AAPL"): 40, (ACCT, "MSFT"): 500, (ACCT, "TSLA"): 0})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**12)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core), caps=CAPS,
                               allowlist=frozenset({"AAPL", "MSFT", "TSLA", "XYZ"}), run_check=self.core.check)

    def _sell(self, intent_id: str, conviction: int, instrument: str = "MSFT", **more: object) -> Intent:
        return self.gw.size_sell(intent_id=intent_id, account=ACCT, instrument=instrument, limit_price_minor=PRICE,
                                 conviction_bps=conviction, run_id="run-1", connection="conn-a", **more)

    def _buy(self, intent_id: str, conviction: int = 1_000) -> Intent:
        return self.gw.size_buy(intent_id=intent_id, account=ACCT, instrument="MSFT", limit_price_minor=PRICE,
                                conviction_bps=conviction, run_id="run-1", connection="conn-a")

    def _book(self, intent: Intent) -> dict:
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        return self.gw.submit(intent, approval, AUTHORITY)

    def _refused_before_core(self, code: str, stage: str, intent: Intent) -> None:
        calls, orders = list(self.core.calls), self.book.orders()
        for attempt in (lambda: self.gw.request_approval(intent), lambda: self.gw.submit(intent, "apr_none", AUTHORITY)):
            with self.assertRaises(Refused) as ctx:
                attempt()
            self.assertEqual((ctx.exception.code, ctx.exception.cause), (code, None))
            row = self.book.audit()[-1]
            self.assertEqual((row["seq"], row["event"], row["stage"], row["code"]),
                             (ctx.exception.audit_seq, "refused", stage, code))
        self.assertEqual((self.core.calls, self.book.orders()), (calls, orders))

    def test_sell_is_sized_from_conviction(self) -> None:
        # 2_500 bps of the 1_000_000 notional cap is 250_000, 25 shares at PRICE; 500 are held.
        for conviction, shares in ((2_500, 25), (1, 0), (100, 1), (5_999, 59)):
            with self.subTest(conviction=conviction):
                if shares == 0:
                    with self.assertRaises(Refused) as ctx:
                        self._sell("c-%d" % conviction, conviction)
                    self.assertEqual(ctx.exception.code, "size_zero")
                    continue
                intent = self._sell("c-%d" % conviction, conviction)
                self.assertEqual((intent.side, intent.quantity, intent.instrument), ("sell", shares, "MSFT"))
        sell = self._sell("booked", 2_500)
        out = self._book(sell)
        self.assertEqual((out["status"], out["notional_minor"]), ("paper_working", 250_000))
        self.assertEqual(self.book.held_shares(ACCT, "MSFT"), 475)
        self.assertEqual(self.book.available_cash_minor(ACCT), 10**9)  # a sell reserves shares, not cash

    def test_sell_is_capped_at_the_position_held(self) -> None:
        # Full conviction asks for 60 shares (the count cap); only 40 AAPL are held.
        sell = self._sell("cap", 10_000, instrument="AAPL")
        self.assertEqual(sell.quantity, 40)
        self._book(sell)
        self.assertEqual(self.book.held_shares(ACCT, "AAPL"), 0)
        # Shares reserved by a working sell are not held any more: nothing left to size.
        with self.assertRaises(Refused) as ctx:
            self._sell("cap-2", 10_000, instrument="AAPL")
        self.assertEqual(ctx.exception.code, "size_zero")
        # The quantity cap still binds when more is held than the conviction asks for.
        self.assertEqual(self._sell("msft-full", 10_000).quantity, 60)

    def test_cap_reads_the_position_left_after_earlier_sells(self) -> None:
        first = self._sell("p-1", 3_000, instrument="AAPL")  # 30 of 40
        self._book(first)
        second = self._sell("p-2", 3_000, instrument="AAPL")  # 30 asked, 10 left
        self.assertEqual((first.quantity, second.quantity), (30, 10))
        self.gw.halt()
        self.gw.cancel_all()  # the cancel releases the reserved shares
        self.assertEqual(self.book.held_shares(ACCT, "AAPL"), 40)

    def test_zero_position_is_refused_size_zero(self) -> None:
        for n, instrument in enumerate(("TSLA", "XYZ")):  # a zero row, and no row at all
            with self.subTest(instrument=instrument):
                calls = list(self.core.calls)
                with self.assertRaises(Refused) as ctx:
                    self._sell("zero-%d" % n, 10_000, instrument=instrument)
                self.assertEqual((ctx.exception.code, ctx.exception.cause), ("size_zero", None))
                row = self.book.audit()[-1]
                self.assertEqual((row["seq"], row["stage"], row["code"], row["intent_id"]),
                                 (ctx.exception.audit_seq, "size", "size_zero", "zero-%d" % n))
                self.assertEqual((self.core.calls, self.book.orders()), (calls, []))
        with self.assertRaises(Refused) as ctx:
            self.gw.size_sell(intent_id="nobody", account="other", instrument="MSFT", limit_price_minor=PRICE,
                              conviction_bps=10_000, run_id="run-1", connection="conn-a")
        self.assertEqual(ctx.exception.code, "account_unknown")

    def test_caller_supplied_quantity_is_rejected(self) -> None:
        # The sizer has no quantity argument at all.
        with self.assertRaises(TypeError):
            self._sell("q", 2_500, quantity=7)
        self.assertEqual(self.gw._sized, {})
        # A hand-built sell with the caller's quantity never reaches the core.
        hand = Intent(intent_id="hand", account=ACCT, instrument="MSFT", side="sell", quantity=7,
                      limit_price_minor=PRICE, run_id="run-1", connection="conn-a")
        self._refused_before_core("quantity_not_sized", "size", hand)
        # A sized sell whose quantity is changed afterwards is no longer the sized intent.
        sized = self._sell("resized", 2_500)
        object.__setattr__(sized, "quantity", 7)
        self._refused_before_core("quantity_not_sized", "size", sized)
        self.assertEqual(self.book.held_shares(ACCT, "MSFT"), 500)

    def test_sized_buy_cannot_become_a_sell_and_back(self) -> None:
        buy = self._buy("flip-b")
        self.assertEqual((buy.side, buy.quantity), ("buy", 10))
        object.__setattr__(buy, "side", "sell")
        self._refused_before_core("quantity_not_sized", "size", buy)
        sell = self._sell("flip-s", 1_000)
        object.__setattr__(sell, "side", "buy")
        self._refused_before_core("quantity_not_sized", "size", sell)
        # A flip after approval, before submit: refused at submit, before the charge.
        buy = self._buy("flip-late")
        approval = self.gw.request_approval(buy)["approval_id"]
        self.core.operator_approves(approval)
        object.__setattr__(buy, "side", "sell")
        spent = self.core.spent
        with self.assertRaises(Refused) as ctx:
            self.gw.submit(buy, approval, AUTHORITY)
        self.assertEqual((ctx.exception.code, self.core.spent, self.book.orders()), ("quantity_not_sized", spent, []))
        self.assertEqual(self.book.held_shares(ACCT, "MSFT"), 500)

    def test_values_racing_the_sizing_check_are_refused(self) -> None:
        """The sizing check compares the effect the risk pass built (the one charged and booked),
        not a second read of the intent. Here the intent carries the caller's values while the
        risk pass reads it and its sized values again right after, the window a thread flipping
        the fields would hit."""
        cases = (("sell_quantity", lambda: self._sell("race-s", 100), {"quantity": 57}),
                 ("buy_to_sell", lambda: self._buy("race-b"), {"side": "sell", "quantity": 1}))
        for name, make, swap in cases:
            with self.subTest(race=name):
                intent = make()
                sized_values = {field: getattr(intent, field) for field in swap}

                def racing_risk_pass(target: object, _swap=swap, _back=sized_values) -> dict:
                    for field, value in _swap.items():
                        object.__setattr__(target, field, value)
                    try:
                        return OrderGateway.risk_pass(self.gw, target)
                    finally:
                        for field, value in _back.items():
                            object.__setattr__(target, field, value)
                self.gw.risk_pass = racing_risk_pass
                try:
                    calls, spent, orders = list(self.core.calls), self.core.spent, self.book.orders()
                    for attempt in (lambda: self.gw.request_approval(intent),
                                    lambda: self.gw.submit(intent, "apr_none", AUTHORITY)):
                        with self.assertRaises(Refused) as ctx:
                            attempt()
                        self.assertEqual((ctx.exception.code, ctx.exception.cause), ("quantity_not_sized", None))
                    self.assertEqual((self.core.calls, self.core.spent, self.book.orders()), (calls, spent, orders))
                finally:
                    del self.gw.risk_pass
                # Control: untouched, the same sized intent books with its sized values.
                out = self._book(intent)
                self.assertEqual(out["status"], "paper_working")
        self.assertEqual([(o[0], o[2]) for o in self.book.orders()], [("race-s", "working"), ("race-b", "working")])
        self.assertEqual(self.book.held_shares(ACCT, "MSFT"), 499)  # the sized sell of 1, never 57

    def test_sell_sizing_validates_like_buy_sizing(self) -> None:
        for kwargs, code in (({"conviction": 0}, "conviction_invalid"), ({"conviction": 10_001}, "conviction_invalid"),
                             ({"conviction": 1_000, "instrument": "NOPE"}, "instrument_not_allowlisted"),
                             ({"conviction": 1_000, "instrument": "msft"}, "instrument_invalid")):
            with self.subTest(**kwargs):
                with self.assertRaises(Refused) as ctx:
                    self._sell("v", **kwargs)
                self.assertEqual(ctx.exception.code, code)
        self.assertEqual((self.gw._sized, self.core.calls), ({}, []))


if __name__ == "__main__":
    unittest.main()
