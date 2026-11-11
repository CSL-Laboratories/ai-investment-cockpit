"""Adversarial: a venue status that only *compares* equal to "confirmed" is not confirmed.

killswitch.py normalizes each venue dict with ``out.get("status") != "confirmed"`` (``__eq__``),
not the exact-type closed set used elsewhere (``type(x) is str``). Mocks only.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from cockpit.paper.gateway import PaperBook, create_book
from cockpit.paper.killswitch import KillSwitch


class Sneaky:
    """Not a str; claims equality with anything, so ``!= "confirmed"`` is False."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = object.__hash__


class Sneakier(str):
    """A str subclass also outside the exact-type closed set."""


class HostileVenue:
    def __init__(self, status: object) -> None:
        self.status = status

    def kill(self) -> dict:
        return {"venue": "hostile", "status": self.status}


class RiggedEqStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.mkdtemp(prefix="adv-eq-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "book.db")
        create_book(path, cash_minor={"paper-acct": 10**15})
        self.book = PaperBook(path)
        self.addCleanup(self.book.close)

    def _kill(self, status: object) -> dict:
        return KillSwitch(self.book.halt, [HostileVenue(status)], paper_book=self.book).kill()

    def test_sneaky_eq_status_is_forced_pending(self) -> None:
        out = self._kill(Sneaky())
        hostile = out["venues"][-1]
        self.assertNotEqual(out["status"], "confirmed",
                            "a Sneaky __eq__ status made the whole KillSwitch report confirmed")
        self.assertEqual(out["status"], "pending")
        self.assertIs(type(hostile["status"]), str, "venue status must be normalized to an exact str")
        self.assertEqual(hostile["status"], "pending")

    def test_str_subclass_status_is_forced_pending(self) -> None:
        out = self._kill(Sneakier("confirmed"))
        hostile = out["venues"][-1]
        self.assertEqual(out["status"], "pending", "a str-subclass status made the KillSwitch report confirmed")
        self.assertIs(type(hostile["status"]), str)
        self.assertEqual(hostile["status"], "pending")


if __name__ == "__main__":
    unittest.main()
