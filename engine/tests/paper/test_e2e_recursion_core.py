"""Cross-repo paper flow against the real Recursion core (build-order item 9).

Real here: the core's loader, trust store, approval gate, authority ledger, connection
breaker and ``CoreService`` boundary, and Cockpit's signed plugin entry, gateway and paper
book. Stand-ins here: the developer and installer keys (made per test), the operator UI
(a draw callback that records, and two fixed credentials), the clock (fixed at 1000) and
the GIL probe (always reports the GIL off). No provider, venue, network or keyring is
touched.

The suite skips only when ``recursion_core`` or ``recursion_core.plugin_security`` itself
is absent from the path; any other import failure, including a missing ``cryptography``
once the core imports, is an error. Run with both trees on the path, for example::

    PYTHONPATH=engine:/path/to/recursion/core python3 -B -m pytest -q -rs engine/tests

Flow: developer-signed Cockpit plugin loaded by the core's loader -> permission -> run
check on the connection breaker -> operator approval drawn by the core -> installer-signed
authority profile and budgets -> the single paper OrderGateway.

``build_world`` and ``sized_buy`` are shared with the other real-core suites. The host side of
``build_world`` hands the plugin the breaker's own ``check`` as ``run_check`` and ``ALLOWLIST``
as the order universe. Every buy is sized by the composed gateway's ``size_buy``; ``INTENT``
only names the field values a full-size test buy is expected to have.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Callable

from cockpit.paper.core_client import CoreClient, CoreRefused
from cockpit.paper.gateway import Caps, Intent, PaperBook, Refused, create_book

AWAITING_328 = ("awaiting recursion #328 (recursion_core.plugin_security not importable here; "
                "pinned core aa34e9a7 has none)")
SKIPPABLE_MISSING = frozenset({"recursion_core", "recursion_core.plugin_security"})
CORE_SKIP = None

# This skip is removed once Cockpit pins a core that includes recursion #328.
try:
    from recursion_core.plugin_security import (
        ApprovalGate, Authority, CoreHealth, CoreService, Grant, PluginLoader,
        ReservedEffects, TrustStore, canonical, create_ledger,
    )
    from recursion_core.plugin_security.breaker import TYPED_LIMITS, ConnectionBreaker, create_breaker_store
    from recursion_core.plugin_security.errors import Refused as CoreError
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
OTHER_PLUGIN_ID = "csl.other.plugin"
OPERATOR, PLUGIN_TOKEN = "operator-token-fake", "plugin-token-fake"
# The connection is the billed login or API key a run goes through, never an engine or model name.
RUN, CONNECTION, PROVIDER, MODEL_VERSION = "run-1", "conn-a", "anthropic", "claude-sonnet-4-5"
SCOPE = {"account": "paper-acct", "venue": "paper", "contract_family": "unclassified",
         "adapter": "paper-book", "environment": "paper"}
INTENT = Intent(intent_id="e2e-1", account="paper-acct", instrument="AAPL", side="buy", quantity=5,
                limit_price_minor=10_000, run_id=RUN, connection=CONNECTION)
CAPS = (100_000, 50)  # max notional (minor units), max quantity
ALLOWLIST = frozenset({"AAPL"})


class _Key:
    def __init__(self) -> None:
        self._k = Ed25519PrivateKey.generate()
        self.public = self._k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    def sign(self, raw: bytes) -> str:
        return self._k.sign(raw).hex()


class _NoGil:
    """Stand-in GIL probe that always reports the GIL off, so the flow also runs on a GIL build.
    The real probe and the poison path are covered by the core's own tests."""

    def gil_enabled(self) -> bool:
        return False


class World:
    """One daemon's worth of real core objects around the loaded Cockpit plugin.

    ``directory``, ``dev`` and ``installer`` (the keys), ``gateway`` and ``kill_switch`` (what the
    plugin composed), ``loaded``, ``profile``, ``drawn`` (every approval the core drew for the
    operator), ``plugin_ops`` (every op the plugin sent over its core transport),
    ``plugin_transport``, ``breaker`` and ``breaker_path``, ``book`` (the test's own connection to
    the book file the plugin opened), ``core`` (the ``CoreService``), and the host-side calls
    ``host(op, **args)`` and ``decide(approval, token)``. A plain class, not a dataclass: the import-error tests execute
    this module outside ``sys.modules``.
    """

    def __init__(self, **parts: object) -> None:
        self.__dict__.update(parts)


def build_world(case: unittest.TestCase, grant_order: bool = True, *, run_owner: str = PLUGIN_ID,
                venue_kills: Callable[[PaperBook], list] | None = None, prior: World | None = None) -> World:
    """Build the core around the signed plugin. ``prior`` restarts on that world's directory and
    keys: the stores, ledger and book are reopened, never created, and no run is bound again.
    ``venue_kills`` receives the test's book connection and returns the venue kills to compose."""
    if prior is None:
        d = tempfile.mkdtemp(prefix="cockpit-e2e-")
        case.addCleanup(shutil.rmtree, d, True)
        dev, installer = _Key(), _Key()
    else:
        d, dev, installer = prior.directory, prior.dev, prior.installer
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
    drawn: list = []
    gate = ApprovalGate(lambda *a: drawn.append(a),
                        {OPERATOR: "operator_ui", PLUGIN_TOKEN: "plugin"}.get, lambda: 1_000)
    ledger = os.path.join(d, "ledger.db")
    breaker_path = os.path.join(d, "breaker.db")
    book_path = os.path.join(d, "book.db")
    if prior is None:  # the installer's steps; the daemon never creates these
        create_ledger(ledger, installer.public)
        create_breaker_store(breaker_path)
        create_book(book_path, cash_minor={"paper-acct": 10**9})
    authority = Authority(installer.public, ledger, lambda: 1_000)
    case.addCleanup(authority.close)
    breaker = ConnectionBreaker(breaker_path)
    case.addCleanup(breaker.close)
    core = CoreService(loader=loader, gate=gate, breaker=breaker, health=health, authority=authority,
                       effect_permissions={"order": "order"}, authority_required=frozenset({"order"}))
    if prior is None:
        breaker.bind_run(RUN, CONNECTION, run_owner, PROVIDER, MODEL_VERSION)
    book = PaperBook(book_path)
    case.addCleanup(book.close)
    plugin_ops: list = []
    plugin_client = core.client_for(PLUGIN_ID)

    def plugin_transport(raw: bytes) -> bytes:
        plugin_ops.append(json.loads(raw)["op"])
        return plugin_client(raw)
    composed = loaded.module.compose(plugin_transport, book_path, Caps(*CAPS), run_check=breaker.check,
                                     allowlist=ALLOWLIST,
                                     venue_kills=[] if venue_kills is None else venue_kills(book))
    body = {"schema": "csl-authority-profile/vNext", "profile_id": "cockpit-paper-1", "issuer": "installer",
            "plugin_id": PLUGIN_ID, "artifact_sha256": digest, "scope": dict(SCOPE),
            "provider": PROVIDER, "model_version": MODEL_VERSION, "actions": ["order.submit"],
            "permission": "order", "not_before": 0, "expires_at": 10_000,
            "budgets": {"currency": "USD", "max_effect_minor": 100_000, "total_minor": 120_000}}
    profile = {"profile": body, "signature": installer.sign(canonical(body))}
    host_client = core.client_for("host")

    def host(op: str, **args: object) -> dict:
        return json.loads(host_client(canonical({"protocol": "csl-core/v0", "op": op, "args": args})))

    def decide(approval: dict, token: str = OPERATOR) -> dict:
        return host("approval.decide", approval_id=approval["approval_id"], digest=approval["digest"],
                    credential=token, decision="approve")
    return World(directory=d, dev=dev, installer=installer, gateway=composed.gateway,
                 kill_switch=composed.kill_switch, loaded=loaded, profile=profile, drawn=drawn,
                 plugin_ops=plugin_ops, plugin_transport=plugin_transport, breaker=breaker,
                 breaker_path=breaker_path, book=book, core=core, host=host, decide=decide)


def sized_buy(w: World, intent_id: str = INTENT.intent_id, *, quantity: int = INTENT.quantity,
              run_id: str = RUN, connection: str = CONNECTION) -> Intent:
    """A buy of ``quantity`` AAPL at 10_000, sized by the world's gateway from a conviction.

    With ``CAPS`` the notional cap buys 10 shares at full conviction, so 1_000 bps a share.
    """
    intent = w.gateway.size_buy(intent_id=intent_id, account=INTENT.account, instrument=INTENT.instrument,
                                limit_price_minor=INTENT.limit_price_minor, conviction_bps=quantity * 1_000,
                                run_id=run_id, connection=connection)
    expected = replace(INTENT, intent_id=intent_id, quantity=quantity, run_id=run_id, connection=connection)
    if intent != expected:
        raise AssertionError("size_buy returned %r, expected %r" % (intent, expected))
    return intent


@unittest.skipIf(CORE_SKIP is not None, AWAITING_328)
class EndToEndPaperTests(unittest.TestCase):
    def _refused(self, code: str, call: Callable[[], object]) -> None:
        with self.assertRaises(Refused) as ctx:
            call()
        self.assertEqual(ctx.exception.code, code)

    def test_paper_order_through_permission_approval_profile_gateway(self) -> None:
        w = build_world(self)
        gateway, loaded = w.gateway, w.loaded
        self.assertEqual((loaded.trust, loaded.registry_label), ("signed", "non_verified"))
        self.assertEqual(w.breaker.check(RUN, CONNECTION, PLUGIN_ID),
                         {"provider": PROVIDER, "model_version": MODEL_VERSION})
        intent = sized_buy(w)
        approval = gateway.request_approval(intent)
        self.assertEqual(len(w.drawn), 1)
        self.assertEqual(w.decide(approval, PLUGIN_TOKEN)["refusal"]["code"], "approval_requires_operator")
        self.assertTrue(w.decide(approval)["ok"])
        out = gateway.submit(intent, approval["approval_id"], w.profile)
        self.assertEqual((out["status"], out["digest"]), ("paper_working", approval["digest"]))
        self._refused("intent_duplicate",  # refused before the core
                      lambda: gateway.submit(intent, approval["approval_id"], w.profile))
        self.assertEqual(len(w.book.orders()), 1)
        self.assertEqual(w.plugin_ops, ["effect.request", "effect.authorize"])

    def test_split_orders_over_profile_budget_refused(self) -> None:
        w = build_world(self)
        codes = []
        for i in range(4):  # 50_000 each against a 120_000 total
            intent = sized_buy(w, "split-%d" % i)
            approval = w.gateway.request_approval(intent)
            w.decide(approval)
            try:
                codes.append(w.gateway.submit(intent, approval["approval_id"], w.profile)["status"])
            except Refused as exc:
                codes.append(exc.code)
        self.assertEqual(codes, ["paper_working", "paper_working", "core:budget_total_exceeded",
                                 "core:budget_total_exceeded"])

    def test_plugin_without_order_grant_and_tripped_connection(self) -> None:
        w = build_world(self, grant_order=False)
        self.assertNotIn("order", w.loaded.permissions)
        self._refused("core:permission_required", lambda: w.gateway.request_approval(sized_buy(w)))
        w = build_world(self)
        intent = sized_buy(w)
        approval = w.gateway.request_approval(intent)
        w.decide(approval)
        # The core's own timeouts and a caller's cancel never trip the connection.
        for event in ("core_timeout", "core_deadline", "caller_cancelled"):
            self.assertIs(w.breaker.report(CONNECTION, event), False)
        self.assertFalse(w.breaker.tripped(CONNECTION))
        with self.assertRaises(CoreError) as ctx:
            w.breaker.report(CONNECTION, "engine_looks_slow")
        self.assertEqual(ctx.exception.code, "untyped_breaker_event")
        self.assertIn("plan_limit", TYPED_LIMITS)
        self.assertIs(w.breaker.report(CONNECTION, "plan_limit"), True)
        self._refused("core:connection_tripped",
                      lambda: w.gateway.submit(intent, approval["approval_id"], w.profile))
        self.assertEqual(w.book.orders(), [])
        self._refused("core:connection_tripped", lambda: w.gateway.request_approval(sized_buy(w, "e2e-2")))
        self.assertEqual(len(w.drawn), 1)
        # The trip clears only through the leased probe; the unused approval then still books.
        lease = w.breaker.begin_probe(CONNECTION)
        self.assertIs(w.breaker.reset_after_probe(CONNECTION, lease, False), False)
        self.assertTrue(w.breaker.tripped(CONNECTION))
        lease = w.breaker.begin_probe(CONNECTION)
        self.assertIs(w.breaker.reset_after_probe(CONNECTION, lease, True), True)
        self.assertIs(w.breaker.reset_after_probe(CONNECTION, lease, True), False)  # one use
        out = w.gateway.submit(intent, approval["approval_id"], w.profile)
        self.assertEqual(out["status"], "paper_working")

    def test_run_bound_to_another_plugin_is_refused_with_no_approval_drawn(self) -> None:
        w = build_world(self, run_owner=OTHER_PLUGIN_ID)
        intent = sized_buy(w)
        self._refused("core:run_not_owned", lambda: w.gateway.request_approval(intent))
        self._refused("core:run_not_owned", lambda: w.gateway.submit(intent, "apr-none", w.profile))
        self.assertEqual((w.drawn, w.book.orders()), ([], []))
        # The binding is immutable: the host cannot move the run to this plugin or connection.
        for args, code in (((RUN, CONNECTION, PLUGIN_ID, PROVIDER, MODEL_VERSION), "run_binding_mismatch"),
                           ((RUN, "conn-b", OTHER_PLUGIN_ID, PROVIDER, MODEL_VERSION), "run_connection_mismatch"),
                           ((RUN, "conn a", OTHER_PLUGIN_ID, PROVIDER, MODEL_VERSION), "invalid_connection")):
            with self.subTest(code=code):
                with self.assertRaises(CoreError) as ctx:
                    w.breaker.bind_run(*args)
                self.assertEqual(ctx.exception.code, code)
        w.breaker.bind_run(RUN, CONNECTION, OTHER_PLUGIN_ID, PROVIDER, MODEL_VERSION)  # same values: no-op
        self._refused("core:run_not_owned", lambda: w.gateway.request_approval(intent))
        # A run nobody bound, and this plugin's own run cited on another connection.
        self._refused("core:run_unknown", lambda: w.gateway.request_approval(sized_buy(w, run_id="run-9")))
        w.breaker.bind_run("run-2", CONNECTION, PLUGIN_ID, PROVIDER, MODEL_VERSION)
        self._refused("core:run_connection_mismatch",
                      lambda: w.gateway.request_approval(sized_buy(w, run_id="run-2", connection="conn-b")))
        self.assertEqual((w.drawn, w.book.orders()), ([], []))
        self.assertTrue(w.gateway.request_approval(sized_buy(w, run_id="run-2"))["approval_id"])
        self.assertEqual(len(w.drawn), 1)

    def test_reopened_breaker_store_still_refuses_a_faulted_run(self) -> None:
        w = build_world(self)
        w.breaker.bind_run("run-2", CONNECTION, PLUGIN_ID, PROVIDER, MODEL_VERSION)
        intent = sized_buy(w)
        approval = w.gateway.request_approval(intent)
        w.decide(approval)
        self.assertEqual(w.host("run.fault", run_id=RUN), {"ok": True, "protocol": "csl-core/v0", "result": {}})
        self._refused("core:run_faulted", lambda: w.gateway.submit(intent, approval["approval_id"], w.profile))
        w.breaker.close()
        # Restart: a new breaker on the same store, a new loader, gate and service. Nothing is bound again.
        w2 = build_world(self, prior=w)
        self.assertIsNot(w2.breaker, w.breaker)
        self.assertEqual(w2.breaker_path, w.breaker_path)
        with self.assertRaises(CoreError) as ctx:
            w2.breaker.check(RUN, CONNECTION, PLUGIN_ID)
        self.assertEqual(ctx.exception.code, "run_faulted")
        self._refused("core:run_faulted", lambda: w2.gateway.request_approval(sized_buy(w2)))
        with self.assertRaises(CoreRefused) as raw:  # at the core itself, not only through the gateway
            CoreClient(w2.plugin_transport).request_approval(w.gateway.risk_pass(intent))
        self.assertEqual(raw.exception.code, "run_faulted")
        self.assertEqual((w2.drawn, w2.book.orders()), ([], []))
        # The other run's binding persisted too, so only the faulted run is refused.
        healthy = sized_buy(w2, "e2e-2", run_id="run-2")
        approval = w2.gateway.request_approval(healthy)
        self.assertTrue(w2.decide(approval)["ok"])
        self.assertEqual(w2.gateway.submit(healthy, approval["approval_id"], w2.profile)["status"], "paper_working")
        self.assertEqual(w2.book.orders(), [("e2e-2", 1, "working")])


if __name__ == "__main__":
    unittest.main()
