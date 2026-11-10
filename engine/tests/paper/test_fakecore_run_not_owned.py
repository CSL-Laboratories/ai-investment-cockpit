"""FakeCore parity with the real core breaker's run ownership (``run_not_owned``).

The real Recursion core (recursion #328, ``core/recursion_core/plugin_security/breaker.py``)
binds a run to ``(connection, plugin_id, provider, model_version)`` with
``bind_run(run_id, connection, plugin_id, provider, model_version)``, and
``ConnectionBreaker.check(run_id, connection, plugin_id)`` refuses in this order:
``run_unknown``, ``run_not_owned``, ``run_connection_mismatch``, ``run_faulted``,
``connection_tripped``. ``CoreService._check_plugin_effect`` runs that check, with the
authenticated caller (which must equal ``effect["plugin_id"]``), on both ``effect.request``
and ``effect.authorize``, before the approval gate is touched.

These cells drive the gateway against the shipped ``FakeCore`` the way the gateway would.
A run already bound to another plugin must be refused ``core:run_not_owned``, before
``core:run_connection_mismatch``, at request and at authorize, and the refusal audited.
They fail until ``FakeCore`` records a run's owning plugin and checks it like the core.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, Intent, OrderGateway, PaperBook, Refused
from tests.paper.fakecore import AUTHORITY, FakeCore
from tests.paper.test_gateway import _book_path

N = 5
OWNER = "csl.cockpit.paper"
OTHER = "other.plugin"
PROVIDER, MODEL = "provider-fake", "model-fake"
BASE = Intent(intent_id="own", account="paper-acct", instrument="AAPL", side="buy", quantity=1,
              limit_price_minor=100, run_id="run-ok", connection="conn-ok")


def _bind(core: FakeCore, run: str, connection: str, plugin_id: str) -> bool:
    """Bind like the core's ``run.bind``. Returns False when FakeCore cannot record an owner."""
    try:
        core.bind_run(run, connection, plugin_id, PROVIDER, MODEL)
        return True
    except TypeError:
        core.bind_run(run, connection)
        return False


class FakeCoreRunNotOwned(unittest.TestCase):
    def setUp(self) -> None:
        self.core = FakeCore(budget_minor=10**9, runs={})

    def _gateway(self, plugin_id: str) -> tuple[OrderGateway, PaperBook]:
        book = PaperBook(_book_path(self))
        self.addCleanup(book.close)
        gw = OrderGateway(plugin_id=plugin_id, book=book, core=CoreClient(self.core), caps=Caps(10**9, 10**6))
        return gw, book

    def _code(self, call, *args) -> str | None:
        try:
            call(*args)
        except Refused as exc:
            return exc.code
        return None

    def test_run_owned_by_another_plugin_refused_at_request(self) -> None:
        gw, book = self._gateway(OWNER)
        codes, owner_recorded = [], []
        for i in range(N):
            run, conn = "run-x%d" % i, "conn-x%d" % i
            owner_recorded.append(_bind(self.core, run, conn, OTHER))
            intent = replace(BASE, intent_id="x%d" % i, run_id=run, connection=conn)
            codes.append(self._code(gw.request_approval, intent))
        self.assertEqual(codes, ["core:run_not_owned"] * N,
                         "a run bound to %r was not refused run_not_owned for %r (owner recorded by "
                         "FakeCore.bind_run: %r)" % (OTHER, OWNER, owner_recorded))
        self.assertEqual(self.core.approvals, {}, "an approval was drawn for a run another plugin owns")
        self.assertEqual([(r["event"], r["stage"], r["code"]) for r in book.audit()],
                         [("refused", "request", "core:run_not_owned")] * N)
        self.assertEqual(book.orders(), [])

    def test_run_not_owned_precedes_connection_mismatch(self) -> None:
        gw, book = self._gateway(OWNER)
        codes = []
        for i in range(N):
            run = "run-y%d" % i
            _bind(self.core, run, "conn-bound%d" % i, OTHER)
            intent = replace(BASE, intent_id="y%d" % i, run_id=run, connection="conn-sent%d" % i)
            codes.append(self._code(gw.request_approval, intent))
        self.assertEqual(codes, ["core:run_not_owned"] * N,
                         "the core checks ownership before the connection: run_not_owned must win over "
                         "run_connection_mismatch")
        self.assertEqual([r["code"] for r in book.audit()], ["core:run_not_owned"] * N)

    def test_run_not_owned_at_authorize_before_approval_gate(self) -> None:
        owner_gw, owner_book = self._gateway(OWNER)
        other_gw, other_book = self._gateway(OTHER)
        codes, approvals = [], []
        for i in range(N):
            run, conn = "run-z%d" % i, "conn-z%d" % i
            _bind(self.core, run, conn, OWNER)
            intent = replace(BASE, intent_id="z%d" % i, run_id=run, connection=conn)
            approval = owner_gw.request_approval(intent)["approval_id"]
            self.core.operator_approves(approval)
            approvals.append((intent, approval))
            # Another plugin presents the owner's run and approval: the core refuses the run
            # ownership in its breaker check, before the approval gate sees the digest.
            codes.append(self._code(other_gw.submit, intent, approval, AUTHORITY))
        self.assertEqual(codes, ["core:run_not_owned"] * N,
                         "the core's breaker check (run ownership) precedes the approval gate at authorize")
        self.assertEqual([(r["event"], r["stage"], r["code"]) for r in other_book.audit()],
                         [("refused", "authorize", "core:run_not_owned")] * N)
        self.assertEqual(other_book.orders(), [])
        # The refused attempt spent nothing: the owner still books with its own approval.
        for intent, approval in approvals:
            self.assertEqual(owner_gw.submit(intent, approval, AUTHORITY)["status"], "paper_working")
        self.assertEqual([o[0] for o in owner_book.orders()], ["z%d" % i for i in range(N)])

    def test_run_owned_by_this_plugin_still_books(self) -> None:
        """Control: ownership parity must not refuse the run's own plugin."""
        gw, book = self._gateway(OWNER)
        for i in range(N):
            run, conn = "run-o%d" % i, "conn-o%d" % i
            _bind(self.core, run, conn, OWNER)
            intent = replace(BASE, intent_id="o%d" % i, run_id=run, connection=conn)
            approval = gw.request_approval(intent)["approval_id"]
            self.core.operator_approves(approval)
            self.assertEqual(gw.submit(intent, approval, AUTHORITY)["status"], "paper_working")
        self.assertEqual([o[0] for o in book.orders()], ["o%d" % i for i in range(N)])


if __name__ == "__main__":
    unittest.main()
