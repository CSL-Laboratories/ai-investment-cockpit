"""RW-16 — PURE parsers for congressional periodic transaction reports. No I/O, ever.

Everything in this module is a function from a STRING (or bytes we were handed) to values.
Nothing here opens a socket, a file or a database, for the same reason ``risk/`` is pure: the
part of a feed that decides what a document MEANS is the part you most need to be able to
replay against a fixture, byte for byte, without a network. The providers
(``house_clerk.py`` / ``senate_efd.py``) own the fetching; this module owns the reading.

THE PDF PROBLEM, AND THE SEAM WE CHOSE INSTEAD OF A DEPENDENCY
---------------------------------------------------------------
House PTRs are PDFs. Extracting text from a PDF means a heavy third-party dependency
(pdfminer/pypdf and their transitive trees) on the ingest path of a security-sensitive
cockpit, and the RW-16 scope does not justify that. So this module NEVER touches a PDF: it
takes ALREADY-EXTRACTED TEXT as a plain string argument. The provider carries an injected
``pdf_extractor`` seam that defaults to ``None``, and when it is None every PTR document is
counted as ``pdf_extractor_unavailable`` — a loud, named, first-class "this feed is wired but
cannot read its documents yet" state, never a silent zero.

The consequence is deliberate and worth stating plainly: **out of the box the House provider
produces awareness of WHO filed and WHEN (from the index), and zero transactions.** That is an
honest partial feed. The operator wires an extractor — any callable ``(bytes, doc_id) -> str``
— and the transactions start flowing through the exact parsing logic this module's tests
already pin against fixture text.

A ROW YOU CANNOT READ CONFIDENTLY IS AN ERROR YOU COUNT
--------------------------------------------------------
Every refusal in here returns a named reason and never a guess. An unknown transaction code
does NOT fall back to "purchase"; an amount outside the STOCK Act tiers does NOT fall back to
the smallest bucket; an ambiguous asset does NOT fall back to equity. The fail-closed default
of the asset classifier is ``ASSET_OTHER``, which ``copytrade/engine.py`` never trades. This
matters more here than in most feeds because the artifact being parsed is a scanned or
hand-typed government form: the failure mode of a lenient parser is not "a missing row", it is
"a confidently wrong row that reaches the order path".

SEC-02: every free-text field that survives into a :class:`Disclosure` — the filer's display
name and the asset description — goes through ``ingest.normalize.escape_untrusted`` HERE, at
the boundary where it becomes a stored value, rather than at some later display site that a
future caller might bypass. The escaper is a FIXED POINT (``f(f(x)) == f(x)``), so escaping a
value that a caller already escaped is safe — and, since RW-16 F4, no longer corrupting: ``&``
is deliberately NOT entity-encoded, so a filer named "Dodd & Frank" survives the repeated pass
intact. See ``ingest.normalize.escape_untrusted`` for why that costs no neutralization.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from xml.etree import ElementTree

from copytrade.models import (
    ASSET_EQUITY,
    ASSET_OPTION_CALL,
    ASSET_OPTION_PUT,
    ASSET_OTHER,
    CHAMBER_HOUSE,
    CHAMBER_SENATE,
    TX_EXCHANGE,
    TX_PURCHASE,
    TX_SALE,
    TX_SALE_PARTIAL,
    AmountRange,
    Disclosure,
)
from copytrade.policy import normalize_filer
from ingest.normalize import escape_untrusted

# ---- untrusted-input bounds ----------------------------------------------------------------
# These are not tuning knobs. They bound what ONE hostile or corrupt document can cost in
# memory and CPU inside a scheduled job. Exceeding one is a counted refusal, never a crash.
MAX_DOCUMENT_CHARS = 4 * 1024 * 1024
MAX_LINES = 20_000
MAX_ROWS_PER_DOCUMENT = 500
MAX_INDEX_ENTRIES = 200_000
MAX_DESCRIPTION_CHARS = 500

# ---- the STOCK Act disclosure tiers ---------------------------------------------------------
# Congress discloses a BUCKET, never a fill. These are the brackets both chambers print on a
# periodic transaction report. The table is EXACT-MATCH ONLY: a parsed (low, high) pair that is
# not one of these is ``unknown_amount``, counted, and never rounded into a neighbouring tier —
# because the tier is the entire conviction signal (``copytrade/sizing.tier_weight``) and a
# silently-widened bucket is a silently-larger position.
STOCK_ACT_TIERS: tuple[tuple[Decimal, Decimal | None], ...] = (
    (Decimal("1001"), Decimal("15000")),
    (Decimal("15001"), Decimal("50000")),
    (Decimal("50001"), Decimal("100000")),
    (Decimal("100001"), Decimal("250000")),
    (Decimal("250001"), Decimal("500000")),
    (Decimal("500001"), Decimal("1000000")),
    (Decimal("1000001"), Decimal("5000000")),
    (Decimal("5000001"), Decimal("25000000")),
    (Decimal("25000001"), Decimal("50000000")),
    (Decimal("50000001"), None),                 # the open-ended top tier
)
_TIER_INDEX = {(low, high): AmountRange(low=low, high=high) for low, high in STOCK_ACT_TIERS}

# ---- transaction codes ----------------------------------------------------------------------
# The House prints single letters; the Senate spells them out. BOTH spellings map here and
# ANYTHING ELSE maps to None. There is deliberately no fuzzy match and no default: an unknown
# code that defaulted to a purchase would turn a parser gap into a buy order.
TX_CODES: dict[str, str] = {
    "p": TX_PURCHASE,
    "purchase": TX_PURCHASE,
    "s": TX_SALE,
    "sale": TX_SALE,
    "sale (full)": TX_SALE,
    "s (full)": TX_SALE,
    "s (partial)": TX_SALE_PARTIAL,
    "sale (partial)": TX_SALE_PARTIAL,
    "sp": TX_SALE_PARTIAL,          # some House text layers collapse "S (partial)" to "SP"
    "e": TX_EXCHANGE,
    "exchange": TX_EXCHANGE,
}

# ---- House asset-type codes (the bracketed suffix on a House PTR line) -----------------------
# The House prints an asset-class code in square brackets after the asset name. Only two of
# them are actionable; every OTHER code is a definite, positive statement that the asset is not
# an equity, which is far better evidence than the free text and is used as such below.
HOUSE_ASSET_CODE_STOCK = "st"
HOUSE_ASSET_CODE_OPTION = "op"

_TICKER = re.compile(r"^[A-Z0-9.\-]{1,12}$")     # same shape as ingest.normalize._TICKER
_HAS_LETTER = re.compile(r"[A-Z]")
# Tokens that appear inside parentheses on these forms and are NOT tickers. A ticker-shaped
# non-ticker is the one failure mode that silently attaches a real member's trade to the wrong
# company, so the gate is "unambiguous or nothing".
_NOT_TICKERS = frozenset({
    "N", "A", "NA", "N.A", "NONE", "N-A", "TBD", "NULL", "X", "XX", "ST", "OP", "EF", "MF",
    "CS", "GS", "RE", "OT", "PE", "IH", "IRA", "LLC", "LP", "INC", "CO", "CORP", "USD",
    "FULL", "PART", "JT", "SP", "DC", "UNKNOWN",
})

# A document id is interpolated into a URL PATH and into ``Disclosure.raw_ref``. Both are
# reasons to keep it to a conservative ASCII charset: a value containing '/' or '..' walks the
# clerk's site, and ``raw_ref`` is carried into prompt-visible citation text downstream
# (the BUG-085 lesson in ingest/normalize.py). Explicit [0-9A-Za-z] rather than \w: \w matches
# Unicode letters, and a fullwidth-digit doc id would pass a \w gate and alias a real one.
_DOC_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$", re.ASCII)

# ASCII-ONLY DIGITS, EVERYWHERE A NUMBER IS READ OFF A FILING.
#
# Python's ``\d`` is Unicode-aware and matches fullwidth ("１"), Arabic-Indic ("١") and a few
# hundred other decimal digits; ``int()`` and ``Decimal()`` then accept them and produce the
# ASCII value. So without ``re.ASCII`` one logical row has many distinct wire spellings that all
# launder into the same tier and the same date — different ``raw_row`` and ``content_hash``,
# identical claimed trade. That is precisely the aliasing ``_DOC_ID`` refuses forty lines below
# ("a fullwidth-digit doc id would pass a ``\\w`` gate and alias a real one") and
# ``validate_ticker`` refuses with its ``isascii()`` check, so the digit gates say it too.
_US_DATE = re.compile(r"\b([0-9]{1,2})/([0-9]{1,2})/([0-9]{2,4})\b", re.ASCII)
_ISO_DATE = re.compile(r"\b([0-9]{4})-([0-9]{2})-([0-9]{2})\b", re.ASCII)
_WS = re.compile(r"\s+")
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-",
                         "—": "-", "―": "-", "−": "-"})


# ------------------------------------------------------------------------------ small helpers

def _collapse(text: str | None) -> str:
    """Whitespace-collapse and dash-normalize. Applied before PATTERN MATCHING ONLY.

    Never applied to a value on its way into a ``Disclosure`` identity field: collapsing is a
    lossy transform, and a raw wire identity that CHANGES under normalization is exactly what
    ``ingest.normalize._canonical_ticker`` refuses (a padded spelling can alias a real one).
    """
    if not isinstance(text, str):
        return ""
    return _WS.sub(" ", text.translate(_DASHES)).strip()


def validate_ticker(raw) -> str | None:
    """Return an uppercase US ticker, or None. STRICT — this is an attribution boundary.

    The pattern is deliberately the same shape ``ingest.normalize._TICKER`` applies, so a
    symbol that survives here also survives ``to_market_event`` and the two gates cannot drift
    into disagreeing about what a ticker is. Beyond the pattern:

      * the raw value must be ASCII and must not change under ``strip`` — a padded or
        Unicode-look-alike spelling must never impersonate a real ticker;
      * it must contain at least one LETTER — "---" and "1" match the character class but are
        not tickers;
      * it must not be one of the form's own non-ticker tokens (``_NOT_TICKERS``) — a House
        asset-class code in parentheses would otherwise become a symbol.
    """
    if type(raw) is not str or not raw.isascii() or raw != raw.strip() or not raw:
        return None
    ticker = raw.upper()
    if ticker in _NOT_TICKERS or not _HAS_LETTER.search(ticker):
        return None
    return ticker if _TICKER.match(ticker) else None


def validate_doc_id(raw) -> str | None:
    """Return a document id safe to put in a URL path and a raw_ref, or None."""
    if type(raw) is not str or not raw.isascii() or raw != raw.strip():
        return None
    return raw if _DOC_ID.match(raw) else None


def parse_filing_date(text) -> date | None:
    """Parse a date as these forms print it (M/D/YYYY, MM/DD/YY) or as ISO. None otherwise.

    A two-digit year is read as 20YY. That is a guess in principle and not one in practice:
    the electronic filing systems this parser reads did not exist before 2012, and the
    alternative (refusing every 2-digit year) would discard real rows. It is bounded by the
    caller's window check, which refuses anything outside the requested fetch window anyway.
    """
    if not isinstance(text, str):
        return None
    s = _collapse(text)
    m = _ISO_DATE.search(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _US_DATE.search(s)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_tx_type(text) -> str | None:
    """Map a House letter code or a Senate spelled-out type to a ``TX_*`` constant, or None.

    None is a REFUSAL, not a default. ``copytrade/engine.py`` counts it as ``unknown_tx_type``
    and skips the row; nothing downstream may treat an unreadable code as tradeable.
    """
    if not isinstance(text, str):
        return None
    key = _collapse(text).lower().rstrip(".")
    key = key.replace("( ", "(").replace(" )", ")")
    return TX_CODES.get(key)


def parse_amount(text) -> AmountRange | None:
    """Map printed amount text to one of the STOCK Act tiers, or None.

    EXACT TIER MATCH ONLY. The parsed pair must BE a published bracket; a pair that merely
    looks plausible ("$1,001 - $16,000" off a bad text layer) is refused and counted, because
    the tier is the whole conviction signal and there is no safe direction in which to round.
    """
    if not isinstance(text, str):
        return None
    s = _collapse(text).replace("$", "").replace(",", "")
    if not s:
        return None
    # ``[0-9]`` rather than ``\d``: see the note on ``_US_DATE``. ``Decimal("１００１")`` is
    # ``Decimal('1001')``, so a Unicode-digit amount would otherwise be laundered into a real
    # STOCK Act tier — and the tier is the whole conviction signal.
    low_only = re.fullmatch(r"(?i)(?:over|greater than|more than|>)\s*([0-9]+)(?:\s*\+)?", s)
    if low_only is None:
        low_only = re.fullmatch(r"([0-9]+)\s*\+", s)
    if low_only is not None:
        try:
            low = Decimal(low_only.group(1))
        except InvalidOperation:
            return None
        return _TIER_INDEX.get((low, None))
    pair = re.fullmatch(r"([0-9]+)\s*-\s*([0-9]+)", s)
    if pair is None:
        return None
    try:
        low, high = Decimal(pair.group(1)), Decimal(pair.group(2))
    except InvalidOperation:
        return None
    return _TIER_INDEX.get((low, high))


_CLASS_CODE = re.compile(r"\[([A-Za-z0-9]{1,4})\]")


def _bracket_code(text: str) -> str | None:
    """The House's bracketed asset-class code, lowercased ("[ST]" -> "st"), or None.

    RIGHTMOST WINS, and that is a security property rather than a style choice. The only part of
    a House row the FILER TYPES is the asset name, and it is printed to the LEFT of the class
    code the form itself emits. So a "[OP]" (or "[ST]") that appears earlier in the line than
    another one was typed into the description by the filer, and must never outrank the form's
    own column. Same rule, same reason, as ``_locate_columns`` below.
    """
    matches = _CLASS_CODE.findall(text)
    return matches[-1].lower() if matches else None


# An option row that never spells the word "option". Both chambers print these: "AAPL - Jan 2027
# $200 Calls", "NVDA 08/01/2026 Call $500.00", "…, Puts, strike $150". Matching a bare "call" or
# "put" ANYWHERE would be worse than the gap it closes — "Calloway Golf", "First Call Corp",
# "Putative Holdings", "callable note", "call date 2027" are all equities or non-securities, and
# an equity misread as an option is dropped or flipped to a SELL. So the word alone is never
# enough: it must be ADJACENT to an option TERM (a strike price, an expiry date, a month-year, an
# "@" or the literal word "strike"/"exp"). Word boundaries throughout, so "Calloway" and
# "putative" cannot match the word at all, and the adjacency requirement is what keeps a company
# whose NAME contains "Call" from being re-classed by a dollar figure elsewhere in the line.
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_PRICE_OR_EXPIRY = (
    r"(?:\$\s?[\d,]+(?:\.\d+)?"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b" + _MONTH + r"\s+\d{2,4}\b)"
)
_OPTION_STRUCTURE = re.compile(
    # a strike / expiry token IMMEDIATELY BEFORE the word: "$200 calls", "jun 2026 puts"
    r"(?i)" + _PRICE_OR_EXPIRY + r"[\s\-]*\b(?:calls?|puts?)\b"
    # ... or IMMEDIATELY AFTER it: "call 01/16/2027", "puts, strike $150", "calls @ 200"
    r"|\b(?:calls?|puts?)\b[\s,.;:\-]*"
    r"(?:@|strike|exp\b|expir|\$\s?[\d,]|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})"
)


def classify_asset(description, *, type_hint: str | None = None) -> str:
    """Classify a PTR asset line as ``ASSET_*``. FAIL-CLOSED: the default is ``ASSET_OTHER``.

    Evidence is taken in order of how much the FILER committed to it:

      1. the House bracketed class code (``[ST]``/``[OP]``) or the Senate's own asset-type
         column, because that is a structured field the filer selected — not prose;
      2. an explicit option vocabulary in the description;
      3. an explicit equity vocabulary.

    A definite non-equity code short-circuits to ``ASSET_OTHER``. An asset the filer called an
    option but whose DIRECTION is ambiguous (both "call" and "put" present, or neither) is also
    ``ASSET_OTHER``: an option we cannot orient is not a signal, and ``ASSET_OTHER`` is the one
    value ``copytrade`` never trades. Guessing the direction of an option is the single most
    expensive wrong guess available in this whole package.

    THE ORDER ABOVE IS THE CODE'S ORDER, NOW. It used to evaluate the option vocabulary FIRST,
    which inverted the whole point of the list: the description is the ONE field on a House PTR
    the filer types, and it was outranking ``[ST]`` — the form's own structured asset-class
    column. ``classify_asset("Tiny Co common stock (NVDA) put options program [ST]")`` returned
    ``option_put``, and a disclosed stock PURCHASE then translated (``copytrade/options.py``)
    into ``exit_underlying`` — a member's BUY became a SELL of the underlying, driven entirely
    by words typed into the asset-name field. The same inversion dropped genuine equities: a
    company whose NAME contains the substring "option" ("Option Care Health Inc. (OPCH) Common
    Stock [ST]") classified as ``ASSET_OTHER`` and was never traded.

    So a structured class code now short-circuits BOTH ways, before any free text is read, and
    the free-text option test only runs on a row that carries no structured class at all. That
    test is also word-bounded (``\\boptions?\\b``) rather than a substring, for the same reason
    ``_OPTION_STRUCTURE`` requires adjacency: "Option Care Health" is a real NYSE equity.
    """
    text = _collapse(description).lower()
    hint = _collapse(type_hint).lower() if type_hint else ""
    code = _bracket_code(text)

    option_hint = "option" in hint
    stock_hint = bool(hint) and hint.startswith("stock") and not option_hint
    # "Non-Public Stock" is a real Senate asset type and is NOT tradeable — it names a private
    # company. A hint containing it must never reach the equity branch.
    if "non-public" in hint or "non public" in hint:
        return ASSET_OTHER

    def _orient() -> str:
        has_call = re.search(r"\bcalls?\b", text) is not None or "call" in hint
        has_put = re.search(r"\bputs?\b", text) is not None or "put" in hint
        if has_call and not has_put:
            return ASSET_OPTION_CALL
        if has_put and not has_call:
            return ASSET_OPTION_PUT
        return ASSET_OTHER                       # an option we cannot orient is not a signal

    # 1 ---- the STRUCTURED field wins outright, in both directions ------------------------
    if option_hint or code == HOUSE_ASSET_CODE_OPTION:
        return _orient()
    if stock_hint or code == HOUSE_ASSET_CODE_STOCK:
        return ASSET_EQUITY
    if code is not None:
        return ASSET_OTHER                       # a definite non-stock, non-option class code
    if hint:
        return ASSET_OTHER                       # the filer named a class and it was not stock

    # 2 ---- no structured class at all: only now does the description get a vote -----------
    # ``\boptions?\b`` rather than a substring, and ``_OPTION_STRUCTURE`` for the rows that
    # never say the word. An option row misread as equity is still the expensive direction (it
    # bypasses the haircut, the expiry gate AND the put -> exit flip), so the test stays
    # deliberately eager HERE — where there is no form column to contradict it.
    if (re.search(r"\boptions?\b", text) is not None
            or _OPTION_STRUCTURE.search(text) is not None):
        return _orient()
    # 3 ---- an explicit equity vocabulary counts, and any fund / debt / non-security
    # vocabulary vetoes it. Everything else is ASSET_OTHER by default.
    if re.search(r"\b(fund|etf|index|trust|bond|note|treasury|municipal|ira|401|annuity|"
                 r"crypto|bitcoin|real estate|llc|partnership|deposit|cd)\b", text):
        return ASSET_OTHER
    if re.search(r"\b(common stock|class [a-c] common|ordinary shares|stock|shares)\b", text):
        return ASSET_EQUITY
    return ASSET_OTHER


def extract_symbol(description, *, explicit: str | None = None) -> str | None:
    """The ticker the filing STATES, or None. Never inferred from a company name.

    ``explicit`` is a dedicated ticker column (the Senate has one). When it is present and
    valid it wins outright. Otherwise the House convention is a parenthesised ticker in the
    asset line — and the rule is UNAMBIGUOUS OR NOTHING: zero candidates gives None, and two
    DIFFERENT candidates also gives None, because a line naming two symbols cannot be resolved
    to one position without guessing whose trade it was.

    THE CANDIDATE IS VALIDATED RAW. ``_collapse`` deletes Unicode whitespace and rewrites six
    Unicode dashes to ASCII "-" — exactly the spellings ``ingest.normalize._canonical_ticker``
    refuses, because a padded or look-alike spelling must not be able to impersonate a real
    ticker and consume its dedup key. Running it over an untrusted ticker BEFORE
    ``validate_ticker`` would launder those spellings into acceptance and put the two gates into
    disagreement about what a ticker is; it also contradicts ``_collapse``'s own docstring
    ("never applied to a value on its way into a Disclosure identity field"). Only ASCII space
    and tab are stripped — that is layout padding around a column, not a different identity.
    """
    if explicit is not None:
        ticker = validate_ticker(_strip_layout(explicit))
        if ticker is not None:
            return ticker
    text = description if isinstance(description, str) else ""
    candidates = {t for t in (validate_ticker(_strip_layout(c))
                              for c in re.findall(r"\(([^()]{1,20})\)", text)) if t}
    if len(candidates) == 1:
        return candidates.pop()
    return None


def _strip_layout(value):
    """Strip ASCII space/tab ONLY — never Unicode whitespace. See :func:`extract_symbol`."""
    return value.strip(" \t") if type(value) is str else value


_BARE_DATE = re.compile(r"\b([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b",
                        re.ASCII)
_OPTION_VOCAB = re.compile(r"(?i)\b(?:options?|calls?|puts?)\b")


_EXPIRY_KEYWORD = re.compile(
    r"(?i)\b(?:exp(?:ir(?:es|ation|y)?)?\.?)\s*[:\s]\s*"
    r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.ASCII,
)
REASON_AMBIGUOUS_OPTION_EXPIRY = "unparsed_row:ambiguous_option_expiry"
"""An option row whose expiry cannot be resolved to ONE date. See
:func:`option_expiry_is_ambiguous`. Keeps the ``unparsed_row`` prefix so
``provider.is_retriable_reason`` classifies it as STRUCTURAL — re-fetching cannot disambiguate
a description that names two dates."""


def _readable_dates(text: str) -> set[date]:
    """Every distinct date the description states, keyword or not. Unreadable ones are dropped."""
    dates = {parse_filing_date(d) for d in _BARE_DATE.findall(text)}
    dates.discard(None)
    return dates


def option_expiry_is_ambiguous(description) -> bool:
    """True when an option row's expiry is UNRESOLVABLE rather than merely undisclosed.

    The distinction is the whole point. ``copytrade/options.py`` runs its expired / near-expiry
    gate ONLY when ``option_expiry`` is known, so "unknown" is an ADMISSION, not a refusal — the
    contract becomes a fresh haircut long in the underlying, flagged ``option_expiry_undisclosed``
    but traded. That is the right answer for a filing that never stated an expiry (there is
    nothing to check, and the position is in the underlying anyway). It is the WRONG answer for a
    filing that stated several dates and left us unable to say which one is the expiry: there the
    evidence exists, we cannot read it, and admitting the row means the gate the operator asked
    for silently did not run.

    So: reads as an option, no keyword expiry, and more than one distinct readable date -> the
    row is refused and counted by ``build_disclosure``. Zero readable dates is undisclosed, not
    ambiguous, and stays admitted.
    """
    text = _collapse(description)
    if not _reads_as_option(text) or _EXPIRY_KEYWORD.search(text) is not None:
        return False
    return len(_readable_dates(text)) > 1


def _reads_as_option(text: str) -> bool:
    """True when the description itself says "this is an option". Used ONLY to decide whether a
    keyword-free date may be read as an expiry — never to classify (that is ``classify_asset``,
    which is stricter about a bare "call"/"put" because it decides what gets TRADED)."""
    return (_OPTION_VOCAB.search(text) is not None
            or _bracket_code(text) == HOUSE_ASSET_CODE_OPTION)


def extract_option_terms(description) -> tuple[date | None, Decimal | None]:
    """``(expiry, strike)`` where the filing states them, else ``(None, None)``.

    Both are optional on the form and both are refused rather than guessed. An option whose
    expiry is unknown is handled by ``copytrade/options.py`` as unknown — it is NOT assumed to
    be far-dated, which would defeat ``options_min_days_to_expiry``.

    THE EXPIRY IS ALSO READ WITHOUT AN "exp" KEYWORD, because the common House spelling has
    none: "NVIDIA Corporation (NVDA) Call Options 08/01/2026 $500.00 [OP]". Reading only the
    keyword form returned ``None`` for those rows, and an expiry of ``None`` means the
    expired / near-expiry gate in ``copytrade/options.py`` NEVER RUNS — an already-expired
    contract would be copied as a fresh long position, which is the operator's single most
    explicit requirement inverted. The keyword-free read is still not a guess:

      * it only runs on a description that reads as an OPTION (a call/put/option word, or the
        House ``[OP]`` class code) — a bare date on an equity row is a neighbouring column;
      * it only runs when the KEYWORD form found nothing. If the filing says "exp:" and the
        date behind it is unreadable, that is a refusal, not licence to use some other date;
      * it requires exactly ONE distinct readable date in the description. Two dates cannot be
        resolved to one expiry, and picking the later of them is precisely the guess that would
        walk an expired contract past the gate.

    THAT LAST REFUSAL IS FAIL-CLOSED ONLY BECAUSE ``build_disclosure`` MAKES IT ONE. Returning
    ``None`` here is a refusal to *state* an expiry; on its own it is not a refusal to *trade*,
    because ``copytrade/options.py`` skips its whole expiry block when the expiry is unknown. So
    a multi-date option row — "NVDA Call 08/01/2025 [OP] - part of a spread opened 06/01/2025",
    every date already in the past — was admitted as ``option_expiry_undisclosed`` and became a
    fresh haircut long in the underlying. ``build_disclosure`` therefore refuses the ROW with
    ``REASON_AMBIGUOUS_OPTION_EXPIRY`` (see :func:`option_expiry_is_ambiguous`), which is a
    counted parse error rather than an admission. A row with NO readable date at all is
    genuinely undisclosed and still admitted under the note — there is nothing there to refuse.

    The STRIKE stays keyword-only, deliberately. No gate anywhere reads it — ``copytrade``
    consumes the expiry and the direction — so inferring which of several dollar figures on a
    hand-typed row is the strike would be a guess that buys nothing and can only be wrong.
    """
    text = _collapse(description)
    expiry = None
    m = _EXPIRY_KEYWORD.search(text)
    if m:
        expiry = parse_filing_date(m.group(1))
    elif _reads_as_option(text):
        dates = _readable_dates(text)
        if len(dates) == 1:
            expiry = dates.pop()
    strike = None
    m = re.search(r"(?i)\bstrike\s*(?:price)?\s*[:\s]\s*\$?\s*([\d,]+(?:\.\d{1,4})?)", text)
    if m is None:
        m = re.search(r"(?i)\$\s*([\d,]+(?:\.\d{1,4})?)\s*strike", text)
    if m:
        try:
            strike = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            strike = None
        if strike is not None and strike <= 0:
            strike = None
    return expiry, strike


def row_content_hash(document_hash: str, line_no: int, raw_row: str) -> str:
    """Bind ONE parsed row to the RAW DOCUMENT it came out of (Invariant 8 / SEC-13).

    The hash covers the document's own digest AND the untouched row text, so the audit chain
    is: cockpit ``content_hash`` -> (document digest + row bytes) -> the fetched PDF/HTML at
    ``raw_ref``. An operator re-fetches the primary document, re-hashes it, and can prove that
    the stored row came from those bytes and not from a later edit — which is the entire point
    of keeping raw separate from derived.
    """
    payload = f"{document_hash}\x00{int(line_no)}\x00{raw_row}".encode("utf-8", "surrogatepass")
    return hashlib.sha256(payload).hexdigest()


def document_hash(body: bytes | str) -> str:
    """SHA-256 of the document AS FETCHED. The anchor every row's hash is bound to."""
    if isinstance(body, str):
        body = body.encode("utf-8", "surrogatepass")
    return hashlib.sha256(body).hexdigest()


# ------------------------------------------------------------------------------ result shapes

@dataclass(frozen=True)
class ParsedDocument:
    """What one document yielded, INCLUDING everything it did not yield.

    ``candidate_rows`` is the count of lines that looked like transactions; ``ignored_lines``
    is everything else (headers, page furniture, the certification block). Keeping both means
    "this PDF parsed to nothing" and "this PDF had nothing in it" are different observations —
    the same reason ``EdgarPipelineResult`` separates ``baselines`` from ``poll_errors``.
    """

    disclosures: tuple[Disclosure, ...] = ()
    row_errors: tuple[tuple[str, str], ...] = ()
    lines_seen: int = 0
    candidate_rows: int = 0
    ignored_lines: int = 0

    @property
    def ok(self) -> bool:
        return not self.row_errors


@dataclass(frozen=True)
class HouseIndexEntry:
    """One row of the House Clerk's per-year filing INDEX.

    The index tells you WHO filed WHAT and WHEN. It does NOT carry a single transaction — those
    live in the PDF at ``doc_id``. Treating an index row as a trade signal would be the single
    most dangerous misreading available in this feed, so the type is deliberately separate from
    :class:`Disclosure` and carries no amount, no ticker and no transaction.
    """

    doc_id: str
    filer_display: str                # ESCAPED (SEC-02)
    filer_slug: str
    filing_type: str
    filed_date: date
    year: int
    state_dst: str

    @property
    def is_ptr(self) -> bool:
        """FilingType 'P' is the Periodic Transaction Report. Everything else (annual 'O'/'A',
        candidate 'C', extensions, blind-trust filings) is not a transaction report and is not
        fetched — bandwidth spent on a document that cannot contain a trade is bandwidth taken
        from a document that can."""
        return self.filing_type == FILING_TYPE_PTR


FILING_TYPE_PTR = "P"


@dataclass(frozen=True)
class ParsedIndex:
    entries: tuple[HouseIndexEntry, ...] = ()
    errors: tuple[tuple[str, str], ...] = ()
    rows_seen: int = 0


# ------------------------------------------------------------------------- the House index XML

# Any DOCTYPE or ENTITY declaration, at ANY offset. Deliberately liberal (optional whitespace
# after "<!"), because this is a refusal gate: over-matching costs a counted error on a document
# the House index has no legitimate reason to send, and under-matching costs entity expansion.
_DTD_DECL = re.compile(r"<!\s*(?:doctype|entity)", re.IGNORECASE)


def parse_house_index(xml_text: str) -> ParsedIndex:
    """Parse ``<YEAR>FD.xml`` (the House Clerk's index of every filing that year).

    XML FROM AN UNTRUSTED SOURCE IS A KNOWN HAZARD AND IS TREATED AS ONE. Before parsing we
    refuse any document declaring a DTD or an entity: ``xml.etree`` is not an XML hardening
    library, and a document that wants to define entities has no legitimate reason to be in a
    filing index. The size cap bounds the rest. A refusal returns a counted error, never an
    exception — an index we will not parse is a dead feed for the day, not a dead process.

    THE SCAN COVERS THE WHOLE DOCUMENT, not a prolog-sized window. A window is trivially walked:
    an XML comment is legal before the doctype, so ``<!--`` + padding + ``<!DOCTYPE r [<!ENTITY
    …>]>`` pushes the declaration past any fixed offset and hands expat a document with an
    internal DTD to expand. The cost of scanning it all is one bounded regex pass over at most
    ``MAX_DOCUMENT_CHARS``; the cost of not scanning it all is billion-laughs.
    """
    if not isinstance(xml_text, str) or not xml_text.strip():
        return ParsedIndex(errors=(("index", "index_empty"),))
    if len(xml_text) > MAX_DOCUMENT_CHARS:
        return ParsedIndex(errors=(("index", "index_too_large"),))
    if _DTD_DECL.search(xml_text) is not None:
        # Fail closed. Nothing legitimate in this index needs a DTD; anything that does is
        # either corrupt or hostile, and either way it is not worth an expat entity expansion.
        return ParsedIndex(errors=(("index", "index_declares_dtd"),))
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        return ParsedIndex(errors=(("index", f"index_malformed:{type(exc).__name__}"),))

    entries: list[HouseIndexEntry] = []
    errors: list[tuple[str, str]] = []
    rows = 0
    for member in root.iter():
        if member is root or len(member) == 0:
            continue
        doc_id_raw = _child_text(member, "DocID")
        if doc_id_raw is None:
            continue                              # not a filing row (a wrapper element)
        rows += 1
        if rows > MAX_INDEX_ENTRIES:
            errors.append(("index", "index_entry_cap"))
            break
        doc_id = validate_doc_id(doc_id_raw)
        if doc_id is None:
            # A doc id we will not put in a URL. Counted, never sanitised into something we
            # then fetch: a "cleaned" id points at a DIFFERENT document than the index meant.
            errors.append((f"index:{rows}", "bad_doc_id"))
            continue
        filed = parse_filing_date(_child_text(member, "FilingDate") or "")
        if filed is None:
            errors.append((doc_id, "bad_filed_date"))
            continue
        last = _child_text(member, "Last") or ""
        first = _child_text(member, "First") or ""
        prefix = _child_text(member, "Prefix") or ""
        suffix = _child_text(member, "Suffix") or ""
        display_raw = _collapse(f"{prefix} {first} {last} {suffix}")
        slug = normalize_filer(f"{last}, {first}" if last else display_raw)
        if not slug:
            # An unparseable name fails CLOSED at the policy layer anyway (an empty slug
            # matches no follow entry), but it is recorded here so the operator can see that a
            # filing was skipped for a NAME reason rather than silently never appearing.
            errors.append((doc_id, "index_entry_invalid"))
            continue
        filing_type = _collapse(_child_text(member, "FilingType") or "").upper()[:4]
        year_text = _collapse(_child_text(member, "Year") or "")
        try:
            year = int(year_text) if year_text.isdigit() else filed.year
        except ValueError:
            year = filed.year
        entries.append(HouseIndexEntry(
            doc_id=doc_id,
            filer_display=escape_untrusted(display_raw)[:200],
            filer_slug=slug,
            filing_type=filing_type,
            filed_date=filed,
            year=year,
            state_dst=escape_untrusted(_collapse(_child_text(member, "StateDst") or ""))[:16],
        ))
    return ParsedIndex(entries=tuple(entries), errors=tuple(errors), rows_seen=rows)


def _child_text(element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None:
        return None
    return child.text if child.text is not None else ""


# --------------------------------------------------------------------- the PTR transaction rows

# The printed row, in the order both chambers' forms use:
#   <asset description> [<class code>]  <tx code>  <transaction date>  [<notif date>]  <amount>
#
# THE FILER TYPES THE ASSET DESCRIPTION. EVERY OTHER COLUMN IS THE FORM'S.
# That asymmetry is the whole of this section. A member can type anything at all into the asset
# name — including a convincing copy of the remaining columns:
#
#   Tiny Co common stock (NVDA) P 08/01/2026 $50,000,001 + [ST] P 08/01/2026 $1,001 - $15,000
#   \________________ TYPED BY THE FILER ______________/ \___ THE FORM'S OWN COLUMNS ____/
#
# A pattern anchored at ``^`` with a non-greedy description takes the LEFTMOST match, so it reads
# the typed copy and discards the real one: the transaction code, the transaction date and — the
# expensive one — the STOCK Act amount TIER all come from the attacker. The tier is the entire
# conviction signal (``copytrade/sizing.tier_weight``), so a forged top bracket is a forged
# maximum-size position.
#
# So the columns are located STRUCTURALLY instead of positionally:
#
#   1. The bracketed asset-class code ("[ST]", "[OP]", "[OT]", …) is the reliable delimiter. It
#      is emitted by the form, and it is printed AFTER the asset name — therefore every byte the
#      filer typed lies to its LEFT. The RIGHTMOST class code on the line is the anchor, and the
#      real columns can only be to the right of it. Anything the filer typed is out of scope by
#      construction, not by pattern luck.
#   2. UNAMBIGUOUS OR NOTHING, IN THE ANCHORED REGION TOO. The form emits EXACTLY ONE column
#      group per printed row, so within the region to the right of the anchor exactly one group
#      may match. Two is a COUNTED parse error (``unparsed_row:ambiguous_columns``) and never a
#      choice between them.
#
#      This rule used to apply only to a line with NO class code, and preferring the rightmost
#      group in the anchored case simply INVERTED the original vulnerability instead of closing
#      it. The region to the right of the anchor is not attacker-free: a House PTR prints the
#      filer-typed FILING STATUS / DESCRIPTION sub-fields AFTER the amount, and any extractor
#      that joins them lands them on the same line. So
#
#        Apple Inc. (AAPL) [ST] P 08/01/2026 08/15/2026 $1,001 - $15,000 FILING STATUS: New
#        DESCRIPTION: closes the S 07/01/2026 $50,000,001 + lot
#
#      read as a SALE at the top tier — a forged side AND a 4x forged size (tier weight 0.50 ->
#      2.00), with no counted error. Two printed rows merged onto one extracted line have the
#      same shape and the same outcome. Neither can be resolved by picking a side.
#   3. Within a region that holds exactly one group, that group IS ``matches[0]`` and
#      ``matches[-1]``; the code takes ``matches[0]`` because the form's columns immediately
#      follow the form's own class code, so a reader does not have to reason about which end.
#
# The ambiguity reason keeps the ``unparsed_row`` prefix so ``provider.is_retriable_reason``
# still classifies it as STRUCTURAL: re-fetching the document cannot make it less ambiguous.
_TX_CODE_ALTERNATION = "|".join(
    re.escape(code) for code in sorted(TX_CODES, key=len, reverse=True)
)
# One column group. Unanchored, scanned with ``finditer`` — the caller decides which match is the
# real one. The leading ``(?:^|[\s|])`` keeps the code a whole token (a company name ending in
# "P" cannot supply it), and the amount is still the far anchor: the code must be followed by a
# date AND a bracket amount, so a stray " P " in prose cannot match on its own.
# ``re.ASCII`` so ``\d`` and ``\s`` mean the ASCII ones (see the note on ``_US_DATE``): a
# fullwidth-digit amount must not be laundered into a real STOCK Act tier.
_COLUMNS = re.compile(
    r"(?:^|[\s|])(?P<code>" + _TX_CODE_ALTERNATION + r")[\s|]+"
    r"(?P<tx>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})[\s|]+"
    r"(?:(?P<notified>\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})[\s|]+)?"
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|(?:Over|Greater than|More than)\s*\$[\d,]+"
    r"|\$[\d,]+\s*\+)",
    re.IGNORECASE | re.ASCII,
)
REASON_AMBIGUOUS_COLUMNS = "unparsed_row:ambiguous_columns"


def _locate_columns(line: str) -> tuple[re.Match | None, str | None]:
    """Find the form's OWN columns on a printed row. ``(match, None)`` or ``(None, reason)``.

    See the block comment above. Two rules, and they now apply to EVERY line rather than only to
    the ones with no class code: the search starts to the right of the rightmost class code, and
    a region containing anything other than exactly one column group is a counted refusal.
    """
    anchors = list(_CLASS_CODE.finditer(line))
    if not anchors:
        # No structural evidence at all. Unambiguous or nothing, over the whole line.
        matches = list(_COLUMNS.finditer(line))
        if not matches:
            return None, "unparsed_row"
        if len(matches) > 1:
            return None, REASON_AMBIGUOUS_COLUMNS
        return matches[0], None

    # Each class code OWNS the text from its own end up to the next class code (or the end of
    # the line). A column group before the FIRST anchor belongs to no code and is filer free
    # text — that is the anchor rule, and it is what makes a typed column set unreachable.
    owners = [
        found
        for start, end in (
            (a.end(), anchors[i + 1].start() if i + 1 < len(anchors) else len(line))
            for i, a in enumerate(anchors)
        )
        if (found := list(_COLUMNS.finditer(line, start, end)))
    ]
    if not owners:
        # Includes "the class code is the last thing on the line": a row whose columns are not
        # where the form puts them is a row we cannot read, and reading it anyway would mean
        # falling back to the very region the filer controls.
        return None, "unparsed_row"
    if len(owners) > 1:
        # TWO class codes each followed by their own column group: two printed rows landed on
        # one extracted line. Taking the rightmost silently DISCARDED the other real trade (and
        # in the reproduced case turned a purchase at the bottom tier into a sale at the top).
        return None, REASON_AMBIGUOUS_COLUMNS
    matches = owners[0]
    if len(matches) != 1:
        # One class code, two groups after it. The region to the right of the anchor is NOT
        # attacker-free — a House PTR prints the filer-typed FILING STATUS / DESCRIPTION
        # sub-fields after the amount — so a second group there is free text, not a column.
        return None, REASON_AMBIGUOUS_COLUMNS
    return matches[0], None
# A line is a CANDIDATE transaction row when it carries both a date and a dollar figure. Lines
# that are not candidates are counted as ``ignored_lines`` rather than as errors: a PTR PDF is
# mostly headers, page numbers and a certification block, and an error count inflated by page
# furniture is an error count nobody reads.
_CANDIDATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})")


def parse_ptr_text(
    text: str,
    *,
    chamber: str,
    doc_id: str,
    filer_display: str,
    filer_slug: str,
    filed_date: date,
    raw_ref: str,
    doc_hash: str,
) -> ParsedDocument:
    """Parse the EXTRACTED TEXT of a PTR into :class:`Disclosure` rows.

    ``text`` is whatever the injected extractor produced. This function never sees a PDF (see
    the module docstring), which is precisely what makes it testable from a fixture string.

    ``line_no`` IS THE CANDIDATE-ROW ORDINAL, NOT THE EMITTED ORDINAL — load-bearing. It is
    half of ``Disclosure.key``, so numbering only the rows that PARSED would mean that fixing a
    parser gap re-keys every row after the previously-failing one: the same trade would come
    back as new evidence, be re-emitted, and be re-proposed to the operator. A failed row
    therefore HOLDS ITS SLOT, and the slot is stable for as long as the candidate-detection
    rule is. (Residual, stated rather than hidden: changing ``_CANDIDATE`` itself CAN shift
    ordinals. Amended filings re-issue under a new ``doc_id`` and are unaffected.)
    """
    if not isinstance(text, str) or not text.strip():
        return ParsedDocument(row_errors=((doc_id, "empty_document"),))
    if len(text) > MAX_DOCUMENT_CHARS:
        return ParsedDocument(row_errors=((doc_id, "document_too_large"),))

    disclosures: list[Disclosure] = []
    errors: list[tuple[str, str]] = []
    lines = text.splitlines()
    lines_seen = min(len(lines), MAX_LINES)
    candidate_no = 0
    ignored = 0
    for raw_line in lines[:MAX_LINES]:
        line = _collapse(raw_line)
        if not line or _CANDIDATE.search(line) is None or "$" not in line:
            ignored += 1
            continue
        line_no = candidate_no
        candidate_no += 1
        if candidate_no > MAX_ROWS_PER_DOCUMENT:
            errors.append((f"{doc_id}:{line_no}", "row_cap_reached"))
            break
        disclosure, reason = _row_to_disclosure(
            line, chamber=chamber, doc_id=doc_id, line_no=line_no,
            filer_display=filer_display, filer_slug=filer_slug, filed_date=filed_date,
            raw_ref=raw_ref, doc_hash=doc_hash,
        )
        if disclosure is None:
            errors.append((f"{doc_id}:{line_no}", reason or "unparsed_row"))
            continue
        disclosures.append(disclosure)
    return ParsedDocument(
        disclosures=tuple(disclosures), row_errors=tuple(errors), lines_seen=lines_seen,
        candidate_rows=candidate_no, ignored_lines=ignored,
    )


def _row_to_disclosure(line: str, *, chamber, doc_id, line_no, filer_display, filer_slug,
                       filed_date, raw_ref, doc_hash) -> tuple[Disclosure | None, str | None]:
    """One printed row -> one Disclosure, or ``(None, reason)``. Never a partial guess."""
    m, reason = _locate_columns(line)
    if m is None:
        return None, reason or "unparsed_row"
    # Everything to the LEFT of the located columns is the description — including the class
    # code and any option terms printed after it, which ``classify_asset`` and
    # ``extract_option_terms`` both need. Everything to the right is form furniture (filing
    # status, page numbering) and is dropped, as before.
    description = _collapse(line[:m.start()])
    if not description:
        return None, "empty_description"
    tx_type = parse_tx_type(m.group("code"))
    if tx_type is None:
        return None, "unknown_tx_type"
    tx_date = parse_filing_date(m.group("tx"))
    if tx_date is None:
        return None, "bad_tx_date"
    amount = parse_amount(m.group("amount"))
    if amount is None:
        return None, "unknown_amount"
    return build_disclosure(
        chamber=chamber, doc_id=doc_id, line_no=line_no, filer_display=filer_display,
        filer_slug=filer_slug, tx_date=tx_date, filed_date=filed_date, tx_type=tx_type,
        amount=amount, description=description, explicit_symbol=None, type_hint=None,
        raw_ref=raw_ref, doc_hash=doc_hash, raw_row=line,
    )


def build_disclosure(*, chamber, doc_id, line_no, filer_display, filer_slug, tx_date,
                     filed_date, tx_type, amount: AmountRange, description,
                     explicit_symbol=None, type_hint=None, raw_ref, doc_hash,
                     raw_row) -> tuple[Disclosure | None, str | None]:
    """The ONE construction site for a Disclosure. Shared by both chambers on purpose.

    Two chambers, one set of validations: if the House path validated the disclosure lag and
    the Senate path did not, the two feeds would disagree about what a legal filing is, and the
    difference would only ever show up as a strange position.

    Note what is NOT derived here: the amount arrives already parsed, because it comes from a
    COLUMN in both chambers and never from the description. Re-deriving it from the free text
    would mean parsing an untrusted string twice with two different rules.
    """
    if filed_date < tx_date:
        # Disclosed BEFORE it happened. A negative disclosure lag is not a data curiosity: it
        # feeds ``age_days`` and the staleness decay, where it would size a nonsense row UP.
        return None, "tx_after_filed"
    asset_type = classify_asset(description, type_hint=type_hint)
    symbol = extract_symbol(description, explicit=explicit_symbol)
    expiry, strike = extract_option_terms(description)
    if (asset_type in (ASSET_OPTION_CALL, ASSET_OPTION_PUT) and expiry is None
            and option_expiry_is_ambiguous(description)):
        # An expiry we cannot resolve is NOT an expiry we may ignore: ``copytrade/options.py``
        # skips its expired / near-expiry gate on an unknown expiry, so admitting this row
        # would translate a possibly-dead contract into a fresh long. Counted, never guessed.
        return None, REASON_AMBIGUOUS_OPTION_EXPIRY
    if asset_type not in (ASSET_OPTION_CALL, ASSET_OPTION_PUT):
        # Option terms on a non-option row are noise from a neighbouring column; dropping them
        # keeps ``is_option`` and the option fields from ever disagreeing.
        expiry, strike = None, None
    return Disclosure(
        chamber=chamber,
        doc_id=doc_id,
        line_no=line_no,
        filer_slug=filer_slug,
        # Idempotent (``&`` first), so escaping a caller-escaped value is safe and the escape
        # can live at EVERY boundary rather than at one a future caller might skip. SEC-02.
        filer_display=escape_untrusted(filer_display)[:200],
        tx_date=tx_date,
        filed_date=filed_date,
        tx_type=tx_type,
        asset_type=asset_type,
        amount=amount,
        symbol=symbol,
        asset_description=escape_untrusted(description)[:MAX_DESCRIPTION_CHARS],
        option_expiry=expiry,
        option_strike=strike,
        raw_ref=raw_ref,
        content_hash=row_content_hash(doc_hash, line_no, raw_row),
    ), None


# ----------------------------------------------------------------------- the Senate HTML table

class _TableRows(HTMLParser):
    """Collect ``<tr>`` rows of cell text from an HTML document. Stdlib only, no dependency.

    Deliberately dumb: it does not build a DOM, does not execute anything, and does not follow
    anything. It exists because the Senate publishes electronically-filed PTRs as an HTML
    table, and a regex over nested markup is how you get a parser that is confidently wrong.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.header: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._is_header_row = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row, self._is_header_row = [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "th":
                self._is_header_row = True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_collapse("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._is_header_row and not self.header:
                self.header = [c.lower() for c in self._row]
            elif self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


# Senate column headers, as the eFD electronic PTR page prints them. Mapping is BY HEADER NAME,
# never by position: a positional map silently re-reads the whole table the day a column is
# inserted, and "amount" landing in the "type" slot is a wrong trade rather than a parse error.
_SENATE_REQUIRED = ("transaction date", "type", "amount")
_SENATE_ALIASES = {
    "transaction date": ("transaction date", "date"),
    "type": ("type", "transaction type"),
    "amount": ("amount",),
    "ticker": ("ticker", "symbol"),
    "asset name": ("asset name", "asset"),
    "asset type": ("asset type",),
    "owner": ("owner",),
    "comment": ("comment", "comments"),
}


def parse_senate_ptr_html(
    html_text: str,
    *,
    doc_id: str,
    filer_display: str,
    filer_slug: str,
    filed_date: date,
    raw_ref: str,
    doc_hash: str,
) -> ParsedDocument:
    """Parse an eFD electronically-filed PTR page into :class:`Disclosure` rows.

    Required headers missing -> the WHOLE document is one counted error and zero rows. That is
    the fail-closed reading: a table whose columns we cannot name is a table we cannot read,
    and reading it positionally "just in case" is how a page redesign becomes a wrong trade.
    """
    if not isinstance(html_text, str) or not html_text.strip():
        return ParsedDocument(row_errors=((doc_id, "empty_document"),))
    if len(html_text) > MAX_DOCUMENT_CHARS:
        return ParsedDocument(row_errors=((doc_id, "document_too_large"),))
    parser = _TableRows()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:                            # noqa: BLE001 - malformed markup, not a crash
        return ParsedDocument(row_errors=((doc_id, "senate_html_malformed"),))

    index: dict[str, int] = {}
    for canonical, aliases in _SENATE_ALIASES.items():
        for i, head in enumerate(parser.header):
            if head in aliases:
                index[canonical] = i
                break
    if any(k not in index for k in _SENATE_REQUIRED):
        return ParsedDocument(row_errors=((doc_id, "senate_table_headers_missing"),),
                              lines_seen=len(parser.rows))

    disclosures: list[Disclosure] = []
    errors: list[tuple[str, str]] = []
    line_no = 0
    for row in parser.rows[:MAX_ROWS_PER_DOCUMENT]:
        def cell(name: str) -> str:
            i = index.get(name)
            return row[i] if i is not None and i < len(row) else ""

        current = line_no
        line_no += 1
        tx_date = parse_filing_date(cell("transaction date"))
        if tx_date is None:
            errors.append((f"{doc_id}:{current}", "bad_tx_date"))
            continue
        tx_type = parse_tx_type(cell("type"))
        if tx_type is None:
            errors.append((f"{doc_id}:{current}", "unknown_tx_type"))
            continue
        amount = parse_amount(cell("amount"))
        if amount is None:
            errors.append((f"{doc_id}:{current}", "unknown_amount"))
            continue
        description = _collapse(f"{cell('asset name')} {cell('comment')}")
        if not description:
            errors.append((f"{doc_id}:{current}", "empty_description"))
            continue
        disclosure, reason = build_disclosure(
            chamber=CHAMBER_SENATE, doc_id=doc_id, line_no=current,
            filer_display=filer_display, filer_slug=filer_slug, tx_date=tx_date,
            filed_date=filed_date, tx_type=tx_type, amount=amount, description=description,
            explicit_symbol=cell("ticker"), type_hint=cell("asset type"), raw_ref=raw_ref,
            doc_hash=doc_hash, raw_row=" | ".join(row),
        )
        if disclosure is None:
            errors.append((f"{doc_id}:{current}", reason or "unparsed_row"))
            continue
        disclosures.append(disclosure)
    return ParsedDocument(disclosures=tuple(disclosures), row_errors=tuple(errors),
                          lines_seen=len(parser.rows), candidate_rows=line_no,
                          ignored_lines=max(0, len(parser.rows) - line_no))


__all__ = [
    "CHAMBER_HOUSE",
    "FILING_TYPE_PTR",
    "REASON_AMBIGUOUS_COLUMNS",
    "REASON_AMBIGUOUS_OPTION_EXPIRY",
    "STOCK_ACT_TIERS",
    "TX_CODES",
    "HouseIndexEntry",
    "ParsedDocument",
    "ParsedIndex",
    "classify_asset",
    "document_hash",
    "extract_option_terms",
    "extract_symbol",
    "option_expiry_is_ambiguous",
    "parse_amount",
    "parse_filing_date",
    "parse_house_index",
    "parse_ptr_text",
    "parse_senate_ptr_html",
    "parse_tx_type",
    "row_content_hash",
    "validate_doc_id",
    "validate_ticker",
]
