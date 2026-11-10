"""Cross-repo paper flow against the real Recursion core (build-order item 9). Fakes for UI only.

Skipped only when ``recursion_core`` or ``recursion_core.plugin_security`` itself is absent
(awaiting recursion #328); any other import failure, including a missing ``cryptography``
once the core imports, is an error. Run with both trees on the path, for example::

    PYTHONPATH=engine:/path/to/recursion/core python3 -B -m unittest \\
        discover -s engine/tests/paper -t engine -v

Flow: developer-signed Cockpit plugin loaded by the core's loader -> permission ->
operator approval drawn by the core -> installer-signed authority profile and caps ->
the single paper OrderGateway. No provider, venue, network or keyring is touched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cockpit.paper.gateway import Caps, Intent, PaperBook, Refused, create_book

AWAITING_328 = ("awaiting recursion #328 (recursion_core.plugin_security not importable here; "
                "pinned core aa34e9a7 has none)")
SKIPPABLE_MISSING = frozenset({"recursion_core", "recursion_core.plugin_security"})
CORE_SKIP = None

# This skip is removed once Cockpit pins a core that includes recursion #328.
try:
    from recursion_core.plugin_security import (
        ApprovalGate, Authority, ConnectionBreaker, CoreHealth, CoreService, Grant, PluginLoader,
        ReservedEffects, TrustStore, canonical, create_ledger,
    )
    from recursion_core.plugin_security.signing import key_id
except ModuleNotFoundError as exc:
    if exc.name not in SKIPPABLE_MISSING:
        raise
    CORE_SKIP = AWAITING_328
else:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PLUGIN_SOURCE = (Path(__file__).resolve().parents[2] / "cockpit" / "paper").joinpath("plugin.py").read_bytes()
PLUGIN_ID = "csl.cockpit.paper"
OPERATOR, PLUGIN_TOKEN = "operator-token-fake", "plugin-token-fake"
INTENT = Intent(intent_id="e2e-1", account="paper-acct", instrument="AAPL", side="buy", quantity=5,
                limit_price_minor=10_000, run_id="run-1", connection="conn-a")


class _Key:
    def __init__(self) -> None:
        self._k = Ed25519PrivateKey.generate()
        self.public = self._k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    def sign(self, raw: bytes) -> str:
        return self._k.sign(raw).hex()


@unittest.skipIf(CORE_SKIP is not None, AWAITING_328)
class EndToEndPaperTests(unittest.TestCase):
    def _world(self, grant_order: bool = True):
        d = tempfile.mkdtemp(prefix="cockpit-e2e-")
        self.addCleanup(shutil.rmtree, d, True)
        dev, installer = _Key(), _Key()
        digest = hashlib.sha256(PLUGIN_SOURCE).hexdigest()
        manifest = canonical({
            "schema": "csl-plugin-manifest/vNext", "plugin_id": PLUGIN_ID, "version": "0.1.0",
            "artifact_sha256": digest, "dependency_lock_sha256": None, "install_scripts": [],
            "developer_key": dev.public, "permissions": ["read", "propose", "order"],
            "network_destinations": [], "credential_refs": [],
        })
        perms = ("read", "propose", "order") if grant_order else ("read", "propose")
        health = CoreHealth()
        loader = PluginLoader(TrustStore({"csl-dev": dev.public}),
                              {PLUGIN_ID: Grant(PLUGIN_ID, digest, key_id(dev.public), perms)},
                              ReservedEffects(frozenset({"kalshi.com", "elections.kalshi.com"}), ("venue:",)),
                              health, _NoGil())
        loaded = loader.load(manifest, dev.sign(manifest), PLUGIN_SOURCE, registry_label="non_verified")
        drawn = []
        gate = ApprovalGate(lambda *a: drawn.append(a),
                            {OPERATOR: "operator_ui", PLUGIN_TOKEN: "plugin"}.get, lambda: 1_000)
        ledger = os.path.join(d, "ledger.db")
        create_ledger(ledger, installer.public)
        authority = Authority(installer.public, ledger, lambda: 1_000)
        self.addCleanup(authority.close)
        breaker = ConnectionBreaker()
        core = CoreService(loader=loader, gate=gate, breaker=breaker, health=health, authority=authority,
                           effect_permissions={"order": "order"}, authority_required=frozenset({"order"}))
        breaker.bind_run("run-1", "conn-a")
        book_path = os.path.join(d, "book.db")
        create_book(book_path, cash_minor={"paper-acct": 10**9})
        gateway = loaded.module.build(core.client_for(PLUGIN_ID), book_path, Caps(100_000, 50))
        body = {"schema": "csl-authority-profile/vNext", "profile_id": "cockpit-paper-1", "issuer": "installer",
                "plugin_id": PLUGIN_ID, "artifact_sha256": digest,
                "scope": {"account": "paper-acct", "venue": "paper"}, "permission": "order",
                "not_before": 0, "expires_at": 10_000,
                "budgets": {"currency": "USD", "max_effect_minor": 100_000, "total_minor": 120_000}}
        profile = {"profile": body, "signature": installer.sign(canonical(body))}
        host = core.client_for("host")

        def decide(approval: dict, token: str = OPERATOR) -> dict:
            import json
            return json.loads(host(canonical({"protocol": "csl-core/v0", "op": "approval.decide", "args": {
                "approval_id": approval["approval_id"], "digest": approval["digest"], "credential": token,
                "decision": "approve"}})))
        return gateway, profile, decide, drawn, breaker, PaperBook(book_path), loaded

    def test_paper_order_through_permission_approval_profile_gateway(self) -> None:
        gateway, profile, decide, drawn, _, book, loaded = self._world()
        self.addCleanup(book.close)
        self.assertEqual((loaded.trust, loaded.registry_label), ("signed", "non_verified"))
        approval = gateway.request_approval(INTENT)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(decide(approval, PLUGIN_TOKEN)["refusal"]["code"], "approval_requires_operator")
        self.assertTrue(decide(approval)["ok"])
        out = gateway.submit(INTENT, approval["approval_id"], profile)
        self.assertEqual((out["status"], out["digest"]), ("paper_working", approval["digest"]))
        with self.assertRaises(Refused) as ctx:
            gateway.submit(INTENT, approval["approval_id"], profile)
        self.assertEqual(ctx.exception.code, "intent_duplicate")  # refused before the core
        self.assertEqual(len(book.orders()), 1)

    def test_split_orders_over_profile_budget_refused(self) -> None:
        gateway, profile, decide, _, _, book, _ = self._world()
        self.addCleanup(book.close)
        codes = []
        for i in range(4):  # 50_000 each against a 120_000 total
            intent = replace(INTENT, intent_id="split-%d" % i)
            approval = gateway.request_approval(intent)
            decide(approval)
            try:
                codes.append(gateway.submit(intent, approval["approval_id"], profile)["status"])
            except Refused as exc:
                codes.append(exc.code)
        self.assertEqual(codes, ["paper_working", "paper_working", "core:budget_total_exceeded",
                                 "core:budget_total_exceeded"])

    def test_plugin_without_order_grant_and_tripped_connection(self) -> None:
        gateway, *_ , book, loaded = self._world(grant_order=False)
        self.addCleanup(book.close)
        self.assertNotIn("order", loaded.permissions)
        with self.assertRaises(Refused) as ctx:
            gateway.request_approval(INTENT)
        self.assertEqual(ctx.exception.code, "core:permission_required")
        gateway, profile, decide, _, breaker, book2, _ = self._world()
        self.addCleanup(book2.close)
        approval = gateway.request_approval(INTENT)
        decide(approval)
        breaker.report("conn-a", "plan_limit")
        with self.assertRaises(Refused) as ctx:
            gateway.submit(INTENT, approval["approval_id"], profile)
        self.assertEqual(ctx.exception.code, "core:connection_tripped")
        self.assertEqual(book2.orders(), [])


class _NoGil:
    """SIMULATED GIL-off probe: the suite runs on a GIL build; real checks live in the core's tests."""

    def gil_enabled(self) -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
