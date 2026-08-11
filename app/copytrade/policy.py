"""RW-16 — the copy-trade policy: every knob, in one reviewable place.

PURE. Builds from ``Settings`` but holds no reference to it, so the engine can be tested
without an environment and the operator can read the whole policy off one object.

THE FOLLOW LIST IS AN ALLOWLIST, AND IT IS THE FIRST GATE. An empty follow list copies
NOBODY. There is deliberately no "follow everyone" value: the disclosure feed carries every
member of both chambers, and a policy that defaults to all of them would turn a config
oversight into several hundred positions.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

# A follower name normalizes to "last-first", lowercase ASCII. Filings spell a name several
# ways ("Pelosi, Nancy", "Hon. Nancy Pelosi", "PELOSI, NANCY P."), so both the follow list
# and the parsed filer run through the SAME normalizer and are compared on the result.
_NAME_STRIP = re.compile(r"[^a-z ,.-]")
_HONORIFICS = frozenset({"hon", "hon.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "dr", "dr."})
_SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "md", "phd"})

MAX_FOLLOW_ENTRIES = 50

# Options handling. The operator picks ONE; there is no "copy the contract" value, because
# the risk engine does not support options as an asset type at all (``risk.engine
# .SUPPORTED_ASSET_TYPES``) and adding one would mean putting an instrument with a hard
# expiry on the end of a 30-45 day information lag.
OPTIONS_UNDERLYING = "underlying"   # translate to a haircut position in the underlying
OPTIONS_IGNORE = "ignore"           # never act; surface for awareness only
OPTIONS_POLICIES = frozenset({OPTIONS_UNDERLYING, OPTIONS_IGNORE})

MAX_OPTIONS_HAIRCUT = Decimal("0.5")
"""The largest admissible ``options_haircut``, and it is a SHARE-GRID bound, not a taste.

``sizing.sized_copy_notional`` guarantees that an options-derived NOTIONAL is strictly below the
equity one. The number that reaches the human gate and the broker is not that notional, though —
it is ``qty = floor(notional / quote_price)`` (``workflows.copytrade_orders._resolve_order``), and
``floor`` is not injective. Write ``f`` for the equity notional, ``h`` for the options one and
``p`` for the quote price. A collision needs ``floor(h/p) == floor(f/p) == k`` for some ``k >= 1``,
which forces ``h/p >= k`` and ``f/p < k + 1 <= 2k``, hence ``h/f > 1/2``. Because the ceiling is
discounted by the same multiplier, ``h <= haircut * f`` exactly — so ``haircut <= 1/2`` makes the
collision arithmetically impossible, at every price and every portfolio value.

Without this bound the guarantee held only by accident of the shipped default: at
``options_haircut=0.75`` and a $60k portfolio, 146 of the first 1000 integer quote prices produce
an IDENTICAL share count and limit price for the options and the equity order — the same
byte-identical failure the haircut fix closed, displaced one layer down to the layer that
actually trades."""


class PolicyError(ValueError):
    """A copy policy that cannot be built. Fail closed — never fall back to a default that
    trades."""


def normalize_filer(name: str | None) -> str:
    """Canonicalize a filer name to a ``last-first`` slug, or ``""`` when unusable.

    Deliberately lossy and deliberately strict. Two spellings that differ only by honorific,
    suffix, middle initial, case or punctuation must collide; anything else must not. A name
    that normalizes to the empty string never matches a follow entry, so an unparseable filer
    fails CLOSED (not followed) rather than matching everyone.
    """
    if not name or not isinstance(name, str):
        return ""
    # NFKD-fold accents to ASCII so "Núñez" and "Nunez" are one person, not two.
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = _NAME_STRIP.sub(" ", ascii_only)

    if "," in cleaned:
        last, _, rest = cleaned.partition(",")
        parts = [last] + rest.split()
    else:
        parts = cleaned.split()
        if parts:
            parts = [parts[-1]] + parts[:-1]       # trailing surname -> leading

    tokens = [p.strip(" .") for p in parts]
    tokens = [t for t in tokens if t and t not in _HONORIFICS and t not in _SUFFIXES]
    if not tokens:
        return ""
    last = tokens[0]
    # Only the FIRST given name is significant; middle initials are dropped so
    # "Pelosi, Nancy P." and "Pelosi, Nancy" are the same follow key.
    first = tokens[1] if len(tokens) > 1 else ""
    # A bare initial ("n") carries too little to disambiguate; keep it rather than
    # discarding, so "Pelosi, N." does NOT silently match "Pelosi, Nancy".
    return f"{last}-{first}" if first else last


def parse_follow_list(raw: str | None) -> frozenset[str]:
    """Parse ``COCKPIT_CONGRESS_FOLLOW`` into normalized filer slugs.

    Accepts ``;``-separated entries so a "Last, First" spelling survives the split
    (comma-separating would tear every name in half).
    """
    if not raw or not str(raw).strip():
        return frozenset()
    entries = [e.strip() for e in str(raw).split(";") if e.strip()]
    if len(entries) > MAX_FOLLOW_ENTRIES:
        raise PolicyError(
            f"follow list may contain at most {MAX_FOLLOW_ENTRIES} entries, got {len(entries)}"
        )
    slugs = {normalize_filer(e) for e in entries}
    bad = [e for e in entries if not normalize_filer(e)]
    if bad:
        raise PolicyError(f"unparseable follow-list entries: {bad}")
    return frozenset(slugs)


@dataclass(frozen=True)
class CopyPolicy:
    """The complete deterministic policy. Every field is consulted by a pure function."""

    follow: frozenset[str] = field(default_factory=frozenset)

    # ---- the staleness gate ----------------------------------------------------------
    max_age_days: int = 45
    """Refuse any disclosure whose TRADE is older than this. 45 is the STOCK Act reporting
    deadline, so this default means "act on a filing that arrived on time, refuse one that
    did not." Tightening it is the single most effective knob here."""

    staleness_decay: Decimal = Decimal("0.5")
    """How much of the position is given up at maximum age, linearly. 0.5 means a trade
    disclosed at day 45 is sized at half of one disclosed today. Set to 0 to size every
    in-window disclosure the same (not recommended — it prices a 44-day-old signal as if it
    were fresh)."""

    # ---- sizing -----------------------------------------------------------------------
    copy_fraction: Decimal = Decimal("0.02")
    """Fraction of YOUR portfolio value for a baseline (weight 1.0) copied buy."""

    max_notional_per_trade: Decimal = Decimal("500")
    """Hard per-intent ceiling, applied after every other sizing factor. The risk engine has
    its own caps; this one exists so a policy misconfiguration is bounded before it ever
    reaches the engine."""

    min_notional_per_trade: Decimal = Decimal("25")
    """Below this the intent is dropped rather than emitted: a $3 order is all cost and no
    exposure, and it still consumes an approval from a human."""

    min_amount_low: Decimal = Decimal("1001")
    """Ignore disclosures whose LOW bound is under this. The bottom bucket starts at $1,001,
    so the default admits everything; raising it filters out a member's smallest positions."""

    # ---- options (the non-1:1 rule) ---------------------------------------------------
    options_policy: str = OPTIONS_UNDERLYING
    options_haircut: Decimal = Decimal("0.25")
    """Options-derived intents are sized at this multiple of an equivalent equity copy — BOTH
    the raw size and the per-trade ceiling (``sizing.haircut_ceiling``), so the discount cannot
    be erased by the clamp at a large portfolio value.

    Must be inside ``(0, MAX_OPTIONS_HAIRCUT]``. ``1`` is not an available setting: it means
    copying an options disclosure one for one with an equity disclosure, which is the exact thing
    the operator's standing requirement forbids, and it would make the strict inequality the
    sizing layer guarantees ("an options-derived notional is always smaller") false. Anything
    above ``0.5`` is refused for the share-grid reason written up on ``MAX_OPTIONS_HAIRCUT``: the
    inequality has to survive ``floor(notional / price)``, not merely hold in dollars. A policy
    that cannot be run is refused here rather than silently degraded."""

    options_min_days_to_expiry: int = 30
    """Drop an options-derived intent whose disclosed expiry is nearer than this. By the time
    a PTR arrives the contract may already have expired; this refuses to translate a signal
    whose own thesis window has closed.

    MUST BE AT LEAST 1, and that is a safety floor rather than a default. ``options.py``'s
    ``REASON_NEAR_EXPIRY`` docstring states that an expiry falling exactly on ``today`` "reads as
    near expiry, not expired — both drop, so nothing unsafe hangs on which side of the line it
    lands." That claim is TRUE ONLY WHILE THIS IS >= 1: at ``0`` a 0-DTE contract passes both
    ``dte < 0`` and ``dte < 0``, and a contract worthless within hours translates into a fresh
    multi-week long position in the underlying. It was the one policy field with no validation at
    all, and ``Settings`` accepted ``0``."""

    # ---- exits -------------------------------------------------------------------------
    mirror_sells: bool = True
    """Copy disclosed SALES as exits of positions you actually hold. Independent of buys on
    purpose: mirroring only the buys builds a portfolio that never sells."""

    # ---- universe ----------------------------------------------------------------------
    max_universe_symbols: int = 40
    universe_ttl_days: int = 120
    """A symbol promoted by a disclosure leaves the copy universe after this long without a
    new one, so the deterministic allowlist stays bounded and current."""

    def __post_init__(self) -> None:
        if self.options_policy not in OPTIONS_POLICIES:
            raise PolicyError(
                f"options_policy must be one of {sorted(OPTIONS_POLICIES)}, "
                f"got {self.options_policy!r}"
            )
        if self.max_age_days <= 0:
            raise PolicyError("max_age_days must be positive")
        if not (Decimal("0") <= self.staleness_decay <= Decimal("1")):
            raise PolicyError("staleness_decay must be within [0, 1]")
        if self.copy_fraction <= 0:
            raise PolicyError("copy_fraction must be positive")
        if not (Decimal("0") < self.options_haircut <= MAX_OPTIONS_HAIRCUT):
            raise PolicyError(
                f"options_haircut must be within (0, {MAX_OPTIONS_HAIRCUT}] — 1 would copy an "
                f"options disclosure one for one, which the options policy forbids outright, and "
                f"anything above {MAX_OPTIONS_HAIRCUT} lets share quantization collapse an "
                f"options-derived order onto the equity one (see MAX_OPTIONS_HAIRCUT)"
            )
        if self.options_min_days_to_expiry < 1:
            raise PolicyError(
                "options_min_days_to_expiry must be at least 1 — 0 admits a 0-DTE contract as a "
                "fresh multi-week long in the underlying, and a negative value disables the "
                "near-expiry gate entirely"
            )
        if self.min_notional_per_trade > self.max_notional_per_trade:
            raise PolicyError("min_notional_per_trade exceeds max_notional_per_trade")
        if self.max_universe_symbols <= 0:
            raise PolicyError("max_universe_symbols must be positive")

    def follows(self, filer_slug: str) -> bool:
        """An empty follow list follows NOBODY (see the module docstring)."""
        return bool(filer_slug) and filer_slug in self.follow


def policy_from_settings(settings) -> CopyPolicy:
    """Build the policy from ``Settings``. Raises ``PolicyError`` on a bad configuration —
    the caller reports one dead feature rather than trading on a half-parsed policy."""
    return CopyPolicy(
        follow=parse_follow_list(getattr(settings, "congress_follow", "")),
        max_age_days=int(getattr(settings, "congress_max_age_days", 45)),
        staleness_decay=Decimal(str(getattr(settings, "congress_staleness_decay", "0.5"))),
        copy_fraction=Decimal(str(getattr(settings, "congress_copy_fraction", "0.02"))),
        max_notional_per_trade=Decimal(
            str(getattr(settings, "congress_max_notional_per_trade", "500"))),
        min_notional_per_trade=Decimal(
            str(getattr(settings, "congress_min_notional_per_trade", "25"))),
        min_amount_low=Decimal(str(getattr(settings, "congress_min_amount_low", "1001"))),
        options_policy=str(getattr(settings, "congress_options_policy", OPTIONS_UNDERLYING)),
        options_haircut=Decimal(str(getattr(settings, "congress_options_haircut", "0.25"))),
        options_min_days_to_expiry=int(
            getattr(settings, "congress_options_min_days_to_expiry", 30)),
        mirror_sells=bool(getattr(settings, "congress_mirror_sells", True)),
        max_universe_symbols=int(getattr(settings, "congress_max_universe_symbols", 40)),
        universe_ttl_days=int(getattr(settings, "congress_universe_ttl_days", 120)),
    )
