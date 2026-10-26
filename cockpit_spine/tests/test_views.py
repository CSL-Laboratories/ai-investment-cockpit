from __future__ import annotations

import unittest

from cockpit_spine import projections as proj
from cockpit_spine import views
from cockpit_spine.views import Line, ViewError

REGISTRY = proj.load_registry()
GOOD_LINK = REGISTRY.figure("x2-fake-driver-cells-accepted").text()


def check(*lines: Line) -> None:
    views.validate_lines(list(lines), REGISTRY)


class AcceptedLineTests(unittest.TestCase):
    def test_each_kind_has_an_accepted_shape(self) -> None:
        check(
            Line("id", "session", "s-1"),
            Line("time", "at", "2026-10-26T20:00:01Z"),
            Line("digest", "head", "sha256:" + "a" * 64),
            Line("code", "result", "session_stopped"),
            Line("term", "quantity", "3"),
            Line("term", "notional", "none"),
            Line("text", "state", "running"),
            Line("quoted", "thesis", "masked [n] text"),
            views.figure("accepted", REGISTRY.figure("x2-fake-driver-cells-accepted")),
            views.figure("withheld", None),
        )


class UnlinkedNumberTests(unittest.TestCase):
    def assertRejected(self, line: Line) -> None:
        with self.assertRaises(ViewError, msg=str(line)):
            check(line)

    def test_a_bare_number_in_a_figure_line_is_rejected(self) -> None:
        for value in ("15", "15 cells", "15 cells [projection:nope@sha256:" + "0" * 64 + "]"):
            self.assertRejected(Line("figure", "accepted", value))

    def test_a_link_whose_number_was_altered_is_rejected(self) -> None:
        self.assertRejected(Line("figure", "accepted", GOOD_LINK.replace("15 cells", "16 cells")))
        self.assertRejected(Line("figure", "accepted", GOOD_LINK.replace("cells", "rows")))
        digest = REGISTRY.get("x2-fake-driver-cells-accepted").digest
        self.assertRejected(Line("figure", "accepted", GOOD_LINK.replace(digest, "sha256:" + "0" * 64)))

    def test_a_link_to_another_projections_number_is_rejected(self) -> None:
        other = REGISTRY.figure("x2-fake-driver-cells-total")
        swapped = GOOD_LINK.replace(
            "x2-fake-driver-cells-accepted", "x2-fake-driver-cells-total"
        ).replace(REGISTRY.get("x2-fake-driver-cells-accepted").digest, other.projection_digest)
        self.assertRejected(Line("figure", "accepted", swapped))

    def test_digits_in_fixed_text_labels_and_quoted_prose_are_rejected(self) -> None:
        self.assertRejected(Line("text", "state", "up 12 percent"))
        self.assertRejected(Line("text", "label 2", "words"))
        self.assertRejected(Line("quoted", "thesis", "expects 3 percent"))
        self.assertRejected(Line("quoted", "thesis", "x" * 281))

    def test_numbers_cannot_hide_in_identifier_like_kinds(self) -> None:
        self.assertRejected(Line("id", "session", "1234567"))
        self.assertRejected(Line("id", "session", "12.5"))
        self.assertRejected(Line("id", "session", "has space"))
        self.assertRejected(Line("time", "at", "yesterday"))
        self.assertRejected(Line("digest", "head", "sha256:abc"))
        self.assertRejected(Line("code", "result", "http_429"))
        self.assertRejected(Line("term", "profit", "5"))
        self.assertRejected(Line("term", "quantity", "5 percent gain"))
        self.assertRejected(Line("number", "x", "5"))


class MaskTests(unittest.TestCase):
    def test_numerals_are_masked_everywhere_in_model_prose(self) -> None:
        text = "expects 3 percent, then 1,000.50 and 10% or 2026-10-26 and v2"
        line = views.quoted("thesis", text)
        self.assertNotRegex(line.value, r"[0-9]")
        check(line)

    def test_quoted_flattens_control_characters_and_survives_non_strings(self) -> None:
        self.assertEqual("a b", views.quoted("t", "a\n\x1b\tb").value)
        self.assertEqual("", views.quoted("t", None).value)
        self.assertEqual("", views.quoted("t", 5).value)

    def test_codes_with_digits_or_garbage_are_replaced(self) -> None:
        for bad in ("http_429", "Upper", "", None, 7, "x" * 70):
            self.assertEqual(views.UNRECOGNIZED, views.code("result", bad).value)
        self.assertEqual("timeout", views.code("result", "timeout").value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
