"""B31: size to cash, refuse at the gateway (CSL-724; Lucas t1414u; PRODUCT.md d84cdd26a4de2366f3c5d34c4aec5215f822a494 (CSLR-D-012), 4.2).

"The sizer caps the share count at available cash when it turns conviction into an order, so
the approved order already fits. The gateway still checks cash at send time and rejects
anything over it; it never clamps or resizes." A held-share sell below the $2,000 equity floor
is allowed; only buys are sized to cash.

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
from dataclasses import replace

import cockpit.paper.gateway as gateway_module
from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import AUTHORITY, FakeCore

ACCT = "paper-acct"


class CashTests(unittest.TestCase):
    def _gateway(self, cash: int, holdings: dict | None = None, caps: Caps = Caps(10**9, 10**6)) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-cash-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: cash}, holdings=holdings)
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**12)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                               caps=caps)

    def _size(self, intent_id: str = "b31", price: int = 10_000, conviction: int = 10_000) -> Intent:
        return self.gw.size_buy(intent_id=intent_id, account=ACCT, instrument="AAPL", limit_price_minor=price,
                                conviction_bps=conviction, run_id="run-1", connection="conn-a")

    def _approved(self, intent: Intent) -> str:
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        return approval

    def _racer_books(self, intent: Intent) -> None:
        """Another process's gateway books ``intent`` on the same book file, through its own
        risk pass, core and one-shot grant (#317: the bare ``_GATEWAY_ONLY`` books nothing)."""
        other = PaperBook(self.path)  # another connection, as another process would
        core = FakeCore(budget_minor=10**12)
        gw = OrderGateway(plugin_id="csl.cockpit.paper", book=other, core=CoreClient(core),
                          caps=Caps(10**9, 10**6))
        approval = gw.request_approval(intent)["approval_id"]
        core.operator_approves(approval)
        gw.submit(intent, approval, AUTHORITY)
        other.close()

    def _refused(self, code: str, fn, *args, **kwargs) -> Refused:
        with self.assertRaises(Refused) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_sizer_caps_buy_shares_at_available_cash(self) -> None:
        for cash, expected in ((50_000, 5), (59_999, 5), (10_000, 1), (10**7, 1000)):
            with self.subTest(cash=cash):
                self._gateway(cash, caps=Caps(10_000_000, 1000))
                intent = self._size()
                self.assertEqual(intent.quantity, expected)
                self.assertLessEqual(intent.quantity * intent.limit_price_minor, cash)
                out = self.gw.submit(intent, self._approved(intent), AUTHORITY)
                self.assertEqual(out["status"], "paper_working")
                self.assertEqual(self.book.available_cash_minor(ACCT), cash - expected * 10_000)

    def test_sizer_honours_conviction_and_caps_below_cash(self) -> None:
        self._gateway(10**9, caps=Caps(max_notional_minor=1_000_000, max_quantity=60))
        self.assertEqual(self._size(conviction=10_000).quantity, 60)    # quantity cap
        self.assertEqual(self._size(conviction=5_000).quantity, 50)     # 500_000 / 10_000
        self.assertEqual(self._size(conviction=1_999).quantity, 19)     # floor, never rounds up
        for bad in (0, -1, 10_001, 0.5, True, "100"):
            with self.subTest(conviction=bad):
                self._refused("conviction_invalid", self._size, "x", 10_000, bad)

    def test_sizer_refuses_when_cash_buys_no_whole_share(self) -> None:
        self._gateway(9_999)
        exc = self._refused("size_zero", self._size)
        self.assertEqual([(r["seq"], r["stage"], r["code"]) for r in self.book.audit()],
                         [(exc.audit_seq, "size", "size_zero")])
        row = self.book.audit()[0]
        self.assertEqual((row["intent_id"], row["run_id"], row["connection"]),
                         ("b31", "run-1", "conn-a"))
        self.assertIsNone(row["effect_sha256"])  # no sized effect exists on refusal
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.book.orders(), [])

    def test_size_refusals_audit_the_callers_ids(self) -> None:
        self._gateway(100_000)
        cases = (("conviction_invalid", {"conviction_bps": 0}),
                 ("money_invalid", {"limit_price_minor": 0}),
                 ("intent_invalid", {"account": "not an account"}),
                 ("account_unknown", {"account": "missing"}),
                 ("size_zero", {"limit_price_minor": 100_001}))
        for n, (code, changes) in enumerate(cases):
            args = dict(intent_id="proposal-%d" % n, account=ACCT, instrument="AAPL",
                        limit_price_minor=10_000, conviction_bps=10_000,
                        run_id="caller-run-%d" % n, connection="caller-conn-%d" % n)
            args.update(changes)
            with self.subTest(code=code):
                exc = self._refused(code, self.gw.size_buy, **args)
                row = self.book.audit()[-1]
                self.assertEqual((row["seq"], row["event"], row["stage"], row["code"]),
                                 (exc.audit_seq, "refused", "size", code))
                self.assertEqual((row["intent_id"], row["run_id"], row["connection"]),
                                 (args["intent_id"], args["run_id"], args["connection"]))
                self.assertIsNone(row["effect_sha256"])
        reopened = PaperBook(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.audit(), self.book.audit())
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.available_cash_minor(ACCT), 100_000)

    def test_cash_drop_between_size_and_send_refused_without_resizing(self) -> None:
        self._gateway(100_000)
        intent = self._size("first")
        self.assertEqual(intent.quantity, 10)
        approval = self._approved(intent)
        effect_digest = hashlib.sha256(canonical(self.gw.risk_pass(intent))).hexdigest()
        other = replace(intent, intent_id="other", quantity=1)  # spends $100 before "first" is sent
        self.gw.submit(other, self._approved(other), AUTHORITY)
        self.assertEqual(self.book.available_cash_minor(ACCT), 90_000)
        exc = self._refused("cash_insufficient", self.gw.submit, intent, approval, AUTHORITY)
        self.assertEqual(intent.quantity, 10)
        self.assertEqual([o[0] for o in self.book.orders()], ["other"])
        self.assertEqual(self.book.available_cash_minor(ACCT), 90_000)
        row = [r for r in self.book.audit() if r["seq"] == exc.audit_seq][0]
        self.assertEqual((row["event"], row["code"], row["effect_sha256"]),
                         ("refused", "cash_insufficient", effect_digest))  # the approved effect, unresized

    def test_cash_drop_during_core_authorize_is_a_leftover_charge(self) -> None:
        self._gateway(100_000)
        intent = self._size("first")
        approval = self._approved(intent)
        effect_digest = hashlib.sha256(canonical(self.gw.risk_pass(intent))).hexdigest()
        original = self.core.__call__

        def spend_during_core_call(raw: bytes) -> bytes:
            self._racer_books(replace(intent, intent_id="racer", quantity=1))
            return original(raw)
        self.gw._core = CoreClient(spend_during_core_call)
        # The early funds read passed and the core charged; the booking's own check then refused.
        exc = self._refused("leftover_charge", self.gw.submit, intent, approval, AUTHORITY)
        self.assertEqual((exc.cause, str(exc)), ("cash_insufficient", "leftover_charge:cash_insufficient"))
        self.assertEqual([(r["event"], r["stage"], r["code"], r["effect_sha256"])
                          for r in self.book.audit() if r["seq"] == exc.audit_seq],
                         [("refused", "book", "leftover_charge:cash_insufficient", effect_digest)])
        self.assertEqual(intent.quantity, 10)  # the approved effect, never clamped or resized
        self.assertEqual([o[0] for o in self.book.orders()], ["racer"])
        self.assertEqual(self.book.available_cash_minor(ACCT), 90_000)
        self.assertEqual(self.core.calls.count("effect.authorize"), 1)  # charged once, not reversed

    def test_share_drop_during_core_authorize_is_a_leftover_charge(self) -> None:
        self._gateway(0, holdings={(ACCT, "AAPL"): 10})
        sell = Intent(intent_id="first", account=ACCT, instrument="AAPL", side="sell", quantity=10,
                      limit_price_minor=10_000, run_id="run-1", connection="conn-a")
        approval = self._approved(sell)
        original = self.core.__call__

        def sell_during_core_call(raw: bytes) -> bytes:
            self._racer_books(replace(sell, intent_id="racer", quantity=1))
            return original(raw)
        self.gw._core = CoreClient(sell_during_core_call)
        exc = self._refused("leftover_charge", self.gw.submit, sell, approval, AUTHORITY)
        self.assertEqual((exc.cause, str(exc)), ("holding_insufficient", "leftover_charge:holding_insufficient"))
        self.assertEqual([(r["stage"], r["code"]) for r in self.book.audit() if r["seq"] == exc.audit_seq],
                         [("book", "leftover_charge:holding_insufficient")])
        self.assertEqual([o[0] for o in self.book.orders()], ["racer"])
        self.assertEqual(self.book.held_shares(ACCT, "AAPL"), 9)
        self.assertEqual(self.core.calls.count("effect.authorize"), 1)

    def test_account_removed_during_core_authorize_is_a_leftover_charge(self) -> None:
        self._gateway(100_000)
        intent = self._size("first")
        approval = self._approved(intent)
        effect_digest = hashlib.sha256(canonical(self.gw.risk_pass(intent))).hexdigest()
        original = self.core.__call__

        def drop_account_during_core_call(raw: bytes) -> bytes:
            other = sqlite3.connect(self.path)  # another connection, as another process would
            # Test-only: no production code deletes a cash row.
            other.execute("DELETE FROM cash WHERE account = ?", (ACCT,))
            other.commit()
            other.close()
            return original(raw)
        self.gw._core = CoreClient(drop_account_during_core_call)
        # The early funds read passed and the core charged; the booking's own read then refused.
        exc = self._refused("leftover_charge", self.gw.submit, intent, approval, AUTHORITY)
        self.assertEqual((exc.cause, str(exc)), ("account_unknown", "leftover_charge:account_unknown"))
        self.assertEqual([(r["event"], r["stage"], r["code"], r["effect_sha256"])
                          for r in self.book.audit() if r["seq"] == exc.audit_seq],
                         [("refused", "book", "leftover_charge:account_unknown", effect_digest)])
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.core.spent, 100_000)  # the core charged, and nothing reversed it
        self.assertEqual(self.core.calls.count("effect.authorize"), 1)  # charged once, not reversed

    def test_gateway_never_clamps_or_resizes(self) -> None:
        self._gateway(50_000)
        for quantity in (6, 7, 100, 1000):
            over = Intent(intent_id="o-%d" % quantity, account=ACCT, instrument="AAPL", side="buy",
                          quantity=quantity, limit_price_minor=10_000, run_id="run-1", connection="conn-a")
            with self.subTest(quantity=quantity):
                self._refused("cash_insufficient", self.gw.request_approval, over)
                # The booking's own send-time check, reached with a grant forged for this gateway
                # (test-only): it passes the grant checks and the risk pass, then cash refuses it.
                grant = gateway_module._Grant(gateway_module._GATEWAY_ONLY, gateway=self.gw, book=self.book,
                                              intent=over, effect=self.gw.risk_pass(over))
                self._refused("cash_insufficient", self.book._book, over, grant)
                self._refused("book_direct_refused", self.book._book, over, gateway_module._GATEWAY_ONLY)
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.available_cash_minor(ACCT), 50_000)
        exact = Intent(intent_id="exact", account=ACCT, instrument="AAPL", side="buy", quantity=5,
                       limit_price_minor=10_000, run_id="run-1", connection="conn-a")
        self.assertEqual(self.gw.submit(exact, self._approved(exact), AUTHORITY)["notional_minor"], 50_000)
        self.assertEqual(self.book.available_cash_minor(ACCT), 0)

    def test_held_share_sell_below_2000_allowed(self) -> None:
        # $500 cash + 10 AAPL at $100 = $1,500 equity, under the $2,000 floor frozen app/ enforces.
        self._gateway(50_000, holdings={(ACCT, "AAPL"): 10})
        sell = Intent(intent_id="s-1", account=ACCT, instrument="AAPL", side="sell", quantity=10,
                      limit_price_minor=10_000, run_id="run-1", connection="conn-a")
        self.assertEqual(self.gw.submit(sell, self._approved(sell), AUTHORITY)["status"], "paper_working")
        self.assertEqual(self.book.held_shares(ACCT, "AAPL"), 0)
        self.assertEqual(self.book.available_cash_minor(ACCT), 50_000)  # sells are not sized to cash
        self._refused("holding_insufficient", self.gw.request_approval, replace(sell, intent_id="s-2", quantity=1))
        self._refused("holding_insufficient", self.gw.request_approval,
                      replace(sell, intent_id="s-3", instrument="MSFT", quantity=1))

    def test_held_share_sell_allowed_with_zero_cash_and_small_buy_has_no_floor(self) -> None:
        self._gateway(100, holdings={(ACCT, "AAPL"): 3})
        sell = Intent(intent_id="s-1", account=ACCT, instrument="AAPL", side="sell", quantity=1,
                      limit_price_minor=10_000, run_id="run-1", connection="conn-a")
        buy = replace(sell, intent_id="b-1", instrument="XYZ", side="buy", limit_price_minor=100)
        self.assertEqual(self.gw.submit(buy, self._approved(buy), AUTHORITY)["notional_minor"], 100)
        self.assertEqual(self.book.available_cash_minor(ACCT), 0)
        self.assertEqual(self.gw.submit(sell, self._approved(sell), AUTHORITY)["status"], "paper_working")

    def test_cancel_releases_reserved_cash_and_shares(self) -> None:
        self._gateway(100_000, holdings={(ACCT, "AAPL"): 4})
        buy = self._size("b")
        self.gw.submit(buy, self._approved(buy), AUTHORITY)
        sell = replace(buy, intent_id="s", side="sell", quantity=4)
        self.gw.submit(sell, self._approved(sell), AUTHORITY)
        self.assertEqual((self.book.available_cash_minor(ACCT), self.book.held_shares(ACCT, "AAPL")), (0, 0))
        self.gw.halt()
        self.assertEqual(self.gw.cancel_all(), 2)
        self.assertEqual((self.book.available_cash_minor(ACCT), self.book.held_shares(ACCT, "AAPL")), (100_000, 4))
        self.assertEqual(self.gw.cancel_all(), 0)
        self.assertEqual(self.book.available_cash_minor(ACCT), 100_000)

    def test_unknown_account_refused(self) -> None:
        self._gateway(100_000)
        self._refused("account_unknown", self.gw.size_buy, intent_id="x", account="other", instrument="AAPL",
                      limit_price_minor=100, conviction_bps=100, run_id="r", connection="c")
        intent = Intent(intent_id="x", account="other", instrument="AAPL", side="buy", quantity=1,
                        limit_price_minor=100, run_id="r", connection="c")
        self._refused("account_unknown", self.gw.request_approval, intent)
        # At submit, before the core, it stays a plain refusal: no charge, so no leftover_charge.
        calls = list(self.core.calls)
        exc = self._refused("account_unknown", self.gw.submit, intent, "apr_0", AUTHORITY)
        self.assertEqual((exc.cause, str(exc)), (None, "account_unknown"))
        self.assertEqual([(r["event"], r["stage"], r["code"])
                          for r in self.book.audit() if r["seq"] == exc.audit_seq],
                         [("refused", "funds", "account_unknown")])
        self.assertEqual((self.core.calls, self.core.spent), (calls, 0))
        self.assertEqual(self.book.orders(), [])

    def test_create_book_validates_cash_before_making_the_file(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-cash-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        for cash in ({}, {ACCT: -1}, {ACCT: 1.5}, {ACCT: True}, {"a b": 1}, None):
            with self.subTest(cash=cash):
                self._refused("invalid_cash", create_book, path, cash_minor=cash)
        for holdings in ({("nobody", "AAPL"): 1}, {(ACCT, "aapl"): 1}, {(ACCT, "AAPL"): -1}, {ACCT: 1}):
            with self.subTest(holdings=holdings):
                with self.assertRaises(Refused) as ctx:
                    create_book(path, cash_minor={ACCT: 1}, holdings=holdings)
                self.assertEqual(ctx.exception.code, "invalid_holding")
        self.assertFalse(os.path.exists(path))

    def test_book_without_cash_tables_refused(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-cash-")
        self.addCleanup(shutil.rmtree, d, True)
        # A real book whose only fault is a missing cash or holdings table (from before B31).
        for table in ("cash", "holdings"):
            with self.subTest(table=table):
                path = os.path.join(d, "no-%s.db" % table)
                create_book(path, cash_minor={ACCT: 1})
                db = sqlite3.connect(path)
                db.execute("DROP TABLE " + table)
                db.commit()
                db.close()
                self._refused("book_without_cash", PaperBook, path)


if __name__ == "__main__":
    unittest.main()
