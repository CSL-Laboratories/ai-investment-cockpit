"""Test-only bridge to Recursion's in-process reference adapter (read-only checkout).

Recursion's code is never copied into this tree. It is imported from a sibling
checkout named by ``RECURSION_ROUTE_CONTRACT_CHECKOUT`` after every file in the
pin's ``subject_manifest`` is proved byte-identical, so the conformance run
cannot silently exercise a different adapter than the one the pin names.

Both repositories have a top-level ``scripts`` package. The import below swaps
``sys.modules`` for the duration of the load and restores Cockpit's entries
afterwards. Recursion's modules have no lazy imports, so they keep working
from their own already-bound references.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import unittest
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema

from successor.route_consumer import pin
from successor.route_consumer.pin import PinError, sha256_hex

CHECKOUT_ENV = "RECURSION_ROUTE_CONTRACT_CHECKOUT"
OPT_OUT_ENV = "ROUTE_CONSUMER_ALLOW_NO_RECURSION"


class CheckoutError(RuntimeError):
    """The Recursion checkout is missing or is not the pinned one."""


def require_checkout() -> Path:
    """Fail closed. Only an explicit opt-out skips the conformance run."""

    raw = os.environ.get(CHECKOUT_ENV)
    if not raw:
        if os.environ.get(OPT_OUT_ENV) == "1":
            raise unittest.SkipTest(f"{OPT_OUT_ENV}=1: conformance against the fake driver NOT run")
        raise CheckoutError(
            f"set {CHECKOUT_ENV} to a read-only checkout of CSL-Laboratories/recursion at "
            f"{pin.pinned_head()} (or set {OPT_OUT_ENV}=1 to skip, which proves nothing)"
        )
    return Path(raw).resolve()


def verify_checkout(root: Path) -> None:
    manifest = pin.load_pin()["subject_manifest"]
    for relative, expected in manifest.items():
        path = root / relative
        if not path.is_file():
            raise CheckoutError(f"{relative}: missing from the checkout")
        actual = sha256_hex(path.read_bytes())
        if actual != expected:
            raise CheckoutError(f"{relative}: sha256 {actual} is not the pinned {expected}")
    for key, entry in pin.load_pin()["vendored"].items():
        source = root / entry["source_path"]
        if not source.is_file() or sha256_hex(source.read_bytes()) != entry["sha256"]:
            raise CheckoutError(f"{entry['source_path']}: differs from the vendored {key}")
    if (root / ".git").exists():
        head = checkout_head(root)
        if head != pin.pinned_head():
            raise CheckoutError(f"checkout HEAD {head or '<unknown>'} is not the pin {pin.pinned_head()}")


def checkout_head(root: Path) -> str:
    """The checked-out commit via git itself, so a worktree (``.git`` is a file) and a branch work."""

    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()


@cache
def load(root: Path) -> SimpleNamespace:
    verify_checkout(root)
    saved = {
        name: module for name, module in sys.modules.items()
        if name == "scripts" or name.startswith("scripts.")
    }
    for name in saved:
        del sys.modules[name]
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    try:
        rec = SimpleNamespace(
            root=root,
            credentials=importlib.import_module("scripts.v3.route_adapter.credentials"),
            fake=importlib.import_module("scripts.v3.route_adapter.fake"),
            session=importlib.import_module("scripts.v3.route_adapter.session"),
            argv=importlib.import_module("scripts.v3.route_contract.argv"),
            errors=importlib.import_module("scripts.v3.route_contract.errors"),
            executor=importlib.import_module("scripts.v3.route_contract.executor"),
            health=importlib.import_module("scripts.v3.route_contract.health"),
            live=importlib.import_module("scripts.v3.route_contract.live"),
            model=importlib.import_module("scripts.v3.route_contract.model"),
            transport=importlib.import_module("scripts.v3.route_contract.transport"),
        )
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write
        for name in [n for n in sys.modules if n == "scripts" or n.startswith("scripts.")]:
            del sys.modules[name]
        sys.modules.update(saved)
    return rec


def recursion() -> SimpleNamespace:
    return load(require_checkout())


class InProcessRoutePort:
    """A ``RoutePort`` over Recursion's ``ReferenceAdapter`` and the AT1 fake driver."""

    def __init__(
        self,
        rec: SimpleNamespace,
        engine_id: str,
        *,
        transport: Any,
        driver: Any,
        health: Any,
        secret_values: Any = None,
        pinned_cwd: str | None = None,
        strict_records: bool = True,
    ) -> None:
        self._rec = rec
        self._strict = strict_records
        self._schema: dict[str, Any] = {}
        self.submitted: list[dict[str, Any]] = []

        def validate(body: Any) -> str | None:
            errors = sorted(
                jsonschema.Draft202012Validator(self._schema).iter_errors(body),
                key=lambda e: list(e.path),
            )
            return errors[0].message if errors else None

        self.adapter = rec.executor.ReferenceAdapter(
            engine_id,
            transport=transport,
            driver=driver,
            health=health,
            validate=validate,
            secret_values=secret_values,
            pinned_cwd=pinned_cwd,
        )

    def support(self) -> dict[str, Any]:
        return self.adapter.support()

    def submit(
        self, request: dict[str, Any], *, prompt: str, output_schema: dict[str, Any]
    ) -> dict[str, Any]:
        rec = self._rec
        self._schema = output_schema
        self.submitted.append(request)
        built = rec.model.ExecutionRequest(
            request_id=request["id"],
            route_id=request["route_id"],
            engine_id=request["engine_id"],
            route_descriptor_digest=request["route_descriptor_digest"],
            process_requirement_id=request["process_requirement_id"],
            workload_id=request["workload_id"],
            session=rec.session.SessionKey(
                request["session"]["tenant"],
                request["session"]["route"],
                request["session"]["session"],
            ),
            credential=rec.credentials.CredentialRef(
                tenant_id=request["credential_ref"]["tenant_id"],
                provider=request["credential_ref"]["provider"],
                name=request["credential_ref"]["name"],
            ),
            cli_model=request["cli_model"],
            prompt=prompt,
            output_schema=output_schema,
            max_turns=request["max_turns"],
            timeout_s=request["timeout_ms"] / 1000,
            privacy_class=request["privacy_class"],
            max_output_bytes=request["output"]["max_output_bytes"],
        )
        if self._strict and built.to_record() != request:
            raise AssertionError("consumer request record differs from Recursion's own builder")
        try:
            result = self.adapter.execute(built)
        except rec.errors.RouteRefusal as refusal:
            return refusal.to_record(request["id"])
        return result.to_record(built)


class LiveRoutePort:
    """Recursion's live adapter, which reports ``unsupported`` and refuses to execute."""

    def __init__(self, rec: SimpleNamespace, engine_id: str) -> None:
        self._live = rec.live.live_adapter(engine_id)
        self.submit_calls = 0

    def support(self) -> dict[str, Any]:
        return self._live.support()

    def submit(
        self, request: dict[str, Any], *, prompt: str, output_schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.submit_calls += 1
        return {
            "type": "RouteRefusal@0",
            "request_id": request["id"],
            "code": "unsupported",
            "dispatched": False,
        }


def vectors() -> dict[str, Any]:
    return json.loads(pin.read_vendored(pin.VECTORS_KEY).decode("utf-8"))


__all__ = (
    "CHECKOUT_ENV", "CheckoutError", "InProcessRoutePort", "LiveRoutePort", "OPT_OUT_ENV",
    "PinError", "recursion", "require_checkout", "vectors", "verify_checkout",
)
