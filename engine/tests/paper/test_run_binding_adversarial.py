"""Run and connection binding of an approved order, on the fake core.

PRODUCT.md 4.2 (d84cdd26a4de2366f3c5d34c4aec5215f822a494): "Two breakers, both in the
way" (an intent from a faulted run or a tripped route is refused at the ``OrderGateway``)
and "Approve or veto is bound to what was shown" (the operator approves the exact order
by its digest).

The run and the connection are fields of the effect the operator approves, so an approval
drawn for one run cannot carry an order citing another: not a healthy sibling of a faulted
run, not another connection, not a spelling variant. Malformed run and connection ids never
reach the core. Every refusal here is audited and nothing is booked.

What this does not cover: a fresh approval requested under a healthy run for a proposal
that came from another run of the same plugin. The run check cannot see that; it is the
proposal-to-run binding the core still has to carry.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/tests/paper -t engine -v``.
Paper only: no provider, venue, network, credential or keyring is touched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from dataclasses import replace

from cockpit.paper.core_client import CoreClient, canonical
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import ALLOWLIST, AUTHORITY, DEFAULT_PLUGIN, FakeCore, sized

ACCT = "paper-acct"
CASH = 1_000_000
BASE = Intent(intent_id="rb", account=ACCT, instrument="AAPL", side="buy", quantity=2,
              limit_price_minor=100, run_id="run-a", connection="conn-s")


class StrSub(str):
    """Equal to its text for every ``str`` comparison, but not exactly a ``str``."""


class RunBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="cockpit-run-binding-")
        self.addCleanup(shutil.rmtree, d, True)
        self.path = os.path.join(d, "book.db")
        create_book(self.path, cash_minor={ACCT: CASH})
        self.book = PaperBook(self.path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=10**9, runs={})
        # Two sibling runs of this plugin on one connection, and a third run on another one.
        for run, conn in (("run-a", "conn-s"), ("run-b", "conn-s"), ("run-c", "conn-c")):
            self.core.bind_run(run, conn)
        self.gw = OrderGateway(plugin_id=DEFAULT_PLUGIN, book=self.book, core=CoreClient(self.core),
                               caps=Caps(10**9, 10**6), allowlist=ALLOWLIST, run_check=self.core.check)

    def _approved(self, intent: Intent) -> str:
        approval = self.gw.request_approval(sized(self.gw, intent))["approval_id"]
        self.core.operator_approves(approval)
        return approval

    def _refusal(self, call, *args: object) -> Refused:
        with self.assertRaises(Refused) as ctx:
            call(*args)
        return ctx.exception

    def _nothing_booked(self) -> None:
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.available_cash_minor(ACCT), CASH)
        self.assertNotIn("booked", [r["event"] for r in self.book.audit()])

    def test_run_and_connection_are_bound_into_the_approved_digest(self) -> None:
        """``run_id`` and ``connection`` are top-level effect fields, so the digest the core
        draws (and the operator approves) changes when either changes."""
        a = self.gw.risk_pass(BASE)
        b = self.gw.risk_pass(replace(BASE, run_id="run-b"))
        c = self.gw.risk_pass(replace(BASE, run_id="run-c", connection="conn-c"))
        self.assertEqual((a["run_id"], a["connection"], a["plugin_id"]), ("run-a", "conn-s", DEFAULT_PLUGIN))
        self.assertEqual(len({hashlib.sha256(canonical(e)).hexdigest() for e in (a, b, c)}), 3)
        drawn = self.gw.request_approval(sized(self.gw, BASE))
        self.assertEqual(drawn["digest"], hashlib.sha256(canonical(a)).hexdigest())

    def test_faulted_run_relabelled_onto_healthy_sibling_is_refused(self) -> None:
        """run-b's approved intent, after run-b faults, re-sent with run-b's approval but citing
        its healthy sibling run-a (same plugin, same connection): the approval names run-b."""
        b = replace(BASE, intent_id="rb-b", run_id="run-b")
        approval = self._approved(b)
        self.core.faulted_runs.add("run-b")
        relabel = replace(b, run_id="run-a")
        codes = [self._refusal(self.gw.submit, relabel, approval, AUTHORITY).code,  # as the caller made it
                 self._refusal(self.gw.submit, sized(self.gw, relabel), approval, AUTHORITY).code,
                 self._refusal(self.gw.submit, b, approval, AUTHORITY).code]
        self.assertEqual(codes, ["quantity_not_sized", "core:approval_digest_mismatch", "core:run_faulted"])
        self.assertEqual(self.core.spent, 0)
        self._nothing_booked()
        self.assertEqual([(r["stage"], r["code"], r["run_id"]) for r in self.book.audit()],
                         [("size", "quantity_not_sized", "run-a"),
                          ("authorize", "core:approval_digest_mismatch", "run-a"),
                          ("authorize", "core:run_faulted", "run-b")])

    def test_connection_or_run_changed_between_approval_and_submit_is_refused(self) -> None:
        """An approved intent moved to another healthy connection is ``run_connection_mismatch``;
        moved to another healthy run on that run's own connection it is
        ``approval_digest_mismatch``."""
        approval = self._approved(BASE)
        moved = sized(self.gw, replace(BASE, connection="conn-c"))
        other = sized(self.gw, replace(BASE, run_id="run-c", connection="conn-c"))
        codes = [self._refusal(self.gw.submit, intent, approval, AUTHORITY).code for intent in (moved, other)]
        self.assertEqual(codes, ["core:run_connection_mismatch", "core:approval_digest_mismatch"])
        self.assertEqual((self.core.spent, self.core.approvals[approval]["state"]), (0, "approved"))
        self._nothing_booked()

    def test_run_id_and_connection_variants_are_refused_before_the_core(self) -> None:
        """None, empty, a ``str`` subclass, whitespace, control characters, non-ASCII
        look-alikes, bytes, int and over-long ids are ``intent_invalid`` at the risk stage,
        at request and at submit, each audited, with no core call."""
        self.core.faulted_runs.add("run-a")
        variants = (None, "", StrSub("run-a"), " run-a", "run-a ", "run-a\n", "\trun-a", "run-a\x00",
                    "run‐a", "ｒun-a", "run-а", b"run-a", 0, "r" * 65)
        n = 0
        for field in ("run_id", "connection"):
            for value in variants:
                with self.subTest(field=field, value=value):
                    n += 1
                    intent = sized(self.gw, replace(BASE, intent_id="v%d" % n, **{field: value}))
                    for call, args in ((self.gw.request_approval, (intent,)),
                                       (self.gw.submit, (intent, "apr_0", AUTHORITY))):
                        exc = self._refusal(call, *args)
                        self.assertEqual((exc.code, exc.cause), ("intent_invalid", None))
                        row = self.book.audit()[-1]
                        self.assertEqual((row["seq"], row["stage"], row["code"], row["intent_id"]),
                                         (exc.audit_seq, "risk", "intent_invalid", intent.intent_id))
        self.assertEqual(self.core.calls, [], "an invalid run or connection reached the core")
        self.assertEqual(len(self.book.audit()), 2 * n)
        self._nothing_booked()

    def test_case_variants_of_a_faulted_run_and_a_tripped_connection_do_not_book(self) -> None:
        """A case variant is another id: the core refuses it (``run_unknown`` or
        ``run_connection_mismatch``) while run-a is faulted and conn-s tripped."""
        self.core.faulted_runs.add("run-a")
        self.core.tripped_connections.add("conn-s")
        cases = {("RUN-A", "conn-s"): "core:run_unknown", ("Run-a", "conn-s"): "core:run_unknown",
                 ("run-a", "CONN-S"): "core:run_connection_mismatch",
                 ("run-a", "Conn-s"): "core:run_connection_mismatch", ("run-a", "conn-s"): "core:run_faulted",
                 ("run-b", "conn-s"): "core:connection_tripped"}
        got = {}
        for i, (run, conn) in enumerate(cases):
            intent = sized(self.gw, replace(BASE, intent_id="cv%d" % i, run_id=run, connection=conn))
            got[(run, conn)] = self._refusal(self.gw.request_approval, intent).code
        self.assertEqual(got, cases)
        self.assertEqual(self.core.approvals, {})
        self.assertEqual([(r["stage"], r["code"]) for r in self.book.audit()],
                         [("request", code) for code in cases.values()])
        self._nothing_booked()

    def test_leftover_charge_row_survives_the_rolled_back_booking(self) -> None:
        """A halt that lands during the core call refuses the booking; the ``leftover_charge``
        refusal is still a committed audit row, read here by a raw connection opened after it."""
        approval = self._approved(BASE)
        core = self.core

        def halting(raw: bytes) -> bytes:
            out = core(raw)
            self.book.halt()
            return out
        self.gw._core = CoreClient(halting)
        exc = self._refusal(self.gw.submit, BASE, approval, AUTHORITY)
        self.assertEqual((exc.code, exc.cause), ("leftover_charge", "order_halted"))
        raw = sqlite3.connect(self.path)
        self.addCleanup(raw.close)
        self.assertEqual(raw.execute("SELECT seq, event, stage, code FROM audit").fetchall(),
                         [(exc.audit_seq, "refused", "book", "leftover_charge:order_halted")])
        self._nothing_booked()


if __name__ == "__main__":
    unittest.main()
