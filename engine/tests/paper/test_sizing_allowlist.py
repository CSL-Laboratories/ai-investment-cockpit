"""Gateway sizing and the instrument allowlist (PRODUCT.md at csl-research d84cdd26, 4.2).

Lines 1183-1184: "Proposals carry conviction, not quantity, and the gateway sizes orders
(grill Q30; O47)." A buy whose quantity the caller chose never reaches approval or booking:
only the unchanged ``Intent`` object this gateway's ``size_buy`` returned does.

Lines 1474-1475: "The order universe is a static, operator-approved allowlist; no model or
API changes execution eligibility at runtime." The allowlist is fixed at construction and
an instrument outside it is refused before any core call.

Sells are sized too (Lucas, Nov 11: "Size sells from conviction, capped at the position
held, no caller-set quantity"): a caller-quantity sell is refused below, and
``test_sell_sizing_nov11.py`` covers ``size_sell``.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from dataclasses import replace

from cockpit.paper import plugin
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import SIZED_MAX, Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper import test_e2e_recursion_core as e2e
from tests.paper.fakecore import AUTHORITY, FakeCore

ACCT, OTHER_ACCT = "paper-acct", "other-acct"
ALLOWED = frozenset({"AAPL", "MSFT"})
PRICE = 10_000


class StrSub(str):
    """Equal to its text for every ``str`` comparison, but not exactly a ``str``."""


class AlwaysEqual:
    """Claims to equal anything and hashes like ``"AAPL"``, so a set lookup would find it."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return hash("AAPL")


class _GatewayCase(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-sizing-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: 10**9, OTHER_ACCT: 10**9},
                    holdings={(ACCT, "AAPL"): 10, (ACCT, "TSLA"): 10})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**12)
        self.gw = self._gateway()

    def _gateway(self, allowlist: frozenset = ALLOWED) -> OrderGateway:
        return OrderGateway(plugin_id="csl.cockpit.paper", book=self.book, core=CoreClient(self.core),
                            caps=Caps(max_notional_minor=1_000_000, max_quantity=100), allowlist=allowlist,
                            run_check=self.core.check)

    def _size(self, intent_id: str = "s-1", conviction: int = 5_000, gw: OrderGateway | None = None,
              **changes: object) -> Intent:
        args = dict(intent_id=intent_id, account=ACCT, instrument="AAPL", limit_price_minor=PRICE,
                    conviction_bps=conviction, run_id="run-1", connection="conn-a")
        args.update(changes)
        return (self.gw if gw is None else gw).size_buy(**args)

    def _approved(self, intent: Intent) -> str:
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        return approval

    def _refused_before_core(self, code: str, stage: str, intent: object, approval: str = "apr_none") -> None:
        """Refused with ``code`` at request and at submit, audited at ``stage``, with no core call,
        no approval drawn or used, no charge and nothing booked."""
        calls, drawn, spent, orders = list(self.core.calls), len(self.core.approvals), self.core.spent, \
            self.book.orders()
        for name, attempt in (("request", lambda: self.gw.request_approval(intent)),
                              ("submit", lambda: self.gw.submit(intent, approval, AUTHORITY))):
            with self.subTest(entry=name):
                with self.assertRaises(Refused) as ctx:
                    attempt()
                self.assertEqual((ctx.exception.code, ctx.exception.cause), (code, None))
                row = self.book.audit()[-1]
                self.assertEqual((row["seq"], row["event"], row["stage"], row["code"]),
                                 (ctx.exception.audit_seq, "refused", stage, code))
        self.assertEqual((self.core.calls, len(self.core.approvals), self.core.spent, self.book.orders()),
                         (calls, drawn, spent, orders))
        if approval in self.core.approvals:
            self.assertEqual(self.core.approvals[approval]["state"], "approved")  # not used up


class SizingTests(_GatewayCase):
    def test_sized_buy_books(self) -> None:
        intent = self._size()
        self.assertEqual((intent.side, intent.quantity), ("buy", 50))  # 5_000 bps of 1_000_000 at 10_000
        out = self.gw.submit(intent, self._approved(intent), AUTHORITY)
        self.assertEqual((out["status"], out["intent_id"], out["notional_minor"]), ("paper_working", "s-1", 500_000))
        self.assertEqual(self.book.orders(), [("s-1", 1, "working")])
        self.assertEqual(self.book.available_cash_minor(ACCT), 10**9 - 500_000)
        # Still this gateway's sized intent, so a resend is the duplicate it is.
        with self.assertRaises(Refused) as ctx:
            self.gw.request_approval(intent)
        self.assertEqual(ctx.exception.code, "intent_duplicate")

    def test_caller_set_quantity_is_refused_equal_below_and_above_the_sized_one(self) -> None:
        sized = self._size()
        approval = self._approved(sized)  # a real approval of the sized twin
        for label, quantity in (("equal", sized.quantity), ("below", sized.quantity - 1),
                                ("above", sized.quantity + 1), ("one", 1), ("cap", 100)):
            with self.subTest(quantity=label):
                hand = Intent(intent_id="s-1", account=ACCT, instrument="AAPL", side="buy", quantity=quantity,
                              limit_price_minor=PRICE, run_id="run-1", connection="conn-a")
                self.assertEqual(hand == sized, label == "equal")
                self._refused_before_core("quantity_not_sized", "size", hand, approval)
        with self.subTest(quantity="copy"):
            self._refused_before_core("quantity_not_sized", "size", replace(sized), approval)
        # Control: the sized object itself still books on that approval.
        self.assertEqual(self.gw.submit(sized, approval, AUTHORITY)["status"], "paper_working")

    def test_mutated_sized_intent_is_refused(self) -> None:
        changes = {"quantity": 49, "limit_price_minor": PRICE - 1, "intent_id": "s-other", "account": OTHER_ACCT,
                   "instrument": "MSFT", "run_id": "run-ok", "connection": "conn-ok"}
        for field, value in changes.items():
            with self.subTest(field=field):
                intent = self._size("m-" + field)
                approval = self._approved(intent)
                original = getattr(intent, field)
                object.__setattr__(intent, field, value)
                self._refused_before_core("quantity_not_sized", "size", intent, approval)
                # Put back exactly as sized, it is the sized intent again.
                object.__setattr__(intent, field, original)
                self.assertEqual(self.gw.submit(intent, approval, AUTHORITY)["status"], "paper_working")
        self.assertEqual(len(self.book.orders()), len(changes))

    def test_only_this_gateways_size_buy_counts(self) -> None:
        other = self._gateway()
        foreign = self._size("foreign", gw=other)
        self._refused_before_core("quantity_not_sized", "size", foreign)
        # A refused size_buy mints nothing.
        with self.assertRaises(Refused):
            self._size("zero", limit_price_minor=1_000_001)
        self.assertEqual(self.gw._sized, {})
        # The other gateway books its own.
        approval = other.request_approval(foreign)["approval_id"]
        self.core.operator_approves(approval)
        self.assertEqual(other.submit(foreign, approval, AUTHORITY)["status"], "paper_working")

    def test_sizing_memory_is_bounded(self) -> None:
        first = self._size("bound-first")
        kept = [self._size("bound-%d" % n) for n in range(SIZED_MAX)]
        self.assertEqual(len(self.gw._sized), SIZED_MAX)
        self._refused_before_core("quantity_not_sized", "size", first)  # dropped: must be sized again
        self.assertTrue(self.gw.request_approval(kept[0])["approval_id"])
        self.assertTrue(self.gw.request_approval(kept[-1])["approval_id"])

    def test_size_buy_validates_the_ids_it_mints(self) -> None:
        for field in ("intent_id", "account", "run_id", "connection"):
            for bad in (StrSub("run-1"), AlwaysEqual(), "", "a b", None):
                with self.subTest(field=field, bad=repr(bad)):
                    with self.assertRaises(Refused) as ctx:
                        self._size(**{field: bad})
                    self.assertEqual(ctx.exception.code, "intent_invalid")
        self.assertEqual((self.gw._sized, self.core.calls), ({}, []))

    def test_caller_quantity_sell_is_refused(self) -> None:
        """Lucas, Nov 11: "no caller-set quantity". A hand-built sell never reaches the core;
        ``test_sell_sizing_nov11.py`` covers ``size_sell`` itself."""
        sell = Intent(intent_id="sell-1", account=ACCT, instrument="AAPL", side="sell", quantity=4,
                      limit_price_minor=PRICE, run_id="run-1", connection="conn-a")
        self._refused_before_core("quantity_not_sized", "size", sell)
        self.assertEqual(self.book.held_shares(ACCT, "AAPL"), 10)


class AllowlistTests(_GatewayCase):
    def test_not_allowlisted_instrument_refused_at_size_request_and_submit(self) -> None:
        approval = self._approved(self._size("twin"))
        self.core.calls.clear()
        with self.assertRaises(Refused) as ctx:
            self._size("tsla", instrument="TSLA")
        self.assertEqual(ctx.exception.code, "instrument_not_allowlisted")
        row = self.book.audit()[-1]
        self.assertEqual((row["seq"], row["stage"], row["code"], row["intent_id"], row["effect_sha256"]),
                         (ctx.exception.audit_seq, "size", "instrument_not_allowlisted", "tsla", None))
        for side in ("buy", "sell"):  # the account holds TSLA: only the allowlist stands in the way
            with self.subTest(side=side):
                hand = Intent(intent_id="tsla-" + side, account=ACCT, instrument="TSLA", side=side, quantity=1,
                              limit_price_minor=PRICE, run_id="run-1", connection="conn-a")
                self._refused_before_core("instrument_not_allowlisted", "risk", hand, approval)
        # A sized buy relabelled onto an instrument outside the list.
        intent = self._size("relabel")
        object.__setattr__(intent, "instrument", "TSLA")
        self._refused_before_core("instrument_not_allowlisted", "risk", intent, approval)
        self.assertEqual(self.core.calls, [])
        # Control: an allowlisted instrument other than the first still sizes and books.
        msft = self._size("msft", instrument="MSFT")
        self.assertEqual(self.gw.submit(msft, self._approved(msft), AUTHORITY)["status"], "paper_working")

    def test_rigged_instrument_is_refused(self) -> None:
        for rigged in (StrSub("AAPL"), AlwaysEqual(), b"AAPL", None):
            with self.subTest(instrument=type(rigged).__name__):
                with self.assertRaises(Refused) as ctx:
                    self._size("rig", instrument=rigged)
                self.assertEqual(ctx.exception.code, "instrument_invalid")
                for side in ("buy", "sell"):
                    hand = Intent(intent_id="rig-" + side, account=ACCT, instrument=rigged, side=side, quantity=1,
                                  limit_price_minor=PRICE, run_id="run-1", connection="conn-a")
                    self._refused_before_core("instrument_invalid", "risk", hand)
                intent = self._size("rig-sized")
                object.__setattr__(intent, "instrument", rigged)
                self._refused_before_core("instrument_invalid", "risk", intent)
        # The format check answers before the allowlist.
        with self.assertRaises(Refused) as ctx:
            self._size("lower", instrument="aapl")
        self.assertEqual(ctx.exception.code, "instrument_invalid")
        self.assertEqual((self.core.calls, self.book.orders()), ([], []))

    def test_allowlist_cannot_be_changed_after_construction(self) -> None:
        source = frozenset({"AAPL", "MSFT"})
        gw = self._gateway(source)
        self.assertIs(type(gw.allowlist), frozenset)
        self.assertEqual(gw.allowlist, {"AAPL", "MSFT"})
        with self.assertRaises(AttributeError):
            gw.allowlist = frozenset({"TSLA"})
        with self.assertRaises(Refused) as ctx:
            gw._allowlist = frozenset({"TSLA"})
        self.assertEqual(ctx.exception.code, "allowlist_immutable")
        for name in ("add", "update", "remove", "discard", "clear", "pop"):
            self.assertFalse(hasattr(gw.allowlist, name), name)
        public = {name for name in vars(OrderGateway) if not name.startswith("_")}
        self.assertEqual(public, {"allowlist", "halt", "cancel_all", "size_buy", "size_sell", "risk_pass",
                                  "request_approval", "submit"})  # no method takes an instrument list
        self.assertIsNone(vars(OrderGateway)["allowlist"].fset)
        self.assertEqual(gw.allowlist, {"AAPL", "MSFT"})
        with self.assertRaises(Refused) as ctx:
            gw.size_buy(intent_id="t", account=ACCT, instrument="TSLA", limit_price_minor=PRICE,
                        conviction_bps=100, run_id="run-1", connection="conn-a")
        self.assertEqual(ctx.exception.code, "instrument_not_allowlisted")

    def test_allowlist_is_validated_at_construction(self) -> None:
        for bad in (None, {"AAPL"}, ["AAPL"], ("AAPL",), "AAPL", {"AAPL": True}, frozenset({"aapl"}),
                    frozenset({StrSub("AAPL")}), frozenset({1}), frozenset({"AAPL", ""}), frozenset({AlwaysEqual()})):
            with self.subTest(allowlist=repr(bad)):
                with self.assertRaises(Refused) as ctx:
                    self._gateway(bad)
                self.assertEqual(ctx.exception.code, "invalid_allowlist")
                with self.assertRaises((Refused, TypeError)):
                    plugin.compose(self.core, self.path, Caps(1_000_000, 100), run_check=self.core.check,
                                   allowlist=bad)
        with self.assertRaises(TypeError):  # the entry point has no default order universe
            plugin.compose(self.core, self.path, Caps(1_000_000, 100), run_check=self.core.check)
        with self.assertRaises(TypeError):
            plugin.build(self.core, self.path, Caps(1_000_000, 100), run_check=self.core.check)
        # An empty list is valid and trades nothing.
        empty = self._gateway(frozenset())
        with self.assertRaises(Refused) as ctx:
            empty.size_buy(intent_id="e", account=ACCT, instrument="AAPL", limit_price_minor=PRICE,
                           conviction_bps=100, run_id="run-1", connection="conn-a")
        self.assertEqual(ctx.exception.code, "instrument_not_allowlisted")


@unittest.skipIf(e2e.CORE_SKIP is not None, e2e.AWAITING_328)
class RealCoreSizingTests(unittest.TestCase):
    def test_sized_buy_books_and_caller_sized_buy_is_refused(self) -> None:
        w = e2e.build_world(self)
        self.assertEqual(w.gateway.allowlist, e2e.ALLOWLIST)
        # A caller-built buy, field for field what size_buy would return, never reaches the core.
        hand = replace(e2e.INTENT)
        for attempt in (lambda: w.gateway.request_approval(hand),
                        lambda: w.gateway.submit(hand, "apr-none", w.profile)):
            with self.assertRaises(Refused) as ctx:
                attempt()
            self.assertEqual((ctx.exception.code, ctx.exception.cause), ("quantity_not_sized", None))
            self.assertEqual((w.book.audit()[-1]["stage"], w.book.audit()[-1]["code"]), ("size", "quantity_not_sized"))
        self.assertEqual((w.plugin_ops, w.drawn, w.book.orders()), ([], [], []))
        # The gateway-sized buy with the same fields books.
        intent = e2e.sized_buy(w)
        self.assertEqual(intent, hand)
        approval = w.gateway.request_approval(intent)
        self.assertTrue(w.decide(approval)["ok"])
        # Even with the operator's approval of that exact effect, the caller's copy is refused.
        with self.assertRaises(Refused) as ctx:
            w.gateway.submit(hand, approval["approval_id"], w.profile)
        self.assertEqual(ctx.exception.code, "quantity_not_sized")
        self.assertEqual(w.plugin_ops, ["effect.request"])
        out = w.gateway.submit(intent, approval["approval_id"], w.profile)
        self.assertEqual((out["status"], out["notional_minor"], out["digest"]),
                         ("paper_working", 50_000, approval["digest"]))
        self.assertEqual(w.book.orders(), [("e2e-1", 1, "working")])

    def test_instrument_outside_the_allowlist_never_reaches_the_core(self) -> None:
        w = e2e.build_world(self)
        with self.assertRaises(Refused) as ctx:
            w.gateway.size_buy(intent_id="msft-1", account="paper-acct", instrument="MSFT",
                               limit_price_minor=10_000, conviction_bps=5_000, run_id=e2e.RUN,
                               connection=e2e.CONNECTION)
        self.assertEqual(ctx.exception.code, "instrument_not_allowlisted")
        intent = e2e.sized_buy(w)
        object.__setattr__(intent, "instrument", "MSFT")
        for attempt in (lambda: w.gateway.request_approval(intent),
                        lambda: w.gateway.submit(intent, "apr-none", w.profile)):
            with self.assertRaises(Refused) as ctx:
                attempt()
            self.assertEqual(ctx.exception.code, "instrument_not_allowlisted")
        self.assertEqual((w.plugin_ops, w.drawn, w.book.orders()), ([], [], []))


if __name__ == "__main__":
    unittest.main()
