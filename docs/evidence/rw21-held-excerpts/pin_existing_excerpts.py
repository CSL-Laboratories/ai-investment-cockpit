"""Pin already-reviewed repository excerpts without SEC access or app changes."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

BASE_COMMIT = "b4a199ff41ed84d78f21602a84d9665c1072ec6a"
REPOSITORY = Path(__file__).resolve().parents[3]
DESTINATION = Path(__file__).resolve().parent / "existing-excerpts.json"
TEST_SOURCE = "app/tests/strategies/test_merger_semantics.py"
NOTE_SOURCE = "docs/evidence/w24-bhf-date-saft-audit-2026-09-02.md"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def historical(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{path}"], cwd=REPOSITORY,
        check=True, capture_output=True,
    ).stdout


def main() -> None:
    if DESTINATION.exists():
        raise RuntimeError("pin already exists; do not overwrite historical evidence")
    test_bytes = historical(TEST_SOURCE)
    note_bytes = historical(NOTE_SOURCE)
    source_text = test_bytes.decode("utf-8")
    source_lines = source_text.splitlines(keepends=True)
    assignments = {
        node.targets[0].id: node
        for node in ast.parse(source_text).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    entries = []
    for symbol, accession, name, kind, expected_cash in (
        ("BHF", "0001685040-26-000028", "_BHF_10Q_CASH_HEAD", "cash_clause", "70.00"),
        ("SAFT", "0001172052-26-000023", "_SAFT_10Q_CASH_HEAD", "cash_clause", "105.00"),
        ("SAFT", "0001172052-26-000023", "_SAFT_10Q_AGREEMENT_HEAD", "agreement_clause", None),
    ):
        node = assignments[name]
        source_slice = "".join(source_lines[node.lineno - 1:node.end_lineno])
        text = ast.literal_eval(node.value)
        entries.append({
            "id": name.lower().lstrip("_"), "symbol": symbol, "accession": accession,
            "kind": kind, "expected_cash": expected_cash,
            "source_file": TEST_SOURCE, "source_file_sha256": digest(test_bytes),
            "source_start_line": node.lineno, "source_end_line": node.end_lineno,
            "source_slice": source_slice,
            "source_slice_sha256": digest(source_slice.encode("utf-8")),
            "text_transform": "python_ast_literal_eval_assignment",
            "text": text, "text_sha256": digest(text.encode("utf-8")),
        })
    note_lines = note_bytes.decode("utf-8").splitlines(keepends=True)
    for identifier, symbol, accession, needle, kind in (
        ("bhf_conditional_year_only", "BHF", "0001685040-26-000028",
         "> … if the Merger has not closed by", "conditional_date_quote"),
        ("saft_repurchase_count", "SAFT", "0001172052-26-000023",
         "> … as of june 30, 2026", "known_stock_false_positive_quote"),
    ):
        found = [(index, line) for index, line in enumerate(note_lines, 1)
                 if line.startswith(needle)]
        if len(found) != 1:
            raise RuntimeError(f"historical source quote is not unique: {identifier}")
        line_number, source_slice = found[0]
        text = source_slice.removeprefix("> ").replace("**", "").rstrip("\r\n")
        entries.append({
            "id": identifier, "symbol": symbol, "accession": accession,
            "kind": kind, "expected_cash": None,
            "source_file": NOTE_SOURCE, "source_file_sha256": digest(note_bytes),
            "source_start_line": line_number, "source_end_line": line_number,
            "source_slice": source_slice,
            "source_slice_sha256": digest(source_slice.encode("utf-8")),
            "text_transform": "remove_quote_prefix_bold_markup_and_trailing_newline",
            "text": text, "text_sha256": digest(text.encode("utf-8")),
        })
    manifest = {
        "schema": "rw21-existing-repository-excerpts.v1",
        "base_commit": BASE_COMMIT,
        "capture_kind": "existing_repository_review_material",
        "raw_sec_html": False,
        "complete_primary_document": False,
        "independent_holdout": False,
        "selection_authority": False,
        "sources": {
            "BHF": "https://www.sec.gov/Archives/edgar/data/1685040/000168504026000028/bhf-20260630.htm",
            "SAFT": "https://www.sec.gov/Archives/edgar/data/1172052/000117205226000023/saft-20260630x10q.htm",
        },
        "excluded": {
            "CRNX": "Existing test is described as minimal frozen grammar, not a verbatim primary excerpt",
            "FSEA_SLP_CCO": "No exact primary bytes are present in the reviewed repository",
        },
        "entries": entries,
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with DESTINATION.open("xb") as output:
        output.write(encoded)
    DESTINATION.with_suffix(".json.sha256").write_text(digest(encoded) + "\n", encoding="ascii")
    print(f"Pinned {len(entries)} historical excerpts; no SEC requests or app mutations.")
    print(f"Manifest SHA-256: {digest(encoded)}")


if __name__ == "__main__":
    main()
