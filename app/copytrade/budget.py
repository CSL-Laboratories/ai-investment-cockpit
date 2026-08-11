"""RW-17 §2 — the budget rails that replace the human approver on the PAPER auto lane.

WHY THIS MODULE EXISTS AT ALL
-----------------------------
A human tapping *Approve* on every copy order was, incidentally, the thing that bounded how
much damage one bad day could do. RW-17 removes that tap (the operator asked for it), so the
bound has to be re-created explicitly or it simply disappears. These four rails ARE that
bound. They are not configuration garnish; deleting one deletes a safety control.

  * ``max_daily_notional``            — total $ the lane may commit in one UTC day;
  * ``max_trades_per_day``            — how many things may happen while nobody is looking;
  * ``max_open_positions``            — how many distinct names the lane may hold at once;
  * ``max_notional_per_symbol_per_day`` — so one hot ticker cannot eat the whole day.

THE SPEND IS DERIVED FROM THE SIGNED CHAIN, NEVER FROM A COUNTER
---------------------------------------------------------------
An in-memory tally is reset by a restart, and "restart the process" is not an authorization
mechanism. So every number here is re-derived, on every call, from HMAC-VERIFIED
``EVENT_ADJUDICATION`` entries carrying ``payload['lane'] == AUTOEXEC_LANE`` — the same
posture ``gateway._pending_ok`` takes toward pending exposure, and for the same threat model
(a keyless writer with arbitrary UPDATE/DELETE on the domain tables can rewrite a table but
cannot forge an HMAC-signed payload).

FAIL CLOSED: if ``verify_chain()`` is not ok, if the external anchor (when wired) does not
cover the verified head, or if the entries cannot be read at all, the budget is EXHAUSTED —
:data:`RAIL_CHAIN_UNREADABLE`. An unreadable ledger means the spend is UNKNOWN, and unknown
spend must never read as zero spend.

THE COUNTING MOMENT IS THE ADJUDICATION, NOT THE FILL
-----------------------------------------------------
Spend is counted when the lane MINTS its auto-approval, which is strictly before the broker
call. So an approval whose submit then failed still consumes budget. That over-counts, and
over-counting is the fail-safe direction: it can only ever produce FEWER auto-trades. The
alternative (count fills) would let a flapping broker burn an unbounded number of approvals
against a budget that never moves.

The same asymmetry governs :attr:`BudgetState.open_symbols`: a BUY opens a position the instant
the lane adjudicates it, but a position is only CLOSED once a signed ``EVENT_ORDER_TRANSITION``
shows a SELL of that symbol actually reaching the broker. A sell that was adjudicated and then
refused at the gateway must not free a slot it never freed in the account.

The closing edge is deliberately NOT lane-scoped (RW-17 adversarial round). Reading only the
lane's own adjudications meant a name the lane bought and the operator later sold BY HAND was
never released — the human gate's adjudication carries no ``lane`` marker — so with the shipped
default of ten the rail ratcheted to a permanent floor and then refused every new name forever,
reporting an exhausted budget when the truth was a stale ledger. A rail that bounds concurrent
exposure has to read the account, and the signed order transitions ARE the account.

THE SAME RATCHET HAD A SECOND SOURCE, AND IT IS CLOSED THE SAME WAY (C2). An adjudicated buy
the GATEWAY then refused — gate (d) risk, the SEC-07 pending-exposure cap, a tripped breaker,
HALT — opened a slot that no event could ever release: there is no position, so there can be
no sell, and open positions are correctly not day-scoped. Ten of those and the lane refused
every new name forever. So the opening edge is now qualified by the order path's OWN signed
evidence: a lane buy whose order id carries a signed ``order_refused`` and NEITHER a signed
submit intent NOR a transition into a broker-reached state never opened anything, and holds no
slot. Everything else still opens on the adjudication, unconditionally and immediately.

That qualification is strictly evidence-based, never a timer, and it is asymmetric on purpose:
  * a refusal recorded AFTER the two-phase ``intent:order_submit`` may be response loss on an
    order that LANDED (``gateway`` says so in as many words), so it releases NOTHING;
  * an order that ever reached ``submitted``/``partially_filled``/``filled`` releases NOTHING
    on a later refusal (a second attempt on a real position is still a real position);
  * an adjudication with no ``order_id`` cannot be matched to a refusal and keeps the old
    over-counting behaviour rather than being released on a guess.
Unknown always reads as OPEN. The rail only ever gives back a slot the ledger proves was never
taken.

EVERY REFUSAL IS NAMED AND COUNTABLE. :data:`ALL_RAILS` is a closed vocabulary the operator
surface aggregates on — "the lane did nothing today" has to be a positive statement, because a
budget that silently stops is indistinguishable from a budget that is broken.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, NamedTuple

from sqlalchemy import select

from orchestrator.state import EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION
from schemas.models.audit_entry import AuditEntry

# The marker this lane stamps into its signed adjudication payloads. It is what makes the
# lane's own spend separable from a human adjudication on the same chain: a human approval
# must never consume the robot's budget, and a robot approval must never be mistaken for a
# human one. ``copytrade/autoexec.py`` is the only writer.
AUTOEXEC_LANE = "copytrade_autoexec"

# ---- the closed refusal vocabulary ----------------------------------------------------
RAIL_CHAIN_UNREADABLE = "budget_chain_unreadable"
RAIL_DAILY_NOTIONAL = "budget_daily_notional"
RAIL_TRADES_PER_DAY = "budget_trades_per_day"
RAIL_SYMBOL_NOTIONAL = "budget_symbol_notional"
RAIL_OPEN_POSITIONS = "budget_open_positions"
ALL_RAILS: tuple[str, ...] = (
    RAIL_CHAIN_UNREADABLE,
    RAIL_DAILY_NOTIONAL,
    RAIL_TRADES_PER_DAY,
    RAIL_SYMBOL_NOTIONAL,
    RAIL_OPEN_POSITIONS,
)

# Order states that mean "this order is live in the account". Mirrors the gateway's open set
# plus ``filled`` — a filled buy is exactly the position ``max_open_positions`` bounds.
_SUBMITTED_STATES = frozenset({"submitted", "partially_filled", "filled"})

# Two order-path event names, by STRING and not by import: ``brokers.gateway`` pulls in the
# whole order path (risk engine, capability registry, state machine) and this module is read
# from inside it. ``test_budget`` pins both against their real writers, so a rename cannot
# quietly turn the release edge below into dead code — which would be the ratchet, back, with
# every test still green.
#   * ``order_refused``        — ``OrderGateway._record_refusal`` (gateway.EVENT_ORDER_REFUSED),
#                                written for every gate refusal, refs ``order_id``;
#   * ``intent:order_submit``  — ``AuditLog.intent("order_submit")``, written immediately BEFORE
#                                the broker call, refs ``order_id``. Its presence makes a later
#                                refusal AMBIGUOUS (the order may have landed).
_ORDER_REFUSED = "order_refused"
_SUBMIT_INTENT = "intent:order_submit"


def _dec(x) -> Decimal:
    """Decimal-or-NaN. NaN propagates into every comparison as False, so a malformed notional
    can never satisfy a ``<=`` bound — it is refused, not admitted."""
    try:
        return x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("NaN")


def _naive_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class AutoExecBudget:
    """The four rails, as one immutable policy object.

    The defaults are deliberately SMALL. This is the configuration of a robot that trades
    without asking, so the shipped default has to be a number the operator would shrug at
    losing entirely on the day they discover the feature is misbehaving. Widening them is the
    operator's explicit act, exactly like enabling the lane at all.

    They are also the SAME numbers ``config/settings.py`` ships for the corresponding
    ``congress_autoexec_*`` fields, and ``test_budget`` pins the two together. A dataclass
    default that quietly disagreed with the configured default would mean the rail an operator
    reads in ``.env.example`` is not the rail an un-wired caller actually gets.
    """

    max_daily_notional: Decimal = Decimal("500")
    max_trades_per_day: int = 3
    max_open_positions: int = 10
    max_notional_per_symbol_per_day: Decimal = Decimal("250")

    def __post_init__(self) -> None:
        # A rail that cannot be evaluated is not a rail. Refuse a malformed policy at
        # construction rather than discovering it as a silently-dead control at run time —
        # the same failure ``CopyPolicy`` refuses for ``options_haircut``.
        for name in ("max_daily_notional", "max_notional_per_symbol_per_day"):
            value = _dec(getattr(self, name))
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative Decimal, got "
                                 f"{getattr(self, name)!r}")
            object.__setattr__(self, name, value)
        for name in ("max_trades_per_day", "max_open_positions"):
            try:
                value = int(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be an integer, got "
                                 f"{getattr(self, name)!r}") from exc
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
            object.__setattr__(self, name, value)

    @classmethod
    def from_settings(cls, settings) -> "AutoExecBudget":
        """Read the rails off ``Settings`` by ``getattr`` with the conservative defaults above.

        ``getattr`` rather than attribute access on purpose: this module must keep working
        against a ``Settings`` that predates the RW-17 fields (and against the injected test
        settings the suite builds), and the fail-safe answer to "that knob is not configured"
        is the small default, never "unbounded".
        """
        return cls(
            max_daily_notional=_dec(getattr(
                settings, "congress_autoexec_max_daily_notional", cls.max_daily_notional)),
            max_trades_per_day=int(getattr(
                settings, "congress_autoexec_max_trades_per_day", cls.max_trades_per_day)),
            max_open_positions=int(getattr(
                settings, "congress_autoexec_max_open_positions", cls.max_open_positions)),
            max_notional_per_symbol_per_day=_dec(getattr(
                settings, "congress_autoexec_max_notional_per_symbol_per_day",
                cls.max_notional_per_symbol_per_day)),
        )


@dataclass(frozen=True)
class BudgetState:
    """What the SIGNED chain says this lane has already committed.

    ``readable=False`` is the fail-closed value: the chain could not be verified or could not
    be read, so nothing is known and :func:`check` refuses everything with
    :data:`RAIL_CHAIN_UNREADABLE`. ``error`` carries the human-readable why, for the notifier.
    """

    day: date
    readable: bool = True
    spent_notional: Decimal = Decimal("0")
    trades: int = 0
    per_symbol: Mapping[str, Decimal] = field(default_factory=dict)
    open_symbols: frozenset[str] = frozenset()
    error: str | None = None

    def remaining(self, budget: AutoExecBudget) -> dict:
        """A JSON-native "what is left today" snapshot for the daily digest. All-zero when the
        chain is unreadable, because an unknown spend leaves no headroom by definition."""
        if not self.readable:
            return {"readable": False, "error": self.error, "notional": "0", "trades": 0,
                    "open_position_slots": 0}
        return {
            "readable": True,
            "error": None,
            "notional": str(max(budget.max_daily_notional - self.spent_notional, Decimal("0"))),
            "trades": max(budget.max_trades_per_day - self.trades, 0),
            "open_position_slots": max(
                budget.max_open_positions - len(self.open_symbols), 0),
        }


class BudgetCheck(NamedTuple):
    """``(allowed, reason)`` — a tuple so the spec's two-value contract unpacks directly, with
    ``detail`` carried alongside for the refusal notification (an exhausted rail must tell the
    operator WHICH rail and by how much, never just "no")."""

    allowed: bool
    reason: str | None = None
    detail: str | None = None


def _unreadable(day: date, error: str) -> BudgetState:
    return BudgetState(day=day, readable=False, error=error)


def _orders_that_never_reached_the_broker(verified) -> frozenset[str]:
    """Order ids the SIGNED chain proves were refused BEFORE any broker call (C2).

    Order-INDEPENDENT by construction — it asks "does this id have refusal evidence and no
    reached-the-broker evidence anywhere in the chain", which is why it is a separate pass over
    the same rows rather than a branch inside the seq-ascending replay: a refusal at seq 5 and a
    submit at seq 40 (a re-adjudicated order that landed the second time) must resolve to
    LANDED whichever one the replay meets first.

    Evidence that an order DID reach the broker, any one of which disqualifies it:
      * a signed ``intent:order_submit`` — the two-phase intent is written immediately before
        the broker call, so from here on a refusal is ambiguous, not proof of nothing;
      * a signed transition into ``submitted``/``partially_filled``/``filled``.
    """
    refused: set[str] = set()
    reached: set[str] = set()
    for row in verified:
        payload = row.payload or {}
        refs = row.refs or {}
        if row.event_type == _ORDER_REFUSED:
            order_id = str(refs.get("order_id") or payload.get("order_id") or "")
            if order_id:
                refused.add(order_id)
        elif row.event_type == _SUBMIT_INTENT:
            order_id = str(refs.get("order_id") or "")
            if order_id:
                reached.add(order_id)
        elif (row.event_type == EVENT_ORDER_TRANSITION
                and payload.get("to") in _SUBMITTED_STATES):
            # ``record_state_change`` refs the order row as ``row_id``; the id is inside the
            # HMAC material (``_canonical`` hashes refs), so this is signed evidence too.
            order_id = str(refs.get("row_id") or "")
            if order_id:
                reached.add(order_id)
    return frozenset(refused - reached)


def budget_state(st, *, day: date) -> BudgetState:
    """Re-derive today's lane spend from the HMAC-verified audit chain. Never a counter.

    ``day`` is a UTC calendar date, matched against the SIGNED ``payload['_ts']`` that
    ``AuditLog._insert`` stamps in naive UTC (SEC-12). Using the signed timestamp rather than
    the mutable ``created_at`` column means a keyless writer cannot move yesterday's spend out
    of today's window by editing a date.

    Fail-closed layers, in the order they are applied — any one of them yields
    ``readable=False`` and therefore an exhausted budget:

      1. ``verify_chain()`` is not ok (unhealed truncation/tamper), or raises;
      2. an external anchor is wired but does not cover the verified head (SEC-11 rollback);
      3. the entries cannot be read (DB fault).

    Individual entries that fail ``verify_entry`` are SKIPPED rather than fatal, matching
    ``copytrade/universe.py``: a forged entry must not be able to CREATE spend it can also
    not be allowed to hide behind. Skipping only ever lowers the derived spend, which would
    be the wrong direction — so note the belt: a keyless writer who DELETES a lane entry to
    free budget breaks chain contiguity and trips layer (1) instead.
    """
    try:
        verified = st.audit.verify_chain()
    except Exception as exc:                      # noqa: BLE001 - any probe fault is unreadable
        return _unreadable(day, f"audit chain verification raised {type(exc).__name__}")
    if not verified.ok:
        return _unreadable(day, f"audit chain does not verify: {verified.error}")

    # Same anchor consult the gateway's pending-exposure sum performs: a replayed in-DB
    # watermark can only be caught by the out-of-DB witness (SEC-11).
    anchor_store = getattr(st, "anchor_store", None)
    if anchor_store is not None:
        try:
            anchor = anchor_store.latest()
            anchored = (anchor is not None
                        and st.audit.verify_against_anchor(anchor[0], anchor[1]).ok)
        except Exception as exc:                  # noqa: BLE001 - unreadable witness fails closed
            return _unreadable(day, f"audit anchor unreadable ({type(exc).__name__})")
        if not anchored:
            return _unreadable(day, "audit chain does not cover the external anchor (SEC-11)")

    try:
        with st.session_factory() as s:
            rows = s.execute(
                select(AuditEntry)
                .where(AuditEntry.event_type.in_(
                    [EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION, _ORDER_REFUSED, _SUBMIT_INTENT]))
                .order_by(AuditEntry.seq)
            ).scalars().all()
    except Exception as exc:                      # noqa: BLE001 - unreadable ledger fails closed
        return _unreadable(day, f"audit entries unreadable ({type(exc).__name__})")

    # HMAC-filter ONCE, then read the surviving rows twice. Every derivation below rests on the
    # signed record and nothing else, so an unverifiable row must be invisible to both passes.
    verified_rows = []
    for row in rows:
        try:
            if not st.audit.verify_entry(row):
                continue
        except Exception:                         # noqa: BLE001 - an unverifiable row is skipped
            continue
        verified_rows.append(row)

    # Pass 1 (order-independent): which adjudicated orders provably never reached the broker.
    never_landed = _orders_that_never_reached_the_broker(verified_rows)

    spent = Decimal("0")
    trades = 0
    per_symbol: dict[str, Decimal] = {}
    open_symbols: set[str] = set()

    # Pass 2: ONE seq-ascending replay over BOTH event types, deliberately interleaved. The
    # open-position set is a REPLAY of the account's history and the two event types are its two
    # edges, so they have to be read in the order they actually happened: a buy at seq 10
    # followed by a sell at seq 20 is a closed position; the same two entries read out of order
    # is an open one.
    for row in verified_rows:
        if row.event_type not in (EVENT_ADJUDICATION, EVENT_ORDER_TRANSITION):
            continue                              # refusals/intents are pass-1 evidence only
        payload = row.payload or {}

        if row.event_type == EVENT_ORDER_TRANSITION:
            # A POSITION IS CLOSED BY THE ACCOUNT, NOT BY THIS LANE. Previously a slot was
            # released only by a LANE sell whose order reached the broker, so a name the lane
            # bought and the OPERATOR later sold by hand — through the human paper gate, whose
            # adjudication carries no ``lane`` marker — was never released. With the shipped
            # default of ten open positions the rail ratcheted to a permanent floor: after the
            # tenth distinct name the lane had ever bought, every new name was refused forever
            # with ``budget_open_positions``, reporting an exhausted budget when the truth was a
            # stale ledger. Any signed sell that REACHED the broker now frees the slot,
            # whichever surface adjudicated it.
            #
            # ``side`` is an RW-17 addition to the transition payload; entries written before it
            # simply do not release, which is the pre-existing (over-counting, fail-safe)
            # behaviour rather than a new one.
            if (payload.get("to") in _SUBMITTED_STATES
                    and str(payload.get("side") or "").lower() == "sell"):
                open_symbols.discard(str(payload.get("symbol") or "").upper())
            continue

        if payload.get("lane") != AUTOEXEC_LANE or payload.get("decision") != "approve":
            continue

        symbol = str(payload.get("symbol") or "").upper()
        side = str(payload.get("side") or "").lower()
        notional = _dec(payload.get("notional"))
        if not notional.is_finite() or notional < 0:
            # A lane entry whose signed notional is unusable is counted as a TRADE but its
            # dollars cannot be summed. Treat it as consuming the whole daily notional rather
            # than as free: an unreadable commitment is not a zero commitment.
            notional = Decimal("NaN")

        if side == "buy" and str(payload.get("order_id") or "") not in never_landed:
            # Immediately, and BEFORE the order is known to have reached the broker: an
            # adjudicated buy still in flight over-counts, and over-counting is the only safe
            # direction for a rail that bounds concurrent exposure. The ONE exception is an
            # order the chain PROVES was refused before any broker call (C2) — that is not
            # over-counting, it is a slot nothing can ever give back. An adjudication with no
            # ``order_id`` has an empty key, which is never in ``never_landed``, so it opens.
            open_symbols.add(symbol)

        try:
            ts = _naive_utc(datetime.fromisoformat(str(payload.get("_ts"))))
        except (TypeError, ValueError):
            continue                              # unplaceable in time -> not attributable to a day
        if ts.date() != day:
            continue
        trades += 1
        spent = spent + notional
        per_symbol[symbol] = per_symbol.get(symbol, Decimal("0")) + notional

    return BudgetState(
        day=day, readable=True, spent_notional=spent, trades=trades,
        per_symbol=dict(per_symbol), open_symbols=frozenset(open_symbols),
    )


def check(state: BudgetState, budget: AutoExecBudget, *, symbol: str, side: str,
          notional) -> BudgetCheck:
    """Would ONE more trade fit inside the rails? ``(allowed, reason, detail)``.

    Rails are evaluated in a fixed order so the reported reason is deterministic and
    replayable. Every side is counted against every rail, with ONE structural exception that
    needs no special case: ``max_open_positions`` is evaluated on the set that WOULD be open
    afterwards, and a sell never adds a symbol to that set — so an exit can never be refused
    for "too many open positions". That is the same "a bound may prevent a new buy but must
    never block an exit" property ``copytrade/universe.py`` documents.

    The other three rails DO apply to exits, and that is deliberate rather than an oversight:
    ``max_trades_per_day`` is the operator's real question — *how many things may happen while
    I am not looking* — and a refused exit is a NAMED, NOTIFIED refusal the operator can act on
    by hand, not a silently dropped one. Fail closed, then tell them.
    """
    if not state.readable:
        return BudgetCheck(False, RAIL_CHAIN_UNREADABLE,
                           state.error or "the audit chain could not be read")

    symbol = str(symbol or "").upper()
    side = str(side or "").lower()
    want = _dec(notional)
    if not want.is_finite() or want < 0:
        # Not a rail the operator configured — an unpriceable order cannot be measured against
        # any of them, so it cannot be admitted by any of them either.
        return BudgetCheck(False, RAIL_DAILY_NOTIONAL,
                           f"order notional {notional!r} is not a finite, non-negative amount")

    if state.trades + 1 > budget.max_trades_per_day:
        return BudgetCheck(
            False, RAIL_TRADES_PER_DAY,
            f"{state.trades} auto-trade(s) already today; the daily limit is "
            f"{budget.max_trades_per_day}",
        )

    spent = state.spent_notional
    if not spent.is_finite() or spent + want > budget.max_daily_notional:
        return BudgetCheck(
            False, RAIL_DAILY_NOTIONAL,
            f"${spent} already committed today + ${want} would exceed the daily cap "
            f"${budget.max_daily_notional}",
        )

    sym_spent = _dec(state.per_symbol.get(symbol, Decimal("0")))
    if not sym_spent.is_finite() or sym_spent + want > budget.max_notional_per_symbol_per_day:
        return BudgetCheck(
            False, RAIL_SYMBOL_NOTIONAL,
            f"${sym_spent} already committed in {symbol} today + ${want} would exceed the "
            f"per-symbol cap ${budget.max_notional_per_symbol_per_day}",
        )

    # The rail bites only on a trade that OPENS A NEW NAME. Evaluated as "would this ADD a
    # symbol, and would the result exceed the cap" rather than "is the resulting set too big":
    # the latter refuses an EXIT (and a top-up of a name already held) whenever the book is
    # already over a freshly-LOWERED cap, which is a control that blocks the very action that
    # would bring the book back inside it. Lowering the cap must stop new names, not strand
    # the existing ones.
    opens_a_new_name = side == "buy" and symbol not in state.open_symbols
    if opens_a_new_name and len(state.open_symbols) + 1 > budget.max_open_positions:
        return BudgetCheck(
            False, RAIL_OPEN_POSITIONS,
            f"{len(state.open_symbols)} open auto position(s) ({', '.join(sorted(state.open_symbols))}); "
            f"opening {symbol} would exceed the limit of {budget.max_open_positions}",
        )

    return BudgetCheck(True, None, None)
