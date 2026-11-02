"""Read-only cross-check of the committed matrix; never rewrites its attestations."""
from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "docs/evidence/rw20-local-matrix-2026-09-06"
CELLS = ("w311", "w312", "l311", "l312")


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_functions(source):
    return {
        node.name: ast.dump(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def main():
    output = Path(sys.argv[1])
    summary = json.loads((BASE / "matrix-summary.json").read_text())
    manifest = json.loads((BASE / "w311/source-manifest.json").read_text())
    errors = []
    for path, digest in summary["artifacts_sha256"].items():
        if sha((BASE / path).read_bytes()) != digest:
            errors.append("artifact:" + path)
    for path, digest in manifest.items():
        if sha(git("show", summary["full_app_commit"] + ":" + path)) != digest:
            errors.append("source:" + path)
    nodes, cells = {}, {}
    for name in CELLS:
        attestation = json.loads((BASE / name / "attestation.json").read_text())
        if json.loads((BASE / name / "source-manifest.json").read_text()) != manifest:
            errors.append("manifest:" + name)
        cases = ET.parse(BASE / name / "pytest.xml").getroot().findall(".//testcase")
        nodes[name] = {
            case.get("classname") + "." + case.get("name"): (
                "skipped" if case.find("skipped") is not None
                else "failed" if case.find("failure") is not None or case.find("error") is not None
                else "passed"
            ) for case in cases
        }
        if len(nodes[name]) != len(cases):
            errors.append("duplicate-node:" + name)
        cells[name] = {
            "python": attestation["python"], "platform": attestation["platform"],
            "counts": dict(Counter(nodes[name].values())),
            "skips": {
                case.get("classname") + "." + case.get("name"): case.find("skipped").get("message")
                for case in cases if case.find("skipped") is not None
            },
        }
    all_nodes = set.union(*(set(cases) for cases in nodes.values()))
    differences = {
        node: {cell: cases.get(node, "absent") for cell, cases in nodes.items()}
        for node in sorted(all_nodes)
        if len({cases.get(node, "absent") for cases in nodes.values()}) > 1
    }
    changes = {}
    for merge in ("efc626f", "61ca0af", "c5ef0ad"):
        paths = git("diff", "--name-only", merge + "^1", merge, "--", "app/tests").decode().splitlines()
        records = []
        for path in paths:
            if not path.endswith(".py"):
                continue
            old = subprocess.run(
                ["git", "show", merge + "^1:" + path], cwd=ROOT,
                capture_output=True, check=False,
            )
            previous = test_functions(old.stdout if old.returncode == 0 else b"")
            current = test_functions(git("show", merge + ":" + path))
            for name, body in current.items():
                if previous.get(name) == body:
                    continue
                prefix = path.removeprefix("app/").removesuffix(".py").replace("/", ".") + "." + name
                counts = {
                    cell: dict(Counter(
                        status for node, status in cases.items()
                        if node == prefix or node.startswith(prefix + "[")
                    )) for cell, cases in nodes.items()
                }
                records.append({
                    "test": prefix, "change": "modified" if name in previous else "new",
                    "recorded_cell_results": counts,
                })
                if any(not count or set(count) != {"passed"} for count in counts.values()):
                    errors.append("changed-test-not-passing:" + prefix)
        changes[merge] = records
    trees = {
        commit: git("rev-parse", commit + ":app").decode().strip()
        for commit in ("efc626f", "61ca0af", "69a1c9c", "c5ef0ad", "HEAD")
    }
    if trees["69a1c9c"] != trees["c5ef0ad"] or trees["c5ef0ad"] != trees["HEAD"]:
        errors.append("app-tree-drift")
    record = {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "claim": "integrity and coverage audit of historical local results; no new execution or authority",
        "matrix_summary_sha256": sha((BASE / "matrix-summary.json").read_bytes()),
        "artifact_hashes_checked": len(summary["artifacts_sha256"]),
        "source_manifest_files_checked_against_git": len(manifest),
        "mismatches": errors, "app_trees": trees, "cells": cells,
        "node_status_differences": differences, "merge_test_lineage": changes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps({
        "artifact_hashes": len(summary["artifacts_sha256"]), "source_files": len(manifest),
        "mismatches": errors, "cell_counts": {k: v["counts"] for k, v in cells.items()},
    }, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
