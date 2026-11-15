"""cockpit.research_case/1: the made-up fixture, its pinned digest, and every refusal."""

import copy
import json
import os
import unittest

from cockpit.research import CASE_SCHEMA, ResearchCase, case_from_value, parse_case
from cockpit.research.errors import Refused

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def case_text():
    with open(os.path.join(FIXTURES, "case_valid.json"), encoding="ascii") as f:
        return f.read()


def case_value():
    return json.loads(case_text())


def pinned(name):
    with open(os.path.join(FIXTURES, "canonical_vectors.json"), encoding="ascii") as f:
        for v in json.load(f)["vectors"]:
            if v["name"] == name:
                return v
    raise KeyError(name)


class StrSub(str):
    pass


class IntSub(int):
    pass


class ResearchCaseFixture(unittest.TestCase):
    def test_fixture_parses(self):
        case = parse_case(case_text())
        self.assertIs(type(case), ResearchCase)
        self.assertEqual(case.case_id, "case-0001-made-up")
        self.assertEqual(case.instrument, "FAKE-001")
        self.assertEqual(case.ref_ids(), frozenset({"ev-1", "ev-2"}))
        self.assertEqual(case.max_turns, 8)

    def test_value_round_trips_and_digest_is_pinned(self):
        case = parse_case(case_text())
        self.assertEqual(case.value(), case_value())
        self.assertEqual(case.value()["schema"], CASE_SCHEMA)
        self.assertEqual(case.digest(), "sha256:" + pinned("case_valid")["sha256"])

    def test_case_is_immutable(self):
        case = parse_case(case_text())
        with self.assertRaises(AttributeError):
            case.instrument = "FAKE-002"


class ResearchCaseRefusals(unittest.TestCase):
    def refused(self, value, code):
        with self.assertRaises(Refused) as ctx:
            case_from_value(value)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_every_field_is_required(self):
        for key in case_value():
            with self.subTest(key=key):
                v = case_value()
                del v[key]
                self.refused(v, "missing_field")
        for key in ("max_turns", "max_output_bytes"):
            v = case_value()
            del v["budget"][key]
            self.refused(v, "missing_field")
        for key in ("ref_id", "sha256"):
            v = case_value()
            del v["evidence_refs"][0][key]
            self.refused(v, "missing_field")

    def test_unknown_fields(self):
        for where in ("top", "budget", "evidence"):
            with self.subTest(where=where):
                v = case_value()
                {"top": v, "budget": v["budget"], "evidence": v["evidence_refs"][0]}[where]["extra"] = 1
                self.refused(v, "unknown_field")

    def test_schema_version(self):
        for bad in ("cockpit.research_case/2", "cockpit.research_case/1 ", StrSub(CASE_SCHEMA)):
            v = case_value()
            v["schema"] = bad
            with self.subTest(bad=bad):
                self.refused(v, "schema_mismatch")

    def test_wrong_types(self):
        bad_values = {
            "case_id": [1, None, True, ["x"], StrSub("case-1")],
            "question": [1, None, b"q", StrSub("why?")],
            "instrument": [1, None, StrSub("FAKE-001")],
            "route": [1, None, StrSub("claude-code/x")],
            "evidence_refs": [{}, "ev-1", None, ()],
            "budget": [[], None, 8],
        }
        for key, values in bad_values.items():
            for bad in values:
                with self.subTest(key=key, bad=repr(bad)):
                    v = case_value()
                    v[key] = bad
                    self.refused(v, "wrong_type")

    def test_budget_numbers(self):
        for bad, code in ((0, "out_of_range"), (-1, "out_of_range"), (201, "out_of_range"),
                          (True, "wrong_type"), (8.0, "wrong_type"), ("8", "wrong_type"),
                          (IntSub(8), "wrong_type"), (float("nan"), "wrong_type")):
            with self.subTest(bad=repr(bad)):
                v = case_value()
                v["budget"]["max_turns"] = bad
                self.refused(v, code)
        v = case_value()
        v["budget"]["max_output_bytes"] = (1 << 20) + 1
        self.refused(v, "out_of_range")

    def test_ids_and_text(self):
        for key, bad in (("case_id", "Case-1"), ("case_id", "../x"), ("case_id", ""), ("case_id", "a" * 65),
                         ("case_id", "a\n"), ("instrument", "fake-001"), ("instrument", "FAKE 001"),
                         ("route", "Claude"), ("route", "")):
            with self.subTest(key=key, bad=bad):
                v = case_value()
                v[key] = bad
                self.refused(v, "invalid_value")
        for bad, code in (("", "invalid_length"), ("   ", "invalid_value"), ("x" * 2001, "invalid_length"),
                          ("caf" + chr(0xE9), "text_not_ascii"), ("a" + chr(0xD800), "text_not_ascii"),
                          ("a\x00b", "text_control_character"), ("a\x7fb", "text_control_character"),
                          ("a\rb", "text_control_character")):
            with self.subTest(bad=repr(bad)):
                v = case_value()
                v["question"] = bad
                self.refused(v, code)

    def test_evidence_refs(self):
        v = case_value()
        v["evidence_refs"] = []
        self.refused(v, "invalid_length")
        v = case_value()
        v["evidence_refs"].append(copy.deepcopy(v["evidence_refs"][0]))
        self.refused(v, "duplicate_value")
        for bad in ("A" * 64, "0" * 63, "0" * 65, "g" * 64):
            v = case_value()
            v["evidence_refs"][0]["sha256"] = bad
            with self.subTest(bad=bad):
                self.refused(v, "invalid_value")

    def test_text_input(self):
        text = case_text()
        dup = text.replace('"case_id": "case-0001-made-up",',
                           '"case_id": "case-0001-made-up", "case_id": "case-0002-made-up",')
        self.assertNotEqual(dup, text)
        with self.assertRaises(Refused) as ctx:
            parse_case(dup)
        self.assertEqual(ctx.exception.code, "duplicate_key")
        for bad, code in ((text.encode("ascii"), "not_text"), ("[" + text + "]", "wrong_type"),
                          (text + text, "not_json"), (" " * (64 * 1024 + 1), "too_large")):
            with self.subTest(code=code):
                with self.assertRaises(Refused) as ctx:
                    parse_case(bad)
                self.assertEqual(ctx.exception.code, code)


if __name__ == "__main__":
    unittest.main()
