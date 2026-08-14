"""QT-09 — operator-facing rendering for the quant surface. No discord import.

Same split as the rest of the bridge: the rendering is a pure function over a dict, so it is
unit-testable without discord.py, and ``bridge/bot.py`` only turns the result into a message.

DISCORD IMPOSES A HARD LIMIT AND IT IS LOAD-BEARING HERE
----------------------------------------------------------
A Discord message caps at 2,000 characters. A tearsheet that exceeds it does not get
truncated politely — the API rejects the whole send, and the operator sees NOTHING. So the
renderers below are deliberately narrow, and ``clamp`` is applied at the end as a backstop
rather than trusted to never be needed.

The failure mode this avoids is specific and bad: an operator runs a command during an
incident, gets silence, and cannot tell whether the engine is wedged or the message was
rejected.

MONEY IS QUANTIZED WITH ``Decimal``, NEVER PARSED TO FLOAT
------------------------------------------------------------
The tearsheet's money fields arrive as strings because they are ``Decimal`` server-side, and
they arrive at full precision — a borrow accrual over fractional seconds legitimately
produces ``-18.60520016950453079076293760``. That is correct and unreadable, and an operator
scanning a channel needs the number, not the derivation.

So display quantizes to cents through ``Decimal`` — exact decimal arithmetic, the same type
the value was computed in. What it must never do is ``float(value)`` first: that reintroduces
binary rounding at the last step, on the one representation the operator will actually quote
back, and the displayed figure would stop matching the stored one for reasons nobody could
find later. The API payload keeps full precision regardless; only the rendering rounds.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

DISCORD_MESSAGE_LIMIT = 2_000
_CENTS = Decimal("0.01")


def money(value) -> str:
    """Render a money string at cent precision. Decimal throughout — never via float.

    Passes anything unparseable straight through rather than raising: a renderer that dies
    on an unexpected field leaves the operator with no tearsheet at all, which is strictly
    worse than one odd-looking line.
    """
    if value is None:
        return "—"
    try:
        return str(Decimal(str(value)).quantize(_CENTS))
    except (InvalidOperation, ValueError, ArithmeticError):
        return str(value)


def clamp(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> str:
    """Keep a message inside Discord's hard cap, with a visible marker when it bites.

    Silent truncation would be worse than the cap: an operator reading a tearsheet that
    simply stops has no way to know a line is missing. The marker is the whole point.
    """
    if len(text) <= limit:
        return text
    marker = "\n… [truncated]"
    return text[: limit - len(marker)] + marker


def render_status(status: dict) -> str:
    """The engine's posture, as an operator reads it before trusting anything else."""
    verification = status.get("verification") or {}
    containment = status.get("containment") or {}
    impact_y = verification.get("impact_coefficient_y")
    adv_cap = verification.get("max_adv_participation")
    net_multiple = verification.get("net_cost_multiple")
    lines = [
        "**Quant engine**",
        f"engine: `{status.get('engine', 'unknown')}`",
        f"strategies: {', '.join(status.get('strategies') or []) or 'none'}",
        f"book feed: `{status.get('book_feed', 'unknown')}`",
        "",
        "**Verification**",
        f"Tier A: {verification.get('tier_a', '?')}",
        f"Tier B: {verification.get('tier_b', '?')}",
        f"verdict required: `{verification.get('require_quant_verdict')}`",
        f"net edge required: `{verification.get('require_net_edge')}`",
        f"edge source: {verification.get('expected_edge_source', '?')}",
        f"impact Y / ADV cap: "
        f"{'dormant' if impact_y is None else f'{impact_y} / {adv_cap}'}",
        f"minimum net/cost: {'dormant' if net_multiple is None else f'{net_multiple}x'}",
        f"cost ceiling: {verification.get('max_execution_cost_bps')}bps",
        "",
        "**Containment**",
        f"Tier C venue: {containment.get('tier_c_venue', '?')}",
        f"may reach a broker: `{containment.get('quant_may_import_brokers')}`",
        f"order authority: {containment.get('order_authority', '?')}",
    ]
    return clamp("\n".join(lines))


def render_tearsheet(sim: dict) -> str:
    """One simulated run, as a Discord message.

    The CAVEAT is rendered LAST and unconditionally. Last because it is the line most likely
    to survive a truncation of the middle, and unconditionally because a tearsheet pasted
    into a channel outlives the conversation that produced it — a plausible PnL with no
    provenance attached is precisely how a synthetic number becomes someone's evidence.
    """
    attribution = sim.get("attribution") or {}
    request = sim.get("request") or {}
    book = sim.get("book") or {}
    maker_ratio = attribution.get("maker_ratio")
    lines = [
        f"**Sim — {request.get('strategy', '?')}** "
        f"({sim.get('events', 0):,} events, seed {request.get('seed')})",
        "```",
        f"orders sent      {sim.get('orders_sent', 0):,}",
        f"fills            {attribution.get('fills', 0):,} "
        f"(maker {attribution.get('maker_fills', 0):,} / taker {attribution.get('taker_fills', 0):,})",
        f"maker ratio      {maker_ratio if maker_ratio is None else f'{maker_ratio:.1%}'}",
        f"desync cancels   {sim.get('desync_cancels', 0):,}",
        "",
        f"spread capture   {money(attribution.get('spread_capture'))}",
        f"inventory drift  {money(attribution.get('inventory_drift'))}",
        f"fees             {money(attribution.get('fees'))}  ({attribution.get('fee_bps')}bps)",
        f"net              {money(attribution.get('total'))}",
        "",
        f"book             bid {book.get('best_bid_ticks')} / ask {book.get('best_ask_ticks')}"
        f"  ({book.get('resting_orders', 0)} orders, crossed={book.get('crossed')})",
        "```",
        f"_{sim.get('caveat', '')}_",
    ]
    return clamp("\n".join(lines))


def render_execution_line(execution: dict | None) -> str:
    """One line of execution quality for a proposal, from the embed's ``execution`` block.

    Its job is to be READ during an approval decision, so it leads with the verdict and the
    single number that decides it. An operator scanning a channel gets the answer before the
    detail, or they get neither.
    """
    if not execution or not execution.get("evaluated"):
        summary = (execution or {}).get("summary", "not evaluated")
        return f"⚪ execution: {summary}"
    if execution.get("verdict") == "insufficient_data":
        return clamp(
            f"⚪ execution: **{execution.get('label')}** · "
            f"edge {execution.get('net_edge_state', 'unavailable')}"
        )

    icon = {"ok": "🟢", "marginal": "🟡", "uneconomic": "🔴"}.get(
        str(execution.get("verdict")), "⚪"
    )
    parts = [
        f"{icon} execution: **{execution.get('label')}**",
        f"cost {execution.get('expected_cost_bps')}bps",
        f"(spread {execution.get('expected_spread_bps')} + "
        f"impact {execution.get('expected_impact_bps')} + "
        f"fee {execution.get('expected_fee_bps')} + "
        f"borrow {execution.get('expected_borrow_bps')})",
        f"ADV participation {execution.get('adv_participation_rate')}",
        f"edge {execution.get('net_edge_state', 'unavailable')}",
    ]
    first_negative = execution.get("first_negative_notional")
    if first_negative is not None:
        parts.append(f"net turns negative at ${first_negative}")
    return clamp(" · ".join(parts))
