#!/usr/bin/env python3
"""Schema-invalid O8 packets raise O8Error, not TypeError.

    python -X utf8 -B tools/test_check_o8_phase_a_schema_guard.py
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_o8_phase_a.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_o8_phase_a", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CHECKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SchemaGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()
        cls.packet = cls.mod._load_json(cls.mod.PACKET_PATH)

    def _reject(self, document: dict, field: str) -> None:
        with self.assertRaises(self.mod.O8Error) as caught:
            self.mod.validate_packet(document)
        reasons = caught.exception.reasons
        self.assertTrue(reasons, "schema rejection produced no reasons")
        self.assertTrue(
            any(reason.startswith("schema ") and field in reason for reason in reasons),
            reasons,
        )

    def test_frozen_packet_is_accepted(self) -> None:
        self.mod.validate_packet(copy.deepcopy(self.packet))

    def test_packet_negative_still_reports_its_designed_reason(self) -> None:
        document = self.mod._load_json(self.mod.NEGATIVE_DIR / "replay-from-duplicate.json")
        with self.assertRaises(self.mod.O8Error) as caught:
            self.mod.validate_packet(document)
        self.assertTrue(
            any("replay sources must be distinct" in reason for reason in caught.exception.reasons),
            caught.exception.reasons,
        )

    def test_non_array_parallel_search_ids(self) -> None:
        document = copy.deepcopy(self.packet)
        document["research"]["parallel_search_ids"] = 1
        self._reject(document, "parallel_search_ids")

    def test_non_array_cited_urls(self) -> None:
        document = copy.deepcopy(self.packet)
        document["research"]["cited_urls"] = 1
        self._reject(document, "cited_urls")

    def test_non_array_tier1_cases(self) -> None:
        document = copy.deepcopy(self.packet)
        document["protocol"]["tier1"]["cases"] = 1
        self._reject(document, "cases")

    def test_non_array_migration_seams(self) -> None:
        document = copy.deepcopy(self.packet)
        document["migration"]["seams"] = 1
        self._reject(document, "seams")

    def test_non_array_disposition_paths(self) -> None:
        document = copy.deepcopy(self.packet)
        document["disposition"][0]["paths"] = 1
        self._reject(document, "paths")

    def test_cli_rejects_schema_invalid_packet(self) -> None:
        document = copy.deepcopy(self.packet)
        document["research"]["parallel_search_ids"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = self.mod.main(["--manifest", str(path), "--kind", "packet"])
        text = stderr.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("O8-phase-A: rejected:", text)
        self.assertIn("parallel_search_ids", text)
        self.assertNotIn("TypeError", text)


if __name__ == "__main__":
    unittest.main()
