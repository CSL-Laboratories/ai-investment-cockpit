"""Offline checks of previously reviewed excerpts; never cohort or order authority.

Run this file directly with a project interpreter. It writes no files, performs no
network request, and does not require pytest or modify the application test matrix.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE.parents[2] / "app"))

from strategies.merger_semantics import (
    _CASH,
    _EXPECTED_DATE,
    _OUTSIDE_DATE,
    _STOCK_CONSIDERATION,
    SemanticEvidenceUnavailable,
    _assertion_matches,
    _combined_source_text,
    _derive_eligibility,
    _text,
    _unique_match,
)

MANIFEST_SHA256 = "a8626387e2de9d5e037f3277e7914d6161eb0f3f9ad3a114aebe385b31678ef1"
SOURCE_HASHES = {
    "app/tests/strategies/test_merger_semantics.py":
        "a7f1abe304d8b22d151114e6f9ccfd7c405ef7d235a5933228fe17197bd5850c",
    "docs/evidence/w24-bhf-date-saft-audit-2026-09-02.md":
        "63aee2b7071ccca35e4d9666fe6673665674daf812cf743b107d5a5d2a3b9539",
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExistingExcerptChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (HERE / "existing-excerpts.json").read_bytes()
        cls.manifest = json.loads(cls.raw)
        cls.entries = {entry["id"]: entry for entry in cls.manifest["entries"]}

    def test_manifest_bytes_and_authority_scope_are_pinned(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), MANIFEST_SHA256)
        self.assertEqual(
            (HERE / "existing-excerpts.json.sha256").read_text().strip(), MANIFEST_SHA256,
        )
        self.assertEqual(
            self.manifest["base_commit"], "b4a199ff41ed84d78f21602a84d9665c1072ec6a",
        )
        for field in ("raw_sec_html", "complete_primary_document", "independent_holdout",
                      "selection_authority"):
            self.assertIs(self.manifest[field], False)

    def test_each_pinned_text_reproduces_its_exact_historical_source_slice(self):
        self.assertEqual(len(self.entries), 5)
        for entry in self.entries.values():
            with self.subTest(entry=entry["id"]):
                self.assertEqual(entry["source_file_sha256"], SOURCE_HASHES[entry["source_file"]])
                self.assertEqual(sha256(entry["source_slice"]), entry["source_slice_sha256"])
                self.assertEqual(sha256(entry["text"]), entry["text_sha256"])
                if entry["text_transform"] == "python_ast_literal_eval_assignment":
                    node = ast.parse(entry["source_slice"]).body[0]
                    self.assertIsInstance(node, ast.Assign)
                    reconstructed = ast.literal_eval(node.value)
                else:
                    self.assertEqual(
                        entry["text_transform"],
                        "remove_quote_prefix_bold_markup_and_trailing_newline",
                    )
                    reconstructed = entry["source_slice"].removeprefix("> ").replace(
                        "**", "",
                    ).rstrip("\r\n")
                self.assertEqual(reconstructed, entry["text"])

    def test_existing_bhf_and_saft_cash_amounts_are_preserved(self):
        for identifier, expected in (("bhf_10q_cash_head", "70.00"),
                                     ("saft_10q_cash_head", "105.00")):
            with self.subTest(entry=identifier):
                text = _text(self.entries[identifier]["text"].encode("utf-8"))
                self.assertEqual(
                    {match.group(1) for match in _assertion_matches(_CASH, text)}, {expected},
                )

    def test_no_excerpt_supplies_either_pinned_current_deal_date(self):
        for entry in self.entries.values():
            for pattern, field in ((_EXPECTED_DATE, "expected date"),
                                   (_OUTSIDE_DATE, "outside date")):
                with (
                    self.subTest(entry=entry["id"], field=field),
                    self.assertRaises(SemanticEvidenceUnavailable),
                ):
                    _unique_match(pattern, _text(entry["text"].encode()), field=field)

    def test_bhf_cash_plus_its_real_conditional_dates_still_refuses_date_authority(self):
        text = _combined_source_text([
            _text(self.entries[identifier]["text"].encode())
            for identifier in ("bhf_10q_cash_head", "bhf_conditional_year_only")
        ])
        self.assertEqual(
            {match.group(1) for match in _assertion_matches(_CASH, text)}, {"70.00"},
        )
        for pattern in (_EXPECTED_DATE, _OUTSIDE_DATE):
            with self.assertRaises(SemanticEvidenceUnavailable):
                _unique_match(pattern, text, field="date")

    def test_excerpt_only_inputs_cannot_supply_complete_strategy_eligibility(self):
        for entry in self.entries.values():
            with (
                self.subTest(entry=entry["id"]),
                self.assertRaises(SemanticEvidenceUnavailable),
            ):
                _derive_eligibility(_text(entry["text"].encode()), amended=False)

    def test_saft_note_retains_the_known_unfixed_repurchase_false_positive(self):
        # A PASS here reproduces the open defect; it is expressly not a repair claim.
        text = _text(self.entries["saft_repurchase_count"]["text"].encode())
        self.assertIsNotNone(_STOCK_CONSIDERATION.search(text))


if __name__ == "__main__":
    unittest.main()
