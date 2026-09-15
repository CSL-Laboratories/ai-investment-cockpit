#!/usr/bin/env python3
"""Deterministic swarm filing intake: jsonschema only. No LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - smoke environment
    sys.stderr.write("jsonschema is required\n")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = {
    "leaf": ROOT / "docs/research/swarm-schemas/leaf-filing.schema.json",
    "review": ROOT / "docs/research/swarm-schemas/review-filing.schema.json",
    "synthesis": ROOT / "docs/research/swarm-schemas/synthesis.schema.json",
}
MAX_FILING_BYTES = 128 * 1024


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validated_bytes(kind: str, path: Path) -> tuple[dict, str]:
    schema = load_json(SCHEMAS[kind])
    with path.open("rb") as source:
        raw = source.read(MAX_FILING_BYTES + 1)
    if len(raw) > MAX_FILING_BYTES:
        raise ValueError("oversized filing")
    instance = json.loads(raw, object_pairs_hook=_unique_object)
    jsonschema.validate(instance=instance, schema=schema)
    return instance, hashlib.sha256(raw).hexdigest()


def validate(kind: str, path: Path) -> None:
    validated_bytes(kind, path)


def write_manifest(run_dir: Path, entries: list[dict[str, str]]) -> Path:
    manifest_path = run_dir / "manifest.json"
    payload = {
        "run_id": run_dir.name,
        "empirical_pass_claimed": False,
        "filings": entries,
    }
    with manifest_path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(payload, indent=2) + "\n")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--leaf", action="append", default=[], type=Path)
    parser.add_argument("--review", action="append", default=[], type=Path)
    parser.add_argument("--synthesis", type=Path)
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    failures: list[str] = []
    source_families: set[str] = set()
    leaf_ids: set[str] = set()
    attempts: set[tuple[str, int]] = set()

    for kind, paths in (("leaf", args.leaf), ("review", args.review)):
        for path in paths:
            path = path.resolve()
            try:
                filing, digest = validated_bytes(kind, path)
                identity = (filing["job_id"], filing["attempt"])
                if identity in attempts:
                    raise ValueError("duplicate attempt")
                attempts.add(identity)
                source_families.add(filing["route_family"])
                if kind == "leaf":
                    leaf_ids.add(filing["job_id"])
            except (jsonschema.ValidationError, ValueError, OSError):
                failures.append(f"{kind}: invalid filing (content omitted)")
                continue
            entries.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "sha256": digest,
                }
            )

    if args.synthesis is not None:
        path = args.synthesis.resolve()
        try:
            filing, digest = validated_bytes("synthesis", path)
            if (not leaf_ids or set(filing["leaf_ids"]) != leaf_ids
                    or set(filing["source_families"]) != source_families
                    or filing["route_family"] in source_families):
                raise ValueError("unbound or same-family synthesis")
        except (jsonschema.ValidationError, ValueError, OSError):
            failures.append("synthesis: invalid or unbound filing (content omitted)")
        else:
            entries.append(
                {
                    "kind": "synthesis",
                    "path": str(path),
                    "sha256": digest,
                }
            )

    if failures:
        print(json.dumps({"manifest": None, "ok": False, "failures": failures}, indent=2))
        return 1
    if not entries:
        print(json.dumps({"manifest": None, "ok": False, "failures": ["empty intake"]}))
        return 1
    try:
        manifest_path = write_manifest(run_dir, entries)
    except OSError:
        print(json.dumps({"manifest": None, "ok": False, "failures": ["manifest unavailable or already exists"]}))
        return 1
    print(json.dumps({"manifest": str(manifest_path), "ok": not failures, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
