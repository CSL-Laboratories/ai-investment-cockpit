"""Adversarial: importing gateway._GATEWAY_ONLY must not let a caller book past every check.

``_GATEWAY_ONLY`` is a plain module attribute; ``book._book(intent, gateway._GATEWAY_ONLY)``
books with no halt-free risk pass, caps, core permission, approval or authority check.
Mocks only (FakeCore).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from dataclasses import replace

import cockpit.paper.gateway as gateway
from cockpit.paper.core_client import CoreClient
from cockpit.paper.gateway import Caps, OrderGateway, PaperBook, Refused, create_book
from tests.paper.fakecore import FakeCore
from tests.paper.test_gateway import INTENT


class GatewayOnlyBypassTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="adv-gw-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        create_book(path, cash_minor={"paper-acct": 10**15})
        self.book = PaperBook(path)
        self.addCleanup(self.book.close)
        self.core = FakeCore(budget_minor=100_000)
        self.gw = OrderGateway(plugin_id="csl.cockpit.paper", book=self.book,
                               core=CoreClient(self.core), caps=Caps(100_000, 50))
        self.huge = replace(INTENT, intent_id="huge-1", quantity=999_999_999, limit_price_minor=999_999_999)

    def test_risk_pass_refuses_the_oversized_intent(self) -> None:
        # Control: the gateway's own path refuses it.
        with self.assertRaises(Refused) as ctx:
            self.gw.risk_pass(self.huge)
        self.assertEqual(ctx.exception.code, "size_cap_quantity")

    def test_imported_capability_does_not_book_oversized_order(self) -> None:
        booked = None
        refused = None
        try:
            booked = self.book._book(self.huge, gateway._GATEWAY_ONLY)
        except Refused as exc:
            refused = exc
        self.assertIsNotNone(refused, "book._book(intent, gateway._GATEWAY_ONLY) booked seq=%r with no "
                                      "risk pass, caps, core permission, approval or authority" % (booked,))
        self.assertEqual(self.book.orders(), [])
        self.assertEqual(self.book.working(), 0)
        self.assertEqual(self.core.calls, [])

    def test_gateway_held_book_does_not_book_via_imported_capability(self) -> None:
        with self.assertRaises(Refused):
            self.gw._book._book(replace(self.huge, intent_id="huge-2"), gateway._GATEWAY_ONLY)
        self.assertEqual(self.book.orders(), [])


if __name__ == "__main__":
    unittest.main()
