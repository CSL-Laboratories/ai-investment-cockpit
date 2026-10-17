"""CSL-221 exact-candidate qualification evidence.

The validator accepts a bundle only when every required provider-free cell is
present, bound to the expected commit and tree, and backed by a JUnit file
with no failures, errors, or skips. Skipped, unknown, and excluded cells are
never accepted. This module does not make a release decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from xml.etree import ElementTree

REPOSITORY = "CSL-Laboratories/ai-investment-cockpit"
WORKFLOW = "ci"
SCHEMA_VERSION = "csl221.qualification-evidence.v1"
MATRIX_VERSION = "csl221.qualification-matrix.v1"
JOB_KEY = "qualification"
EXECUTED_TIER = "provider-free"
PLATFORMS = ("linux", "windows", "macos")
PYTHONS = ("3.11", "3.12")
TIERS = ("provider-free", "replay", "simulation", "paper-demo")
NON_PASS_STATUSES = frozenset(
    {"skipped", "unknown", "excluded", "not_run", "missing", "failed"}
)
BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "repository",
        "workflow",
        "commit_sha",
        "tree_sha",
        "run_id",
        "run_attempt",
        "run_url",
        "cells",
    }
)
CELL_KEYS = frozenset(
    {
        "cell_id",
        "platform",
        "python",
        "tier",
        "status",
        "commit_sha",
        "tree_sha",
        "run_id",
        "run_attempt",
        "job_id",
        "job_key",
        "job_url",
        "runner_image",
        "python_version",
        "python_implementation",
        "dependency_digest",
        "junit",
        "artifacts",
        "artifact_dir",
    }
)
JUNIT_KEYS = frozenset(
    {
        "path",
        "sha256",
        "tests",
        "passed",
        "failures",
        "errors",
        "skipped",
        "inventory",
    }
)
INVENTORY_KEYS = frozenset({"classname", "name", "status"})
ARTIFACT_NAMES = ("junit.xml", "dependencies.txt", "runner.json")
RUNNER_KEYS = frozenset(
    {
        "image",
        "runner_os",
        "runner_arch",
        "runner_name",
        "image_os",
        "image_version",
    }
)
WRONG_COMMIT = "wrong-commit"
WRONG_TREE = "wrong-tree"
SKIPPED_CELL = "skipped-cell"
MISSING_JUNIT = "missing-junit"
MISSING_CELL = "missing-cell"
UNKNOWN_STATUS = "unknown-status"
EXCLUDED_CELL = "excluded-cell"
JUNIT_SKIPPED = "junit-skipped"
ARTIFACT_DIGEST = "artifact-digest"
DEPENDENCY_DIGEST = "dependency-digest"
UNKNOWN_CELL = "unknown-cell"
NOT_PASS = "not-pass"
STATUS_CODES = {
    "skipped": SKIPPED_CELL,
    "unknown": UNKNOWN_STATUS,
    "excluded": EXCLUDED_CELL,
    "missing": MISSING_CELL,
}


class QualificationError(Exception):
    """The evidence contract file itself is unusable."""


class JunitSuiteError(QualificationError):
    """A JUnit suite summary does not match its testcases."""


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonicalize_dependencies(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return "\n".join(sorted(lines)) + "\n"


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    )


def _digits(value: object) -> bool:
    return isinstance(value, str) and value.isdigit() and value != ""


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 7 + 64
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def err(code: str, message: str) -> str:
    return f"{code}: {message}"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def matrix_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parents[1]
    return base / "docs" / "evidence" / "csl221" / "qualification-matrix.json"


def load_matrix(path: Path) -> dict:
    document = load_json(path)
    if not isinstance(document, dict):
        raise QualificationError("matrix is not an object")
    if document.get("schema_version") != MATRIX_VERSION:
        raise QualificationError("matrix schema_version")
    if document.get("issue") != "CSL-221":
        raise QualificationError("matrix issue")
    if document.get("does_not_replace") != "DEC-09":
        raise QualificationError("matrix must not replace DEC-09")
    if document.get("release_decision") != "not_made":
        raise QualificationError("matrix release decision")
    if document.get("executed_tier") != EXECUTED_TIER:
        raise QualificationError("matrix executed tier")
    if document.get("pythons") != list(PYTHONS):
        raise QualificationError("matrix pythons")
    if document.get("tiers") != list(TIERS):
        raise QualificationError("matrix tiers")
    platforms = document.get("platforms")
    if not isinstance(platforms, dict) or tuple(platforms) != PLATFORMS:
        raise QualificationError("matrix platforms")
    for name, spec in platforms.items():
        if not isinstance(spec, dict):
            raise QualificationError(f"platform {name}")
        for key in ("runner_image", "runner_os", "runner_arch"):
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise QualificationError(f"platform {name} {key}")
        if not spec["runner_image"].startswith("blacksmith-"):
            raise QualificationError(f"platform {name} runner is not Blacksmith")
    cells = document.get("cells")
    if not isinstance(cells, list):
        raise QualificationError("matrix cells")
    expected = {
        f"{host}/{python}/{tier}"
        for host in PLATFORMS
        for python in PYTHONS
        for tier in TIERS
    }
    seen: set[str] = set()
    for cell in cells:
        if not isinstance(cell, dict):
            raise QualificationError("matrix cell")
        cell_id = cell.get("cell_id")
        if not isinstance(cell_id, str) or cell_id in seen:
            raise QualificationError("matrix cell id")
        seen.add(cell_id)
        host = cell.get("platform")
        python = cell.get("python")
        tier = cell.get("tier")
        if cell_id != f"{host}/{python}/{tier}":
            raise QualificationError(f"cell id {cell_id}")
        spec = platforms[host]
        if cell.get("runner_image") != spec["runner_image"]:
            raise QualificationError(f"{cell_id} runner")
        if cell.get("runner_os") != spec["runner_os"]:
            raise QualificationError(f"{cell_id} runner_os")
        if cell.get("runner_arch") != spec["runner_arch"]:
            raise QualificationError(f"{cell_id} runner_arch")
        required = tier == EXECUTED_TIER
        if cell.get("requirement") != ("required" if required else "excluded"):
            raise QualificationError(f"{cell_id} requirement")
        if cell.get("pass_allowed") is not required:
            raise QualificationError(f"{cell_id} pass_allowed")
        owner = "CSL-221 qualification evidence" if required else "Lucas"
        if cell.get("owner") != owner:
            raise QualificationError(f"{cell_id} owner")
        evidence = cell.get("evidence_requirement")
        if not isinstance(evidence, str) or not evidence.strip():
            raise QualificationError(f"{cell_id} evidence")
        if cell.get("recorded_status") != "not_run":
            raise QualificationError(f"{cell_id} recorded_status")
    if seen != expected:
        raise QualificationError("matrix is not the platform x python x tier product")
    historical = document.get("historical_records")
    if not isinstance(historical, list) or not historical:
        raise QualificationError("historical records")
    for record in historical:
        if not isinstance(record, dict):
            raise QualificationError("historical record")
        if record.get("pass_allowed") is not False:
            raise QualificationError("historical pass_allowed")
        if record.get("replacement_status") != "historical":
            raise QualificationError("historical status")
        if not _sha(record.get("commit_sha")) or not _sha(record.get("tree_sha")):
            raise QualificationError("historical sha")
        if not isinstance(record.get("source"), str) or not record["source"]:
            raise QualificationError("historical source")
    return document


def required_cells(matrix: dict) -> list[dict]:
    return [cell for cell in matrix["cells"] if cell["requirement"] == "required"]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _case_status(element: ElementTree.Element) -> str:
    status = "passed"
    for child in element:
        name = _local(child.tag)
        if name == "error":
            return "error"
        if name == "failure" and status != "error":
            status = "failure"
        elif name == "skipped" and status == "passed":
            status = "skipped"
    return status


def _require_suite_counts(element: ElementTree.Element) -> None:
    declared = {}
    for key in ("tests", "failures", "errors", "skipped"):
        if key not in element.attrib:
            continue
        try:
            declared[key] = int(element.attrib[key])
        except ValueError as exc:
            raise JunitSuiteError(f"junit suite {key} is not an integer") from exc
    if not declared:
        return
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for child in element.iter():
        if child is element or _local(child.tag) != "testcase":
            continue
        counts["tests"] += 1
        status = _case_status(child)
        if status == "failure":
            counts["failures"] += 1
        elif status == "error":
            counts["errors"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
    for key, value in declared.items():
        if counts[key] != value:
            raise JunitSuiteError(
                f"junit suite {key}={value} does not match {counts[key]} testcases"
            )


def parse_junit(data: bytes) -> dict:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise QualificationError(f"junit xml: {exc}") from exc
    for element in root.iter():
        if _local(element.tag) in {"testsuite", "testsuites"}:
            _require_suite_counts(element)
    cases = []
    for element in root.iter():
        if _local(element.tag) != "testcase":
            continue
        cases.append(
            {
                "classname": element.attrib.get("classname", ""),
                "name": element.attrib.get("name", ""),
                "status": _case_status(element),
            }
        )
    cases.sort(key=lambda item: (item["classname"], item["name"]))
    counts = {"passed": 0, "failure": 0, "error": 0, "skipped": 0}
    for case in cases:
        counts[case["status"]] += 1
    return {
        "path": "junit.xml",
        "sha256": digest(data),
        "tests": len(cases),
        "passed": counts["passed"],
        "failures": counts["failure"],
        "errors": counts["error"],
        "skipped": counts["skipped"],
        "inventory": cases,
    }


def _exact_keys(value: dict, allowed: frozenset[str], label: str, errors: list[str]) -> None:
    extra = set(value) - allowed
    missing = allowed - set(value)
    if extra or missing:
        errors.append(
            err(NOT_PASS, f"{label} keys extra={sorted(extra)} missing={sorted(missing)}")
        )


def _cell_dir(root: Path, cell: dict) -> Path:
    artifact_dir = cell.get("artifact_dir")
    if not isinstance(artifact_dir, str) or not artifact_dir or "/" in artifact_dir or "\\" in artifact_dir:
        return root / "__missing__"
    return root / artifact_dir


def validate_bundle(
    bundle: object,
    *,
    matrix: dict,
    expected_commit: str,
    expected_tree: str,
    artifact_root: Path,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(bundle, dict):
        return [err(NOT_PASS, "bundle is not an object")]
    _exact_keys(bundle, BUNDLE_KEYS, "bundle", errors)
    if bundle.get("schema_version") != SCHEMA_VERSION:
        errors.append(err(NOT_PASS, "schema_version"))
    if bundle.get("repository") != REPOSITORY:
        errors.append(err(NOT_PASS, "repository"))
    if bundle.get("workflow") != WORKFLOW:
        errors.append(err(NOT_PASS, "workflow"))
    if bundle.get("commit_sha") != expected_commit or not _sha(expected_commit):
        errors.append(err(WRONG_COMMIT, "bundle commit does not match the candidate"))
    if bundle.get("tree_sha") != expected_tree or not _sha(expected_tree):
        errors.append(err(WRONG_TREE, "bundle tree does not match the candidate"))
    run_id = bundle.get("run_id")
    run_attempt = bundle.get("run_attempt")
    if not _digits(run_id) or not _digits(run_attempt):
        errors.append(err(NOT_PASS, "run id"))
    expected_url = f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"
    if bundle.get("run_url") != expected_url:
        errors.append(err(NOT_PASS, "run_url"))
    cells = bundle.get("cells")
    if not isinstance(cells, list):
        return errors + [err(NOT_PASS, "cells")]
    by_id: dict[str, dict] = {}
    known = {cell["cell_id"]: cell for cell in matrix["cells"]}
    for cell in cells:
        if not isinstance(cell, dict) or not isinstance(cell.get("cell_id"), str):
            errors.append(err(NOT_PASS, "cell"))
            continue
        cell_id = cell["cell_id"]
        if cell_id in by_id:
            errors.append(err(NOT_PASS, f"duplicate cell {cell_id}"))
            continue
        by_id[cell_id] = cell
        if cell_id not in known:
            errors.append(err(UNKNOWN_CELL, cell_id))
    for spec in required_cells(matrix):
        cell_id = spec["cell_id"]
        cell = by_id.get(cell_id)
        if cell is None:
            errors.append(err(MISSING_CELL, cell_id))
            continue
        errors.extend(
            _validate_required_cell(
                cell,
                spec,
                expected_commit=expected_commit,
                expected_tree=expected_tree,
                run_id=run_id if isinstance(run_id, str) else "",
                run_attempt=run_attempt if isinstance(run_attempt, str) else "",
                artifact_root=artifact_root,
            )
        )
    for cell_id, cell in by_id.items():
        spec = known.get(cell_id)
        if spec is None or spec["pass_allowed"]:
            continue
        if cell.get("status") == "pass":
            errors.append(err(EXCLUDED_CELL, f"{cell_id} cannot pass"))
        elif cell.get("status") not in NON_PASS_STATUSES:
            errors.append(err(NOT_PASS, f"{cell_id} status"))
    return errors


def _validate_required_cell(
    cell: dict,
    spec: dict,
    *,
    expected_commit: str,
    expected_tree: str,
    run_id: str,
    run_attempt: str,
    artifact_root: Path,
) -> list[str]:
    errors: list[str] = []
    cell_id = spec["cell_id"]
    _exact_keys(cell, CELL_KEYS, cell_id, errors)
    status = cell.get("status")
    if status in STATUS_CODES:
        errors.append(err(STATUS_CODES[status], cell_id))
        return errors
    if status != "pass":
        errors.append(err(NOT_PASS, f"{cell_id} status {status!r}"))
        return errors
    for key, expected in (
        ("platform", spec["platform"]),
        ("python", spec["python"]),
        ("tier", spec["tier"]),
        ("runner_image", spec["runner_image"]),
        ("commit_sha", expected_commit),
        ("tree_sha", expected_tree),
        ("run_id", run_id),
        ("run_attempt", run_attempt),
        ("job_key", JOB_KEY),
    ):
        if cell.get(key) != expected:
            code = NOT_PASS
            if key == "commit_sha":
                code = WRONG_COMMIT
            elif key == "tree_sha":
                code = WRONG_TREE
            errors.append(err(code, f"{cell_id} {key}"))
    if not _digits(cell.get("job_id")):
        errors.append(err(NOT_PASS, f"{cell_id} job_id"))
    job_url = (
        f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/job/{cell.get('job_id')}"
    )
    if cell.get("job_url") != job_url:
        errors.append(err(NOT_PASS, f"{cell_id} job_url"))
    python_version = cell.get("python_version")
    if not isinstance(python_version, str) or not python_version.startswith(spec["python"] + "."):
        errors.append(err(NOT_PASS, f"{cell_id} python_version"))
    if cell.get("python_implementation") != "CPython":
        errors.append(err(NOT_PASS, f"{cell_id} python_implementation"))
    directory = _cell_dir(artifact_root, cell)
    artifacts = cell.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARTIFACT_NAMES):
        errors.append(err(ARTIFACT_DIGEST, f"{cell_id} artifact set"))
        artifacts = {}
    junit = cell.get("junit")
    if not isinstance(junit, dict):
        errors.append(err(MISSING_JUNIT, cell_id))
        return errors
    _exact_keys(junit, JUNIT_KEYS, f"{cell_id} junit", errors)
    if junit.get("path") != "junit.xml":
        errors.append(err(MISSING_JUNIT, f"{cell_id} path"))
    junit_path = directory / "junit.xml"
    if not junit_path.is_file():
        errors.append(err(MISSING_JUNIT, cell_id))
        return errors
    junit_bytes = junit_path.read_bytes()
    try:
        parsed = parse_junit(junit_bytes)
    except JunitSuiteError as exc:
        errors.append(err(NOT_PASS, f"{cell_id} {exc}"))
        return errors
    except QualificationError as exc:
        errors.append(err(MISSING_JUNIT, f"{cell_id} {exc}"))
        return errors
    if junit.get("sha256") != parsed["sha256"] or artifacts.get("junit.xml") != parsed["sha256"]:
        errors.append(err(ARTIFACT_DIGEST, f"{cell_id} junit"))
    for count in ("tests", "passed", "failures", "errors", "skipped"):
        if junit.get(count) != parsed[count]:
            errors.append(err(NOT_PASS, f"{cell_id} junit {count}"))
    inventory = junit.get("inventory")
    if inventory != parsed["inventory"]:
        errors.append(err(NOT_PASS, f"{cell_id} inventory"))
    elif not parsed["inventory"]:
        errors.append(err(MISSING_JUNIT, f"{cell_id} empty inventory"))
    else:
        for case in parsed["inventory"]:
            if set(case) != INVENTORY_KEYS:
                errors.append(err(NOT_PASS, f"{cell_id} inventory keys"))
                break
            if case["status"] == "skipped":
                errors.append(err(JUNIT_SKIPPED, f"{cell_id} {case['classname']}::{case['name']}"))
            elif case["status"] != "passed":
                errors.append(err(NOT_PASS, f"{cell_id} {case['status']}"))
    if parsed["skipped"]:
        errors.append(err(JUNIT_SKIPPED, cell_id))
    if parsed["failures"] or parsed["errors"]:
        errors.append(err(NOT_PASS, f"{cell_id} junit failures"))
    dependency_path = directory / "dependencies.txt"
    if not dependency_path.is_file():
        errors.append(err(DEPENDENCY_DIGEST, f"{cell_id} missing dependencies"))
    else:
        body = dependency_path.read_bytes()
        if body.decode("utf-8") != canonicalize_dependencies(body.decode("utf-8")):
            errors.append(err(DEPENDENCY_DIGEST, f"{cell_id} dependencies not canonical"))
        expected_digest = digest(body)
        if cell.get("dependency_digest") != expected_digest:
            errors.append(err(DEPENDENCY_DIGEST, cell_id))
        if artifacts.get("dependencies.txt") != expected_digest:
            errors.append(err(ARTIFACT_DIGEST, f"{cell_id} dependencies"))
    runner_path = directory / "runner.json"
    if not runner_path.is_file():
        errors.append(err(ARTIFACT_DIGEST, f"{cell_id} missing runner"))
    else:
        raw = runner_path.read_bytes()
        if artifacts.get("runner.json") != digest(raw):
            errors.append(err(ARTIFACT_DIGEST, f"{cell_id} runner"))
        try:
            runner = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            errors.append(err(NOT_PASS, f"{cell_id} runner json"))
            return errors
        if not isinstance(runner, dict) or set(runner) != RUNNER_KEYS:
            errors.append(err(NOT_PASS, f"{cell_id} runner keys"))
            return errors
        if raw != canonical_json(runner):
            errors.append(err(ARTIFACT_DIGEST, f"{cell_id} runner canonical"))
        if runner.get("image") != spec["runner_image"] or cell.get("runner_image") != spec["runner_image"]:
            errors.append(err(NOT_PASS, f"{cell_id} runner image"))
        if runner.get("runner_os") != spec["runner_os"]:
            errors.append(err(NOT_PASS, f"{cell_id} runner_os"))
        if runner.get("runner_arch") != spec["runner_arch"]:
            errors.append(err(NOT_PASS, f"{cell_id} runner_arch"))
        if not isinstance(runner.get("runner_name"), str) or not runner["runner_name"]:
            errors.append(err(NOT_PASS, f"{cell_id} runner_name"))
        for key in ("image_os", "image_version"):
            if not isinstance(runner.get(key), str):
                errors.append(err(NOT_PASS, f"{cell_id} {key}"))
    return errors


def build_cell_files(
    *,
    spec: dict,
    commit_sha: str,
    tree_sha: str,
    run_id: str,
    run_attempt: str,
    job_id: str,
    python_version: str,
    junit_bytes: bytes,
    dependency_text: str,
    runner_name: str,
    image_os: str = "",
    image_version: str = "",
) -> tuple[dict, dict[str, bytes]]:
    parsed = parse_junit(junit_bytes)
    if parsed["tests"] == 0 or parsed["failures"] or parsed["errors"] or parsed["skipped"]:
        raise QualificationError("junit is not a clean provider-free inventory")
    if not python_version.startswith(spec["python"] + "."):
        raise QualificationError("python version does not match the cell")
    dependencies = canonicalize_dependencies(dependency_text).encode("utf-8")
    runner = {
        "image": spec["runner_image"],
        "image_os": image_os,
        "image_version": image_version,
        "runner_arch": spec["runner_arch"],
        "runner_name": runner_name,
        "runner_os": spec["runner_os"],
    }
    runner_bytes = canonical_json(runner)
    artifact_dir = (
        f"qualification-provider-free-{spec['platform']}-{spec['python']}-attempt-{run_attempt}"
    )
    files = {
        "junit.xml": junit_bytes,
        "dependencies.txt": dependencies,
        "runner.json": runner_bytes,
    }
    cell = {
        "artifact_dir": artifact_dir,
        "artifacts": {name: digest(files[name]) for name in ARTIFACT_NAMES},
        "cell_id": spec["cell_id"],
        "commit_sha": commit_sha,
        "dependency_digest": digest(dependencies),
        "job_id": job_id,
        "job_key": JOB_KEY,
        "job_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/job/{job_id}",
        "junit": parsed,
        "platform": spec["platform"],
        "python": spec["python"],
        "python_implementation": "CPython",
        "python_version": python_version,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "runner_image": spec["runner_image"],
        "status": "pass",
        "tier": spec["tier"],
        "tree_sha": tree_sha,
    }
    return cell, files


def write_cell(directory: Path, cell: dict, files: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (directory / name).write_bytes(body)
    (directory / "cell.json").write_bytes(canonical_json(cell))


def assemble_bundle(artifact_root: Path, *, expected_commit: str, expected_tree: str) -> dict:
    cells = []
    run_id = ""
    run_attempt = ""
    for child in sorted(path for path in artifact_root.iterdir() if path.is_dir()):
        cell = json.loads((child / "cell.json").read_text(encoding="utf-8"))
        if cell.get("artifact_dir") != child.name:
            raise QualificationError(f"{child.name} artifact_dir does not match the folder")
        if cell.get("commit_sha") != expected_commit:
            raise QualificationError(WRONG_COMMIT)
        if cell.get("tree_sha") != expected_tree:
            raise QualificationError(WRONG_TREE)
        if not run_id:
            run_id = cell["run_id"]
            run_attempt = cell["run_attempt"]
        elif cell.get("run_id") != run_id or cell.get("run_attempt") != run_attempt:
            raise QualificationError("cells do not share one run")
        cells.append(cell)
    if not cells:
        raise QualificationError(MISSING_CELL)
    return {
        "cells": cells,
        "commit_sha": expected_commit,
        "repository": REPOSITORY,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "run_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
        "schema_version": SCHEMA_VERSION,
        "tree_sha": expected_tree,
        "workflow": WORKFLOW,
    }


def _spec_for(matrix: dict, cell_id: str) -> dict:
    for cell in matrix["cells"]:
        if cell["cell_id"] == cell_id:
            return cell
    raise QualificationError(cell_id)


def materialize_passing_bundle(root: Path, matrix: dict, *, commit: str, tree: str) -> dict:
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True)
    junit = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<testsuites><testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0">'
        b'<testcase classname="qualification.fixture" name="test_provider_free"></testcase>'
        b"</testsuite></testsuites>"
    )
    for index, spec in enumerate(required_cells(matrix), start=1):
        cell, files = build_cell_files(
            spec=spec,
            commit_sha=commit,
            tree_sha=tree,
            run_id="1000",
            run_attempt="1",
            job_id=str(2000 + index),
            python_version=f"{spec['python']}.0",
            junit_bytes=junit,
            dependency_text="pytest==8.3.5\n",
            runner_name=f"runner-{spec['platform']}-{spec['python']}",
        )
        write_cell(artifact_root / cell["artifact_dir"], cell, files)
    return assemble_bundle(artifact_root, expected_commit=commit, expected_tree=tree)


def _codes(errors: list[str]) -> set[str]:
    return {item.split(":", 1)[0] for item in errors}


def run_negative_controls(
    matrix: dict,
    *,
    bundle: dict | None = None,
    artifact_root: Path | None = None,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
    work: Path | None = None,
) -> list[str]:
    """Return log lines. Raise QualificationError if a control is accepted."""
    cleanup = work is None
    if work is None:
        import tempfile

        work = Path(tempfile.mkdtemp(prefix="qual-neg-"))
    try:
        if bundle is None:
            commit = "a" * 40
            tree = "b" * 40
            bundle = materialize_passing_bundle(work / "base", matrix, commit=commit, tree=tree)
            artifact_root = work / "base" / "artifacts"
            expected_commit = commit
            expected_tree = tree
        else:
            if artifact_root is None or expected_commit is None or expected_tree is None:
                raise QualificationError("negative controls need the candidate bundle")
            accepted = validate_bundle(
                bundle,
                matrix=matrix,
                expected_commit=expected_commit,
                expected_tree=expected_tree,
                artifact_root=artifact_root,
            )
            if accepted:
                raise QualificationError("base bundle was already rejected: " + "; ".join(accepted))
            commit = expected_commit
            tree = expected_tree
        lines = []

        def expect(name: str, code: str, errors: list[str]) -> None:
            if code not in _codes(errors):
                raise QualificationError(
                    f"negative-control {name} was accepted or missed {code}: {errors}"
                )
            lines.append(f"negative-control {name}: rejected ({code})")

        expect(
            "wrong-sha",
            WRONG_COMMIT,
            validate_bundle(
                bundle,
                matrix=matrix,
                expected_commit="c" * 40,
                expected_tree=tree,
                artifact_root=artifact_root,
            ),
        )
        expect(
            "wrong-tree",
            WRONG_TREE,
            validate_bundle(
                bundle,
                matrix=matrix,
                expected_commit=commit,
                expected_tree="d" * 40,
                artifact_root=artifact_root,
            ),
        )
        skipped = json.loads(json.dumps(bundle))
        skipped["cells"][0]["status"] = "skipped"
        expect(
            "skipped-cell",
            SKIPPED_CELL,
            validate_bundle(
                skipped,
                matrix=matrix,
                expected_commit=commit,
                expected_tree=tree,
                artifact_root=artifact_root,
            ),
        )
        missing_root = work / "missing-junit"
        shutil.copytree(artifact_root, missing_root)
        target = missing_root / bundle["cells"][0]["artifact_dir"] / "junit.xml"
        target.unlink()
        expect(
            "missing-junit",
            MISSING_JUNIT,
            validate_bundle(
                bundle,
                matrix=matrix,
                expected_commit=commit,
                expected_tree=tree,
                artifact_root=missing_root,
            ),
        )
        missing_cell = json.loads(json.dumps(bundle))
        missing_cell["cells"] = missing_cell["cells"][1:]
        expect(
            "missing-cell",
            MISSING_CELL,
            validate_bundle(
                missing_cell,
                matrix=matrix,
                expected_commit=commit,
                expected_tree=tree,
                artifact_root=artifact_root,
            ),
        )
        suite_root = work / "suite-counts"
        shutil.copytree(artifact_root, suite_root)
        suite_target = suite_root / bundle["cells"][0]["artifact_dir"] / "junit.xml"
        suite_target.write_bytes(
            b'<testsuite tests="2" errors="1">'
            b'<testcase classname="qualification.fixture" name="ok"></testcase>'
            b"</testsuite>"
        )
        expect(
            "suite-counts",
            NOT_PASS,
            validate_bundle(
                bundle,
                matrix=matrix,
                expected_commit=commit,
                expected_tree=tree,
                artifact_root=suite_root,
            ),
        )
        return lines
    finally:
        if cleanup:
            shutil.rmtree(work, ignore_errors=True)


def _live_python(python: str) -> str:
    version = platform.python_version()
    if platform.python_implementation() != "CPython" or not version.startswith(python + "."):
        raise QualificationError(f"live interpreter {version} is not CPython {python}")
    return version


def _runner_from_env() -> dict[str, str]:
    return {
        "runner_name": _env("RUNNER_NAME"),
        "image_os": _env("ImageOS", required=False),
        "image_version": _env("ImageVersion", required=False),
    }


def _env(name: str, *, required: bool = True) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        raise QualificationError(f"missing {name}")
    return value


def command_emit(args: argparse.Namespace) -> int:
    matrix = load_matrix(Path(args.matrix))
    spec = _spec_for(matrix, f"{args.platform}/{args.python}/{EXECUTED_TIER}")
    if spec["requirement"] != "required":
        raise QualificationError("tier is not required")
    if not _sha(args.commit) or not _sha(args.tree):
        raise QualificationError("commit or tree is not a sha")
    if not _digits(args.run_id) or not _digits(args.run_attempt) or not _digits(args.job_id):
        raise QualificationError("run or job id")
    version = _live_python(args.python)
    if os.environ.get("RUNNER_OS") != spec["runner_os"]:
        raise QualificationError("RUNNER_OS does not match the cell")
    if os.environ.get("RUNNER_ARCH") != spec["runner_arch"]:
        raise QualificationError("RUNNER_ARCH does not match the cell")
    observed = _runner_from_env()
    junit_bytes = Path(args.junit).read_bytes()
    dependency_text = Path(args.dependencies_raw).read_text(encoding="utf-8")
    cell, files = build_cell_files(
        spec=spec,
        commit_sha=args.commit,
        tree_sha=args.tree,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        job_id=args.job_id,
        python_version=version,
        junit_bytes=junit_bytes,
        dependency_text=dependency_text,
        runner_name=observed["runner_name"],
        image_os=observed["image_os"],
        image_version=observed["image_version"],
    )
    output = Path(args.output)
    write_cell(output, cell, files)
    print(f"emitted {cell['cell_id']} -> {output}")
    return 0


def command_assemble(args: argparse.Namespace) -> int:
    bundle = assemble_bundle(
        Path(args.artifact_root),
        expected_commit=args.expected_commit,
        expected_tree=args.expected_tree,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_json(bundle))
    print(f"assembled {len(bundle['cells'])} cells -> {destination}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    matrix = load_matrix(Path(args.matrix))
    bundle = load_json(Path(args.bundle))
    errors = validate_bundle(
        bundle,
        matrix=matrix,
        expected_commit=args.expected_commit,
        expected_tree=args.expected_tree,
        artifact_root=Path(args.artifact_root),
    )
    if errors:
        print("qualification-evidence: rejected", file=sys.stderr)
        for item in errors:
            print(item, file=sys.stderr)
        return 1
    print("qualification-evidence: required cells accepted")
    return 0


def command_negative(args: argparse.Namespace) -> int:
    matrix = load_matrix(Path(args.matrix))
    bundle = None
    artifact_root = None
    if args.bundle:
        bundle = load_json(Path(args.bundle))
        artifact_root = Path(args.artifact_root)
    lines = run_negative_controls(
        matrix,
        bundle=bundle,
        artifact_root=artifact_root,
        expected_commit=args.expected_commit,
        expected_tree=args.expected_tree,
    )
    for line in lines:
        print(line)
    print("qualification-negative-controls: rejected as designed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CSL-221 qualification evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit-cell")
    emit.add_argument("--matrix", required=True)
    emit.add_argument("--platform", required=True)
    emit.add_argument("--python", required=True)
    emit.add_argument("--commit", required=True)
    emit.add_argument("--tree", required=True)
    emit.add_argument("--run-id", required=True)
    emit.add_argument("--run-attempt", required=True)
    emit.add_argument("--job-id", required=True)
    emit.add_argument("--junit", required=True)
    emit.add_argument("--dependencies-raw", required=True)
    emit.add_argument("--output", required=True)
    emit.set_defaults(func=command_emit)

    assemble = sub.add_parser("assemble")
    assemble.add_argument("--artifact-root", required=True)
    assemble.add_argument("--expected-commit", required=True)
    assemble.add_argument("--expected-tree", required=True)
    assemble.add_argument("--output", required=True)
    assemble.set_defaults(func=command_assemble)

    validate = sub.add_parser("validate")
    validate.add_argument("--matrix", required=True)
    validate.add_argument("--bundle", required=True)
    validate.add_argument("--artifact-root", required=True)
    validate.add_argument("--expected-commit", required=True)
    validate.add_argument("--expected-tree", required=True)
    validate.set_defaults(func=command_validate)

    negative = sub.add_parser("negative-controls")
    negative.add_argument("--matrix", required=True)
    negative.add_argument("--bundle")
    negative.add_argument("--artifact-root")
    negative.add_argument("--expected-commit")
    negative.add_argument("--expected-tree")
    negative.set_defaults(func=command_negative)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except QualificationError as exc:
        print(f"qualification-evidence: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
