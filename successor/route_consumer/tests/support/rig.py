"""Shared fixtures for the conformance run: a consumer wired to Recursion's fake driver."""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from successor.route_consumer.consumer import RouteConsumer
from successor.route_consumer.profile import WorkloadProfile, canonical_digest
from successor.route_consumer.records import RouteBinding
from successor.route_consumer.tests.support.recursion_bridge import InProcessRoutePort, vectors

CANARY = "canary-secret-RC0-4d1e8b"
DIGEST = "sha256:" + "1" * 64
TENANT = "tenant-a"
CRED_NAME = "route-ref"


def binding(engine: str = "claude", model: str | None = None) -> RouteBinding:
    return RouteBinding(
        route_id="route-fake",
        engine_id=engine,
        route_descriptor_digest=DIGEST,
        process_requirement_id="proc-req-1",
        cli_model=model or vectors()["_provenance"]["model_id"],
        tenant=TENANT,
        credential_provider="fake",
        credential_name=CRED_NAME,
    )


def vector_profile(timeout_s: float = 30.0) -> WorkloadProfile:
    return WorkloadProfile(
        workload_id="workload-a",
        cockpit_job="vector",
        output_schema_name="vector",
        output_schema_digest=canonical_digest(vectors()["schema"]),
        timeout_s=timeout_s,
    )


class ScriptedTransport:
    def __init__(self, rec: SimpleNamespace, results: list[dict[str, Any]]) -> None:
        self._rec = rec
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def run(self, argv: list[str], input_text: str, timeout_s: float, cwd: str | None) -> Any:
        self.calls.append({"argv": list(argv), "input": input_text, "timeout_s": timeout_s})
        raw = self.results.pop(0)
        return self._rec.transport.ProcResult(
            raw["returncode"], raw["stdout"], raw["stderr"], raw["timed_out"]
        )


def make_driver(rec: SimpleNamespace, *, register: bool = True) -> Any:
    resolver = rec.credentials.LocalCredentialResolver()
    if register:
        ref = rec.credentials.CredentialRef(tenant_id=TENANT, provider="fake", name=CRED_NAME)
        resolver.register(ref, CANARY)
    return rec.fake.FakeRouteDriver(owner=rec.session.SessionOwner(), resolver=resolver)


@dataclass
class Rig:
    consumer: RouteConsumer
    port: InProcessRoutePort
    transport: Any
    health: Any
    driver: Any


def make_rig(
    rec: SimpleNamespace,
    engine: str = "claude",
    *,
    script: list[dict[str, Any]] | None = None,
    transport: Any = None,
    health: Any = None,
    driver: Any = None,
    secret_values: Any = None,
    pinned_cwd: str | None = None,
) -> Rig:
    transport = transport or ScriptedTransport(rec, script or [])
    health = health or rec.health.InMemoryHealth()
    driver = driver or make_driver(rec)
    port = InProcessRoutePort(
        rec, engine, transport=transport, driver=driver, health=health,
        secret_values=secret_values, pinned_cwd=pinned_cwd,
    )
    return Rig(RouteConsumer(port, binding=binding(engine)), port, transport, health, driver)


def ok_envelope() -> dict[str, Any]:
    body = json.dumps(vectors()["good"])
    return {
        "returncode": 0,
        "stdout": json.dumps({"session_id": "s1", "result": body}),
        "stderr": "",
        "timed_out": False,
    }


HELPER_TREE = textwrap.dedent(
    """
    import os, subprocess, sys, time
    pid_file = sys.argv[1]
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(pid_file, "w") as handle:
        handle.write(str(child.pid))
    sys.stdin.read()
    time.sleep(60)
    """
)


class LocalHelperTransport:
    """Rewrites the provider argv to a local helper script; the real kill path still runs."""

    def __init__(self, rec: SimpleNamespace, helper_args: list[str]) -> None:
        self.helper_args = helper_args
        self.inner = rec.transport.SubprocessTransport()
        self.seen_argv: list[list[str]] = []

    def run(self, argv: list[str], input_text: str, timeout_s: float, cwd: str | None) -> Any:
        self.seen_argv.append(list(argv))
        return self.inner.run([sys.executable, *self.helper_args], input_text, timeout_s, cwd)
