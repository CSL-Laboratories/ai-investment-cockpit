"""Regression tests for W-23 operator-tool reporting."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "provision_merger_edge_tool", ROOT / "tools" / "provision_merger_edge.py"
)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class _RawStore:
    def stored_hash(self, raw_ref):
        assert raw_ref == "accession-1"
        return "f" * 64


def test_reported_content_hash_supports_evidence_and_filing_rows():
    assert TOOL._reported_content_hash(SimpleNamespace(content_hash="a" * 64)) == "a" * 64
    assert (
        TOOL._reported_content_hash(
            SimpleNamespace(raw_ref="accession-1"), raw_store=_RawStore()
        )
        == "f" * 64
    )
