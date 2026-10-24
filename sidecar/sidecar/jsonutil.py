"""Canonical JSON and schema checks shared by sidecar artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema


def json_ready(value: Any) -> Any:
    """Convert a config tree into strict JSON values.

    ``Decimal`` becomes its exact string. Floats must be finite. Other types are rejected
    so a trial hash cannot depend on an implicit encoder.
    """
    if value is None or isinstance(value, (str, bool, int)) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not allowed in a sidecar artifact")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal is not allowed in a sidecar artifact")
        return format(value, "f")
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("artifact object keys must be strings")
        return {key: json_ready(value[key]) for key in value}
    raise ValueError(f"unsupported artifact value type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    ready = json_ready(value)
    return json.dumps(ready, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(instance: dict, schema_path: Path) -> dict:
    jsonschema.validate(instance=instance, schema=load_schema(schema_path))
    return instance


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
