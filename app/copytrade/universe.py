"""RW-16 — the copy-trade UNIVERSE: an audited, bounded, TTL-expiring promotion lane.

THE PROBLEM THIS EXISTS TO SOLVE
--------------------------------
``risk.engine.evaluate``'s ``symbol_allowlist`` hard check is
``symbol in ctx.holdings or symbol in ctx.watchlist`` (SEC-15) — the reconciled trust
anchor that stops an order being drafted for a name nobody ever admitted into the system.
A copy-traded congressional name is, BY DEFINITION, not on the operator's static
watchlist: the whole point of the feed is that it surfaces names the operator did not
pick. So the copy path had exactly three options:

  1. widen the static watchlist  — NO. It is the operator's own, hand-maintained,
     reviewable list; a feed that can write to it turns an allowlist into a suggestion box.
  2. weaken the risk check       — NO. ``risk/`` is pure and does not change (Invariant 5),
     and a check that has an exemption for one caller is not a check.
  3. build the DISCOVERY -> PROMOTION lane the refactor plan already settled
     (docs/refactor-plan.md §3.2 / RW-05, and CLAUDE.md's "the universe is DYNAMIC").

This module is option 3, scoped to copy-trading. A symbol becomes eligible for the risk
engine to JUDGE because a FOLLOWED filer disclosed a trade in it — and for no other reason.

**PROMOTION IS NOT APPROVAL TO TRADE.** It is strictly weaker: it moves a symbol from
"the risk engine will reject this on sight" to "the risk engine may now evaluate it on its
merits". Every hard check other than ``symbol_allowlist`` still runs, the mode ladder still
runs, and a human still approves. Nothing here can cause an order.

WHY THE AUDIT CHAIN IS THE STORE (and not a table)
--------------------------------------------------
The promoted set is an INPUT TO A RISK CHECK. A mutable ``copy_universe`` table would let
any local DB writer — with no HMAC key, no audit entry, no trace — insert one row and make
an arbitrary symbol tradable. Deriving the set from HMAC-signed ``EVENT_COPY_UNIVERSE``
entries means widening the allowlist requires the key, exactly like widening the audited
runtime limits does (``orchestrator/limits.py`` — the same pattern, for the same reason).
Consequences, all deliberate:

  * an entry that fails ``verify_entry`` is SKIPPED — a forged promotion never widens the
    allowlist (fail closed; a tampered row can only tighten by being ignored);
  * a chain that does not ``verify_chain`` yields the EMPTY universe — the tightest possible
    value, matching the mode oracle's fail-closed floor;
  * the promotion timestamp is the entry's SIGNED ``payload["_ts"]`` (SEC-12), so the TTL
    cannot be extended by editing a mutable column.

BOUNDS, AND WHAT THEY CAN AND CANNOT DO
---------------------------------------
``policy.max_universe_symbols`` caps the set; ``policy.universe_ttl_days`` expires it.
``promote`` REFUSES the overflow and counts it — it NEVER evicts an incumbent to make room.
That direction is chosen on purpose: eviction-to-make-room means a burst of fresh filings
could silently drop a symbol the account is actually holding out of the allowlist, and a
control that removes protection under load is worse than no control.

The safety property that makes both the cap and the TTL non-dangerous: **they can only ever
prevent a NEW BUY, never block an EXIT.** ``symbol_allowlist`` is
``holdings OR watchlist``, and a sell must be a reconciled holding anyway (``sell_backed``),
so a held symbol passes the allowlist through ``ctx.holdings`` whether or not it is in the
universe. Expiry therefore stops the system opening NEW copies of a stale name; it can
never strand a position.

Not pure: this module reads and appends to the audit chain. The pure half of copy-trading
lives in ``copytrade/{models,policy,sizing,options,engine}.py``.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from copytrade.policy import policy_from_settings
from ingest.normalize import escape_untrusted
from schemas.models.audit_entry import AuditEntry

# The one audit event type this lane owns. Module-local to ``copytrade`` by the same
# convention ``workflows/constants.py`` follows: an event only this package reads does not
# belong in the orchestrator's shared registry.
EVENT_COPY_UNIVERSE = "copy_universe"

# Same ticker shape as ``ingest.normalize`` and ``TradeProposal.schema.json#/symbol``. Kept
# as its own literal rather than imported from a private name, but deliberately identical —
# a symbol that could be promoted here but not expressed in a proposal would be a promotion
# that can never do anything.
_TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")

# One promote() call describes one cycle's disclosures. The bound is not a policy knob: it
# stops a malformed upstream batch from writing an unbounded blob into the audit chain,
# which is append-only and therefore cannot be cleaned up afterwards.
MAX_SYMBOLS_PER_CALL = 200

_MAX_REASON_CHARS = 200
_MAX_LABEL_CHARS = 24


def _canonical_ticker(value) -> str | None:
    """One canonical ASCII ticker, or ``None`` for anything else.

    The raw ASCII / no-surrounding-whitespace check precedes ``upper()`` for the reason
    ``ingest.normalize._canonical_ticker`` documents: case folding is not a validation
    boundary, and the long-s spelling ``ſpy`` upper-cases to ``SPY``. A promotion is an
    allowlist write, so a value that can impersonate another ticker must be rejected before
    any normalization can launder it. Exact-type check (not ``isinstance``) so a ``str``
    subclass cannot return one value here and another to the audit payload.
    """
    if type(value) is not str or not value.isascii() or value != value.strip():
        return None
    ticker = value.upper()
    return ticker if _TICKER.match(ticker) else None


def _label(value) -> str:
    """A short, escaped, JSON-native rendering of a REJECTED input, for the audit payload.

    Rejected values are by definition unvalidated, and the audit chain's canonical encoder
    has no ``default=`` — it fails closed on anything non-JSON-native rather than guessing.
    Escaped and truncated so a hostile ticker field cannot smuggle prompt markers into a
    record that a model may later read back.

    ``str(value)`` is itself attacker-reachable — this runs on values that ALREADY failed
    validation, and an object whose ``__str__`` raises would take down the promotion (and
    with it the cycle) from inside the error path. The whole point of this function is to
    RECORD a rejection, so a value that cannot even be rendered is recorded as exactly that
    rather than being allowed to escalate into an exception.
    """
    try:
        rendered = str(value)
    except Exception:                        # noqa: BLE001 - a rejected value must not raise
        return "[unrenderable]"
    return escape_untrusted(rendered)[:_MAX_LABEL_CHARS]


def _naive_utc(dt: datetime) -> datetime:
    """Naive-UTC, the representation ``AuditLog._insert`` signs into ``payload['_ts']``."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _signed_promotions(state) -> dict[str, datetime]:
    """``symbol -> most recent SIGNED promotion time``, from VERIFIED entries only.

    Three fail-closed skips, each of which can only ever SHRINK the universe:
      * the whole chain does not verify -> ``{}`` (a truncation/rollback must not be able to
        resurrect an expired promotion, and an empty universe costs at most a rejected buy);
      * an individual entry fails ``verify_entry`` -> skipped (forged promotion);
      * ``_ts`` missing or unparseable -> skipped (an unplaceable promotion cannot be aged).

    A promotion timestamped in the FUTURE survives this function and is dropped one level up,
    by ``current_universe``'s ``ts <= now`` guard. The split is deliberate: "the future" is
    only definable against the caller's clock, and this function deliberately takes no clock
    so that it stays a straight read of what the chain SAYS. Every clock-relative judgement —
    future-dating and TTL expiry alike — lives in one place, against one ``now``.
    """
    if not state.audit.verify_chain().ok:
        return {}
    with state.session_factory() as s:
        rows = s.execute(
            select(AuditEntry)
            .where(AuditEntry.event_type == EVENT_COPY_UNIVERSE)
            .order_by(AuditEntry.seq)
        ).scalars().all()
    latest: dict[str, datetime] = {}
    for row in rows:
        if not state.audit.verify_entry(row):
            continue
        payload = row.payload or {}
        try:
            ts = _naive_utc(datetime.fromisoformat(str(payload.get("_ts"))))
        except (TypeError, ValueError):
            continue
        eligible = payload.get("eligible")
        if not isinstance(eligible, (list, tuple)):
            continue
        for raw in eligible:
            symbol = _canonical_ticker(raw)
            if symbol is None:
                continue
            if symbol not in latest or ts > latest[symbol]:
                latest[symbol] = ts
    return latest


def current_universe(state, policy, *, clock=None) -> frozenset[str]:
    """The symbols currently eligible for the risk engine to judge: TTL-pruned and bounded.

    Read-side enforcement of BOTH bounds, not just the TTL, and that is deliberate. The cap
    is applied at write time by ``promote`` (refusing overflow), but a cap the operator
    LOWERS afterwards must take effect immediately rather than waiting for 40 symbols to
    age out — a tightening that does not tighten is not a control. When the surviving set
    exceeds the cap the NEWEST promotions are kept (ties broken by symbol, ascending, so
    the result is deterministic and replayable).

    A symbol promoted exactly ``universe_ttl_days`` ago is still in; the day after, it is
    out. Expiry cannot strand a position — see the module docstring: the risk engine's
    allowlist is ``holdings OR watchlist``, so an exit is never gated on this set.
    """
    clock = clock if clock is not None else state.clock
    now = _naive_utc(clock.now())
    ttl = timedelta(days=max(int(policy.universe_ttl_days), 0))
    cap = max(int(policy.max_universe_symbols), 0)

    live = [
        (ts, symbol)
        for symbol, ts in _signed_promotions(state).items()
        if ts <= now and (now - ts) <= ttl
    ]
    if len(live) > cap:
        live.sort(key=lambda pair: pair[1])                    # symbol asc ...
        live.sort(key=lambda pair: pair[0], reverse=True)      # ... then newest first (stable)
        live = live[:cap]
    return frozenset(symbol for _ts, symbol in live)


def promote(state, symbols, *, reason: str, clock=None, policy=None) -> list[str]:
    """Record that ``symbols`` are eligible because a followed filer disclosed a trade.

    Returns the symbols this call made — or KEPT — eligible: newly promoted plus already-
    current ones whose TTL it refreshed. A re-promotion is a real event (the filer traded
    the name again), so refreshing the clock is the correct semantics and the returned list
    says "these are live", not "these are new". The audit payload separates the two.

    EVERY non-promoting outcome is counted and named in the signed payload — ``new``,
    ``refreshed``, ``refused_over_cap``, ``refused_batch_bound``, ``invalid_symbol``. A
    universe lane that silently drops half its input is indistinguishable from a broken one,
    and "nothing was promoted today" has to be a positive statement.

    NEVER EVICTS. At capacity the NEW symbol is refused; an incumbent is never displaced, so
    a burst of filings cannot push a symbol the account is holding out of the allowlist.

    ``policy`` defaults to ``policy_from_settings(state.settings)`` so the pinned two-argument
    call form works, but the caller SHOULD pass the same ``CopyPolicy`` it is planning with —
    a universe bounded by a different policy than the one that sized the trades would be a
    silent split-brain.
    """
    clock = clock if clock is not None else state.clock
    policy = policy if policy is not None else policy_from_settings(state.settings)

    requested = list(symbols or [])
    if not requested:
        # Nothing was asked for, so nothing happened; an audit entry here would be noise in
        # an append-only log. A request that was made and REFUSED always writes one.
        return []

    invalid: list[str] = []
    wanted: list[str] = []
    for raw in requested:
        ticker = _canonical_ticker(raw)
        if ticker is None:
            invalid.append(_label(raw))
        elif ticker not in wanted:
            wanted.append(ticker)
    wanted.sort()
    batch, refused_batch = wanted[:MAX_SYMBOLS_PER_CALL], wanted[MAX_SYMBOLS_PER_CALL:]

    # The orchestrator lock serializes this read-modify-write against a concurrent cycle, so
    # two callers cannot each read the same headroom and both spend it. It is a belt to the
    # read-side cap's braces: even if a writer bypassed this, ``current_universe`` clamps.
    with state.lock:
        current = current_universe(state, policy, clock=clock)
        cap = max(int(policy.max_universe_symbols), 0)
        headroom = cap - len(current)
        new: list[str] = []
        refreshed: list[str] = []
        refused: list[str] = []
        for symbol in batch:
            if symbol in current:
                refreshed.append(symbol)      # already eligible; this refreshes its TTL
            elif headroom > 0:
                new.append(symbol)
                headroom -= 1
            else:
                refused.append(symbol)        # at capacity -> refuse the NEW one, never evict
        eligible = sorted(set(new) | set(refreshed))
        state.audit.append(
            EVENT_COPY_UNIVERSE,
            actor="copytrade_universe",
            payload={
                # ``eligible`` is the ONLY key ``current_universe`` reads back. The rest is
                # the counted narrative of what this call refused and why.
                "eligible": eligible,
                "new": sorted(new),
                "refreshed": sorted(refreshed),
                "refused_over_cap": sorted(refused),
                "refused_batch_bound": sorted(refused_batch),
                "invalid_symbol": sorted(invalid),
                "reason": escape_untrusted(reason)[:_MAX_REASON_CHARS],
                "cap": cap,
                "ttl_days": int(policy.universe_ttl_days),
            },
        )
    return eligible
