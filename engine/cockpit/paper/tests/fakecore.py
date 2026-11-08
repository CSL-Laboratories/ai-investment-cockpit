"""Test double for the Recursion core's ``csl-core/v0`` boundary. Not the core.

It keeps only what the gateway relies on: permission, a one-use operator approval bound
to the exact effect bytes, and a total budget. The real core is exercised by
``test_e2e_recursion_core.py`` when it is importable.
"""

from __future__ import annotations

import hashlib
import json

from cockpit.paper.core_client import canonical


class FakeCore:
    def __init__(self, permissions=("read", "propose", "order"), budget_minor=10_000):
        self.permissions = set(permissions)
        self.budget = budget_minor
        self.spent = 0
        self.approvals: dict[str, dict] = {}
        self.calls: list[str] = []
        self.tripped = False
        self.faulted_runs: set[str] = set()
        self.tripped_connections: set[str] = set()

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
