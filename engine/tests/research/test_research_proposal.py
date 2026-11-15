"""cockpit.proposal/1: conviction, never a quantity. The made-up fixture and every refusal."""

import json
import os
import unittest

from cockpit.research import (CONVICTION_FULL_BPS, PROPOSAL_SCHEMA, Proposal, parse_case,
                              parse_proposal, proposal_from_value)
from cockpit.research.errors import Refused

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def read(name):
    with open(os.path.join(FIXTURES, name), encoding="ascii") as f:
        return f.read()


def proposal_value():
    return json.loads(read("proposal_valid.txt"))


def case():
    return parse_case(read("case_valid.json"))


def pinned(name):
    for v in json.loads(read("canonical_vectors.json"))["vectors"]:
        if v["name"] == name:
            return v
    raise KeyError(name)


class StrSub(str):
    pass


class IntSub(int):
    pass


class ListSub(list):
    pass


class DictSub(dict):
    pass


# Field names a model could use to smuggle a size, an amount or a price in as a size.
QUANTITY_KEYS = (
    "quantity", "qty", "Quantity", "QTY", "size", "orderSize", "order_size", "position_size", "notional",
    "notional_minor", "max_notional_minor", "amount", "amount_usd", "dollars", "usd", "shares", "contracts",
    "units", "lots", "volume", "price", "limit_price", "limitPrice", "target_price", "stop price",
    "weight", "allocation", "allocation_pct", "percent_of_book", "fraction", "leverage", "stake",
    "kelly_fraction", "cash", "exposure", "budget", "count", "num_contracts", "value", "cost", "risk",
    "margin",
)


class ProposalFixture(unittest.TestCase):
    def test_fixture_parses_against_its_case(self):
        p = parse_proposal(read("proposal_valid.txt"), case=case())
        self.assertIs(type(p), Proposal)
        self.assertEqual((p.instrument, p.side, p.conviction_bps, p.horizon), ("FAKE-001", "buy", 2500, "P1W"))
        self.assertEqual(p.citations, ("ev-1", "ev-2"))
        self.assertEqual(p.value(), proposal_value())
        self.assertEqual(p.value()["schema"], PROPOSAL_SCHEMA)

    def test_digest_is_pinned_and_ignores_the_models_layout(self):
        p = parse_proposal(read("proposal_valid.txt"))
        self.assertEqual(p.digest(), "sha256:" + pinned("proposal_valid")["sha256"])
        compact = json.dumps(proposal_value(), separators=(",", ":"), sort_keys=False)
        self.assertEqual(parse_proposal(compact).digest(), p.digest())

    def test_conviction_bounds(self):
        self.assertEqual(CONVICTION_FULL_BPS, 10_000)
        for good in (1, 10_000):
            v = proposal_value()
            v["conviction_bps"] = good
            self.assertEqual(proposal_from_value(v).conviction_bps, good)


class ProposalCarriesNoQuantity(unittest.TestCase):
    def test_every_quantity_like_field_is_refused_by_name(self):
        for key in QUANTITY_KEYS:
            for amount in (1, "1", 0, {"minor": 100}):
                with self.subTest(key=key, amount=repr(amount)):
                    v = proposal_value()
                    v[key] = amount
                    with self.assertRaises(Refused) as ctx:
                        proposal_from_value(v, case=case())
                    self.assertEqual(ctx.exception.code, "quantity_forbidden")

    def test_a_quantity_in_model_text_is_refused(self):
        text = read("proposal_valid.txt").replace('"side": "buy",', '"side": "buy", "quantity": 100,')
        self.assertIn('"quantity"', text)
        with self.assertRaises(Refused) as ctx:
            parse_proposal(text, case=case())
        self.assertEqual(ctx.exception.code, "quantity_forbidden")

    def test_a_quantity_beside_a_missing_field_is_still_a_quantity_refusal(self):
        v = proposal_value()
        del v["conviction_bps"]
        v["size"] = 5
        with self.assertRaises(Refused) as ctx:
            proposal_from_value(v)
        self.assertEqual(ctx.exception.code, "quantity_forbidden")

    def test_conviction_cannot_be_a_size_or_a_price(self):
        for bad, code in ((0, "out_of_range"), (-1, "out_of_range"), (10_001, "out_of_range"),
                          (2**53, "out_of_range"), (True, "wrong_type"), (False, "wrong_type"),
                          (2500.0, "wrong_type"), ("2500", "wrong_type"), (None, "wrong_type"),
                          ([2500], "wrong_type"), ({"bps": 2500}, "wrong_type"), (IntSub(2500), "wrong_type"),
                          (float("inf"), "wrong_type"), (float("nan"), "wrong_type")):
            with self.subTest(bad=repr(bad)):
                v = proposal_value()
                v["conviction_bps"] = bad
                with self.assertRaises(Refused) as ctx:
                    proposal_from_value(v)
                self.assertEqual(ctx.exception.code, code)

    def test_number_forms_in_model_text(self):
        base = read("proposal_valid.txt")
        for literal, code in (("2500.0", "noncanonical_value"), ("2.5e3", "noncanonical_value"),
                              ("NaN", "noncanonical_value"), ("Infinity", "noncanonical_value"),
                              ("-Infinity", "noncanonical_value"), ("9" * 400, "noncanonical_value"),
                              ("true", "wrong_type"), ('"2500"', "wrong_type"), ("02500", "not_json")):
            with self.subTest(literal=literal[:20]):
                text = base.replace('"conviction_bps": 2500', '"conviction_bps": ' + literal)
                self.assertNotEqual(text, base)
                with self.assertRaises(Refused) as ctx:
                    parse_proposal(text)
                self.assertEqual(ctx.exception.code, code)


class ProposalRefusals(unittest.TestCase):
    def refused(self, value, code, **kw):
        with self.assertRaises(Refused) as ctx:
            proposal_from_value(value, **kw)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_unknown_and_missing_fields(self):
        for key in ("comment", "notes", "Side", "schema_version", "id"):
            v = proposal_value()
            v[key] = "x"
            with self.subTest(key=key):
                self.refused(v, "unknown_field")
        for key in proposal_value():
            v = proposal_value()
            del v[key]
            with self.subTest(missing=key):
                self.refused(v, "missing_field")

    def test_container_types(self):
        for bad in (DictSub(proposal_value()), [proposal_value()], None, "x", 1):
            with self.subTest(type=type(bad).__name__):
                self.refused(bad, "wrong_type")
        v = proposal_value()
        v["citations"] = ListSub(v["citations"])
        self.refused(v, "wrong_type")
        v = proposal_value()
        v["citations"] = tuple(v["citations"])
        self.refused(v, "wrong_type")

    def test_schema(self):
        for bad in ("cockpit.proposal/2", "cockpit.research_case/1", StrSub(PROPOSAL_SCHEMA), 1, None):
            v = proposal_value()
            v["schema"] = bad
            with self.subTest(bad=repr(bad)):
                self.refused(v, "schema_mismatch")

    def test_side(self):
        for bad, code in (("BUY", "invalid_value"), ("short", "invalid_value"), ("hold", "invalid_value"),
                          ("", "invalid_value"), (1, "wrong_type"), (StrSub("buy"), "wrong_type"),
                          (None, "wrong_type")):
            v = proposal_value()
            v["side"] = bad
            with self.subTest(bad=repr(bad)):
                self.refused(v, code)
        v = proposal_value()
        v["side"] = "sell"
        self.assertEqual(proposal_from_value(v).side, "sell")

    def test_horizon(self):
        for good in ("P1D", "P3W", "P12M", "P999D"):
            v = proposal_value()
            v["horizon"] = good
            self.assertEqual(proposal_from_value(v).horizon, good)
        for bad in ("P0D", "P1Y", "1W", "P1W ", "p1w", "P1000D", "PT1H", ""):
            v = proposal_value()
            v["horizon"] = bad
            with self.subTest(bad=bad):
                self.refused(v, "invalid_value")

    def test_rationale(self):
        for bad, code in (("", "invalid_length"), (" \n\t", "invalid_value"), ("x" * 4001, "invalid_length"),
                          ("caf" + chr(0xE9), "text_not_ascii"), ("x" + chr(0xD800), "text_not_ascii"),
                          ("x" + chr(0x202E) + "y", "text_not_ascii"), ("a\x1bb", "text_control_character"),
                          ("a\rb", "text_control_character"), (StrSub("why"), "wrong_type"), (1, "wrong_type")):
            v = proposal_value()
            v["rationale"] = bad
            with self.subTest(bad=repr(bad)[:30]):
                self.refused(v, code)

    def test_citations(self):
        for bad, code in (([], "invalid_length"), (["ev-1", "ev-1"], "duplicate_value"),
                          (["EV-1"], "invalid_value"), ([1], "wrong_type"), ("ev-1", "wrong_type"),
                          (["ev-%d" % i for i in range(33)], "invalid_length")):
            v = proposal_value()
            v["citations"] = bad
            with self.subTest(bad=repr(bad)[:30]):
                self.refused(v, code)
        v = proposal_value()
        v["citations"] = ["ev-1", "ev-9"]
        proposal_from_value(v)  # well formed on its own
        self.refused(v, "citation_unknown", case=case())

    def test_instrument_must_be_the_cases(self):
        v = proposal_value()
        v["instrument"] = "FAKE-002"
        proposal_from_value(v)
        self.refused(v, "instrument_mismatch", case=case())
        for bad, code in (("fake-001", "invalid_value"), (1, "wrong_type"), ({"symbol": "FAKE-001"}, "wrong_type")):
            v = proposal_value()
            v["instrument"] = bad
            with self.subTest(bad=repr(bad)):
                self.refused(v, code)

    def test_case_argument_must_be_a_case(self):
        self.refused(proposal_value(), "wrong_type", case=case().value())


class UntrustedModelText(unittest.TestCase):
    def refused(self, text, code):
        with self.assertRaises(Refused) as ctx:
            parse_proposal(text)
        self.assertEqual(ctx.exception.code, code)

    def test_exactly_one_object_and_nothing_around_it(self):
        good = read("proposal_valid.txt")
        self.refused("Here is my proposal:\n" + good, "not_json")
        self.refused("```json\n" + good + "```", "not_json")
        self.refused(good + good, "not_json")
        self.refused("[" + good + "]", "wrong_type")
        self.refused('"just text"', "wrong_type")
        self.refused("", "not_json")

    def test_duplicate_keys_are_refused_not_last_wins(self):
        good = read("proposal_valid.txt")
        text = good.replace('"side": "buy",', '"side": "buy", "side": "sell",')
        self.assertNotEqual(text, good)
        self.refused(text, "duplicate_key")
        text = good.replace('"conviction_bps": 2500,', '"conviction_bps": 1, "conviction_bps": 2500,')
        self.refused(text, "duplicate_key")

    def test_input_types_size_nesting_and_surrogates(self):
        good = read("proposal_valid.txt")
        self.refused(good.encode("ascii"), "not_text")
        self.refused(StrSub(good), "not_text")
        self.refused(None, "not_text")
        self.refused(good + " " * (64 * 1024), "too_large")
        deep = good.replace('"citations": [', '"citations": ' + "[" * 20000 + "[", 1)
        deep = deep.replace('"ev-2"\n  ]', '"ev-2"\n  ]' + "]" * 20000, 1)
        self.assertGreater(len(deep), len(good) + 30000)
        with self.assertRaises(Refused):
            parse_proposal(deep)  # refused, whether the JSON reader or the schema stops it
        self.refused(good.replace("Made-up rationale", "Made-up " + chr(0xD800)), "text_invalid")
        escaped = good.replace("Made-up rationale", "Made-up " + chr(92) + "ud800")
        self.refused(escaped, "text_not_ascii")


if __name__ == "__main__":
    unittest.main()
