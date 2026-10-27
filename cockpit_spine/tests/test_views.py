from __future__ import annotations

import unittest

from cockpit_spine import projections as proj
from cockpit_spine import views
from cockpit_spine.views import Line, ViewError

REGISTRY = proj.load_registry()
GOOD_LINK = REGISTRY.render_id("x2-fake-driver-cells-accepted")


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
            views.figure("accepted", REGISTRY.figure("x2-fake-driver-cells-accepted"), REGISTRY),
            views.figure("withheld", None, REGISTRY),
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
        self.assertRejected(
            Line("figure", "accepted", GOOD_LINK.replace(digest, "sha256:" + "0" * 64)))

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
        self.assertFalse(any(ch.isdecimal() for ch in line.value))
        check(line)
        arabic = views.quoted("thesis", "gain ١٢ percent")
        self.assertEqual("gain [n] percent", arabic.value)
        self.assertFalse(any(ch.isdecimal() for ch in arabic.value))
        check(arabic)

    def test_superscript_fraction_and_roman_numerals_are_masked(self) -> None:
        """², ½ and Ⅻ are numeric, so isdecimal() would leave them on screen."""

        self.assertEqual("[n]", views.mask_numerals("²½Ⅻ"))
        for numeral in ("²", "½", "Ⅻ"):
            line = views.quoted("thesis", f"gain {numeral} percent")
            self.assertEqual("gain [n] percent", line.value)
            self.assertFalse(any(ch.isnumeric() for ch in line.value))
            check(line)
            with self.assertRaises(ViewError):
                check(Line("quoted", "thesis", f"gain {numeral} percent"))
            with self.assertRaises(ViewError):
                check(Line("text", "state", f"up {numeral}"))
            with self.assertRaises(ViewError):
                check(Line("text", f"lab{numeral}", "words"))

    def test_quoted_flattens_control_characters_and_survives_non_strings(self) -> None:
        self.assertEqual("a b", views.quoted("t", "a\n\x1b\tb").value)
        self.assertEqual("", views.quoted("t", None).value)
        self.assertEqual("", views.quoted("t", 5).value)

    def test_codes_with_digits_or_garbage_are_replaced(self) -> None:
        for bad in ("http_429", "Upper", "", None, 7, "x" * 70):
            self.assertEqual(views.UNRECOGNIZED, views.code("result", bad).value)
        self.assertEqual("timeout", views.code("result", "timeout").value)


class SanitizeTests(unittest.TestCase):
    def test_a_bad_line_costs_only_itself(self) -> None:
        good = Line("id", "session", "s-1")
        bad = Line("text", "state", "up 12 percent")
        safe, withheld = views.sanitize_lines([good, bad, good], REGISTRY)
        self.assertEqual(1, withheld)
        self.assertEqual([good, views.WITHHELD_LINE, good], safe)
        views.validate_lines(safe, REGISTRY)
        self.assertNotRegex(views.render_text(safe), r"12")

    def test_all_good_lines_pass_through_untouched(self) -> None:
        lines = [Line("id", "session", "s-1"), Line("code", "result", "stopped")]
        self.assertEqual((lines, 0), views.sanitize_lines(lines, REGISTRY))

    def test_a_forged_figure_line_is_withheld_not_shown(self) -> None:
        forged = Line("figure", "accepted", "99 cells [projection:x@sha256:" + "0" * 64 + "]")
        safe, withheld = views.sanitize_lines([forged], REGISTRY)
        self.assertEqual((1, [views.WITHHELD_LINE]), (withheld, safe))


class IdentifierTests(unittest.TestCase):
    def test_numeric_only_and_spaced_strings_are_not_identifiers(self) -> None:
        from cockpit_spine.ids import is_id

        for bad in ("Samir Shah", "a,b", "123456", "1.5", "", " ", None, 5, "x" * 201):
            self.assertFalse(is_id(bad), repr(bad))
        for good in ("operator-1", "a:b/c#d.e_f@g", "parquet://2026-10-26/events-1#0"):
            self.assertTrue(is_id(good), good)


if __name__ == "__main__":
    unittest.main(verbosity=2)
