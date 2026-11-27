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


def _check_pin_shape(document: object) -> None:
    if not isinstance(document, dict):
        raise PinError("PIN.json: not an object")
    source = document.get("source")
    if not (
        isinstance(source, dict)
        and isinstance(source.get("repository"), str)
        and isinstance(source.get("pull_request"), int)
        and not isinstance(source.get("pull_request"), bool)
        and isinstance(source.get("head_sha"), str)
    ):
        raise PinError("PIN.json: source is malformed")
    vendored = document.get("vendored")
    if not (
        isinstance(vendored, dict)
        and vendored
        and all(
            isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
            for entry in vendored.values()
        )
    ):
        raise PinError("PIN.json: vendored is malformed")


@cache
def load_pin() -> dict[str, Any]:
    try:
        document = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise PinError("PIN.json: unreadable") from None
    _check_pin_shape(document)
    return document


def pinned_head() -> str:
    return load_pin()["source"]["head_sha"]


def read_vendored(key: str) -> bytes:
    """Return the vendored bytes for ``key`` only if they match the pin."""

    entry = load_pin()["vendored"].get(key)
    if entry is None:
        raise PinError(f"{key}: not a pinned artifact")
    try:
        data = (PACKAGE_DIR / key).read_bytes()
    except OSError:
        raise PinError(f"{key}: unreadable") from None
    actual = sha256_hex(data)
    if actual != entry["sha256"]:
        raise PinError(f"{key}: sha256 {actual} does not match pin {entry['sha256']}")
    return data


def verify_vendored() -> None:
    for key in load_pin()["vendored"]:
        read_vendored(key)


@cache
def load_schema() -> dict[str, Any]:
    try:
        schema = json.loads(read_vendored(SCHEMA_KEY).decode("utf-8"))
    except (ValueError, RecursionError):
        raise PinError(f"{SCHEMA_KEY}: not parseable JSON") from None
    if not isinstance(schema, dict) or not isinstance(schema.get("$defs"), dict):
        raise PinError(f"{SCHEMA_KEY}: has no $defs")
    return schema


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
