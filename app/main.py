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


def compose(*, db_url: str = "sqlite:///cockpit.db", parquet_root: str = "data/raw"):
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
    services = build_services(state, parquet_root=parquet_root)
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
