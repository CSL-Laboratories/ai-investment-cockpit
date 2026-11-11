"""Test double for the Recursion core's ``csl-core/v0`` boundary. Not the core.

It keeps only what the gateway relies on: permission, the run and connection checks of the
core's ``ConnectionBreaker`` (recursion ``core/recursion_core/plugin_security/breaker.py``),
a one-use operator approval bound to the exact effect bytes, and a total budget. The real
core is exercised by ``test_e2e_recursion_core.py`` when it is importable.

Run binding mirrors the core: a run must be bound to the connection it is billed through
(host op ``run.bind``) before any effect from it is accepted, and a run cannot move
connections or change its owning plugin. In the order the core checks them: an unbound run
is ``run_unknown``, a run owned by a plugin other than the effect's ``plugin_id`` (which
stands for the authenticated caller) is ``run_not_owned``, a run sent on a connection other
than its bound one is ``run_connection_mismatch``, then ``run_faulted``, then
``connection_tripped``. ``DEFAULT_RUNS`` pre-binds the run ids the shared fixtures use, owned
by ``DEFAULT_PLUGIN``.

``FakeCore.check`` is the double of ``ConnectionBreaker.check(run_id, connection, plugin_id)``,
the callable a host hands the gateway as ``run_check``. It makes the same refusals in the same
order, raised as ``FakeRefused`` with the core's ``code``, and the transport uses it too.

``ALLOWLIST`` is the instrument allowlist the fake-core suites build their gateways with.
``sized`` is a test seam, not a product path: it records a hand-built buy in a gateway's private
sizing memory, standing for a ``size_buy`` that returned exactly that intent, so suites about
other behaviour keep their fixed quantities. The sizing rule itself is tested through the real
``size_buy`` in ``test_sizing_allowlist.py``, and the real-core suites size every buy with it.
"""

from __future__ import annotations

import hashlib
import json

from cockpit.paper.core_client import canonical
from cockpit.paper.gateway import Intent, OrderGateway


DEFAULT_RUNS = {"run-1": "conn-a", "run-ok": "conn-ok"}
DEFAULT_PLUGIN = "csl.cockpit.paper"
ALLOWLIST = frozenset({"AAPL", "MSFT", "XYZ"})
PROVIDER, MODEL_VERSION = "provider-fake", "model-fake"


class FakeRefused(Exception):
    """Stands for the core's ``Refused``: a typed ``code`` and nothing else."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sized(gateway: OrderGateway, intent: Intent) -> Intent:
    """Test seam (see the module doc): mark a hand-built buy as sized by ``gateway``."""
    if type(intent) is Intent and intent.side == "buy":
        gateway._remember_sized(intent)
    return intent


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
        self.run_owner: dict[str, str] = dict.fromkeys(self.run_connection, DEFAULT_PLUGIN)

    def bind_run(self, run_id: str, connection: str, plugin_id: str = DEFAULT_PLUGIN,
                 provider: str | None = None, model_version: str | None = None) -> None:
        """The core's ``run.bind``: record a run's connection and owning plugin.

        A run cannot change owner or move connections. ``provider`` and ``model_version`` are
        accepted for the core's signature and not checked.
        """
        if self.run_owner.get(run_id, plugin_id) != plugin_id:
            raise ValueError("run_not_owned")
        if self.run_connection.get(run_id, connection) != connection:
            raise ValueError("run_connection_mismatch")
        self.run_connection[run_id] = connection
        self.run_owner[run_id] = plugin_id

    def check(self, run_id: str, connection: str, plugin_id: str) -> dict:
        """The core breaker's ``check``: same refusals, same order, then the run's provider and model."""
        bound = self.run_connection.get(run_id)
        if bound is None:
            raise FakeRefused("run_unknown")
        if self.run_owner.get(run_id, DEFAULT_PLUGIN) != plugin_id:
            raise FakeRefused("run_not_owned")
        if bound != connection:
            raise FakeRefused("run_connection_mismatch")
        if run_id in self.faulted_runs:
            raise FakeRefused("run_faulted")
        if self.tripped or connection in self.tripped_connections:
            raise FakeRefused("connection_tripped")
        return {"provider": PROVIDER, "model_version": MODEL_VERSION}

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
        try:
            self.check(effect["run_id"], effect["connection"], effect["plugin_id"])
        except FakeRefused as exc:
            return self._reply(False, {"code": exc.code})
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
