"""RW-17 §3 — the operator's ONLY interface to the zero-touch PAPER copy lane.

THE PREMISE OF THIS FILE
------------------------
The operator asked for trades without approvals, "all i ask is for notifications". So the
notification is not a courtesy log line sitting beside the real interface — after RW-17 it
**is** the interface. Two consequences drive every decision below:

  1. **An unnotified trade is a DEFECT, not a cosmetic gap.** Delivery is a *precondition* of
     execution, exactly as it is for ``auto_veto`` (SEC-23, ``bridge/veto.py``: silence is not
     consent). ``PaperAutoExecutor`` must gate on :attr:`DeliveryResult.confirmed` /
     :meth:`CopyTradeNotifier.queued_delivery_confirmed` and refuse to trade when it is false.
     Everything here is therefore biased toward returning "NOT confirmed" — a false negative
     costs a skipped paper trade, a false positive costs a trade the operator never saw.
  2. **The ``queued`` message is the whole decision surface.** It is the only artefact between
     a disclosure and a filled order, and the operator's only lever is the cancel command
     printed in it. So it carries every fact needed to judge the trade — symbol, side, dollar
     size, filer, trade date, DISCLOSURE LAG, the disclosed range, why it was sized that way —
     and it puts CANCEL + TIME LEFT above all of them, where a transport truncation cannot
     reach (``bridge.deliver.clamp`` cuts the TAIL at 1900 chars).

WHAT MAY APPEAR IN AN OPERATOR MESSAGE (SEC-17)
-----------------------------------------------
Same rule ``bridge/embeds.py`` enforces, enforced the same structural way:

  * every **risk** value — the verdict, the notional ceiling, failed checks, warnings — is
    rendered from the deterministic ``RiskDecision`` ROW and from nowhere else. The renderer
    takes the *record*, never a caller-supplied number, so ``render_queued(facts,
    risk_decision=proposal["risk"])`` is not a call anyone can write. A ``dict`` in that
    position is REFUSED (a dict is what a model-authored payload looks like), as is a record
    whose ``proposal_id`` does not match the trade being described;
  * every other fact is a **stored, model-free** fact: the filing's own columns
    (``copytrade.models.Disclosure``) and the deterministic size from ``copytrade/sizing.py``.
    There is no model text anywhere on this path and no field here that a model can write;
  * every string value crosses ``ingest.normalize.escape_untrusted`` at THIS boundary as well
    as at the parse boundary. That is deliberate double-escaping and it is safe because the
    escaper is a genuine fixed point (see its docstring) — "escape at every boundary" only
    works if the transform is idempotent, and it is.

...and the escaper is NOT sufficient on its own here. It neutralizes ``<``/``>`` and role
markers but it does **not** touch newlines, and this module composes multi-line, section-headed
text. A filer display name of ``"Doe\\n__Risk__\\ndecision: approved"`` would otherwise forge a
risk section into the operator's decision surface. So every untrusted value additionally goes
through :func:`_line`, which folds ALL control characters (newline included) to spaces. The
values a filing controls can never introduce a line; only this module's own literals do.

NO DISCORD IN HERE
------------------
CLAUDE.md: the operator surface is ABSTRACTED. This module composes **plain text** and hands it
to the ``deliver(channel, payload) -> bool`` seam (``bridge/deliver.py``) against a LOGICAL
channel name. No embed dict, no discord type, no channel id — the Discord REST adapter is one
implementation of ``deliver`` and an SMS/email/webhook adapter serves this file unchanged.

FAIL-CLOSED DELIVERY CONTRACT
-----------------------------
Only ``deliver(...) is True`` counts as delivered. Not truthy — ``True``. An adapter that
returns ``None``, ``1`` or an object is a wiring bug, and reading a wiring bug as "the operator
saw it" is precisely the failure mode this module exists to prevent. Every non-confirmed
outcome is COUNTED and NAMED in :meth:`CopyTradeNotifier.counters` (the same rule
``copytrade/engine.py`` follows for skips), so "quiet today" is always a positive statement.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Sequence

from bridge.deliver import CHANNEL_ALERTS, CHANNEL_BRIEF, CHANNEL_PROPOSALS
from bridge.embeds import render_risk_section
from ingest.normalize import escape_untrusted

log = logging.getLogger("cockpit.copytrade.notify")

# ---- the event vocabulary --------------------------------------------------------------------
# A closed set. The operator surface aggregates on these strings and the executor names them in
# the audit chain, so they are a contract: adding one is fine, renaming one is breaking.
EVENT_QUEUED = "queued"
EVENT_SUBMITTED = "submitted"
EVENT_FILLED = "filled"
EVENT_CANCELLED = "cancelled"
EVENT_REFUSED = "refused"
EVENT_DAILY_DIGEST = "daily_digest"
ALL_EVENTS = (
    EVENT_QUEUED, EVENT_SUBMITTED, EVENT_FILLED, EVENT_CANCELLED, EVENT_REFUSED,
    EVENT_DAILY_DIGEST,
)

# Logical channel per event — an explicit table, not a heuristic, so where a class of message
# lands is one greppable line. A queued trade is a DECISION artefact (it has a lever), which is
# what ``#proposals`` means; lifecycle and refusals are ``#alerts``; the digest reads with the
# morning brief. An UNMAPPED channel fails closed inside ``build_deliver`` (returns False) and
# therefore blocks the trade rather than re-routing it somewhere the operator is not looking —
# an operator running the Phase-0.5 configuration (``#brief`` only) gets a lane that queues,
# fails to notify, and trades NOTHING until the proposals channel id is set. Refusals are
# deliberately NOT split between ``#alerts`` and ``#system``: a refusal the operator has to find
# in a second channel is one they will not find, and every extra channel is another one that
# must be configured before the lane can behave.
CHANNEL_BY_EVENT: dict[str, str] = {
    EVENT_QUEUED: CHANNEL_PROPOSALS,
    EVENT_SUBMITTED: CHANNEL_ALERTS,
    EVENT_FILLED: CHANNEL_ALERTS,
    EVENT_CANCELLED: CHANNEL_ALERTS,
    EVENT_REFUSED: CHANNEL_ALERTS,
    EVENT_DAILY_DIGEST: CHANNEL_BRIEF,
}

# ---- delivery outcome vocabulary -------------------------------------------------------------
DELIVERY_CONFIRMED = "confirmed"
"""The seam returned exactly ``True``. The ONLY status a ticket may resolve on."""
DELIVERY_FAILED = "delivery_failed"
"""The seam returned False — unmapped channel, HTTP failure, adapter refusal."""
DELIVERY_RAISED = "delivery_raised"
"""The seam raised. ``build_deliver`` never does; a third-party adapter might."""
DELIVERY_NOT_BOOLEAN = "delivery_not_boolean"
"""The seam returned something other than ``True``/``False``. Read as UNDELIVERED."""
DELIVERY_NO_SEAM = "no_deliver_seam"
"""No ``deliver`` was wired at all. The lane must not trade blind."""
RENDER_REFUSED = "render_refused"
"""The facts did not support an honest message, so nothing was sent and nothing may trade."""

ALL_DELIVERY_STATUSES = (
    DELIVERY_CONFIRMED, DELIVERY_FAILED, DELIVERY_RAISED, DELIVERY_NOT_BOOLEAN,
    DELIVERY_NO_SEAM, RENDER_REFUSED,
)

# ---- the refusal gates this lane can name ----------------------------------------------------
# The operator's question on a refusal is always "which control stopped it?", so the gate is a
# NAME from a closed vocabulary rather than prose. A name outside the vocabulary is still
# rendered verbatim (hiding it would be worse) but is flagged and counted — an unrecognised
# refusal code means the executor and this file have drifted, and that is worth seeing.
GATE_MODE_NOT_PAPER = "mode_not_paper"
GATE_HALT = "halt"
GATE_BREAKER_WATCH_ONLY = "breaker_watch_only"
GATE_BREAKER_ORDER_PATH = "breaker_order_path"
GATE_AUDIT_CHAIN = "audit_chain_unverified"
GATE_UNDELIVERED = "notification_undelivered"
GATE_BUDGET_DAILY_NOTIONAL = "budget_daily_notional"
GATE_BUDGET_TRADES_PER_DAY = "budget_trades_per_day"
GATE_BUDGET_OPEN_POSITIONS = "budget_open_positions"
GATE_BUDGET_SYMBOL_NOTIONAL = "budget_symbol_notional_per_day"
GATE_BUDGET_UNREADABLE = "budget_unreadable"
GATE_RISK_REJECTED = "risk_rejected"
GATE_GATEWAY_REFUSED = "order_gateway_refused"
GATE_CLOCK_BACKWARDS = "clock_backwards"
GATE_TICKET_EXPIRED = "ticket_expired"
# The gates the RW-17 adversarial round added. Each names a condition the operator has a
# DIFFERENT response to, which is the only reason a gate is a name and not prose:
#   pending_exposure    — the approved-but-unfilled ledger has never drained (RW-15), not
#                         anything about this filing;
#   positions_unavailable — the broker's positions/quote seam faulted; nothing was spent;
#   approval_replay / approval_write_failed / order_missing — the lane's own bookkeeping;
#   superseded / queue_full — the QUEUE, not the trade;
#   abandoned_restart   — the process died inside somebody's cancel window;
#   resolve_fault       — an unclassified raise, i.e. the one to send us a log line about.
GATE_PENDING_EXPOSURE = "pending_exposure"
GATE_POSITIONS_UNAVAILABLE = "positions_unavailable"
GATE_APPROVAL_REPLAY = "approval_replay"
GATE_APPROVAL_FAILED = "approval_write_failed"
GATE_ORDER_MISSING = "order_missing"
GATE_SUPERSEDED = "superseded"
GATE_QUEUE_FULL = "queue_full"
GATE_ABANDONED_RESTART = "abandoned_restart"
GATE_RESOLVE_FAULT = "resolve_fault"
KNOWN_REFUSAL_GATES = frozenset({
    GATE_MODE_NOT_PAPER, GATE_HALT, GATE_BREAKER_WATCH_ONLY, GATE_BREAKER_ORDER_PATH,
    GATE_AUDIT_CHAIN, GATE_UNDELIVERED, GATE_BUDGET_DAILY_NOTIONAL,
    GATE_BUDGET_TRADES_PER_DAY, GATE_BUDGET_OPEN_POSITIONS, GATE_BUDGET_SYMBOL_NOTIONAL,
    GATE_BUDGET_UNREADABLE, GATE_RISK_REJECTED, GATE_GATEWAY_REFUSED, GATE_CLOCK_BACKWARDS,
    GATE_TICKET_EXPIRED, GATE_PENDING_EXPOSURE, GATE_POSITIONS_UNAVAILABLE,
    GATE_APPROVAL_REPLAY, GATE_APPROVAL_FAILED, GATE_ORDER_MISSING, GATE_SUPERSEDED,
    GATE_QUEUE_FULL, GATE_ABANDONED_RESTART, GATE_RESOLVE_FAULT,
})

_SIDES = frozenset({"buy", "sell"})

# Variable-length fields are capped HERE rather than left to ``bridge.deliver.clamp``, which
# truncates the TAIL of the whole message. The cancel instruction is at the top, so a tail cut
# could never remove it — but it could remove the risk section, and a decision surface that
# silently loses its risk block is not one to ship. Capping the two unbounded fields keeps the
# whole message comfortably inside the transport limit.
_MAX_REASON_CHARS = 400
_MAX_DETAIL_CHARS = 240
_MAX_DIGEST_ROWS = 40

_CENTS = Decimal("0.01")


# ==============================================================================================
# value formatting — deterministic, escaped, single-line
# ==============================================================================================
def _line(value) -> str:
    """Escape one untrusted value AND flatten it to a single line.

    ``escape_untrusted`` is the injection boundary (SEC-02/M3); it is not a LAYOUT boundary and
    does not claim to be. It leaves ``\\n``, ``\\r`` and other control characters intact, which
    is harmless in a prompt block and NOT harmless in a section-headed operator message: a filer
    display name or asset description carrying a newline could otherwise forge a
    ``__Risk__``/``decision: approved`` block into the surface the operator decides from — the
    exact class of forgery SEC-17 exists to prevent, arriving through layout instead of through
    a field. Folding every control character to a space means only this module's own literals
    can begin a line.
    """
    text = escape_untrusted("" if value is None else value)
    return "".join(" " if ch < " " or ch == "\x7f" else ch for ch in text).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _dec(value) -> Decimal | None:
    """Coerce to a FINITE ``Decimal`` or ``None``. ``None`` means "do not render a number" —
    never "render zero". A money field that cannot be read is a fact we do not have, and
    printing a confident ``$0.00`` for it would misstate the trade."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _money(value) -> str | None:
    d = _dec(value)
    if d is None:
        return None
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d).quantize(_CENTS):,.2f}"


def _qty(value) -> str | None:
    d = _dec(value)
    if d is None:
        return None
    return format(d.normalize(), "f")


def _duration(seconds) -> str:
    """``330`` -> ``"5m 30s"``. The operator's real question is "how long have I got?", so the
    remaining time is stated in the unit they think in, alongside the absolute deadline."""
    d = _dec(seconds)
    if d is None or d < 0:
        return "unknown"
    total = int(d)
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m {total % 60:02d}s"


def _rail_value(value) -> str:
    """Render one budget rail's remaining headroom by its TYPE, not by guessing.

    ``AutoExecBudget`` mixes dollar rails (``Decimal``) with count rails (``int``: trades per
    day, open positions). Sniffing "is it a number? then it is money" would print
    ``2 trades remaining`` as ``$2.00`` — a rail the operator would then read as almost
    exhausted. Decimal means dollars, int means a count, anything else is shown as text.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return _money(value) or _line(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _money(value) or _line(value)
    return _line(value)


def _iso(value) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value in (None, ""):
        return None
    return _line(value)


# ==============================================================================================
# results and facts
# ==============================================================================================
@dataclass(frozen=True)
class DeliveryResult:
    """The gate the executor reads. ``confirmed`` is a DERIVED property, never a stored flag.

    A boolean field could be constructed ``True`` alongside a failure status by any caller (or
    any future refactor) and the executor would then trade on a message nobody received. Making
    it a property of ``status`` means "confirmed" has exactly one meaning and exactly one
    source.
    """

    event: str
    channel: str
    status: str
    attempts: int = 0
    detail: str = ""
    text: str = ""
    proposal_id: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status == DELIVERY_CONFIRMED

    def __bool__(self) -> bool:                     # ``if not result:`` reads as "not delivered"
        return self.confirmed


@dataclass(frozen=True)
class QueuedFacts:
    """Every stored fact the queued message needs. All model-free, all off the filing or off
    ``copytrade/sizing.py``.

    Deliberately NOT carrying any risk value: the risk block is rendered from the
    ``RiskDecision`` record, which is passed separately (SEC-17). If a risk number could live on
    this dataclass, some future caller would populate it from the proposal.
    """

    proposal_id: str
    symbol: str
    side: str
    notional: Decimal
    filer_display: str
    tx_date: date
    filed_date: date
    disclosure_lag_days: int
    amount_label: str
    sizing_reason: str
    cancel_command: str
    window_seconds: float
    deadline: datetime | None = None
    source_key: str = ""
    sizing_notes: tuple[str, ...] = ()
    translated_from: str | None = None

    @staticmethod
    def from_intent(intent, *, proposal_id: str, cancel_command: str, window_seconds: float,
                    deadline: datetime | None = None) -> "QueuedFacts":
        """Build from a ``copytrade.models.CopyIntent`` — the deterministic engine output.

        The disclosure LAG is recomputed here from the two stored dates rather than read from a
        field, because the lag is the single most decision-relevant number in the message (a
        statutory 30-45 day delay is what makes this not a signal edge) and it must be derivable
        from the filing itself, not asserted by whoever built the ticket.
        """
        return QueuedFacts(
            proposal_id=str(proposal_id),
            symbol=str(getattr(intent, "symbol", "") or ""),
            side=str(getattr(intent, "side", "") or ""),
            notional=getattr(intent, "notional", None),
            filer_display=str(getattr(intent, "filer_display", "") or ""),
            tx_date=getattr(intent, "tx_date", None),
            filed_date=getattr(intent, "filed_date", None),
            disclosure_lag_days=_lag_days(getattr(intent, "tx_date", None),
                                          getattr(intent, "filed_date", None)),
            amount_label=str(getattr(intent, "amount_label", "") or ""),
            sizing_reason=str(getattr(intent, "reason", "") or ""),
            cancel_command=str(cancel_command),
            window_seconds=window_seconds,
            deadline=deadline,
            source_key=str(getattr(intent, "source_key", "") or ""),
            sizing_notes=tuple(getattr(intent, "sizing_notes", ()) or ()),
            translated_from=getattr(intent, "translated_from", None),
        )


def _lag_days(tx_date, filed_date) -> int:
    if isinstance(tx_date, date) and isinstance(filed_date, date):
        return (filed_date - tx_date).days
    return -1                                        # invalid -> the fact check refuses to render


@dataclass(frozen=True)
class DigestTrade:
    """One line of the digest's "what traded" section."""

    symbol: str
    side: str
    notional: Decimal | None = None
    status: str = ""
    broker_order_id: str = ""


@dataclass(frozen=True)
class DigestRefusal:
    """One line of the digest's "what was refused, and by which gate" section."""

    symbol: str
    gate: str
    detail: str = ""


# ==============================================================================================
# the renderers — pure functions, no I/O, deterministic
# ==============================================================================================
def missing_queued_facts(facts: QueuedFacts) -> tuple[str, ...]:
    """Which required facts are absent or unusable. NON-EMPTY MEANS DO NOT TRADE.

    The queued message is the operator's only chance to stop the trade, so a message missing
    any of its load-bearing facts is not "a slightly worse message" — it is a trade nobody could
    have judged. Every one of these refuses to render, which refuses delivery, which refuses
    execution.
    """
    missing: list[str] = []
    if not str(facts.proposal_id or "").strip():
        missing.append("proposal_id")
    if not str(facts.symbol or "").strip():
        missing.append("symbol")
    if str(facts.side or "").lower() not in _SIDES:
        missing.append("side")
    notional = _dec(facts.notional)
    if notional is None or notional <= 0:
        missing.append("notional")
    if not str(facts.filer_display or "").strip():
        missing.append("filer_display")
    if not isinstance(facts.tx_date, date):
        missing.append("tx_date")
    if not isinstance(facts.filed_date, date):
        missing.append("filed_date")
    if not isinstance(facts.disclosure_lag_days, int) or facts.disclosure_lag_days < 0:
        # A negative lag means the filing predates the trade — a parse error or a typo, and the
        # one value in this feed that would make a stale signal look fresh (see
        # ``Disclosure.age_days``). Refuse rather than print it.
        missing.append("disclosure_lag_days")
    if not str(facts.amount_label or "").strip():
        missing.append("amount_label")
    if not str(facts.sizing_reason or "").strip():
        missing.append("sizing_reason")
    if not str(facts.cancel_command or "").strip():
        # THE lever. A queued message without it tells the operator a trade is coming and gives
        # them no way to stop it, which is strictly worse than not trading.
        missing.append("cancel_command")
    window = _dec(facts.window_seconds)
    if window is None or window <= 0:
        missing.append("window_seconds")
    return tuple(missing)


def _risk_record_problem(facts: QueuedFacts, risk_decision) -> str | None:
    """SEC-17 structural checks on the risk source. Returns a reason to REFUSE, or None.

    Three ways an operator-facing risk value could stop being a deterministic fact, all closed
    here rather than by convention:

      * ``risk_decision`` is a ``dict``/``list`` — the shape a model-authored payload has. The
        renderer only ever accepts the stored ``RiskDecision`` ROW;
      * it is missing entirely — an auto-executing trade with no adjudicated risk record cannot
        exist under Invariant 1, so being asked to render one means something is wrong upstream;
      * it belongs to a DIFFERENT proposal — rendering another order's ceiling and verdict
        beside this order's size is a lie the operator cannot detect.
    """
    if risk_decision is None:
        return "risk_decision_missing"
    if isinstance(risk_decision, (dict, list, tuple, str, bytes)):
        # Structural, not stylistic: a mapping in this position is how a model-authored risk
        # block would arrive, and there is no way to tell one from a faithful copy of the row.
        return "risk_decision_not_a_record"
    if getattr(risk_decision, "decision", None) in (None, ""):
        return "risk_decision_incomplete"
    rd_proposal = getattr(risk_decision, "proposal_id", None)
    if rd_proposal not in (None, "") and str(rd_proposal) != str(facts.proposal_id):
        return "risk_decision_proposal_mismatch"
    ceiling = _dec(getattr(risk_decision, "max_allowed_notional", None))
    notional = _dec(facts.notional)
    if ceiling is not None and notional is not None and notional > ceiling:
        # The size on the ticket exceeds what the deterministic engine permitted. Whatever
        # produced that disagreement, showing the operator the larger size under the smaller
        # ceiling normalises it. Refuse; the executor then does not trade.
        return "size_exceeds_risk_ceiling"
    return None


def render_risk_line(risk_decision) -> str:
    """The risk block, sourced 100% from the ``RiskDecision`` via ``bridge.embeds`` (SEC-17).

    Reuses ``render_risk_section`` rather than re-reading the row: one implementation of "what
    the operator is allowed to be told about risk" is the point of that function, and a second
    one here would be a second thing to keep in step.
    """
    section = render_risk_section(risk_decision)
    ceiling = _money(section.get("max_allowed_notional"))
    failed = [_line(f) for f in section.get("failed_checks") or []]
    warnings = [_line(w) for w in section.get("warnings") or []]
    parts = [f"verdict: {_line(section.get('decision')) or 'unknown'}"]
    if ceiling:
        parts.append(f"ceiling: {ceiling}")
    parts.append("failed: " + (", ".join(failed) if failed else "none"))
    parts.append("warnings: " + (", ".join(warnings) if warnings else "none"))
    return " | ".join(parts)


def render_queued(facts: QueuedFacts, *, risk_decision) -> str:
    """The decision surface. Field order is a LITERAL, so the message shape is deterministic.

    Layout is load-bearing, in this order and for these reasons:
      1. what will happen (side / symbol / dollar size) — the headline;
      2. **CANCEL + time left** — the lever, above everything, where a tail truncation in the
         transport can never reach it;
      3. the filing (filer, trade date, disclosure lag, disclosed range) — the evidence, and the
         lag is called out in days because it is the fact that makes this not a signal edge;
      4. why it was sized that way — the deterministic sizing sentence and its notes;
      5. the risk block, from the ``RiskDecision`` only.

    Raises ``ValueError`` if the facts do not support an honest message — callers go through
    :meth:`CopyTradeNotifier.queued`, which turns that into a counted ``render_refused`` and
    therefore into a refusal to trade.
    """
    missing = missing_queued_facts(facts)
    if missing:
        raise ValueError(f"queued facts incomplete: {', '.join(missing)}")
    problem = _risk_record_problem(facts, risk_decision)
    if problem is not None:
        raise ValueError(problem)

    side = _line(facts.side).upper()
    symbol = _line(facts.symbol).upper()
    size = _money(facts.notional)
    deadline = _iso(facts.deadline)

    lines = [
        f"**AUTO-TRADE QUEUED — {side} {symbol} {size}**",
        "Congressional copy, PAPER account. This WILL execute unless you cancel it.",
        "",
        f"**CANCEL:** `{_line(facts.cancel_command)}`",
        f"**Time left:** {_duration(facts.window_seconds)}"
        + (f" (until {deadline})" if deadline else ""),
        "",
        "__Filing__",
        f"Filer: {_line(facts.filer_display)}",
        f"Trade date: {_iso(facts.tx_date)} · disclosed: {_iso(facts.filed_date)}",
        f"Disclosure lag: {int(facts.disclosure_lag_days)} days"
        " (statutory; this is public, delayed information — not an edge)",
        f"Disclosed range: {_line(facts.amount_label)}",
        "",
        "__Why this size__",
        _clip(_line(facts.sizing_reason), _MAX_REASON_CHARS),
    ]
    if facts.translated_from:
        lines.append(
            f"Translated from {_line(facts.translated_from)} — the contract itself is never "
            "copied; only the underlying equity."
        )
    notes = [_line(n) for n in facts.sizing_notes if _line(n)]
    if notes:
        lines.append("Sizing notes: " + ", ".join(notes))
    lines += [
        "",
        "__Risk (deterministic RiskDecision)__",
        render_risk_line(risk_decision),
        "",
        f"_proposal {_line(facts.proposal_id)}"
        + (f" · source {_line(facts.source_key)}" if facts.source_key else "")
        + "_",
    ]
    return "\n".join(lines)


def render_submitted(*, proposal_id: str, symbol: str, side: str, broker_order_id: str,
                     quantity=None, notional=None) -> str:
    """The order left for the (paper) broker. Broker id first — it is the handle for every
    follow-up question the operator will have."""
    bits = [f"**AUTO-TRADE SUBMITTED — {_line(side).upper()} {_line(symbol).upper()}**",
            f"Broker order: `{_line(broker_order_id) or 'unknown'}`"]
    qty = _qty(quantity)
    if qty:
        bits.append(f"Quantity: {qty}")
    size = _money(notional)
    if size:
        bits.append(f"Notional: {size}")
    bits.append(f"_proposal {_line(proposal_id)} · PAPER_")
    return "\n".join(bits)


def render_filled(*, proposal_id: str, symbol: str, side: str, broker_order_id: str,
                  filled_qty=None, fill_price=None, notional=None) -> str:
    """The fill. Price and quantity are the broker's report, echoed as stored — never averaged,
    inferred, or back-filled from the intent when the broker did not say."""
    bits = [f"**AUTO-TRADE FILLED — {_line(side).upper()} {_line(symbol).upper()}**",
            f"Broker order: `{_line(broker_order_id) or 'unknown'}`"]
    qty = _qty(filled_qty)
    price = _money(fill_price)
    bits.append(f"Filled: {qty if qty else 'unreported'} @ {price if price else 'unreported'}")
    size = _money(notional)
    if size:
        bits.append(f"Notional: {size}")
    bits.append(f"_proposal {_line(proposal_id)} · PAPER_")
    return "\n".join(bits)


def render_cancelled(*, proposal_id: str, symbol: str, side: str, actor: str = "",
                     at=None) -> str:
    """The operator's veto landed. Says plainly that nothing was sent, because "cancelled" on
    its own leaves open whether an order already reached the broker."""
    bits = [f"**AUTO-TRADE CANCELLED — {_line(side).upper()} {_line(symbol).upper()}**",
            "Cancelled inside the window. Nothing was sent to the broker."]
    if actor:
        bits.append(f"By: {_line(actor)}")
    when = _iso(at)
    if when:
        bits.append(f"At: {when}")
    bits.append(f"_proposal {_line(proposal_id)}_")
    return "\n".join(bits)


def render_refused(*, proposal_id: str, symbol: str, side: str, gate: str,
                   detail: str = "") -> str:
    """A refusal, naming the EXACT gate.

    "The trade did not happen" is not an answer — the operator's next action depends entirely on
    whether it was HALT (they set that), a breaker (something tripped), a budget rail (the lane
    is doing its job) or a risk rejection (the order was wrong). So the gate name is the
    headline, and an unrecognised name is shown rather than swallowed, flagged so the drift is
    visible.
    """
    name = _line(gate) or "unnamed_gate"
    flag = "" if gate in KNOWN_REFUSAL_GATES else "  ⚠ (unrecognised refusal code)"
    bits = [f"**AUTO-TRADE REFUSED — {_line(side).upper()} {_line(symbol).upper()}**",
            f"Gate: `{name}`{flag}"]
    if detail:
        bits.append(f"Detail: {_clip(_line(detail), _MAX_DETAIL_CHARS)}")
    bits.append("No order was sent. The lane stays closed until the gate clears.")
    bits.append(f"_proposal {_line(proposal_id)}_")
    return "\n".join(bits)


def render_daily_digest(*, day, executed: Sequence[DigestTrade] = (),
                        refused: Sequence[DigestRefusal] = (),
                        budget_remaining: Mapping[str, object] | None = None) -> str:
    """The end-of-day statement. A zero-trade day is stated POSITIVELY.

    A lane that traded nothing and a lane that has been broken since 09:31 look identical from
    silence, and this is the one message that arrives whether or not anything happened — so it
    always prints the counts, always prints the refusal histogram, and always prints what is
    left of each budget rail.
    """
    lines = [f"**Congressional copy lane — daily digest {_iso(day) or '?'}**",
             f"Executed: {len(executed)} · Refused: {len(refused)} · PAPER account"]

    lines.append("")
    lines.append("__Traded__")
    if executed:
        for t in list(executed)[:_MAX_DIGEST_ROWS]:
            size = _money(t.notional)
            row = f"• {_line(t.side).upper()} {_line(t.symbol).upper()}"
            if size:
                row += f" {size}"
            if t.status:
                row += f" — {_line(t.status)}"
            if t.broker_order_id:
                row += f" (`{_line(t.broker_order_id)}`)"
            lines.append(row)
        if len(executed) > _MAX_DIGEST_ROWS:
            lines.append(f"• … {len(executed) - _MAX_DIGEST_ROWS} more")
    else:
        lines.append("• nothing traded")

    lines.append("")
    lines.append("__Refused, by gate__")
    if refused:
        by_gate: dict[str, list[str]] = {}
        for r in refused:
            by_gate.setdefault(str(r.gate), []).append(str(r.symbol))
        for gate in sorted(by_gate):
            symbols = ", ".join(_line(s).upper() for s in sorted(by_gate[gate])[:12])
            flag = "" if gate in KNOWN_REFUSAL_GATES else " ⚠"
            lines.append(f"• `{_line(gate)}`{flag} ×{len(by_gate[gate])}: {symbols}")
    else:
        lines.append("• nothing refused")

    lines.append("")
    lines.append("__Budget remaining__")
    rails = budget_remaining or {}
    if rails:
        for rail in sorted(rails, key=str):
            lines.append(f"• {_line(rail)}: {_rail_value(rails[rail])}")
    else:
        # Fail-loud, not fail-quiet: an unreadable budget is exactly when the operator most
        # needs to know, because §2 says an unreadable budget means the lane is CLOSED.
        lines.append("• unavailable — budget rails could not be read (the lane trades nothing)")
    return "\n".join(lines)


RENDERERS: dict[str, Callable[..., str]] = {
    EVENT_QUEUED: render_queued,
    EVENT_SUBMITTED: render_submitted,
    EVENT_FILLED: render_filled,
    EVENT_CANCELLED: render_cancelled,
    EVENT_REFUSED: render_refused,
    EVENT_DAILY_DIGEST: render_daily_digest,
}


# ==============================================================================================
# the notifier
# ==============================================================================================
class CopyTradeNotifier:
    """Renders lane events and pushes them through the ``deliver`` seam, reporting the truth.

    ONE OBJECT PER PROCESS, alongside ``PaperAutoExecutor``. It holds a small ledger of
    per-proposal QUEUED delivery outcomes so ``resolve_due`` can ask, at fire time and without
    carrying the result around, "did the operator actually receive this?" — the direct analogue
    of ``AutoVetoCoordinator.confirm_delivery``, and the reason silence can never be read as
    consent.

    THE LEDGER IS BOUNDED. ``bridge/veto.py`` had to grow ``_prune_settled`` (BUG-080) after its
    ticket dict turned out to be append-only for the life of the process; this one is capped
    from the start, oldest dropped first.
    """

    _MAX_LEDGER = 1024

    def __init__(self, deliver: Callable[[str, object], bool] | None, *,
                 max_attempts: int = 2) -> None:
        self._deliver = deliver
        self.max_attempts = max(1, int(max_attempts))
        self._lock = threading.Lock()
        self._queued_results: dict[str, DeliveryResult] = {}
        self._counts: dict[str, int] = {}

    # ---- counters -------------------------------------------------------------------------
    def counters(self) -> dict[str, int]:
        """A COPY of every named outcome, ``"<event>.<status>"``. Every non-delivery is counted;
        an operator surface that cannot say how many notifications failed is one that will
        report a silent lane as a quiet one."""
        with self._lock:
            return dict(self._counts)

    def _count(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    # ---- the delivery primitive ------------------------------------------------------------
    def _send(self, event: str, text: str, *, proposal_id: str = "") -> DeliveryResult:
        channel = CHANNEL_BY_EVENT[event]
        if self._deliver is None:
            # A lane wired with no operator surface must not trade. Named, counted, loud.
            log.warning("copytrade notify: no deliver seam wired; %s NOT delivered", event)
            with self._lock:
                self._count(f"{event}.{DELIVERY_NO_SEAM}")
            return DeliveryResult(event, channel, DELIVERY_NO_SEAM, 0,
                                  "no deliver callable was supplied", text, proposal_id)

        status, detail, attempts = DELIVERY_FAILED, "", 0
        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            try:
                outcome = self._deliver(channel, text)
            except Exception as exc:                # noqa: BLE001 - delivery is reported, never raised
                # Type only, never the body: a urllib error repr can carry the request URL, and
                # the URL is where a bot token would surface (Inv 4). Same posture as
                # ``bridge/deliver.build_deliver``.
                status, detail = DELIVERY_RAISED, type(exc).__name__
                continue
            if outcome is True:
                status, detail = DELIVERY_CONFIRMED, ""
                break
            if outcome is False:
                status, detail = DELIVERY_FAILED, "seam returned False"
                continue
            # Anything else is a broken adapter. Reading a truthy non-bool as success is how a
            # lane starts trading against a surface that never posted anything.
            status, detail = DELIVERY_NOT_BOOLEAN, type(outcome).__name__
            break

        if status != DELIVERY_CONFIRMED:
            log.warning("copytrade notify: %s to %s NOT delivered (%s) after %d attempt(s)",
                        event, channel, status, attempts)
        with self._lock:
            self._count(f"{event}.{status}")
        return DeliveryResult(event, channel, status, attempts, detail, text, proposal_id)

    def _refuse_render(self, event: str, reason: str, proposal_id: str = "") -> DeliveryResult:
        log.warning("copytrade notify: refusing to render %s (%s) — nothing delivered, "
                    "nothing may trade", event, reason)
        with self._lock:
            self._count(f"{event}.{RENDER_REFUSED}")
        return DeliveryResult(event, CHANNEL_BY_EVENT[event], RENDER_REFUSED, 0, reason, "",
                              proposal_id)

    # ---- events ----------------------------------------------------------------------------
    def queued(self, facts: QueuedFacts, *, risk_decision) -> DeliveryResult:
        """Announce a queued auto-trade. **The executor must not resolve the ticket unless the
        returned result is ``confirmed``.**

        ``risk_decision`` is keyword-only and must be the stored ``RiskDecision`` ROW — see
        :func:`_risk_record_problem` for what is refused and why (SEC-17).
        """
        pid = str(getattr(facts, "proposal_id", "") or "")
        try:
            text = render_queued(facts, risk_decision=risk_decision)
        except Exception as exc:                    # noqa: BLE001 - a bad fact is a refusal, not a crash
            result = self._refuse_render(EVENT_QUEUED, str(exc), pid)
            self._remember(pid, result)
            return result
        result = self._send(EVENT_QUEUED, text, proposal_id=pid)
        self._remember(pid, result)
        return result

    def ensure_queued_delivered(self, facts: QueuedFacts, *, risk_decision) -> DeliveryResult:
        """Idempotent retry: return the CONFIRMED result if this proposal already got one,
        otherwise try again.

        §3 requires an undelivered notification to be retried rather than dropped, and a naive
        retry loop would re-post the same trade to the operator every tick. Confirmed results
        are cached; only failures are re-attempted.
        """
        pid = str(getattr(facts, "proposal_id", "") or "")
        with self._lock:
            cached = self._queued_results.get(pid)
        if cached is not None and cached.confirmed:
            return cached
        return self.queued(facts, risk_decision=risk_decision)

    def queued_delivery_confirmed(self, proposal_id: str) -> bool:
        """THE gate for ``PaperAutoExecutor.resolve_due``: did the operator receive the queued
        message for this proposal?

        Unknown proposal reads ``False``. A ticket this notifier has never heard of is one whose
        notification cannot be shown to have arrived, and that is the fail-closed answer.
        """
        with self._lock:
            result = self._queued_results.get(str(proposal_id))
        return bool(result is not None and result.confirmed)

    def _remember(self, proposal_id: str, result: DeliveryResult) -> None:
        if not proposal_id:
            return
        with self._lock:
            self._queued_results.pop(proposal_id, None)     # re-insert so age = last attempt
            self._queued_results[proposal_id] = result
            excess = len(self._queued_results) - self._MAX_LEDGER
            for key in list(self._queued_results)[:max(0, excess)]:
                del self._queued_results[key]

    def submitted(self, *, proposal_id: str, symbol: str, side: str, broker_order_id: str,
                  quantity=None, notional=None) -> DeliveryResult:
        return self._send(EVENT_SUBMITTED, render_submitted(
            proposal_id=proposal_id, symbol=symbol, side=side,
            broker_order_id=broker_order_id, quantity=quantity, notional=notional,
        ), proposal_id=str(proposal_id))

    def filled(self, *, proposal_id: str, symbol: str, side: str, broker_order_id: str,
               filled_qty=None, fill_price=None, notional=None) -> DeliveryResult:
        return self._send(EVENT_FILLED, render_filled(
            proposal_id=proposal_id, symbol=symbol, side=side,
            broker_order_id=broker_order_id, filled_qty=filled_qty, fill_price=fill_price,
            notional=notional,
        ), proposal_id=str(proposal_id))

    def cancelled(self, *, proposal_id: str, symbol: str, side: str, actor: str = "",
                  at=None) -> DeliveryResult:
        return self._send(EVENT_CANCELLED, render_cancelled(
            proposal_id=proposal_id, symbol=symbol, side=side, actor=actor, at=at,
        ), proposal_id=str(proposal_id))

    def refused(self, *, proposal_id: str, symbol: str, side: str, gate: str,
                detail: str = "") -> DeliveryResult:
        """Announce a named refusal. Exhaustion of a budget rail, a HALT, a breaker and a risk
        rejection all come through here — §2/§4 require every one of them to be NOTIFIED, never
        a silent stop."""
        if gate not in KNOWN_REFUSAL_GATES:
            with self._lock:
                self._count("refused.unknown_gate")
        return self._send(EVENT_REFUSED, render_refused(
            proposal_id=proposal_id, symbol=symbol, side=side, gate=gate, detail=detail,
        ), proposal_id=str(proposal_id))

    def daily_digest(self, *, day, executed: Sequence[DigestTrade] = (),
                     refused: Sequence[DigestRefusal] = (),
                     budget_remaining: Mapping[str, object] | None = None) -> DeliveryResult:
        return self._send(EVENT_DAILY_DIGEST, render_daily_digest(
            day=day, executed=executed, refused=refused, budget_remaining=budget_remaining,
        ))


@dataclass
class NullNotifier:
    """An explicitly-inert notifier for tests and for a lane that is configured OFF.

    It reports ``DELIVERY_NO_SEAM`` — i.e. NOT confirmed — for everything, so wiring it into a
    live lane by accident disables trading rather than enabling silent trading. There is
    deliberately no "notifier that always succeeds".
    """

    sent: list[tuple[str, str]] = field(default_factory=list)

    def _inert(self, event: str, proposal_id: str = "") -> DeliveryResult:
        self.sent.append((event, proposal_id))
        return DeliveryResult(event, CHANNEL_BY_EVENT[event], DELIVERY_NO_SEAM, 0,
                              "notifier disabled", "", proposal_id)

    def queued(self, facts, *, risk_decision) -> DeliveryResult:
        return self._inert(EVENT_QUEUED, str(getattr(facts, "proposal_id", "")))

    def ensure_queued_delivered(self, facts, *, risk_decision) -> DeliveryResult:
        return self.queued(facts, risk_decision=risk_decision)

    def queued_delivery_confirmed(self, proposal_id: str) -> bool:
        return False

    def submitted(self, **kw) -> DeliveryResult:
        return self._inert(EVENT_SUBMITTED, str(kw.get("proposal_id", "")))

    def filled(self, **kw) -> DeliveryResult:
        return self._inert(EVENT_FILLED, str(kw.get("proposal_id", "")))

    def cancelled(self, **kw) -> DeliveryResult:
        return self._inert(EVENT_CANCELLED, str(kw.get("proposal_id", "")))

    def refused(self, **kw) -> DeliveryResult:
        return self._inert(EVENT_REFUSED, str(kw.get("proposal_id", "")))

    def daily_digest(self, **kw) -> DeliveryResult:
        return self._inert(EVENT_DAILY_DIGEST)

    def counters(self) -> dict[str, int]:
        return {}


# ==============================================================================================
# RW-17 integration — the adapter between the EXECUTOR's seam and this module's typed API
# ==============================================================================================
# TWO SHAPES, DELIBERATELY DIFFERENT, JOINED IN EXACTLY ONE PLACE.
#
#   * ``PaperAutoExecutor`` holds ``notify(event, payload) -> bool``. It has to: it is the
#     order path, it must not import a renderer, and the ONLY thing it needs back is "was this
#     confirmed delivered", because that boolean gates execution.
#   * ``CopyTradeNotifier`` takes typed facts (``QueuedFacts`` + the stored ``RiskDecision``
#     ROW) because SEC-17 forbids rendering a risk number from anything except the record, and
#     a ``(event, dict)`` signature is precisely the shape that would let a caller-supplied
#     number reach the operator's decision surface.
#
# Neither may be bent toward the other, so the adapter lives here — the module that owns the
# rendering contract — and it is the ONE place the two vocabularies are reconciled.
#
# IT FAILS CLOSED IN EVERY DIRECTION. An unmapped event, an unparseable payload, a missing
# ``RiskDecision`` row and a renderer refusal all return ``False``, which the executor reads as
# UNDELIVERED, which means the trade does not happen. That asymmetry is the whole design: a
# false negative costs a skipped paper trade, a false positive costs a trade the operator never
# saw.

# The executor's refusal-reason vocabulary (``copytrade.autoexec.ALL_RESOLUTION_REASONS``) and
# this module's gate vocabulary were written independently and do not agree on every spelling.
# Rather than rename one — both are contracts, both are asserted in tests, and both appear in
# the audit chain — the disagreement is reconciled HERE, in a table an auditor can read. An
# unmapped reason is still rendered verbatim (hiding it would be worse) and is counted by
# ``CopyTradeNotifier.refused`` under ``refused.unknown_gate``, so drift stays visible.
REFUSAL_GATE_BY_REASON: dict[str, str] = {
    "mode_not_paper": GATE_MODE_NOT_PAPER,
    "not_paper_transport": GATE_MODE_NOT_PAPER,
    "halt": GATE_HALT,
    "watch_only": GATE_BREAKER_WATCH_ONLY,
    "breaker": GATE_BREAKER_ORDER_PATH,
    "breaker_tripped": GATE_BREAKER_ORDER_PATH,
    "audit_chain_unverified": GATE_AUDIT_CHAIN,
    "undelivered": GATE_UNDELIVERED,
    "clock_backwards": GATE_CLOCK_BACKWARDS,
    "gateway_refused": GATE_GATEWAY_REFUSED,
    "budget_daily_notional": GATE_BUDGET_DAILY_NOTIONAL,
    "budget_trades_per_day": GATE_BUDGET_TRADES_PER_DAY,
    "budget_open_positions": GATE_BUDGET_OPEN_POSITIONS,
    "budget_symbol_notional": GATE_BUDGET_SYMBOL_NOTIONAL,
    "budget_chain_unreadable": GATE_BUDGET_UNREADABLE,
    # ---- added by the RW-17 adversarial round ----
    # These eight were reachable refusals with NO mapping, so they reached the operator as
    # `Gate: approval_replay  ⚠ (unrecognised refusal code)` and were counted as
    # ``refused.unknown_gate``. That is the lane reporting its own drift marker instead of the
    # diagnosis — and the reasons it happened to omit were the ones produced by the most
    # alarming failure mode (a broker outage that burned the budget), i.e. exactly the incident
    # where the operator most needs the name to mean something.
    "pending_exposure": GATE_PENDING_EXPOSURE,
    "positions_unavailable": GATE_POSITIONS_UNAVAILABLE,
    "approval_replay": GATE_APPROVAL_REPLAY,
    "approval_write_failed": GATE_APPROVAL_FAILED,
    "order_missing": GATE_ORDER_MISSING,
    "ticket_expired": GATE_TICKET_EXPIRED,
    "superseded": GATE_SUPERSEDED,
    "queue_full": GATE_QUEUE_FULL,
    "abandoned_restart": GATE_ABANDONED_RESTART,
    "resolve_fault": GATE_RESOLVE_FAULT,
}


def _payload_date(value):
    """An ISO date off a JSON-native payload, or ``None``.

    ``None`` rather than a guess: ``missing_queued_facts`` refuses to render a ticket whose
    trade or filing date is unusable, and a fabricated date would make a stale signal look
    fresh — the single most misleading thing this feed could print.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def queued_facts_from_payload(payload: Mapping) -> QueuedFacts:
    """Rebuild :class:`QueuedFacts` from the executor's JSON-native notify payload.

    The facts originate as a ``copytrade.models.CopyIntent`` (deterministic engine output) and
    are carried across the ticket as ``AutoTicket.context`` — see
    ``main.build_copy_cycle_service``, which is what puts them there. Nothing is invented here:
    a fact the payload does not carry arrives as an empty string or ``None`` and
    :func:`missing_queued_facts` then REFUSES to render, which refuses delivery, which refuses
    the trade. That is the correct outcome for a message the operator could not have judged.

    The disclosure LAG is recomputed from the two dates rather than read from the payload, for
    the same reason :meth:`QueuedFacts.from_intent` recomputes it: it is the number that makes
    this not a signal edge, and it must be derivable from the filing.
    """
    tx_date = _payload_date(payload.get("tx_date"))
    filed_date = _payload_date(payload.get("filed_date"))
    notes = payload.get("sizing_notes") or ()
    return QueuedFacts(
        proposal_id=str(payload.get("proposal_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=str(payload.get("side") or ""),
        notional=payload.get("notional"),
        filer_display=str(payload.get("filer_display") or ""),
        tx_date=tx_date,
        filed_date=filed_date,
        disclosure_lag_days=_lag_days(tx_date, filed_date),
        amount_label=str(payload.get("amount_label") or ""),
        sizing_reason=str(payload.get("sizing_reason") or ""),
        cancel_command=str(payload.get("cancel_command") or ""),
        window_seconds=payload.get("window_seconds"),
        source_key=str(payload.get("source_key") or ""),
        sizing_notes=tuple(str(n) for n in notes),
        translated_from=payload.get("translated_from"),
    )


class AutoExecNotifyAdapter:
    """``notify(event, payload) -> bool`` on the outside; :class:`CopyTradeNotifier` inside.

    ``risk_decision_loader(risk_decision_id) -> RiskDecision | None`` is how the risk block
    reaches the renderer. It is a LOADER and not a payload field on purpose (SEC-17): the row
    is read back from the database by the id the ticket is bound to, so the operator's risk
    section cannot be sourced from anything a caller composed. With no loader wired the queued
    message cannot be rendered at all, so the lane notifies nothing and trades nothing — the
    fail-closed direction.
    """

    def __init__(self, notifier, *, risk_decision_loader=None) -> None:
        self._notifier = notifier
        self._load_risk_decision = risk_decision_loader
        self.unknown_events: dict[str, int] = {}

    @property
    def notifier(self):
        return self._notifier

    def counters(self) -> dict[str, int]:
        merged = dict(self._notifier.counters())
        for event, count in self.unknown_events.items():
            merged[f"{event}.unknown_event"] = count
        return merged

    def daily_digest(self, **kw) -> DeliveryResult:
        """The digest, TYPED, straight through to the notifier.

        Deliberately NOT routed through ``__call__``'s ``(event, payload)`` seam. That signature
        exists for the EXECUTOR, which must not import a renderer and needs only "was this
        confirmed"; the digest carries ``DigestTrade``/``DigestRefusal`` rows and a budget map,
        which a JSON-native payload dict cannot express without this module re-parsing its own
        output. The digest also has no order path behind it, so nothing here gates a trade."""
        return self._notifier.daily_digest(**kw)

    # ---- the executor's seam --------------------------------------------------------------
    def __call__(self, event: str, payload: Mapping) -> bool:
        try:
            result = self._dispatch(str(event), dict(payload or {}))
        except Exception:                   # noqa: BLE001 - a broken notification is UNDELIVERED
            log.exception("copytrade notify adapter: %r could not be delivered; the lane will "
                          "treat it as UNDELIVERED and will not trade", event)
            return False
        return bool(getattr(result, "confirmed", False))

    def _dispatch(self, event: str, payload: dict):
        if event == EVENT_QUEUED:
            return self._queued(payload)
        if event == EVENT_SUBMITTED:
            return self._notifier.submitted(
                proposal_id=str(payload.get("proposal_id") or ""),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or ""),
                broker_order_id=str(payload.get("broker_order_id") or ""),
                notional=payload.get("notional"),
            )
        if event == EVENT_CANCELLED:
            return self._notifier.cancelled(
                proposal_id=str(payload.get("proposal_id") or ""),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or ""),
                actor=str(payload.get("cancelled_by") or ""),
            )
        if event == EVENT_REFUSED:
            reason = str(payload.get("refused_by") or payload.get("gate") or "")
            return self._notifier.refused(
                proposal_id=str(payload.get("proposal_id") or ""),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or ""),
                gate=REFUSAL_GATE_BY_REASON.get(reason, reason),
                detail=str(payload.get("detail") or ""),
            )
        if event == EVENT_FILLED:
            return self._notifier.filled(
                proposal_id=str(payload.get("proposal_id") or ""),
                symbol=str(payload.get("symbol") or ""),
                side=str(payload.get("side") or ""),
                broker_order_id=str(payload.get("broker_order_id") or ""),
                filled_qty=payload.get("filled_qty"),
                fill_price=payload.get("fill_price"),
                notional=payload.get("notional"),
            )
        # An event this module does not render is NOT delivered. Counted and logged rather than
        # swallowed: it means the executor grew a notification the operator surface never got.
        self.unknown_events[event] = self.unknown_events.get(event, 0) + 1
        log.error("copytrade notify adapter: unknown event %r; nothing was delivered", event)
        return None

    def _queued(self, payload: dict):
        facts = queued_facts_from_payload(payload)
        risk_decision = None
        if self._load_risk_decision is not None:
            risk_decision = self._load_risk_decision(payload.get("risk_decision_id"))
        # ``ensure_queued_delivered`` and not ``queued``: the executor RETRIES this event on
        # every scheduler tick until it is confirmed, and a naive retry would re-post the same
        # pending trade to the operator every few seconds.
        return self._notifier.ensure_queued_delivered(facts, risk_decision=risk_decision)


# ==============================================================================================
# RW-17 adversarial round (B2) — the PRODUCER for the ``filled`` event
# ==============================================================================================
# :meth:`CopyTradeNotifier.filled` and :func:`render_filled` were fully implemented and called
# from NOWHERE. The operator therefore learned that an order had been SUBMITTED and never learned
# whether it filled — and RW-16 prices this lane's orders as LIMIT at the gate quote, so an order
# that rests unfilled all day is the NORMAL case, not an edge one. "Submitted" was being read as
# "done"; the two are not the same, and only one of them is a position.
#
# THE PRODUCER READS THE SIGNED CHAIN, exactly as ``copytrade/digest.py`` does and for the same
# reason: the fill is applied by whichever process ran the order-status reconcile tick (RW-15,
# ``brokers/order_status_poll.py``), which is not necessarily the process holding the executor's
# in-memory tickets — and after a restart there are no tickets at all. A signed
# ``EVENT_ORDER_TRANSITION`` to ``filled`` on an order this lane adjudicated is the fill, and it
# is readable by any process at any later time.
#
# IT IS ALSO IDEMPOTENT ACROSS RESTARTS, and that is not a nicety. An in-memory "already told
# them" set would re-announce every fill in the ledger on every boot, which trains the operator
# to ignore the one channel they have left. So a CONFIRMED fill notice appends its own
# :data:`EVENT_AUTOEXEC_FILL_NOTICE` entry, and the "already announced" set is re-derived from
# the chain. The marker is written ONLY on a confirmed delivery, so an unconfirmed one is retried
# on the next tick until it lands — the same retried-until-confirmed contract the executor now
# applies to its terminal notices.
EVENT_AUTOEXEC_FILL_NOTICE = "autoexec_fill_notice"
"""The signed record that the operator was TOLD about one fill. Append-only, HMAC-covered
(Invariant 6), and NOT an authority: nothing grants an order anything on the strength of one."""

_MAX_FILL_NOTICES_PER_TICK = 20
"""A backlog (a surface that was down all day, a first run against an old chain) is drained a
tick at a time rather than in one burst against the rate limit it would trip."""

_MAX_FILL_ATTEMPTS = 5
"""How many times ONE fill notice is retried in this process before it is abandoned — counted
and logged at error. Mirrors ``PaperAutoExecutor._MAX_NOTICE_ATTEMPTS``: a message describing
something that already happened must not retry forever against a dead surface."""


@dataclass(frozen=True)
class FillFacts:
    """One filled lane order, as the operator will be told about it.

    Every field is a STORED fact: the identity comes from the signed adjudication + transition
    payloads, and the fill quantity/price are the broker's own report as persisted on the
    ``Order`` row. Nothing is inferred — an unreported price renders as "unreported" rather than
    as the intent's price, because a fill the broker did not describe is not a fill we may
    describe for it (see :func:`render_filled`).
    """

    order_id: str
    proposal_id: str
    symbol: str
    side: str
    broker_order_id: str = ""
    filled_qty: object = None
    fill_price: object = None
    notional: object = None


def unannounced_lane_fills(st, *, lane: str | None = None) -> tuple[tuple[FillFacts, ...],
                                                                    str | None]:
    """Fills of THIS lane's orders that no confirmed notice covers yet, plus an error string.

    A non-None error means the ledger could not be trusted or read, and the caller must announce
    NOTHING: an unverifiable chain is exactly the state in which a forged transition could
    manufacture a "your trade filled" message. Entries that fail ``verify_entry`` are skipped in
    both directions — a forged fill cannot invent a notice, and a forged notice cannot suppress a
    real one.
    """
    from sqlalchemy import select

    from copytrade.budget import AUTOEXEC_LANE
    from orchestrator.state import EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION
    from schemas.models.audit_entry import AuditEntry
    from schemas.models.order import Order

    lane = AUTOEXEC_LANE if lane is None else lane
    try:
        verified = st.audit.verify_chain()
        if not verified.ok:
            return (), f"audit chain does not verify: {verified.error}"
    except Exception as exc:                     # noqa: BLE001 - a probe fault is unreadable
        return (), f"audit chain verification raised {type(exc).__name__}"

    try:
        with st.session_factory() as s:
            rows = s.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type.in_(
                    [EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION, EVENT_AUTOEXEC_FILL_NOTICE]))
                .order_by(AuditEntry.seq)
            ).scalars().all()
    except Exception as exc:                     # noqa: BLE001 - unreadable ledger announces nothing
        return (), f"audit entries unreadable ({type(exc).__name__})"

    lane_orders: dict[str, dict] = {}
    filled: dict[str, dict] = {}
    announced: set[str] = set()
    for row in rows:
        try:
            if not st.audit.verify_entry(row):
                continue
        except Exception:                        # noqa: BLE001 - an unverifiable row is skipped
            continue
        payload = row.payload or {}
        refs = row.refs or {}
        if row.event_type == EVENT_ADJUDICATION:
            if payload.get("lane") != lane or payload.get("decision") != "approve":
                continue
            order_id = str(payload.get("order_id") or "")
            if order_id:
                lane_orders[order_id] = payload
        elif row.event_type == EVENT_ORDER_TRANSITION:
            if payload.get("to") != "filled":
                continue
            # ``AuditLog.record_state_change`` stamps the row id into refs; the transition
            # payload does not repeat it. This is the ORDER row's id (``refs['model']`` says so).
            order_id = str(refs.get("row_id") or payload.get("order_id") or "")
            if order_id:
                filled[order_id] = payload
        else:                                    # EVENT_AUTOEXEC_FILL_NOTICE
            order_id = str(payload.get("order_id") or refs.get("order_id") or "")
            if order_id:
                announced.add(order_id)

    pending = [oid for oid in filled if oid in lane_orders and oid not in announced]
    if not pending:
        return (), None

    try:
        with st.session_factory() as s:
            order_rows = {
                r.id: r for r in s.execute(
                    select(Order).where(Order.id.in_(sorted(pending)))
                ).scalars().all()
            }
            for r in order_rows.values():
                s.expunge(r)
    except Exception as exc:                     # noqa: BLE001 - the identity is still signed
        log.warning("copytrade fills: the order rows could not be read (%s); the notice will "
                    "carry the signed identity without the broker's fill report",
                    type(exc).__name__)
        order_rows = {}

    out: list[FillFacts] = []
    for order_id in pending:
        adjudication = lane_orders[order_id]
        transition = filled[order_id]
        row = order_rows.get(order_id)
        out.append(FillFacts(
            order_id=order_id,
            proposal_id=str(adjudication.get("proposal_id") or ""),
            symbol=str(transition.get("symbol") or adjudication.get("symbol") or ""),
            side=str(transition.get("side") or adjudication.get("side") or ""),
            broker_order_id=str(getattr(row, "broker_order_id", "") or ""),
            filled_qty=getattr(row, "filled_qty", None),
            fill_price=getattr(row, "avg_fill_price", None),
            notional=transition.get("notional") or adjudication.get("notional"),
        ))
    return tuple(out), None


def _fill_seam(notify) -> Callable[..., bool] | None:
    """Adapt whatever the composition root wired into ``send(**facts) -> confirmed``.

    Accepts the typed :class:`CopyTradeNotifier` / :class:`AutoExecNotifyAdapter` (preferred —
    ``confirmed`` is then read off the real :class:`DeliveryResult`) or the executor's narrow
    ``notify(event, payload) -> bool`` callable. Returns ``None`` when neither is available, so
    the caller registers NO job rather than one that silently drops fills.
    """
    typed = getattr(notify, "notifier", notify)
    filled = getattr(typed, "filled", None)
    if callable(filled):
        def _send_typed(**facts) -> bool:
            return bool(getattr(filled(**facts), "confirmed", False))
        return _send_typed
    if callable(notify):
        def _send_seam(**facts) -> bool:
            return notify(EVENT_FILLED, dict(facts)) is True
        return _send_seam
    return None


class FillAnnouncer:
    """Turns signed fills of this lane's orders into ``filled`` notifications, exactly once.

    Read-only with respect to the order path: it decides nothing, mints nothing and moves
    nothing. The one thing it writes is the :data:`EVENT_AUTOEXEC_FILL_NOTICE` marker that makes
    a delivered notice permanent, so a restart cannot re-announce a fill and a failed delivery
    cannot be forgotten.
    """

    def __init__(self, notify, *, max_per_tick: int = _MAX_FILL_NOTICES_PER_TICK,
                 max_attempts: int = _MAX_FILL_ATTEMPTS) -> None:
        self._send = _fill_seam(notify)
        self._max_per_tick = max(1, int(max_per_tick))
        self._max_attempts = max(1, int(max_attempts))
        self._attempts: dict[str, int] = {}

    @property
    def usable(self) -> bool:
        return self._send is not None

    def announce(self, st) -> dict:
        """One tick. Returns a JSON-native report — never raises, and never reports a chain it
        could not read as a day with no fills."""
        if self._send is None:
            return {"readable": False, "error": "no notification seam", "announced": 0,
                    "undelivered": 0, "pending": 0}
        fills, error = unannounced_lane_fills(st)
        if error is not None:
            log.error("RW-17: the lane's fills could not be read (%s); NO fill notification was "
                      "sent — this is not a day without fills", error)
            return {"readable": False, "error": error, "announced": 0, "undelivered": 0,
                    "pending": 0}
        announced = undelivered = 0
        for facts in fills[:self._max_per_tick]:
            attempts = self._attempts.get(facts.order_id, 0)
            if attempts >= self._max_attempts:
                continue                         # abandoned in this process; already logged loud
            delivered = False
            try:
                delivered = bool(self._send(
                    proposal_id=facts.proposal_id, symbol=facts.symbol, side=facts.side,
                    broker_order_id=facts.broker_order_id, filled_qty=facts.filled_qty,
                    fill_price=facts.fill_price, notional=facts.notional,
                ))
            except Exception:                    # noqa: BLE001 - a failed post is UNDELIVERED
                log.exception("copytrade fills: delivering the fill notice for %s raised",
                              facts.order_id)
            self._attempts[facts.order_id] = attempts + 1
            if not delivered:
                undelivered += 1
                if self._attempts[facts.order_id] >= self._max_attempts:
                    log.error("RW-17: ABANDONING the fill notice for order %s after %d attempts "
                              "— the operator has NOT been told this order filled",
                              facts.order_id, self._attempts[facts.order_id])
                else:
                    log.warning("RW-17: the fill notice for order %s was NOT confirmed "
                                "delivered; it will be retried", facts.order_id)
                continue
            announced += 1
            self._mark_announced(st, facts)
        return {"readable": True, "error": None, "announced": announced,
                "undelivered": undelivered, "pending": len(fills)}

    def _mark_announced(self, st, facts: FillFacts) -> None:
        """Append the signed "the operator was told" marker. NEVER raises: a ledger fault must
        not turn a delivered notification into an exception. The cost of a lost marker is one
        duplicate notice later, which is the safe direction."""
        try:
            st.audit.append(
                EVENT_AUTOEXEC_FILL_NOTICE, actor="copytrade_fills",
                refs={"order_id": facts.order_id, "proposal_id": facts.proposal_id},
                payload={"order_id": facts.order_id, "proposal_id": facts.proposal_id,
                         "symbol": facts.symbol, "side": facts.side,
                         "broker_order_id": facts.broker_order_id,
                         "event": EVENT_FILLED},
            )
        except Exception:                        # noqa: BLE001 - a marker fault is not an outcome
            log.exception("copytrade fills: could not record the fill notice for %s; it may be "
                          "re-announced", facts.order_id)


def build_fill_service(notify):
    """RW-17 — the scheduler service that PRODUCES the ``filled`` event. ``(st, clock) -> dict``.

    Returns ``None`` when there is no usable notification seam, so ``build_scheduler`` registers
    no job rather than one that quietly drops every fill (the ingest posture: an unprovisioned
    thing gets no job at all, never a silent one).

    Wire it in the composition root beside the digest service::

        fills = copytrade.notify.build_fill_service(wiring.notify)
        if fills is not None:
            services["autoexec_fills"] = fills
    """
    announcer = FillAnnouncer(notify)
    if not announcer.usable:
        return None

    def fills(st, clock) -> dict:
        del clock                                # every fact here is stored, none is "now"
        return announcer.announce(st)

    fills.announcer = announcer                  # type: ignore[attr-defined]
    return fills


def build_notifier(deliver, *, st=None, settings=None, clock=None, max_attempts: int = 2):
    """RW-17 §3/§4 — the factory ``main.build_autoexec_notify`` composes the lane through.

    Returns an :class:`AutoExecNotifyAdapter`: callable as the executor's notify seam, backed by
    a real :class:`CopyTradeNotifier` over the abstract ``deliver(channel, text) -> bool`` seam.

    ``st`` is the orchestrator state, used for ONE thing — reading the stored ``RiskDecision``
    row back by id so the queued message's risk block comes from the record and not from a
    payload (SEC-17). ``settings`` and ``clock`` are accepted and unused: the channel map is a
    literal in this module (a queued trade goes to the proposals channel wherever it is
    configured, and ``build_deliver`` owns the ids), and every timestamp in these messages is a
    STORED fact off the filing rather than a reading of now.
    """
    del settings, clock                     # accepted for the composition seam; see the docstring
    notifier = CopyTradeNotifier(deliver, max_attempts=max_attempts)
    return AutoExecNotifyAdapter(notifier, risk_decision_loader=_risk_decision_loader(st))


def _risk_decision_loader(st):
    """``(risk_decision_id) -> RiskDecision | None``, or ``None`` when there is no state.

    Detaches the row from its session before returning it: the renderer reads attributes off it
    outside any session, and a lazily-loading instance would raise there — which the adapter
    would (correctly, but confusingly) report as an undelivered notification.
    """
    if st is None:
        return None

    def _load(risk_decision_id):
        if not risk_decision_id:
            return None
        from schemas.models.risk_decision import RiskDecision

        with st.session_factory() as session:
            row = session.get(RiskDecision, str(risk_decision_id))
            if row is not None:
                session.expunge(row)
            return row

    return _load


__all__ = [
    "ALL_DELIVERY_STATUSES",
    "ALL_EVENTS",
    "CHANNEL_BY_EVENT",
    "REFUSAL_GATE_BY_REASON",
    "AutoExecNotifyAdapter",
    "CopyTradeNotifier",
    "DELIVERY_CONFIRMED",
    "DELIVERY_FAILED",
    "DELIVERY_NOT_BOOLEAN",
    "DELIVERY_NO_SEAM",
    "DELIVERY_RAISED",
    "DeliveryResult",
    "DigestRefusal",
    "DigestTrade",
    "FillAnnouncer",
    "FillFacts",
    "EVENT_AUTOEXEC_FILL_NOTICE",
    "EVENT_CANCELLED",
    "EVENT_DAILY_DIGEST",
    "EVENT_FILLED",
    "EVENT_QUEUED",
    "EVENT_REFUSED",
    "EVENT_SUBMITTED",
    "GATE_AUDIT_CHAIN",
    "GATE_BREAKER_ORDER_PATH",
    "GATE_BREAKER_WATCH_ONLY",
    "GATE_BUDGET_DAILY_NOTIONAL",
    "GATE_BUDGET_OPEN_POSITIONS",
    "GATE_BUDGET_SYMBOL_NOTIONAL",
    "GATE_BUDGET_TRADES_PER_DAY",
    "GATE_BUDGET_UNREADABLE",
    "GATE_CLOCK_BACKWARDS",
    "GATE_GATEWAY_REFUSED",
    "GATE_HALT",
    "GATE_MODE_NOT_PAPER",
    "GATE_RISK_REJECTED",
    "GATE_TICKET_EXPIRED",
    "GATE_UNDELIVERED",
    "KNOWN_REFUSAL_GATES",
    "NullNotifier",
    "QueuedFacts",
    "RENDERERS",
    "RENDER_REFUSED",
    "build_fill_service",
    "build_notifier",
    "missing_queued_facts",
    "unannounced_lane_fills",
    "queued_facts_from_payload",
    "render_cancelled",
    "render_daily_digest",
    "render_filled",
    "render_queued",
    "render_refused",
    "render_risk_line",
    "render_submitted",
]
