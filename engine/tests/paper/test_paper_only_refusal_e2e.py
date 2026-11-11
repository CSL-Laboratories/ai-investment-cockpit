"""Paper-only refusal, end to end through the in-process plugin entry.

Every order whose venue or mode is not exactly the paper book's is refused ``paper_only``
before any side effect: no core call (``effect.request`` or ``effect.authorize``), no
charge, no booking. Values that only claim to equal ``"paper"`` (a ``str`` subclass, an
object whose ``__ne__`` is always false, one whose ``__eq__`` is always true, one with
both) are refused like any other venue.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
No provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import types
import unittest
from dataclasses import asdict, replace

from cockpit.paper import plugin
from cockpit.paper.gateway import Caps, Intent, PaperBook, Refused, create_book
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, FakeCore

FIELDS = dict(intent_id="i-1", account="paper-acct", instrument="AAPL", side="buy", quantity=10,
              limit_price_minor=10_000, run_id="run-1", connection="conn-a")
BOGUS_APPROVAL = "apr_bogus"


class StrSub(str):
    """Equal to its text for every ``str`` comparison, but not exactly a ``str``."""


class AlwaysEqual:
    """Claims to equal anything: ``==`` is always true and ``!=`` always false."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return "AlwaysEqual()"


class NeverUnequal:
    """Only ``!=`` is rigged: it is always false, so a ``venue != "paper"`` test alone passes it."""

    def __ne__(self, other: object) -> bool:
        return False

    def __repr__(self) -> str:
        return "NeverUnequal()"


class EqualOnly:
    """Only ``==`` is rigged: it is always true (and ``!=``, derived from it, always false)."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return "EqualOnly()"


class SubIntent(Intent):
    """An ``Intent`` subclass: not the exact type the gateway accepts."""


RIGGED = [StrSub("paper"), AlwaysEqual(), NeverUnequal(), EqualOnly()]
VENUES = ["kalshi", "kalshi-demo", "coinbase", "gemini", "live", "paper-book", "PAPER", "Paper", " paper",
          "paper ", "paper\n", "pаper", "ｐａｐｅｒ", "paper​", "", None, 0,
          b"paper"] + RIGGED
MODES = [None, "", "live", "demo", "PAPER"] + RIGGED
SIDES = [AlwaysEqual(), NeverUnequal(), EqualOnly(), StrSub("buy"), StrSub("sell"), "BUY", None]


class PaperOnlyRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-paper-only-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        create_book(path, cash_minor={"paper-acct": 10**9})
        self.core = FakeCore(budget_minor=10**9)
        self.gw = plugin.build(self.core, path, Caps(max_notional_minor=500_000, max_quantity=100),
                               run_check=self.core.check, allowlist=ALLOWLIST)
        self.book = PaperBook(path)  # a second connection, read only here
        self.addCleanup(self.book.close)

    def _sized(self) -> Intent:
        """The plain paper buy, sized by the gateway: 2_000 bps of the 500_000 cap is 10 shares."""
        intent = self.gw.size_buy(intent_id=FIELDS["intent_id"], account=FIELDS["account"],
                                  instrument=FIELDS["instrument"], limit_price_minor=FIELDS["limit_price_minor"],
                                  conviction_bps=2_000, run_id=FIELDS["run_id"], connection=FIELDS["connection"])
        self.assertEqual(intent, Intent(**FIELDS))
        return intent

    def _approved_twin(self) -> str:
        """A real, operator-approved approval for the plain paper twin; the core log is then cleared."""
        approval = self.gw.request_approval(self._sized())["approval_id"]
        self.core.operator_approves(approval)
        self.core.calls.clear()
        return approval

    def _no_side_effect(self, approval: str) -> None:
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.core.spent, 0)
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.working(), 0)
        self.assertEqual(self.core.approvals[approval]["state"], "approved")  # not used up

    def _refused_everywhere(self, code: str, order: object) -> None:
        """Refused with ``code`` at request and at submit (bogus and real approval), no side effect."""
        approval = self._approved_twin()
        drawn = len(self.core.approvals)
        attempts = (("request_approval", lambda: self.gw.request_approval(order)),
                    ("submit_bogus_approval", lambda: self.gw.submit(order, BOGUS_APPROVAL, AUTHORITY)),
                    ("submit_real_approval", lambda: self.gw.submit(order, approval, AUTHORITY)))
        for name, attempt in attempts:
            with self.subTest(entry=name):
                with self.assertRaises(Refused) as ctx:
                    attempt()
                self.assertEqual((ctx.exception.code, ctx.exception.cause), (code, None))
                self._no_side_effect(approval)
                self.assertEqual(len(self.core.approvals), drawn)

    def _control_still_books(self) -> None:
        """The plain paper intent books, so nothing above passed by refusing everything."""
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.core.spent, 0)
        intent = self._sized()
        approval = self.gw.request_approval(intent)["approval_id"]
        self.core.operator_approves(approval)
        out = self.gw.submit(intent, approval, AUTHORITY)
        self.assertEqual((out["status"], out["notional_minor"]), ("paper_working", 100_000))
        self.assertEqual(self.book.orders(), [("i-1", 1, "working")])
        self.assertEqual(self.core.spent, 100_000)
        self.assertEqual(self.core.calls, ["effect.request", "effect.authorize"])

    def test_every_non_paper_venue_is_refused_before_any_side_effect(self) -> None:
        for venue in VENUES:
            with self.subTest(venue=repr(venue), type=type(venue).__name__):
                self._refused_everywhere("paper_only", Intent(**FIELDS, venue=venue))
        self._control_still_books()

    def test_every_non_paper_mode_is_refused_before_any_side_effect(self) -> None:
        for mode in MODES:
            with self.subTest(mode=repr(mode), type=type(mode).__name__):
                self._refused_everywhere("paper_only", Intent(**FIELDS, mode=mode))
        self._control_still_books()

    def test_rigged_venue_and_mode_together_are_refused(self) -> None:
        for venue in RIGGED:
            for mode in RIGGED:
                with self.subTest(venue=repr(venue), mode=repr(mode)):
                    self._refused_everywhere("paper_only", Intent(**FIELDS, venue=venue, mode=mode))
        self._control_still_books()

    def test_intent_mutated_after_its_approval_is_refused_at_submit(self) -> None:
        for venue in ["live"] + RIGGED:
            with self.subTest(venue=repr(venue)):
                intent = self._sized()
                approval = self.gw.request_approval(intent)["approval_id"]
                self.core.operator_approves(approval)
                self.core.calls.clear()
                object.__setattr__(intent, "venue", venue)
                with self.assertRaises(Refused) as ctx:
                    self.gw.submit(intent, approval, AUTHORITY)
                self.assertEqual(ctx.exception.code, "paper_only")
                self.assertNotIn("effect.authorize", self.core.calls)
                self._no_side_effect(approval)
        self._control_still_books()

    def test_non_intent_orders_are_refused_before_the_core(self) -> None:
        fields = asdict(Intent(**FIELDS))
        missing_venue = {k: v for k, v in fields.items() if k != "venue"}
        orders = {"dict": dict(fields), "namespace": types.SimpleNamespace(**fields),
                  "namespace_missing_venue": types.SimpleNamespace(**missing_venue),
                  "intent_subclass": SubIntent(**FIELDS),
                  "intent_subclass_live": SubIntent(**FIELDS, venue="live")}
        for name, order in orders.items():
            with self.subTest(order=name):
                self._refused_everywhere("intent_invalid", order)
        self._control_still_books()

    def test_side_variants_are_refused_typed_before_the_core(self) -> None:
        for side in SIDES:
            with self.subTest(side=repr(side), type=type(side).__name__):
                self._refused_everywhere("side_invalid", replace(Intent(**FIELDS), side=side))
        self._control_still_books()

    def test_str_subclass_side_is_a_typed_audited_refusal(self) -> None:
        """A ``side`` that is a ``str`` subclass equal to ``"buy"`` or ``"sell"`` is the typed
        ``side_invalid`` with its audit row at request and at submit, never a raw ``ValueError``
        from the core client's encoder."""
        for n, side in enumerate((StrSub("buy"), StrSub("sell"))):
            intent = replace(Intent(**FIELDS), intent_id="side-%d" % n, side=side)
            for name, attempt in (("request", lambda: self.gw.request_approval(intent)),
                                  ("submit", lambda: self.gw.submit(intent, BOGUS_APPROVAL, AUTHORITY))):
                with self.subTest(side=str(side), entry=name):
                    before = len(self.book.audit())
                    with self.assertRaises(Refused) as ctx:
                        attempt()
                    self.assertEqual((ctx.exception.code, ctx.exception.cause), ("side_invalid", None))
                    rows = self.book.audit()
                    self.assertEqual(len(rows), before + 1)
                    self.assertEqual({k: rows[-1][k] for k in ("seq", "event", "stage", "code", "intent_id")},
                                     {"seq": ctx.exception.audit_seq, "event": "refused", "stage": "risk",
                                      "code": "side_invalid", "intent_id": intent.intent_id})
        self.assertEqual((self.core.calls, self.core.spent, self.book.orders()), ([], 0, []))
        self._control_still_books()

    def test_intent_with_a_missing_field_is_refused_typed_before_the_core(self) -> None:
        """An ``Intent`` built without ``__init__``, or with a field deleted, is ``intent_invalid``
        (never an ``AttributeError``) at request and at submit: no core call, nothing booked."""
        with self.subTest(order="object.__new__"):
            self._refused_everywhere("intent_invalid", object.__new__(Intent))
        for field in FIELDS:
            with self.subTest(deleted=field):
                intent = Intent(**FIELDS)
                object.__delattr__(intent, field)
                self._refused_everywhere("intent_invalid", intent)
        self._control_still_books()

    def test_a_missing_field_keeps_the_check_order(self) -> None:
        """Checks before the missing field's own still answer first; the halt outranks them all."""
        def without_side(**changes: object) -> Intent:
            intent = Intent(**dict(FIELDS, **changes))
            object.__delattr__(intent, "side")
            return intent
        for code, changes in (("paper_only", {"venue": "live"}), ("paper_only", {"mode": "live"}),
                              ("intent_invalid", {"intent_id": ""}), ("instrument_invalid", {"instrument": "aapl"}),
                              ("intent_invalid", {"quantity": 0}), ("intent_invalid", {"quantity": 10**9})):
            with self.subTest(code=code, changes=changes):
                self._refused_everywhere(code, without_side(**changes))
        self.gw.halt()
        for name, order in (("no_fields", object.__new__(Intent)), ("no_side", without_side())):
            with self.subTest(halted=name):
                self._refused_everywhere_halted(order)

    def _refused_everywhere_halted(self, order: object) -> None:
        self.core.calls.clear()
        for attempt in (lambda: self.gw.request_approval(order),
                        lambda: self.gw.submit(order, BOGUS_APPROVAL, AUTHORITY)):
            with self.assertRaises(Refused) as ctx:
                attempt()
            self.assertEqual((ctx.exception.code, ctx.exception.cause), ("order_halted", None))
        self.assertEqual((self.core.calls, self.core.spent, self.book.orders()), ([], 0, []))

    def test_paper_only_is_checked_before_the_later_checks(self) -> None:
        """A non-paper order that is also malformed elsewhere is still refused ``paper_only``."""
        for venue in ["kalshi"] + RIGGED:
            with self.subTest(venue=repr(venue)):
                self._refused_everywhere("paper_only", Intent(**dict(FIELDS, intent_id="", side="short",
                                                                     quantity=0), venue=venue))
        self._control_still_books()


if __name__ == "__main__":
    unittest.main()
