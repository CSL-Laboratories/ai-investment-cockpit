"""MEM-01 — operator rendering for standing theses. No discord import.

WHY THIS FILE DECIDES WHETHER THE FEATURE IS REAL
---------------------------------------------------
A thesis memory that an operator cannot SEE and cannot CLOSE is a library, not a control.
The whole value of recording an invalidation condition is that someone is eventually asked
"did this happen?" — and that question only gets asked if the standing claims are in front of
them and retiring one takes a single command.

So this module and the ``/thesis`` command are not presentation polish on MEM-01. They are
the half that turns a stored field into discipline.

THE INVALIDATED COUNT IS SHOWN ON PURPOSE
-------------------------------------------
``/thesis`` reports how many theses were closed as INVALIDATED, not just how many are open.
A system that records only its live positions can never distinguish discipline from a run of
luck, and the count of times its own stated disqualifying condition fired is the number it
would otherwise never volunteer.
"""
from __future__ import annotations

from bridge.quant_view import DISCORD_MESSAGE_LIMIT, clamp

_STATUS_ICON = {
    "open": "🟢",
    "invalidated": "🔴",
    "closed": "⚪",
    "superseded": "🔵",
}


def render_open_theses(views, *, invalidated: int = 0) -> str:
    """The operator's standing book of claims, as a Discord message."""
    if not views:
        return clamp(
            "**Standing theses**\nNone open.\n\n"
            f"_{invalidated} thesis(es) closed as INVALIDATED to date._"
        )

    lines = [f"**Standing theses** ({len(views)} open)"]
    for v in views:
        lines.append(
            f"\n{_STATUS_ICON.get(v.status, '•')} **{v.side.upper()} {v.symbol}** "
            f"· opened {v.opened_at[:10]} · `{v.id}`"
        )
        lines.append(f"   thesis: {v.thesis[:240]}")
        # The invalidation is the actionable half — it is the question the operator is being
        # asked — so it is never truncated as aggressively as the thesis prose.
        lines.append(f"   **invalidation: {v.invalidation[:400]}**")
    lines.append(f"\n_{invalidated} thesis(es) closed as INVALIDATED to date._")
    lines.append("_Close one with_ `/thesis action:invalidate id:<id> reason:<why>`")
    return clamp("\n".join(lines))


def render_embed_theses(standing: list[dict]) -> str:
    """One line per standing claim, for the proposal embed.

    Read at the moment of approval, so it leads with the fact that matters there: we already
    have a position thesis on this name, and here is what we said would disqualify it.
    """
    if not standing:
        return ""
    parts = []
    for t in standing:
        parts.append(
            f"⚠ existing {t.get('side', '?').upper()} thesis on {t.get('symbol', '?')} "
            f"— invalidates on: {str(t.get('invalidation', ''))[:200]}"
        )
    return clamp("\n".join(parts), DISCORD_MESSAGE_LIMIT)
