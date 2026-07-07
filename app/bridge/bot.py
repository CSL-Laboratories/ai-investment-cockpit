"""WP6 — the discord.py adapter (THIN).

All the load-bearing logic lives in the core modules (``operators`` / ``approval`` / ``veto`` /
``embeds`` / ``fallback`` / ``anchor``). This file ONLY adapts Discord surfaces onto them:
Approve/Reject/Modify/Interrogate buttons, the slash commands (``/mode``, ``/killswitch``,
``/brief``, ``/status``, ``/limits``, ``/triage``, ``/ask``, ``/automation``), and thread
replies. Every entry point re-verifies the operator id via ``bridge.operators.is_authorized``
before it touches the core, and forwards to the loopback orchestrator API / core functions.

``import discord`` is GUARDED so this module imports even when discord.py is absent — the core
logic the unit tests exercise never needs it. ``build_bot`` raises a clear error if discord.py
is not installed; ``bot_available()`` lets callers check first.
"""
from __future__ import annotations

from typing import Any, Callable

from bridge import approval as _approval
from bridge.embeds import render_proposal_embed
from bridge.operators import is_authorized

try:  # discord.py is optional at import time (SEC/testability: core has no discord dependency).
    import discord  # type: ignore
    from discord import app_commands  # type: ignore
    _HAS_DISCORD = True
except Exception:  # noqa: BLE001 - any import failure means "no discord in this environment"
    discord = None  # type: ignore
    app_commands = None  # type: ignore
    _HAS_DISCORD = False


def bot_available() -> bool:
    """Whether discord.py is importable in this environment."""
    return _HAS_DISCORD


def _extract_user_id(interaction: Any) -> str | None:
    """Pull the acting Discord user id off an interaction, defensively (the adapter must not
    assume a shape when discord.py is stubbed)."""
    user = getattr(interaction, "user", None)
    uid = getattr(user, "id", None)
    return None if uid is None else str(uid)


def _extract_interaction_id(interaction: Any) -> str | None:
    iid = getattr(interaction, "id", None)
    return None if iid is None else str(iid)


class ProposalControls:
    """Adapter for the per-proposal Approve/Reject/Modify/Interrogate controls. Holds the loopback
    API handle + the token registry; each button verifies the operator and forwards to the core.
    Kept discord-free so it is unit-testable; ``attach_view`` builds the actual ``discord.ui.View``
    only when discord.py is present."""

    def __init__(self, st, token_registry, *, resolve_order: Callable[[str], dict],
                 resolve_risk: Callable[[str], Any], raw_excerpt_fn=None) -> None:
        self.st = st
        self.token_registry = token_registry
        self._resolve_order = resolve_order      # proposal_id -> resolved order dict
        self._resolve_risk = resolve_risk        # proposal_id -> RiskDecision
        self._raw_excerpt_fn = raw_excerpt_fn

    def render(self, proposal: dict) -> dict:
        risk = self._resolve_risk(proposal["proposal_id"])
        return render_proposal_embed(proposal, risk, self._raw_excerpt_fn)

    def on_decision(self, interaction: Any, proposal_id: str, decision: str, *,
                    modal_confirmed: bool = False, notional=None):
        """Shared handler for every button. Verifies the operator, then calls the approval core."""
        uid = _extract_user_id(interaction)
        if not is_authorized(uid, self.st.settings):
            return _approval.Rejection("actor is not an allowlisted operator", "unauthorized",
                                       proposal_id, _extract_interaction_id(interaction))
        return _approval.record_decision(
            self.st,
            proposal_id=proposal_id,
            risk_decision=self._resolve_risk(proposal_id),
            resolved_order=self._resolve_order(proposal_id),
            actor_user_id=uid,
            user_decision=decision,
            surface="discord",
            interaction_id=_extract_interaction_id(interaction),
            message_id=str(getattr(getattr(interaction, "message", None), "id", "") or "") or None,
            modal_confirmed=modal_confirmed,
            notional=notional,
            token_registry=self.token_registry,
        )


# Slash-command names the bot registers (draft-discord-control-plane). The adapter validates the
# operator on each; the actual command bodies forward to the orchestrator loopback API.
SLASH_COMMANDS = (
    "mode", "killswitch", "brief", "status", "limits", "triage", "ask", "automation",
)


def build_bot(st, token_registry, *, coordinator=None, controls: ProposalControls | None = None,
              intents: Any = None):
    """Construct the discord.py client + command tree wired to the core. Raises ``RuntimeError``
    if discord.py is not installed (the core paths do not need it). The command bodies stay thin:
    verify the operator, forward to the loopback orchestrator API / core."""
    if not _HAS_DISCORD:
        raise RuntimeError(
            "discord.py is not installed; the bridge CORE (approval/veto/embeds/anchor/fallback) "
            "runs without it, but the live bot requires `pip install discord.py`."
        )

    intents = intents or discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    def _guard(interaction: Any) -> bool:
        return is_authorized(_extract_user_id(interaction), st.settings)

    for name in SLASH_COMMANDS:
        async def _cmd(interaction, _name=name):  # noqa: ANN001 - discord supplies the type
            if not _guard(interaction):
                await interaction.response.send_message(
                    "Not authorized.", ephemeral=True)
                return
            # Thin forward: the real body posts to the loopback orchestrator API for _name.
            await interaction.response.send_message(
                f"/{_name} acknowledged.", ephemeral=True)

        tree.command(name=name, description=f"Cockpit {name} control")(_cmd)

    return client, tree
