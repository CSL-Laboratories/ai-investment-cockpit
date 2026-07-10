"""WP8 — the composition root (contract Owner B 'main.py').

Implements docs/build-scaffold-ultracode.md WP8 (production entrypoint: nothing before WP8
wired ``create_app``/``build_scheduler`` outside tests) and SEC-05/Inv 4 (the audit-chain
HMAC key lives ONLY in keyring — get-or-create via ``core.secrets``; it never appears in
env, config, prompts, or logs). ``compose()`` builds the engine/session factory,
``OrchestratorState``, the WP8 service registry, and the FastAPI app; ``run()``
additionally starts the APScheduler jobs and serves uvicorn (loopback-only). ``run()`` is
NOT under test; ``compose()`` gets a smoke test.
"""
from __future__ import annotations

_HMAC_SECRET_NAME = "audit-hmac-key"
_DISCORD_TOKEN_NAME = "discord-bot-token"   # keyring-only (Inv 4); enables the #audit-log witness


def _hmac_key() -> bytes:
    """Get-or-create the audit-chain HMAC key in keyring (Inv 4: never in env/files)."""
    import secrets as _stdlib_secrets

    from core.secrets import get_secret, set_secret

    existing = get_secret(_HMAC_SECRET_NAME)
    if existing is None:
        set_secret(_HMAC_SECRET_NAME, _stdlib_secrets.token_hex(32))
        existing = get_secret(_HMAC_SECRET_NAME)
    return existing.reveal().encode("utf-8")


def _build_anchor_store(settings):
    """SEC-11 (W10, hardened by the G10 red-team 2026-07-08): the durable audit witness. ALWAYS
    a non-None ``GitAuditLogAnchorStore`` — NEVER ``FileAnchorStore``. But the store's TRUST read
    (``latest()``, which the gate/mode/gateway/budget consult) counts ONLY genuinely OUT-OF-DOMAIN
    backends: the ``GitCommitWitness`` (git head file) is a same-trust-domain MIRROR + reviewed-
    history detection layer and does NOT satisfy the auto/live prerequisite on its own (its
    runtime read is a local file a keyless writer can overwrite). The Discord ``#audit-log``
    witness (server-stamped, out-of-domain) is the trust source; it joins once the bot token is in
    keyring AND a channel is configured. Until then ``latest()`` is None and every consult FAILS
    CLOSED — auto unreachable and the live/budget paths refuse. That is the correct posture: a
    genuine out-of-domain witness is the prose prerequisite (bot creds) SEC-11's closure ACTIVATES
    on; a git-only deployment is fail-closed, never falsely 'anchored'."""
    from bridge.anchor import GitAuditLogAnchorStore
    from bridge.witness import GitCommitWitness, discord_rest_witness
    from core.secrets import get_secret

    adapters = [GitCommitWitness(settings.audit_head_file)]   # mirror + reviewed-history detection
    token = get_secret(_DISCORD_TOKEN_NAME)
    if token is not None and settings.audit_log_channel_id is not None:
        adapters.append(discord_rest_witness(settings.audit_log_channel_id, token.reveal()))
    return GitAuditLogAnchorStore(*adapters)


<<<<<<< Updated upstream
def compose(*, db_url: str = "sqlite:///cockpit.db", parquet_root: str = "data/raw"):
=======
def run_startup_security_checks(settings):
    """W11 (SEC-05 / SEC-06) — fail-closed startup checks invoked by ``compose()`` BEFORE the app
    graph is built: (1) the decision-agent account provably cannot read the bearer stores
    (``account_guard``), and (2) every canonical decision-stage job resolves to a tool/MCP-STARVED
    argv (``tool_inspector``). RAISES (``CredentialGuardError`` / ``ToolStarvationError``) on any
    violation — the cockpit refuses to serve a decision/order path it cannot vouch for. Returns a
    summary for observability. In dev (no low-priv account configured) the credential control is
    reported INACTIVE, not passed — and a WARNING is logged, because a live/auto deployment MUST
    provision it (the ACL check + spawn-under-account is only ACTIVE once ``decision_agent_account``
    is set and the account-launching transport is wired; the runner fails closed otherwise)."""
    import logging

    from agent_runner import account_guard, tool_inspector

    cred = account_guard.assert_credential_starved(settings)
    tool_inspector.assert_all_decision_specs_tool_starved(
        tool_inspector.default_decision_stage_specs()
    )
    if not cred.enforced:
        logging.getLogger("cockpit.security").warning(
            "SEC-05 credential starvation INACTIVE: no decision_agent_account configured; decision "
            "agents run under the orchestrator token. Provision the low-priv account before any "
            "live/auto order path (docs/setup/low-priv-agent-account.md)."
        )
    return {"credential_guard_enforced": cred.enforced, "decision_tools_starved": True}


def build_decision_transport(settings):
    """SEC-05 — the transport decision-stage agents spawn through. When a low-priv account is
    configured, an account-launching transport (so the child runs under it, not the orchestrator);
    else the default (dev). The runner independently fails closed if a configured account is not
    applied at the spawn, so a mis-wired transport can never run a decision job privileged."""
    from agent_runner.transport import LowPrivSubprocessTransport, SubprocessTransport

    account = getattr(settings, "decision_agent_account", None)
    return LowPrivSubprocessTransport(account) if account else SubprocessTransport()


def _startup_reconcile(state, *, broker_order_lookup=None):
    """W12 (SEC-13): run the crash-safety reconcile BEFORE the scheduler/API start. An
    unresolved intent or a completeness gap is ambiguous startup state — fail closed by
    dropping to ``review_live`` (never a silent auto-mode survival across a crash), exactly
    like the daily disarm / breaker-trip posture elsewhere in the ladder. The reconcile
    itself already wrote the ``#system`` alert(s) into the audit chain; ``result.alerts``
    is returned for the caller (``run()``) to also surface live."""
    from orchestrator.mode_ladder import ModeLadder
    from orchestrator.reconcile import reconcile_startup

    result = reconcile_startup(state, broker_order_lookup=broker_order_lookup)
    if not result.ok:
        ModeLadder(state).drop_to_review_live(
            "startup reconcile: unresolved intent(s) or completeness gap",
            actor="system", kind="reconcile_gap",
        )
    return result


def compose(*, db_url: str = "sqlite:///cockpit.db", parquet_root: str = "data/raw",
           broker_order_lookup=None):
>>>>>>> Stashed changes
    """Build the full application graph: ``(state, app, services)``."""
    from config.settings import get_settings
    from orchestrator.app import create_app
    from orchestrator.state import OrchestratorState
    from storage.engine import create_all, make_engine, make_session_factory
    from workflows.services import build_services

    settings = get_settings()
    engine = make_engine(db_url)
    create_all(engine)
    session_factory = make_session_factory(engine)
    state = OrchestratorState.build(session_factory, hmac_key=_hmac_key(), settings=settings,
                                    anchor_store=_build_anchor_store(settings))
<<<<<<< Updated upstream
    services = build_services(state, parquet_root=parquet_root)
=======
    _startup_reconcile(state, broker_order_lookup=broker_order_lookup)
    # SEC-05: decision-stage agents spawn under the low-priv account when one is configured.
    services = build_services(state, transport=build_decision_transport(settings),
                              parquet_root=parquet_root)
>>>>>>> Stashed changes
    app = create_app(state, services=services)
    return state, app, services


def run() -> None:  # pragma: no cover - production entrypoint, not under test
    """Start the cockpit: scheduler jobs + the loopback-only API."""
    from orchestrator.app import serve
    from orchestrator.scheduler import build_scheduler

    state, app, services = compose()
    build_scheduler(state, services=services).start()
    serve(app, state.settings)


if __name__ == "__main__":  # pragma: no cover
    run()
