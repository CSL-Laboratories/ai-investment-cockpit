"""WP6 — the discord.py adapter (THIN).

All the load-bearing logic lives in the core modules (``operators`` / ``approval`` / ``veto`` /
``embeds`` / ``fallback`` / ``anchor``). This file ONLY adapts Discord surfaces onto them:
Approve/Reject/Modify/Interrogate buttons, the slash commands (``/mode``, ``/killswitch``,
``/brief``, ``/status``, ``/limits``, ``/triage``, ``/ask``, ``/paper``, ``/automation``), and thread
replies. Every entry point re-verifies the operator id via ``bridge.operators.is_authorized``
before it touches the core, and forwards to the loopback orchestrator API / core functions.

``import discord`` is GUARDED so this module imports even when discord.py is absent — the core
logic the unit tests exercise never needs it. ``build_bot`` raises a clear error if discord.py
is not installed; ``bot_available()`` lets callers check first.
"""
from __future__ import annotations

import asyncio
import json
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


class QuantCommands:
    """Discord-free core for ``/quant`` (QT-09). Same shape as ``ProposalControls``.

    Calls the pure functions in ``orchestrator.quant_api`` DIRECTLY rather than posting to
    the loopback API. That is not a shortcut around the key gate — it is the pattern the
    bridge already uses for the paths that do real work (``ProposalControls`` holds ``st``
    and calls the approval core the same way). The API key protects the network surface; a
    caller already inside this process has whatever the process has, and adding an HTTP hop
    to talk to ourselves would buy no isolation while adding a failure mode during exactly
    the incident when the operator most needs the command to work.

    What DOES gate this: ``is_authorized`` on every entry point, and the fact that the
    functions being called cannot mutate anything. The surface is read-only and
    simulation-only by construction, so the operator check is about noise, not authority.
    """

    UNAUTHORIZED = "Not authorized."

    def __init__(self, st) -> None:
        self.st = st

    def handle(self, interaction: Any, *, action: str = "status", strategy: str = "market_maker",
               events: int | None = None, seed: int = 3,
               requote_threshold_ticks: int = 2) -> str:
        """Route one ``/quant`` invocation to a rendered message. Never raises.

        A command that raises inside a Discord handler surfaces to the operator as silence
        (the interaction times out), which during an incident is indistinguishable from a
        wedged engine. So every failure path returns TEXT explaining itself.
        """
        from fastapi import HTTPException

        from bridge.quant_view import render_status, render_tearsheet
        from orchestrator.quant_api import (
            DEFAULT_SIM_EVENTS,
            SimRequest,
            quant_status,
            run_sim,
        )

        if not is_authorized(_extract_user_id(interaction), self.st.settings):
            return self.UNAUTHORIZED

        try:
            if action == "status":
                return render_status(quant_status(self.st))
            if action == "sim":
                body = SimRequest(
                    strategy=strategy,
                    events=DEFAULT_SIM_EVENTS if events is None else events,
                    seed=seed, requote_threshold_ticks=requote_threshold_ticks,
                )
                return render_tearsheet(run_sim(body))
            return f"Unknown /quant action {action!r}. Expected `status` or `sim`."
        except HTTPException as exc:
            # The 422s from run_sim are OPERATOR INPUT errors (unknown strategy, event count
            # over the cap). They carry an explanatory detail written for exactly this
            # moment, so it is surfaced verbatim rather than replaced with a generic failure.
            return f"⚠ {exc.detail}"
        except Exception as exc:                       # noqa: BLE001 - a handler must not die
            return f"⚠ /quant {action} failed: {type(exc).__name__}: {exc}"


class QuestionCommands:
    """Authenticated Discord adapter for SEC-18 fresh-context interrogation."""

    def __init__(self, st, ask=None) -> None:
        self.st = st
        self.ask = ask

    def handle(self, interaction: Any, *, question: str, proposal_id: str) -> str:
        if not is_authorized(_extract_user_id(interaction), self.st.settings):
            return "Not authorized."
        if self.ask is None:
            return "Fresh interrogation is not wired."
        try:
            result = self.ask(
                self.st,
                question=str(question),
                proposal_id=str(proposal_id),
            )
            return "```json\n" + json.dumps(result, default=str, indent=2)[:1800] + "\n```"
        except Exception as exc:  # noqa: BLE001 - visible failure, sanitized for Discord
            return f"Fresh interrogation failed: {type(exc).__name__}"


class PaperCommands:
    """Discord-free core for the W-22 human-gated paper decision loop.

    This calls the same ``PaperExecutionService`` as the loopback API; broker access therefore
    remains exclusively behind ``OrderGateway``.
    """

    UNAUTHORIZED = "Not authorized."

    def __init__(self, st, paper=None) -> None:
        self.st = st
        self.paper = paper

    def handle(self, interaction: Any, *, action: str, order_id: str,
               confirm_symbol: str = "") -> str:
        if not is_authorized(_extract_user_id(interaction), self.st.settings):
            return self.UNAUTHORIZED
        if self.paper is None:
            return "Paper execution is not wired. Check Alpaca paper credentials."
        request_id = _extract_interaction_id(interaction)
        if not request_id:
            return "Paper decision refused: Discord interaction id is unavailable."
        choice = str(action).strip().lower()
        try:
            if choice == "preview":
                result = self.paper.preview(order_id)
            elif choice in {"approve", "reject", "veto"}:
                result = self.paper.decide(
                    order_id,
                    actor=str(_extract_user_id(interaction)),
                    decision=choice,
                    confirm_symbol=confirm_symbol or None,
                    request_id=request_id,
                    surface="discord",
                )
            else:
                return "Unknown /paper action. Expected preview, approve, reject, or veto."
            return "```json\n" + json.dumps(result, default=str, indent=2)[:1800] + "\n```"
        except Exception as exc:  # noqa: BLE001 - an interaction must return a visible refusal
            code = getattr(exc, "code", type(exc).__name__)
            detail = str(exc) if hasattr(exc, "code") else type(exc).__name__
            return f"Paper decision refused ({code}): {detail}"


# Slash-command names the bot registers (draft-discord-control-plane). The adapter validates the
# operator on each; the actual command bodies forward to the orchestrator loopback API.
SLASH_COMMANDS = (
    "mode", "killswitch", "brief", "status", "limits", "triage", "ask", "automation",
    "quant", "paper",
)

_WIRED_COMMANDS = frozenset({"quant", "ask", "paper"})
"""Commands with a REAL body below, excluded from the acknowledge-only registration loop.

The rest are still WP6 stubs that reply "acknowledged". Listing the wired ones explicitly
means adding a real body cannot silently leave the stub registered alongside it — the two
would race to answer the same interaction, and which one won would depend on registration
order."""


def build_bot(st, token_registry, *, coordinator=None, controls: ProposalControls | None = None,
              quant: "QuantCommands | None" = None, paper=None, ask=None, intents: Any = None):
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

    def _make_stub(command_name: str):
        """Bind the command name through a CLOSURE, not a default parameter.

        The obvious form — ``async def _cmd(interaction, _name=name)`` — is what this file
        used to do, and discord.py rejects it: ``_extract_parameters_from_callback`` requires
        a type annotation on every parameter after the interaction, and treats a bare default
        as a slash-command OPTION rather than as a captured loop variable. It raised
        ``TypeError: parameter '_name' is missing a type annotation`` before a single command
        was registered, so ``build_bot`` could not construct a tree at all.

        That went unnoticed because discord.py is deliberately absent from CI (so the suite
        exercises the no-discord fallback), and the only test of this path asserts the
        RuntimeError raised when it is missing. A closure keeps the callback signature to
        exactly the parameters Discord should see.
        """

        async def _cmd(interaction: Any):
            if not _guard(interaction):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            # Thin forward: the real body posts to the loopback orchestrator API.
            await interaction.response.send_message(
                f"/{command_name} acknowledged.", ephemeral=True)

        return _cmd

    for name in SLASH_COMMANDS:
        if name in _WIRED_COMMANDS:
            continue                       # has a real body below
        tree.command(name=name, description=f"Cockpit {name} control")(_make_stub(name))

    # ---- /quant (QT-09): read-only posture + bounded simulation -------------------------
    quant_core = quant if quant is not None else QuantCommands(st)

    async def _quant(interaction, action: str = "status", strategy: str = "market_maker",
                     events: int | None = None, seed: int = 3):  # noqa: ANN001
        # DEFERRED FIRST. A simulation runs for seconds; Discord expires an un-acknowledged
        # interaction after three, and the operator would see "the application did not
        # respond" while the engine was working perfectly. The deferral must happen before
        # the work, not after it.
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await asyncio.to_thread(
            quant_core.handle,
            interaction,
            action=action,
            strategy=strategy,
            events=events,
            seed=seed,
        )
        await interaction.followup.send(message, ephemeral=True)

    tree.command(name="quant", description="Quant engine status / simulation (read-only)")(_quant)

    # ---- /ask (W-22 / SEC-18): fresh context, never resume the proposal session --------
    question_core = QuestionCommands(st, ask=ask)

    async def _ask(interaction, proposal_id: str, question: str):  # noqa: ANN001
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await asyncio.to_thread(
            question_core.handle,
            interaction,
            proposal_id=proposal_id,
            question=question,
        )
        await interaction.followup.send(message, ephemeral=True)

    tree.command(
        name="ask",
        description="Freshly interrogate a stored proposal before approval",
    )(_ask)

    # ---- /paper (W-22): preview + explicit human approval/veto -------------------------
    paper_core = PaperCommands(st, paper=paper)

    async def _paper(interaction, action: str = "preview", order_id: str = "",
                     confirm_symbol: str = ""):  # noqa: ANN001
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await asyncio.to_thread(
            paper_core.handle,
            interaction,
            action=action,
            order_id=order_id,
            confirm_symbol=confirm_symbol,
        )
        await interaction.followup.send(message, ephemeral=True)

    tree.command(
        name="paper",
        description="Preview, approve, reject, or veto a stored Alpaca paper order",
    )(_paper)

    return client, tree


def run_bot(client: Any, tree: Any, token: str, *, guild_id: int | None = None) -> None:
    """Connect the gateway and BLOCK. Call this on its own thread.

    Blocking is why ``main.run()`` hands this to a daemon thread rather than calling it
    inline: ``serve()`` already blocks forever on uvicorn, and whichever of the two ran second
    would never start.

    COMMANDS ARE SYNCED ON READY, not at build time. The tree built by ``build_bot`` is a
    local object; Discord does not know a command exists until it is registered with the API.
    Without this the bot connects, looks healthy in the member list, and every slash command
    returns "unknown integration" — a failure that reads like a permissions problem and is not.

    ``guild_id`` scopes the sync to one guild, which registers INSTANTLY. A global sync can
    take up to an hour to propagate, so an operator who just deployed and sees no commands has
    most likely not waited rather than misconfigured anything. For a single private guild —
    which is this system's whole deployment model — guild-scoped is the right default and the
    reason this parameter exists.
    """
    if not _HAS_DISCORD:
        raise RuntimeError("discord.py is not installed; cannot connect the gateway.")

    @client.event
    async def on_ready():  # noqa: ANN202 - discord supplies the signature
        if guild_id is not None:
            guild = discord.Object(id=guild_id)
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        else:
            await tree.sync()

    client.run(token)
