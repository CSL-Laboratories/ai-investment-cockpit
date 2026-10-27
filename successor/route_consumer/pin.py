"""Digest-pinned access to the vendored Route Contract v0 schema (CSL-496 draft).

The schema is Recursion's. This module only reads the vendored bytes after
proving they match the sha256 in ``contract/PIN.json``, so a drifted or edited
copy fails closed instead of validating against the wrong contract.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

PACKAGE_DIR = Path(__file__).resolve().parent
PIN_PATH = PACKAGE_DIR / "contract" / "PIN.json"
SCHEMA_KEY = "contract/route-contract-v0.schema.json"
VECTORS_KEY = "contract/oracle_vectors.json"


class PinError(RuntimeError):
    """The vendored contract does not match its pin."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@cache
def load_pin() -> dict[str, Any]:
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def pinned_head() -> str:
    return load_pin()["source"]["head_sha"]


def read_vendored(key: str) -> bytes:
    """Return the vendored bytes for ``key`` only if they match the pin."""

    entry = load_pin()["vendored"].get(key)
    if entry is None:
        raise PinError(f"{key}: not a pinned artifact")
    data = (PACKAGE_DIR / key).read_bytes()
    actual = sha256_hex(data)
    if actual != entry["sha256"]:
        raise PinError(f"{key}: sha256 {actual} does not match pin {entry['sha256']}")
    return data


def verify_vendored() -> None:
    for key in load_pin()["vendored"]:
        read_vendored(key)


@cache
def load_schema() -> dict[str, Any]:
    return json.loads(read_vendored(SCHEMA_KEY).decode("utf-8"))


def schema_defs() -> dict[str, Any]:
    return load_schema()["$defs"]


@cache
def _validator(name: str) -> jsonschema.Draft202012Validator:
    if name not in schema_defs():
        raise PinError(f"{name}: not defined by the pinned contract")
    return jsonschema.Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": schema_defs()},
        format_checker=jsonschema.FormatChecker(),
    )


def validation_errors(name: str, record: object) -> list[str]:
    errors = sorted(_validator(name).iter_errors(record), key=lambda e: list(e.path))
    return [error.message for error in errors]


def is_valid(name: str, record: object) -> bool:
    return not validation_errors(name, record)
