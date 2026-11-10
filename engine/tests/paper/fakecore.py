"""Test double for the Recursion core's ``csl-core/v0`` boundary. Not the core.

It keeps only what the gateway relies on: permission, the run and connection checks of the
core's ``ConnectionBreaker`` (recursion ``core/recursion_core/plugin_security/breaker.py``),
a one-use operator approval bound to the exact effect bytes, and a total budget. The real
core is exercised by ``test_e2e_recursion_core.py`` when it is importable.

Run binding mirrors the core: a run must be bound to the connection it is billed through
(host op ``run.bind``) before any effect from it is accepted, and a run cannot move
connections. In the order the core checks them: an unbound run is ``run_unknown``, a run
sent on a connection other than its bound one is ``run_connection_mismatch``, then
``run_faulted``, then ``connection_tripped``. ``DEFAULT_RUNS`` pre-binds the run ids the
shared fixtures use.
"""

from __future__ import annotations

import hashlib
import json

from cockpit.paper.core_client import canonical


DEFAULT_RUNS = {"run-1": "conn-a", "run-ok": "conn-ok"}


class FakeCore:
    def __init__(self, permissions=("read", "propose", "order"), budget_minor=10_000, runs=None):
        self.permissions = set(permissions)
        self.budget = budget_minor
        self.spent = 0
        self.approvals: dict[str, dict] = {}
        self.calls: list[str] = []
        self.tripped = False
        self.faulted_runs: set[str] = set()
        self.tripped_connections: set[str] = set()
        self.run_connection: dict[str, str] = dict(DEFAULT_RUNS if runs is None else runs)

    def bind_run(self, run_id: str, connection: str) -> None:
        """The core's ``run.bind``: record a run's connection; a run cannot move connections."""
        if self.run_connection.setdefault(run_id, connection) != connection:
            raise ValueError("run_connection_mismatch")

    def _reply(self, ok: bool, body: dict) -> bytes:
        key = "result" if ok else "refusal"
        return canonical({"protocol": "csl-core/v0", "ok": ok, key: body})

    def __call__(self, raw: bytes) -> bytes:
        msg = json.loads(raw)
        op, args = msg["op"], msg["args"]
        self.calls.append(op)
        effect = args["effect"]
        digest = hashlib.sha256(canonical(effect)).hexdigest()
        if "order" not in self.permissions:
            return self._reply(False, {"code": "permission_required"})
        bound = self.run_connection.get(effect["run_id"])
        if bound is None:
            return self._reply(False, {"code": "run_unknown"})
        if bound != effect["connection"]:
            return self._reply(False, {"code": "run_connection_mismatch"})
        if effect["run_id"] in self.faulted_runs:
            return self._reply(False, {"code": "run_faulted"})
        if self.tripped or effect["connection"] in self.tripped_connections:
            return self._reply(False, {"code": "connection_tripped"})
        if op == "effect.request":
            approval_id = "apr_%d" % len(self.approvals)
            self.approvals[approval_id] = {"digest": digest, "state": "pending"}
            return self._reply(True, {"approval_id": approval_id, "digest": digest})
        item = self.approvals.get(args["approval_id"])
        if item is None:
            return self._reply(False, {"code": "approval_required"})
        if item["digest"] != digest:
            return self._reply(False, {"code": "approval_digest_mismatch"})
        if item["state"] == "used":
            return self._reply(False, {"code": "approval_replayed"})
        if item["state"] != "approved":
            return self._reply(False, {"code": "approval_not_granted"})
        item["state"] = "used"
        if args["authority"] != {"profile": "fake", "signature": "fake"}:
            return self._reply(False, {"code": "profile_signature_invalid"})
        if self.spent + effect["amount_minor"] > self.budget:
            return self._reply(False, {"code": "budget_total_exceeded"})
        self.spent += effect["amount_minor"]
        return self._reply(True, {"authorized": True, "digest": digest, "charge": {"spent_minor": self.spent}})

    def operator_approves(self, approval_id: str) -> None:
        self.approvals[approval_id]["state"] = "approved"


AUTHORITY = {"profile": "fake", "signature": "fake"}
