"""cockpit.research.canonical: the shared vectors and every refusal of the form."""

import json
import os
import unittest

import cockpit.research.canonical as cj
from cockpit.research.errors import Refused

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _vectors():
    with open(os.path.join(FIXTURES, "canonical_vectors.json"), encoding="ascii") as f:
        return json.load(f)


class StrSub(str):
    pass


class IntSub(int):
    pass


class DictSub(dict):
    pass


class ListSub(list):
    pass


class CanonicalVectors(unittest.TestCase):
    def test_form_name(self):
        self.assertEqual(cj.DIGEST_FORM, "csl-cj/v0")
        self.assertEqual(_vectors()["form"], cj.DIGEST_FORM)

    def test_every_vector_gives_the_pinned_bytes_and_digest(self):
        vectors = _vectors()["vectors"]
        self.assertGreaterEqual(len(vectors), 7)
        for v in vectors:
            with self.subTest(v["name"]):
                raw = cj.canonical(v["value"])
                self.assertEqual(raw, v["canonical"].encode("ascii"))
                self.assertEqual(cj.digest(v["value"]), "sha256:" + v["sha256"])
                self.assertEqual(cj.parse(raw), v["value"])

    def test_every_refused_byte_string_is_refused_by_parse(self):
        for text in _vectors()["refused_bytes"]:
            with self.subTest(text=text):
                with self.assertRaises(Refused) as ctx:
                    cj.parse(text.encode("utf-8"))
                self.assertIn(ctx.exception.code, ("noncanonical_value", "duplicate_key"))


class CanonicalRefusals(unittest.TestCase):
    def refused(self, value):
        with self.assertRaises(Refused) as ctx:
            cj.canonical(value)
        self.assertEqual(ctx.exception.code, "noncanonical_value")

    def test_floats_and_non_finite(self):
        for value in (1.0, 0.5, float("nan"), float("inf"), float("-inf"), {"a": 2.5}, [1e300]):
            with self.subTest(value=value):
                self.refused(value)

    def test_integer_range(self):
        cj.canonical(2**53 - 1)
        cj.canonical(-(2**53 - 1))
        for value in (2**53, -(2**53), 10**400):
            with self.subTest(value=value):
                self.refused(value)

    def test_text_must_be_ascii(self):
        for value in ("caf" + chr(0xE9), chr(0xD800), "a" + chr(0xDFFF) + "b", {"k": chr(0x2713)}, {chr(0xE9): 1}):
            with self.subTest(value=ascii(value)):
                self.refused(value)

    def test_subclasses_and_other_types(self):
        for value in (StrSub("x"), IntSub(1), DictSub(), ListSub(), (1, 2), {1: "a"}, b"x",
                      bytearray(b"x"), {"a": object()}, {1, 2}):
            with self.subTest(value=type(value).__name__):
                self.refused(value)

    def test_bool_is_kept_as_bool(self):
        self.assertEqual(cj.canonical([True, False, None]), b"[true,false,null]")

    def test_depth_limit(self):
        value = 1
        for _ in range(33):
            value = [value]
        self.refused(value)
        value = 1
        for _ in range(32):
            value = [value]
        cj.canonical(value)

    def test_parse_wants_exact_bytes(self):
        self.assertEqual(cj.parse(b"{}"), {})
        for bad in ("{}", bytearray(b"{}"), memoryview(b"{}"), None):
            with self.subTest(type=type(bad).__name__):
                with self.assertRaises(Refused):
                    cj.parse(bad)

    def test_parse_refuses_deep_nesting_without_crashing(self):
        with self.assertRaises(Refused):
            cj.parse(b"[" * 100000 + b"]" * 100000)


class UntrustedLoads(unittest.TestCase):
    def test_whitespace_is_allowed(self):
        self.assertEqual(cj.loads_untrusted(' {\n "a" : [1, 2] }\n', max_chars=100), {"a": [1, 2]})

    def test_refusals(self):
        cases = {
            '{"a":1,"a":2}': "duplicate_key",
            '{"a":1.5}': "noncanonical_value",
            '{"a":1e2}': "noncanonical_value",
            '{"a":NaN}': "noncanonical_value",
            '{"a":-Infinity}': "noncanonical_value",
            '{"a":' + "9" * 17 + "}": "noncanonical_value",
            '{"a":' + "9" * 5000 + "}": "noncanonical_value",
            '{"a":9007199254740992}': "noncanonical_value",
            "here it is: {}": "not_json",
            "{} {}": "not_json",
            "": "not_json",
            "[" * 50000: "not_json",
        }
        for text, code in cases.items():
            with self.subTest(text=text[:40]):
                with self.assertRaises(Refused) as ctx:
                    cj.loads_untrusted(text, max_chars=1 << 20)
                self.assertEqual(ctx.exception.code, code)

    def test_type_size_and_surrogates(self):
        for bad, code in ((b"{}", "not_text"), (StrSub("{}"), "not_text"), (None, "not_text"),
                          ("{}" + " " * 63, "too_large"), ('{"a":"' + chr(0xD800) + '"}', "text_invalid")):
            with self.subTest(code=code):
                with self.assertRaises(Refused) as ctx:
                    cj.loads_untrusted(bad, max_chars=64)
                self.assertEqual(ctx.exception.code, code)

    def test_an_escaped_lone_surrogate_is_refused_too(self):
        # DeepSeek F9: the escape decodes to a lone surrogate, which the reader refuses.
        for escaped in ('{"a":"' + chr(92) + 'ud800"}', '{"a":"x' + chr(92) + 'udfffy"}',
                        '{"' + chr(92) + 'ud800":1}', '["' + chr(92) + 'udbff"]'):
            with self.subTest(escaped=escaped):
                with self.assertRaises(Refused) as ctx:
                    cj.loads_untrusted(escaped, max_chars=64)
                self.assertEqual(ctx.exception.code, "text_invalid")
        pair = '"' + chr(92) + 'ud83d' + chr(92) + 'ude00"'  # a valid escaped pair is one character
        self.assertEqual(cj.loads_untrusted(pair, max_chars=64), chr(0x1F600))


if __name__ == "__main__":
    unittest.main()
