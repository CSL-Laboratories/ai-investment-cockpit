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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(kind: str, path: Path) -> None:
    schema = load_json(SCHEMAS[kind])
    instance = load_json(path)
    jsonschema.validate(instance=instance, schema=schema)


def write_manifest(run_dir: Path, entries: list[dict[str, str]]) -> Path:
    manifest_path = run_dir / "manifest.json"
    payload = {
        "run_id": run_dir.name,
        "empirical_pass_claimed": False,
        "filings": entries,
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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

    for kind, paths in (("leaf", args.leaf), ("review", args.review)):
        for path in paths:
            path = path.resolve()
            try:
                validate(kind, path)
            except (jsonschema.ValidationError, json.JSONDecodeError, OSError) as exc:
                failures.append(f"{kind} {path}: {exc}")
                continue
            entries.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
            )

    if args.synthesis is not None:
        path = args.synthesis.resolve()
        try:
            validate("synthesis", path)
        except (jsonschema.ValidationError, json.JSONDecodeError, OSError) as exc:
            failures.append(f"synthesis {path}: {exc}")
        else:
            entries.append(
                {
                    "kind": "synthesis",
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
            )

    manifest_path = write_manifest(run_dir, entries)
    print(json.dumps({"manifest": str(manifest_path), "ok": not failures, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
