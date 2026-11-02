"""Read-only verification of the complete RW-23/RW-24 local DEC-09 evidence matrix.

Usage: python verify_evidence.py ROOT
ROOT must contain w311/, w312/, l311/, l312/. JSON summary goes to stdout;
exit 0 means all four cells verified, exit 1 refuses incomplete/invalid evidence.
This checks recorded evidence, not an independent execution or external witness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PurePosixPath

COMMIT = "6d7d7df73b4f480818c64a3a2b8fa8df39c6fdf9"
TREE = "5a9b5b5439a3a4bfb4e4203ddb37597fdc313449"
ARCHIVE = "8247c677f1f055321bce2ee7d8132d0b28949aa0085e7ddbe995062ee3a670ab"
MANIFEST = "520695f201e64f7b754de1e8ab2bc0df46187765e6b70487eb58f02e96074c46"
REGRESSIONS = "6a04f1c76d04fec0bacd7f4b995c1cb814714603771949215cc0ee823f94f2e0"
CELLS = {"w311": ("Windows", "3.11"), "w312": ("Windows", "3.12"),
         "l311": ("Linux", "3.11"), "l312": ("Linux", "3.12")}
CHECKS = {"version", "dependencies", "pip-check", "ruff", "pytest"}
ARTIFACTS = {"source-manifest.json", "pytest.xml", *(name + ".log" for name in CHECKS)}
HASH = re.compile(r"[0-9a-f]{64}\Z")
EXECUTABLES = {
    "w311": "C:/t/a26/env311/Scripts/python.exe",
    "w312": "C:/t/a26/env312r/Scripts/python.exe",
    "l311": "/audit/env311/bin/python", "l312": "/audit/env312/bin/python",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(data):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_pairs)


def timestamp(value, field):
    require(isinstance(value, str) and value.endswith("Z"), f"{field} missing/not UTC")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def duration(value, field):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            f"invalid {field}")
    return value


def xml_summary(data):
    root = ET.fromstring(data)
    require(not any(element.tag in {"failure", "error"} for element in root.iter()),
            "JUnit contains failure/error records")
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    require(root.tag in {"testsuite", "testsuites"} and suites, "missing JUnit suites")
    totals = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
    seconds = 0.0
    for suite in suites:
        require(not suite.findall("testsuite"), "unexpected nested JUnit suites")
        counts = {key: int(suite.attrib[key]) for key in totals}
        require(all(value >= 0 for value in counts.values()), "negative JUnit count")
        cases = suite.findall("testcase")
        require(counts["tests"] == len(cases), "JUnit declared/actual testcase counts differ")
        for count, tag in (("skipped", "skipped"), ("failures", "failure"), ("errors", "error")):
            require(counts[count] == sum(len(case.findall(tag)) for case in cases),
                    f"JUnit declared/actual {count} counts differ")
        require(counts["failures"] == counts["errors"] == 0, "JUnit reports failures/errors")
        for key in totals:
            totals[key] += counts[key]
        seconds += duration(float(suite.attrib["time"]), "JUnit duration")
    require(totals["tests"] > 0 and totals["skipped"] <= totals["tests"], "empty/invalid JUnit")
    return {**totals, "passed": totals["tests"] - totals["skipped"], "duration_seconds": seconds}


def regression_coverage(data):
    expected_bytes = Path(__file__).with_name("expected-regressions.json").read_bytes()
    require(sha256(expected_bytes) == REGRESSIONS, "expected regression manifest changed")
    expected = read_json(expected_bytes)
    require(expected.get("source_commit") == COMMIT, "regression source differs")
    cases = expected.get("cases")
    require(isinstance(cases, list) and len(cases) == 225, "required regression set incomplete")
    required = {tuple(case) for case in cases}
    require(len(required) == 225, "duplicate required regression")
    found = {}
    for case in ET.fromstring(data).iter("testcase"):
        key = (case.get("classname"), case.get("name"))
        if key in required:
            require(key not in found, f"duplicate required testcase: {key}")
            require(not any(case.findall(tag) for tag in ("skipped", "failure", "error")),
                    f"required testcase did not pass: {key}")
            found[key] = True
    require(set(found) == required, "missing required regression cases: " +
            str(sorted(required - set(found))))
    return len(found)


def gate_commands(att, cell):
    executable = att.get("executable")
    require(isinstance(executable, str)
            and executable.replace("\\", "/") == EXECUTABLES[cell], "unexpected cell executable")
    expected = {
        "version": [executable, "-VV"],
        "dependencies": [executable, "-m", "pip", "freeze"],
        "pip-check": [executable, "-m", "pip", "check"],
        "ruff": [executable, "-m", "ruff", "check", "."],
    }
    for check in att["checks"]:
        name, argv = check["name"], check.get("argv")
        if name in expected:
            require(argv == expected[name], f"wrong gate argv: {name}")
        elif name == "pytest":
            prefix = [executable, "-m", "pytest", "-q", "-ra"]
            require(isinstance(argv, list) and len(argv) == 6 and argv[:5] == prefix,
                    "wrong gate argv: pytest")
            base = "C:/t/r24" if cell.startswith("w") else "/audit/rw24"
            allowed = {f"--junitxml={base}/{run}/pytest.xml" for run in (cell, cell + "r")}
            require(isinstance(argv[5], str) and argv[5].replace("\\", "/") in allowed,
                    "wrong pytest report path")


def log_summary(data):
    text = re.sub(r"\x1b\[[0-9;]*m", "", data.decode("utf-8-sig"))
    lines = [line for line in text.splitlines()
             if re.search(r"\bin\s+[0-9.]+s\b", line)
             and re.search(r"\d+\s+(?:passed|skipped|failed|errors?|xfailed|xpassed)\b", line)]
    require(bool(lines), "pytest log has no completed summary")
    line = lines[-1]
    counts = {name.rstrip("s") if name in {"error", "errors"} else name: int(value)
              for value, name in re.findall(
                  r"(\d+)\s+(passed|skipped|failed|errors?|xfailed|xpassed|deselected)\b", line)}
    require(not any(counts.get(name, 0) for name in ("failed", "error", "xpassed", "deselected")),
            "pytest log reports failures/errors/unexpected passes/deselection")
    seconds = float(re.search(r"\bin\s+([0-9.]+)s\b", line).group(1))
    duration(seconds, "pytest log duration")
    return counts, seconds, line.strip()


def verify_cell(folder, cell):
    required = ARTIFACTS | {"attestation.json"}
    missing = sorted(name for name in required if not (folder / name).is_file())
    require(not missing, "missing artifacts: " + ", ".join(missing))
    # Hash and parse the same immutable byte images; never reopen a verified artifact.
    blobs = {name: (folder / name).read_bytes() for name in required}
    att = read_json(blobs["attestation.json"])
    for field, expected in (("commit", COMMIT), ("tree", TREE), ("archive_sha256", ARCHIVE)):
        require(att.get(field) == expected, f"wrong {field}")
    require(att.get("tracked_source_changed") == [], "missing/nonempty tracked_source_changed")
    started = timestamp(att.get("started_utc"), "started_utc")
    completed = timestamp(att.get("completed_utc"), "completed_utc")
    require(completed >= started, "completed_utc predates started_utc")
    checks = att.get("checks")
    require(isinstance(checks, list) and len(checks) == len(CHECKS), "incomplete checks")
    require({check.get("name") for check in checks} == CHECKS, "missing/duplicate check names")
    for check in checks:
        require(type(check.get("returncode")) is int and check["returncode"] == 0,
                f"nonzero/missing exit code: {check['name']}")
        duration(check.get("duration_seconds"), check["name"] + " duration")
    gate_commands(att, cell)
    hashes = att.get("artifacts_sha256")
    require(isinstance(hashes, dict) and set(hashes) == ARTIFACTS, "incomplete/unexpected artifact hashes")
    for name, expected in hashes.items():
        require(isinstance(expected, str) and HASH.fullmatch(expected), f"invalid hash: {name}")
        require(sha256(blobs[name]) == expected, f"artifact hash mismatch: {name}")
    manifest = read_json(blobs["source-manifest.json"])
    require(isinstance(manifest, dict) and manifest, "empty/invalid source manifest")
    require(type(att.get("source_file_count")) is int and len(manifest) == att["source_file_count"],
            "source manifest/file count differs")
    for name, digest in manifest.items():
        require(isinstance(name, str) and name and "\\" not in name and ":" not in name
                and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
                f"unsafe manifest path: {name}")
        require(isinstance(digest, str) and HASH.fullmatch(digest), f"invalid source hash: {name}")
    canonical_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    require(len(manifest) == 735 and sha256(canonical_manifest) == MANIFEST,
            "source manifest differs from the independently reconstructed frozen archive")
    platform_name, minor = CELLS[cell]
    require(str(att.get("platform", "")).startswith(platform_name), "wrong platform for cell")
    match = re.search(r"\bPython (\d+\.\d+\.\d+)\b", blobs["version.log"].decode("utf-8-sig"))
    require(match is not None, "version.log lacks a Python version")
    python = match.group(1)
    require(python.startswith(minor + ".") and str(att.get("python", "")).startswith(python + " "),
            "Python version disagrees with cell/attestation")
    xml = xml_summary(blobs["pytest.xml"])
    regressions = regression_coverage(blobs["pytest.xml"])
    counts, log_seconds, line = log_summary(blobs["pytest.log"])
    require(xml["passed"] == counts.get("passed", 0), "XML/log passed counts differ")
    require(xml["skipped"] == counts.get("skipped", 0) + counts.get("xfailed", 0),
            "XML/log skipped counts differ")
    return {
        "cell": cell, "python": python, "platform": att["platform"],
        "started_utc": att["started_utc"], "completed_utc": att["completed_utc"],
        "wall_duration_seconds": (completed - started).total_seconds(),
        "pytest_check_duration_seconds": next(c["duration_seconds"] for c in checks if c["name"] == "pytest"),
        "pytest_xml_duration_seconds": xml["duration_seconds"], "pytest_log_duration_seconds": log_seconds,
        "tests": xml["tests"], "passed": xml["passed"], "skipped": counts.get("skipped", 0),
        "xfailed": counts.get("xfailed", 0), "failures": xml["failures"], "errors": xml["errors"],
        "source_file_count": len(manifest), "pytest_summary": line,
        "required_regressions_passed": regressions,
    }, manifest


def verify_root(root):
    errors, summaries, baseline = [], [], None
    for cell in CELLS:
        try:
            summary, manifest = verify_cell(root / cell, cell)
            require(baseline is None or manifest == baseline, "source manifests differ between cells")
            baseline = manifest
            summaries.append(summary)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError) as exc:
            errors.append(f"{cell}: {exc}")
    if (root / "source.tar").is_file():
        try:
            require(sha256((root / "source.tar").read_bytes()) == ARCHIVE, "source.tar hash mismatch")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if errors:
        return {"verified": False, "errors": errors}
    return {"verified": True, "commit": COMMIT, "tree": TREE, "archive_sha256": ARCHIVE,
            "source_manifests_identical": True, "cells": summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = verify_root(args.root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
