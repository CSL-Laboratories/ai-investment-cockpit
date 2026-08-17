"""Regression tests for W-23 operator-tool reporting."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_selection_subcommand_requires_outcome_free_manifest_inputs():
    args = TOOL._parser().parse_args([
        "--db-url", "sqlite:///cockpit.db",
        "--raw-root", "data/raw",
        "selection",
        "--candidates", "candidates.json",
        "--training-cutoff", "2026-01-01",
    ])

    assert args.action == "selection"
    assert args.candidates == "candidates.json"
    assert args.training_cutoff == "2026-01-01"


def test_selection_preflight_strictly_polls_the_complete_unique_cik_universe(
    tmp_path, monkeypatch
):
    candidates = [{"target_cik": f"{index:010d}"} for index in range(1, 31)]
    state = SimpleNamespace(
        session_factory=object(),
        audit=object(),
        clock=object(),
        process_epoch="epoch-1",
    )
    settings = object()
    fetcher = object()
    calls = []

    monkeypatch.setattr(TOOL, "build_sec_fetcher", lambda configured: fetcher)
    monkeypatch.setattr(TOOL, "RawStore", lambda path: ("raw-store", path))

    def poll(session_factory, **kwargs):
        calls.append((session_factory, kwargs))
        return SimpleNamespace(ok=True, ciks_polled=30)

    monkeypatch.setattr(TOOL, "run_edgar_pipeline", poll)

    TOOL._poll_selection_candidates(state, settings, tmp_path, candidates)

    assert len(calls) == 1
    session_factory, kwargs = calls[0]
    expected = [f"{index:010d}" for index in range(1, 31)]
    assert session_factory is state.session_factory
    assert kwargs["ciks"] == expected
    assert kwargs["strict_completeness_ciks"] == expected
    assert kwargs["fetcher"] is fetcher
    assert kwargs["audit_log"] is state.audit
    assert kwargs["process_epoch"] == "epoch-1"


def test_selection_preflight_rejects_duplicate_cik_before_poll(tmp_path, monkeypatch):
    candidates = [{"target_cik": f"{index:010d}"} for index in range(1, 30)]
    candidates.append({"target_cik": "0000000001"})
    monkeypatch.setattr(
        TOOL,
        "run_edgar_pipeline",
        lambda *args, **kwargs: pytest.fail("invalid selection must not reach SEC polling"),
    )

    with pytest.raises(ValueError, match="unique"):
        TOOL._poll_selection_candidates(object(), object(), tmp_path, candidates)
