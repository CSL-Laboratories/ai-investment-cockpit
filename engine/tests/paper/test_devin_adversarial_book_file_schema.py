"""Devin adversarial: PaperBook on a file that is not a paper book raises Refused("book_invalid").

``PaperBook.__init__`` only checks ``os.path.isfile``. A zero-byte file, a garbage file, a SQLite
file without the book schema, or one with a ``state`` table missing the ``halted`` row is
accepted, and the failure surfaces later as a raw sqlite3.OperationalError / DatabaseError /
TypeError instead of the ``Refused`` contract. Contract tested: ``PaperBook(path)`` itself, or at
the latest its first public read (``halted()``), raises ``Refused`` with code ``book_invalid``.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest

from cockpit.paper.gateway import PaperBook, Refused


class BookFileSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="devin-adv-book-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _assert_book_invalid(self, path: str) -> None:
        book = None
        try:
            book = PaperBook(path)
            book.halted()
        except Refused as exc:
            self.assertEqual(exc.code, "book_invalid")
            return
        except Exception as exc:  # noqa: BLE001 - the finding: a raw error, not Refused
            self.fail("expected Refused('book_invalid'), got raw %s: %s" % (type(exc).__name__, exc))
        finally:
            if book is not None:
                book.close()
        self.fail("expected Refused('book_invalid'); a non-book file opened and read without error")

    def test_zero_byte_file(self) -> None:
        path = self._path("empty.db")
        open(path, "wb").close()
        self._assert_book_invalid(path)

    def test_garbage_file(self) -> None:
        path = self._path("garbage.db")
        with open(path, "wb") as fh:
            fh.write(b"this is not a sqlite database\x00\xff" * 200)
        self._assert_book_invalid(path)

    def test_sqlite_file_without_book_schema(self) -> None:
        path = self._path("other.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE foo (x INTEGER)")
        db.commit()
        db.close()
        self._assert_book_invalid(path)

    def test_state_table_missing_halted_row(self) -> None:
        path = self._path("partial.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v INTEGER NOT NULL)")
        db.commit()
        db.close()
        self._assert_book_invalid(path)


if __name__ == "__main__":
    unittest.main()
