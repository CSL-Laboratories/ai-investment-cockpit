"""W12 SEC-02 — MCP-response content is a first-class prompt-injection input.

The Robinhood Agentic surface is an MCP tool: positions, quotes, and order-previews it returns
are ATTACKER-INFLUENCEABLE free text (a ticker's company name, an order-preview memo, a corporate
action blurb). If any of that reaches a model prompt un-neutralized it is indistinguishable from a
news headline as an injection vector — so it must run the SAME untrusted-content pipeline the news
packet builder uses, PLUS an account-minimization step that drops every non-Agentic account BEFORE
any field is even eligible to be wrapped.

Per-field pipeline (matches docs/prompt-injection-defense.md M2/M3 + SEC-09):

    minimize_to_agentic  (drop all non-Agentic accounts; FAIL CLOSED to {} if unidentified)
      -> escape_untrusted   (NFKC-fold, role/delimiter-marker neutralize, & < > escape, cap)
      -> scrub_text         (SEC-09 canary; account#/foreign-position => QUARANTINE, never wrapped)
      -> wrap_untrusted     (source="mcp:<tool>", attribute-escaped so a field can't break out)

Only ``HardenedMCP.prompt_block()`` may reach a prompt. A fail-closed minimize yields an EMPTY
block (no field survives), so a snapshot whose Agentic account can't be positively identified
contributes nothing to the model context.

Account-number quarantine inherits the SEC-09 scrubber's current coverage (>=8 consecutive digits
+ masked forms); grouped/hyphenated account#/tax-id redaction is the SEC-09 residual owned by W14
(coverage matrix) — the structural wrap+escape+minimize protections here still apply, and decision
agents have no outbound tool (M1), bounding the exposure to the model's own context.

SCOPE (W12): SEC-02 closure here is STRUCTURAL (minimize + escape + SEC-09 scan + wrap). Pure-letter
homoglyph CONFUSABLES folding (e.g. Cyrillic 'Ѕ' -> Latin 'S' in a ticker) is SEC-22/W14 and is
deliberately NOT implemented here — escape_untrusted already NFKC-folds width/compatibility variants
(fullwidth '＜' -> '<'), which is what neutralizes the delimiter-smuggling class this package closes.

--- G12 R3 CLASS-CLOSING INVARIANTS (why this is not another whack-a-mole shape patch) ---

The prior rounds scoped account membership off a literal ``account_id`` FIELD; each round the
red-team just moved to a new CONTAINER shape (dict-of-lists, nested owner, and finally an
account-id used as a dict KEY). The fix here is driven by the account-id SET, not the container
shape, so it closes the whole class at once:

* MINIMIZE INVARIANT (R3-1): after ``minimize_to_agentic``, NO account identifier other than
  ``agentic_account_id`` may appear ANYWHERE in the tree — as a dict KEY or as a field VALUE.
  ``_scope_section`` recognises a keyed-by-account container by the PRESENCE OF THE AGENTIC KEY
  (which is always known) and keeps ONLY that key, dropping every sibling — even a foreign id we
  have never seen before (``holdings={'AGENTIC-1':…,'PERSONAL-2':…}``). Field-value account ids
  are scoped the same way. A subtree with no known account id passes unchanged (a market clock).

* POSITION INVARIANT (R3-2): a POSITION is a symbol/ticker field TOGETHER WITH a HOLDING SIZE,
  where "size" is detected by VALUE SHAPE (a numeric sibling, or a sibling string bearing a digit)
  rather than a fixed key-name allowlist — so ``market_value``/``holding``/``equity``/``allocation``
  and any future size-key NAME are recognised without a per-name patch. A foreign-symbol position
  is QUARANTINED WHOLE (no leaf wrapped); a subject-symbol position hardens as normal free text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ingest.normalize import escape_untrusted
from workflows.packets import wrap_untrusted
from workflows.scrubber import ScrubHit, scrub_text

# A structured position OBJECT = a symbol/ticker field TOGETHER WITH a holding-size sibling. Its
# ticker and its size live in different leaves, so scrub_text's single-string foreign_ticker_size
# regex never sees them together — harden_mcp_result must quarantine the whole object. The size
# sibling is detected by VALUE SHAPE (see _is_quantity), NOT a fixed key-name list, so a real
# Robinhood key like 'market_value'/'holding'/'equity' is recognised without a per-name patch
# (DEFECT R3-2). _SIZE_KEYS is kept only as a named fallback for a size whose value isn't a digit.
# The symbol side is symmetric with the value-shape size side (R8-2): a foreign-symbol position keyed
# by a real broker SYNONYM (instrument/stock/security/sym/asset/underlying) must be recognised too,
# else it escapes the quarantine and its ticker+size reach the prompt.
_SYMBOL_KEYS = frozenset({"symbol", "ticker", "instrument", "stock", "security", "sym", "asset",
                          "underlying"})
_SIZE_KEYS = frozenset(
    {"weight", "qty", "quantity", "shares", "size", "percent", "pct", "notional", "value",
     "market_value", "market_val", "holding", "holdings", "equity", "allocation", "units",
     "position", "contracts", "lots", "amount", "balance"}
)
# IDENTIFIER keys are NOT holding-size siblings. An account/id VALUE like 'AGENTIC-1' or 'PERSONAL-2'
# CONTAINS a digit but is an IDENTIFIER, not a size — counting it as a size (R5-2 prose-digit signal)
# would false-flag the Agentic account's OWN position and quarantine its legit data. So id keys are
# excluded from ALL size detection (the R3 note's "AGENTIC-1 must NOT be mistaken for a size").
_ID_KEYS = frozenset({"account_id", "id", "account", "account_number", "acct", "acct_id", "acct_ref",
                      "owner"})

# sentinel: a scoped section/branch that must be DROPPED entirely (wholly foreign/unattributable)
_DROP = object()

# A HOLDING-SIZE value is a number/currency/percent/quantity TOKEN (``$1,000.00``, ``12%``,
# ``500 sh``, ``1000``), matched WHOLE — NOT merely "a string containing a digit" (which would
# false-flag an identifier like ``account_id="AGENTIC-1"`` as a size and quarantine the Agentic
# account's own row). This shape test is what lets _position_symbol detect a size sibling by its
# VALUE rather than its key NAME (R3-2 class closure).
# The trailing unit is UNIT-AGNOSTIC (an OPTIONAL arbitrary word/percent), NOT a fixed allowlist
# (R4-2): a real holding size in a non-allowlisted unit ('5 contracts', '100 lots', '2500 coins',
# '5,000 units', '$5,000 dollars') must still classify as a size so the foreign position quarantines.
# A LEADING numeric token is still required, so an identifier like 'AGENTIC-1' (no leading digit) or
# prose is not mistaken for a size.
_QUANTITY_RE = re.compile(
    r"^\s*[-+]?\$?\s*\d[\d,]*(?:\.\d+)?\s*[A-Za-z%]*\s*$", re.IGNORECASE
)


# The literal ``account_id`` field value IS the partition id (== agentic_account_id for the agentic
# account) -> always learned.
_ACCOUNT_ID_KEYS = frozenset({"account_id"})
# Keys that MAY carry an account id but MAY carry a person NAME or a SECONDARY identifier (an account
# NUMBER that DIFFERS from the agentic partition id). Learned ONLY when the value is account-id-SHAPED
# (R7-2), so a FOREIGN partition id 'owner':'PERSONAL-2' IS learned, while a person name
# ('owner':'John Smith') and the agentic account's OWN secondary id ('acct_ref':'account 12345678901'
# — not id-shaped, handled by the SEC-09 scrubber) are NOT learned (avoids over-dropping the agentic
# account's own data).
_MAYBE_ID_KEYS = frozenset({"owner", "holder", "account", "account_number", "acct", "acct_id", "acct_ref"})
# Every key under which a (learned) account id may be carried — used by _drop_foreign to recognise a
# whole foreign-account ROW (R8-1: the literal-`account_id`-only check let a nested foreign account
# keyed by a synonym like `acct_id` survive with only its scalar id stripped and its free-text kept).
_ALL_ID_KEYS = _ACCOUNT_ID_KEYS | _MAYBE_ID_KEYS


def _account_id_field_values(value, id_shape=None) -> set[str]:
    """Every account-id FIELD value appearing ANYWHERE below ``value`` (recursive). Learns a SCALAR
    value under an UNAMBIGUOUS account key (``_ACCOUNT_ID_KEYS``) always, and under an AMBIGUOUS key
    (``_MAYBE_ID_KEYS``) ONLY when the value matches the account-id SHAPE ``id_shape`` — closing
    R7-2 (a foreign id carried under 'owner' etc. was never learned when only the literal
    'account_id' key was scanned)."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(v, (dict, list, tuple)):
                sv = str(v)
                if k in _ACCOUNT_ID_KEYS:
                    out.add(sv)
                elif k in _MAYBE_ID_KEYS and id_shape is not None and id_shape.match(sv):
                    out.add(sv)
            out |= _account_id_field_values(v, id_shape)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= _account_id_field_values(v, id_shape)
    return out


def _known_account_ids(raw: dict, agentic_account_id: str) -> set[str]:
    """The set of account identifiers we can positively name for this snapshot (R3-1).

    = ``{agentic_account_id}`` UNION every account-id FIELD value anywhere in ``raw`` (under any
    ``_ACCOUNT_ID_KEYS`` key, or a ``_MAYBE_ID_KEYS`` key whose value is id-shaped — R7-2) UNION
    every KEY of the top-level ``accounts`` mapping (when it is a dict). This SET — not any
    container shape — drives the scoper, so it closes keyed-by-id / dict-of-lists / dict-of-dicts
    / nested shapes at once. (Note: the agentic id being ALWAYS present lets the scoper recognise
    a keyed-by-account container even when a sibling foreign id has never been seen before.)"""
    ids = {agentic_account_id}
    ids |= _account_id_field_values(raw, _account_id_pattern(agentic_account_id))
    accounts = raw.get("accounts") if isinstance(raw, dict) else None
    if isinstance(accounts, dict):
        ids |= {str(k) for k in accounts}
    return ids


def _known_ids_in(value, known: set[str]) -> set[str]:
    """Every KNOWN account id appearing anywhere below ``value`` as a dict KEY or a scalar VALUE.

    The KEY arm is what R3-1 added: an account id used as a dict key (``holdings['PERSONAL-2']``)
    now counts as the account appearing in the tree, so the minimize invariant ("no non-Agentic id
    survives as a key or a value") can be enforced and verified."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) in known:
                out.add(str(k))
            out |= _known_ids_in(v, known)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= _known_ids_in(v, known)
    else:
        if str(value) in known:
            out.add(str(value))
    return out


def _scope_section(value, agentic_account_id: str, known: set[str]):
    """Scope one section to the Agentic account, RECURSIVE + DEPTH-AWARE, driven by the account-id
    SET (R3-1 class closure — supersedes the field-only scoper the red-team kept out-flanking).

    Invariant after scoping: NO account id other than ``agentic_account_id`` survives ANYWHERE in
    the returned subtree, as a dict KEY or as a field VALUE.

    Rules applied to every subtree:

    * NO known account id anywhere (key or value) -> pass UNCHANGED (e.g. a market clock).
    * A **dict** with a known account id as one of its KEYS is a KEYED-BY-ACCOUNT container: keep
      ONLY the ``agentic_account_id`` key (dropping every sibling — including a foreign id we've
      never seen, since the container is recognised by the agentic key's presence), then recurse
      into the kept value; if the agentic key is absent -> ``_DROP``.
    * A **dict** carrying a known account id only as a field VALUE: agentic-only -> keep whole;
      wholly foreign -> ``_DROP``; spanning both -> recurse per key and drop foreign branches.
    * An account-bearing **list** keeps ONLY positively-Agentic rows: a row carrying a foreign id,
      or NO id at all (unattributable within an account-bearing list), is dropped; a row spanning
      accounts is recursed into. A final safety net drops any kept branch that STILL contains a
      foreign known account id as a key or value.
    """
    if not _known_ids_in(value, known):
        return value                                    # account-agnostic -> pass unchanged
    if isinstance(value, dict):
        if any(str(k) in known for k in value):         # keyed-by-account container
            agentic_keys = [k for k in value if str(k) == agentic_account_id]
            if not agentic_keys:
                return _DROP                            # keyed by foreign accounts only -> drop
            k = agentic_keys[0]
            scoped = _scope_section(value[k], agentic_account_id, known)
            return {k: scoped} if scoped is not _DROP else _DROP
        ids = _known_ids_in(value, known)               # account id(s) present only as VALUES
        if not ids - {agentic_account_id}:
            return value                                # agentic-only -> keep whole
        if agentic_account_id not in ids:
            return _DROP                                # wholly foreign/unattributable -> drop
        out: dict = {}                                  # spans accounts -> recurse per key
        for kk, vv in value.items():
            scoped = _scope_section(vv, agentic_account_id, known)
            if scoped is not _DROP:
                out[kk] = scoped
        if _known_ids_in(out, known) - {agentic_account_id}:
            return _DROP                                # safety net: a foreign id still remains
        return out
    if isinstance(value, (list, tuple)):
        out_list = []
        for el in value:
            ids = _known_ids_in(el, known)
            if not ids:
                continue                                # unattributable row -> drop
            if agentic_account_id not in ids:
                continue                                # wholly foreign -> drop
            scoped = _scope_section(el, agentic_account_id, known)
            if scoped is _DROP:
                continue
            if _known_ids_in(scoped, known) - {agentic_account_id}:
                continue                                # safety net: foreign id still remains
            out_list.append(scoped)
        return out_list
    return value                                        # scalar (agentic-only) -> pass


def _drop_foreign(value, agentic_account_id: str, known: set[str]):
    """Remove every FOREIGN account branch from a value that BELONGS to the Agentic account (R4-1).

    Used on the KEPT Agentic account value, which does NOT carry its own id as a VALUE inside — so
    ``_scope_section``'s "carries a foreign id but not the agentic id => wholly foreign => drop"
    rule would wrongly discard the Agentic account's own data. This recurses instead, dropping any
    nested FOREIGN account row / foreign-keyed entry / bare foreign-id scalar while KEEPING the
    Agentic account's own (foreign-free) data (e.g. ``accounts.AGENTIC-1.linked.account_id='PERSONAL-2'``
    is removed, ``accounts.AGENTIC-1.positions`` survives). Returns ``_DROP`` only if the whole value
    IS a foreign account row / bare foreign id."""
    foreign = known - {agentic_account_id}
    if not (_known_ids_in(value, known) & foreign):
        return value                                    # no foreign id anywhere -> keep whole
    if isinstance(value, dict):
        if any(str(value[k]) in foreign for k in _ALL_ID_KEYS if k in value):
            return _DROP                                # this dict IS a foreign account row (ANY id key, R8-1)
        out: dict = {}
        for k, v in value.items():
            if str(k) in foreign:
                continue                                # drop a foreign-account-KEYED entry
            sub = _drop_foreign(v, agentic_account_id, known)
            if sub is not _DROP:
                out[k] = sub
        return out
    if isinstance(value, (list, tuple)):
        out_list = []
        for el in value:
            sub = _drop_foreign(el, agentic_account_id, known)
            if sub is not _DROP:
                out_list.append(sub)
        return out_list
    return _DROP if str(value) in foreign else value    # a bare foreign-id scalar


def _account_id_pattern(agentic_account_id: str):
    r"""Derive an account-id-SHAPE regex from the agentic id (R5-1 class closure): consecutive
    letters -> ``[A-Za-z]+``, consecutive digits -> ``\d+``, any other char -> that literal char
    (``re.escape``), anchored ``^...$``. Lets a FOREIGN partition key be recognised by its SHAPE
    even when its id was never learned (used ONLY as a dict KEY in a non-``accounts`` sibling with
    the agentic key ABSENT), which is the R5-1 root cause.

    Returns ``None`` when the pattern would be TOO BROAD to be safe: if the agentic id is a single
    character-class (all letters OR all digits, no separator) the pattern would match ordinary field
    names / quantities and drop legit field-keyed sections, so we SKIP the shape strip and fall back
    to declared-set scoping. A pattern is returned only when the id is STRUCTURED — it contains a
    digit AND a letter, or a non-alphanumeric separator."""
    aid = str(agentic_account_id)
    if not aid:
        return None
    has_alpha = any(c.isalpha() for c in aid)
    has_digit = any(c.isdigit() for c in aid)
    has_sep = any(not c.isalnum() for c in aid)
    # ALL-LETTERS (no digit, no separator): the shape `[A-Za-z]+` would match ordinary field names,
    # so the undeclared-partition-key strip cannot be applied without dropping legit field-keyed
    # sections -> SKIP it and fall back to declared-set scoping. RESIDUAL (R7-3, bounded, DOCUMENTED):
    # an UNDECLARED all-letter foreign partition key ('advisories.PERSONAL') is indistinguishable from
    # a field name when the AGENTIC id is itself all-letters — but a REAL broker account number ALWAYS
    # contains a digit (Robinhood/Fidelity/Schwab formats), so this case does not occur in production;
    # a purely alphabetic agentic id is a synthetic/test id, out of the realistic threat model (the
    # DECLARED-set scoping still drops any DECLARED foreign account even for an all-letter id).
    if has_alpha and not has_digit and not has_sep:
        return None
    # ALL-DIGITS — a realistic broker account NUMBER (e.g. '123456789'). The R5-1 strip MUST still
    # run (R6-1: skipping it let an undeclared foreign partition key survive when the agentic id is
    # all-digit). Anchor to the EXACT digit length: a foreign account number of the same broker
    # format matches, while a short index / year / quantity KEY of a different length does not
    # (_strip_foreign_idlike scans KEYS only, so a quantity VALUE is never affected).
    if has_digit and not has_alpha and not has_sep:
        return re.compile(r"^\d{" + str(len(aid)) + r"}$")
    parts: list[str] = []
    i, n = 0, len(aid)
    while i < n:
        if aid[i].isalpha():
            while i < n and aid[i].isalpha():
                i += 1
            parts.append(r"[A-Za-z]+")
        elif aid[i].isdigit():
            while i < n and aid[i].isdigit():
                i += 1
            parts.append(r"\d+")
        else:
            parts.append(re.escape(aid[i]))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def _strip_foreign_idlike(value, agentic_account_id: str, pattern):
    """Recursively DROP any dict entry whose KEY matches the account-id SHAPE ``pattern`` but is NOT
    ``agentic_account_id`` — a FOREIGN account partition key (R5-1). Scans KEYS ONLY, never scalar
    VALUES, so a quantity value like ``'2500'`` or a ticker like ``'AAPL'`` is never mistaken for an
    id. Agentic-keyed entries are kept; a normal field-keyed section ('positions','clock',
    'market_value') does not match the STRUCTURED pattern and is kept unchanged."""
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if str(k) != agentic_account_id and pattern.match(str(k)):
                continue                            # foreign account partition key -> drop
            out[k] = _strip_foreign_idlike(v, agentic_account_id, pattern)
        return out
    if isinstance(value, (list, tuple)):
        return [_strip_foreign_idlike(el, agentic_account_id, pattern) for el in value]
    return value


def minimize_to_agentic(raw: dict, *, agentic_account_id: str) -> dict:
    """Drop EVERY non-Agentic account's data BEFORE any field is eligible for a prompt.

    ``raw`` is the (mocked) MCP payload. Its ``accounts`` may be a list of ``{"account_id": ...}``
    dicts or a mapping keyed by account id. Only the account positively matching
    ``agentic_account_id`` is kept; all other accounts are discarded here so a personal account's
    holdings can never even reach the hardening step.

    FAIL CLOSED: return ``{}`` if ``raw`` is not a dict, ``agentic_account_id`` is falsy, or the
    Agentic account cannot be positively identified in ``raw`` (an ambiguous/absent match must
    contribute NOTHING rather than risk leaking the wrong account). R4-1 FINAL INVARIANT GATE: if,
    after scoping, ANY foreign account id STILL survives anywhere in the output (as a KEY or a
    VALUE), return ``{}`` — the whole snapshot fails closed rather than leak a non-Agentic id."""
    if not isinstance(raw, dict) or not agentic_account_id:
        return {}
    # R4-1: compute the known-account-id SET BEFORE building `kept` so the kept Agentic value can be
    # scoped for nested foreign accounts too.
    known = _known_account_ids(raw, agentic_account_id)
    accounts = raw.get("accounts")
    kept: object = None
    if isinstance(accounts, dict):
        if agentic_account_id in accounts:
            kept = {agentic_account_id: accounts[agentic_account_id]}
    elif isinstance(accounts, list):
        matched = [a for a in accounts
                   if isinstance(a, dict) and str(a.get("account_id")) == agentic_account_id]
        if len(matched) == 1:                       # exactly-one => positively identified
            kept = matched
    if kept is None:
        return {}                                   # FAIL CLOSED: not positively identified
    # R4-1: scope the KEPT Agentic value too — a foreign account NESTED inside it
    # (accounts.AGENTIC-1.linked.account_id='PERSONAL-2' + its advisor_note) must be dropped, not
    # passed whole. `_scope_section`'s wholly-foreign rule can't be used here (the agentic value
    # doesn't carry its own id as a value), so `_drop_foreign` recurses and drops only foreign branches.
    kept = _drop_foreign(kept, agentic_account_id, known)
    if kept is _DROP:
        return {}
    # R3-1 CLASS CLOSURE: scope EVERY account-bearing top-level sibling section too — not just
    # `accounts`. `_scope_section` is driven by the KNOWN-ACCOUNT-ID SET (fields AND keys), so an
    # id used as a dict KEY (holdings['PERSONAL-2'], positions_by_account['PERSONAL-2']), nested
    # under a sub-key, or in a list row is scoped away at once. An account-agnostic section
    # (no account id anywhere) passes unchanged.
    out: dict = {}
    for k, v in raw.items():
        if k == "accounts":
            continue
        scoped = _scope_section(v, agentic_account_id, known)
        if scoped is not _DROP:
            out[k] = scoped
    out["accounts"] = kept
    # R5-1 CLASS CLOSURE (undeclared foreign PARTITION KEY): the declared-set scoper above can only
    # prune ids it has LEARNED (account_id fields + `accounts` keys). A foreign id used ONLY as a
    # dict KEY in a NON-`accounts` sibling section with the agentic key ABSENT is never learned, so
    # _scope_section passed its whole subtree unchanged. Recognise such a key by its SHAPE (a regex
    # generalised from the agentic id) and DROP it — KEYS ONLY, so a quantity/ticker VALUE is never
    # mistaken for an id. Skipped when the agentic id is a single char-class (pattern too broad).
    pattern = _account_id_pattern(agentic_account_id)
    if pattern is not None:
        out = _strip_foreign_idlike(out, agentic_account_id, pattern)
    # R4-1 FINAL INVARIANT GATE (the guarantee): if ANY foreign account id STILL survives anywhere
    # in the output — as a dict KEY or a field VALUE — a scoping gap remained; FAIL CLOSED to {} so
    # no non-Agentic data reaches a prompt. On a normal payload (agentic-only / account-agnostic
    # data) this never fires; it is the belt-and-suspenders that makes the "no non-Agentic id
    # survives" invariant hold regardless of container shape, superseding shape-by-shape patches.
    if _known_ids_in(out, known) - {agentic_account_id}:
        return {}
    return out


@dataclass(frozen=True)
class HardenedMCP:
    """The neutralized MCP snapshot. ``block`` is the ONLY member that may reach a prompt; it
    contains no un-neutralized delimiter/role-marker from any input free-text field. Fields whose
    SEC-09 scrub was ``blocked`` (account#/foreign-position) are QUARANTINED — recorded in
    ``blocked_fields`` and never wrapped into ``block``."""
    block: str
    blocked_fields: tuple[str, ...]
    hits: tuple[ScrubHit, ...]

    def prompt_block(self) -> str:
        return self.block


def _is_quantity(v) -> bool:
    """True iff ``v`` is a HOLDING-SIZE value: a real number, or a string that is WHOLLY a
    number/currency/percent/quantity token (``'12%'``, ``'$1,000.00'``, ``'500 sh'``, ``'1000'``).
    Detecting size by VALUE SHAPE — not a key-name allowlist — is the R3-2 class closure: any
    size-key NAME (market_value/holding/equity/…) is recognised without a per-name patch. Matching
    the WHOLE token (not "contains a digit") is deliberate: an identifier like ``'AGENTIC-1'`` or a
    prose note that merely mentions a number must NOT be mistaken for a size."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, str) and _QUANTITY_RE.match(v) is not None


def _has_size_below(value) -> bool:
    """Whether a HOLDING-SIZE signal appears at ANY depth below a symbol-bearing object — a
    ``_SIZE_KEYS``-named field, a quantity-shaped value, or a digit-bearing string — under a
    NON-symbol, NON-id key. R6-2: a size nested one level deeper
    (``{'symbol':'TSLA','lot':{'display':'500 contracts'}}``) must still mark the object a position
    so a foreign symbol quarantines WHOLE (the old direct-children-only check missed it). ``_ID_KEYS``
    branches are skipped so an identifier value (``'AGENTIC-1'``) is never mistaken for a size."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _SYMBOL_KEYS or k in _ID_KEYS:
                continue
            if k in _SIZE_KEYS or _is_quantity(v):
                return True
            if isinstance(v, str) and any(c.isdigit() for c in v):
                return True
            if isinstance(v, (dict, list, tuple)) and _has_size_below(v):
                return True
    elif isinstance(value, (list, tuple)):
        for el in value:
            if _has_size_below(el):
                return True
    return False


def _position_symbol(value) -> str | None:
    """Return the ticker of a structured POSITION OBJECT (a symbol/ticker field TOGETHER WITH a
    holding-size sibling at ANY depth below it), else ``None``. The size is any NON-symbol/NON-id
    field that is a named size key, a quantity-shaped value (R3-2), a string that merely CONTAINS a
    digit (R5-2, matching the class docstring's "a sibling string bearing a digit"), or ANY of those
    nested one or more levels deeper (R6-2) — so a foreign position whose size lives under a
    non-standard key (``market_value``), as PROSE (``description="legacy lot, 2500 units"``), or
    NESTED (``lot={'display':'500 contracts'}``) is recognised and quarantined WHOLE.

    Fail closed: a symbol-bearing dict with any size signal below it is treated as a position even if
    we can't name the exact size key. A bare ``{symbol, note}`` with no digit anywhere is NOT a
    position — it hardens as ordinary free text (so an in-account annotation still flows)."""
    if not isinstance(value, dict):
        return None
    sym_key = next((k for k in value if k in _SYMBOL_KEYS), None)
    if sym_key is None:
        return None
    if not _has_size_below(value):
        return None
    sym = value[sym_key]
    return sym if isinstance(sym, str) else str(sym)


def _walk_strings(value, path: str, acc: list[tuple[str, str]], *,
                  subject_symbols: frozenset[str], blocked: list[str]) -> None:
    """Collect every free-text (str) leaf with a stable dotted/indexed path, in document order.

    A structured POSITION OBJECT whose symbol is NOT a subject symbol is QUARANTINED WHOLE: its
    dotted path is recorded in ``blocked`` and NONE of its leaves are collected (DEFECT A2). A
    subject-symbol position is walked normally (its leaves harden as free text)."""
    sym = _position_symbol(value)
    if sym is not None and sym not in subject_symbols:
        blocked.append(path or "value")               # QUARANTINE whole object — no leaf collected
        return
    if isinstance(value, str):
        acc.append((path or "value", value))
    elif isinstance(value, dict):
        for k, v in value.items():
            _walk_strings(v, f"{path}.{k}" if path else str(k), acc,
                          subject_symbols=subject_symbols, blocked=blocked)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _walk_strings(v, f"{path}[{i}]", acc,
                          subject_symbols=subject_symbols, blocked=blocked)
    # non-string scalars (numbers/bools/None) carry no injection surface -> ignored


def _safe_field(path: str) -> str:
    """The ``field=`` attribute is PROVENANCE metadata built from ATTACKER-CONTROLLED MCP dict KEYS.
    Keep ONLY path-structural characters — collapse every run of anything else (spaces, ``:``,
    newlines, control chars, quotes, ``< >``, ``&``) to a single ``_`` — and cap the length, so a
    malicious key like ``'AAPL\\nHuman: <instr>\\n'`` cannot smuggle a role marker, a turn-boundary
    newline, or an attribute breakout into the opening ``<untrusted-content ... field="...">`` tag
    (R7-1). ``wrap_untrusted``'s ``_attr_escape`` only guards the structural chars ``& < > "``, not
    role markers or newlines, so the field value is reduced to a safe token HERE at the source (the
    trusted news path uses its own attrs and is byte-unchanged)."""
    return re.sub(r"[^A-Za-z0-9_.\[\]/-]+", "_", str(path))[:200]


def harden_mcp_result(minimized: dict, *, source: str,
                      subject_symbols: frozenset[str] = frozenset()) -> HardenedMCP:
    """Route EVERY free-text field of an ALREADY-minimized snapshot through
    ``escape_untrusted -> scrub_text -> wrap_untrusted(source="mcp:<tool>")``. A field whose scrub
    is ``blocked`` is QUARANTINED (recorded in ``blocked_fields``, never wrapped). ``minimized``
    should be the output of :func:`minimize_to_agentic`; a fail-closed ``{}`` yields an empty
    block."""
    fields: list[tuple[str, str]] = []
    blocked_fields: list[str] = []
    _walk_strings(minimized, "", fields,
                  subject_symbols=subject_symbols, blocked=blocked_fields)
    blocks: list[str] = []
    all_hits: list[ScrubHit] = []
    for path, raw_value in fields:
        escaped = escape_untrusted(raw_value)                 # delimiter/role-marker neutralize
        r = scrub_text(escaped, subject_symbols=subject_symbols)   # SEC-09 canary over the field
        all_hits.extend(r.hits)
        if r.blocked:
            blocked_fields.append(path)                       # QUARANTINE — never reaches a prompt
            continue
        blocks.append(wrap_untrusted(r.clean, source=f"mcp:{source}", field=_safe_field(path)))
    return HardenedMCP(block="\n\n".join(blocks),
                       blocked_fields=tuple(blocked_fields), hits=tuple(all_hits))
