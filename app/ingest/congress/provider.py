"""RW-16 — the disclosure-provider seam: one shape for two very different filing systems.

WHY A PROVIDER PROTOCOL AND NOT TWO POLLERS
--------------------------------------------
The House and the Senate publish the same legal artifact (a STOCK Act periodic transaction
report) through two systems that share nothing: the House Clerk publishes a per-year ZIP
holding an XML INDEX of every filing plus one PDF per report, while the Senate's eFD makes
you accept a terms page for a session cookie and a CSRF token before it will answer a JSON
search POST. Folding both into one poller would mean one fetch loop with two mutually
exclusive halves, and — far worse — one fault domain: an eFD terms-page change would take the
House feed down with it. So each chamber is a PROVIDER with its own fetch, its own errors and
its own UNPROVISIONED reason, and :class:`~ingest.congress.poller.CongressPoller` isolates
them from each other exactly the way ``ingest/edgar_pipeline.py::_poll_isolated`` isolates one
issuer's fault from the rest of the fan-out.

NOT-PROVISIONED IS A FIRST-CLASS STATE, NOT AN EXCEPTION
---------------------------------------------------------
A provider with no declared User-Agent, or with no transport injected, returns
``ProviderResult(provisioned=False, unprovisioned_reason=...)``. It NEVER raises. This is the
cockpit's per-feed rule (``ingest/edgar_pipeline.build_sec_fetcher``): an unprovisioned feed
must not take the order path, the risk engine and the operator surface down with it. It is
also why ``provisioned`` is a separate field from "we got zero rows": a feed that is switched
off and a feed that saw a quiet week must never be the same observation.

THE USER-AGENT IS A REQUIREMENT OF THE COUNTERPARTY, NOT A NICETY
------------------------------------------------------------------
Both chambers publish these documents free and without an API key, and both are small
government sites. The cockpit's obligation is therefore the same one it accepts for SEC fair
access: identify yourself, and stay slow. ``settings.congress_user_agent`` is declared exactly
like ``sec_user_agent`` (no default, non-secret, unset => the feed is NOT PROVISIONED), and
every request goes through a :class:`RequestThrottle` wrapping the shared
``ingest.ratelimit.TokenBucket``. A provider that cannot throttle does not fetch.

ALL NETWORK I/O IS INJECTED
----------------------------
Providers never import an HTTP client. They call a ``transport`` callable with the signature

    transport(method, url, *, headers=None, data=None, timeout=None) -> TransportResponse

so the entire package is driveable from fixtures and no test can reach the internet. The
production transport is expected to be SESSION-SCOPED (it must carry cookies between calls):
the Senate handshake sets a session cookie on the terms page and every later request depends
on it. A stateless transport does not crash the Senate provider — the handshake simply fails
and is COUNTED, like any other fetch fault.

THIS FEED NEEDS BYTES, NOT JSON — WHY ``http_get`` IS ACCEPTED BUT NOT ASSUMED
------------------------------------------------------------------------------
The composition root offers every ingest factory a shared ``http_get``, and ``main
._urllib_json_get`` decodes the response body as JSON before returning it (it exposes only
``.json()``). That is exactly right for the FINRA/Finnhub feeds and exactly WRONG for this
one: the House index is a ZIP, a PTR is a PDF, and an eFD report is HTML. There is no reading
of a JSON decoder that yields those bytes.

So ``http_get`` is ACCEPTED by both providers — dropping the composition root's offer on the
floor would leave the feed permanently transport-less for a reason nobody could see — and it
is ADAPTED by :func:`transport_from_http_get`, which requires the returned object to expose a
BYTE BODY. A getter that exposes only ``.json()`` yields a response this module classifies as
``transport_unusable``: a NAMED, COUNTED, fail-closed refusal. It is never coerced into
"whatever ``json()`` returned, re-encoded", because a document reconstructed from a decode
that already threw the original bytes away is not the document, and every ``content_hash``
computed from it would be a lie told to the audit chain.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from copytrade.models import Disclosure
from ingest.ratelimit import TokenBucket

# Politeness floor, requests/second, SELF-IMPOSED. Neither chamber publishes a documented rate
# cap that could be cited, so this is a floor we choose rather than a ceiling we are trusted to
# respect — the same honesty ``ingest/finra_short_interest.py`` applies to api.finra.org. Two
# seconds between requests against a government file server is slow enough to be uncontroversial
# and fast enough to drain a day's PTRs in one cycle.
DEFAULT_RATE_PER_SEC = 0.5

# One cycle may not download an unbounded number of documents. This is the analogue of
# ``sec_max_new_filings_per_poll``: it bounds what ONE surprise (a chamber dumping a backlog, a
# year-boundary index) can cost in bandwidth, time and counterparty goodwill. Exceeding it is
# NOTED, never silent, and the remainder is picked up next cycle because the cursor cannot pass
# a document that was never read.
DEFAULT_MAX_DOCUMENTS_PER_CYCLE = 25

# Untrusted-input bounds. Both apply to bytes we did not author and cannot predict.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class TransportResponse:
    """What an injected transport hands back. Deliberately tiny and library-agnostic.

    ``headers`` is present because the Senate handshake reads ``Set-Cookie`` semantics through
    it in some transports; providers here never parse cookies themselves (the transport owns
    the session), but a transport that wants to expose them has somewhere to put them.
    """

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= int(self.status) < 300

    def text(self, *, limit: int = MAX_RESPONSE_BYTES) -> str:
        """Decode the body as UTF-8, REPLACING undecodable bytes and truncating to ``limit``.

        ``errors="replace"`` rather than ``strict``: a scanned filing's text layer, or a page
        served in an unannounced encoding, is malformed INPUT, not a reason to raise inside a
        feed. The replacement characters flow into the parsers, which refuse rows they cannot
        read confidently and COUNT them — a counted parse error is a far better outcome than a
        UnicodeDecodeError killing the whole cycle.
        """
        return self.body[:limit].decode("utf-8", errors="replace")


class Transport(Protocol):
    """The injected network seam. Production wires a real, session-scoped HTTP client."""

    def __call__(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                 data: Mapping[str, str] | None = None,
                 timeout: float | None = None) -> TransportResponse: ...


@dataclass
class ProviderResult:
    """One provider's cycle. Every non-emitting outcome is COUNTED and NAMED.

    ``documents_seen`` is the number of FILINGS the provider looked at, not the number of
    transactions it produced: a PTR with twelve transactions is one document. Keeping them
    apart is what lets the poller distinguish "a quiet week in Congress" (documents_seen == 0
    on a healthy fetch) from "we read forty filings and could not parse a single row"
    (documents_seen == 40, disclosures == []). Those two look identical from the outside and
    must never look identical from the inside — the same property ``EdgarPipelineResult``'s
    counters exist to preserve.

    ``parse_errors`` are ``(locator, reason)`` pairs. The locator names the document (and,
    where known, the row within it) so an operator can go and read the primary source; the
    reason is a short snake_case code, classified by :data:`STRUCTURAL_PARSE_REASONS` into
    "deterministic in the document/config" vs "might succeed on a retry" — a distinction the
    poller's cursor depends on.
    """

    provisioned: bool = True
    disclosures: list[Disclosure] = field(default_factory=list)
    documents_seen: int = 0
    parse_errors: list[tuple[str, str]] = field(default_factory=list)
    unprovisioned_reason: str | None = None
    # ADDITIVE, defaulted, and NOT in the pinned contract: cycle-level facts that are neither a
    # parse error nor an unprovisioned reason — "the per-cycle document cap truncated this
    # window", "the throttle ran out of budget". They must not inflate ``parse_errors`` (that
    # would make a healthy-but-capped cycle look like a broken one), and they must not vanish
    # (a truncated cycle that reports nothing is the silence this package is built to refuse).
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.provisioned and not self.parse_errors

    def summary(self) -> str:
        if not self.provisioned:
            return f"NOT PROVISIONED ({self.unprovisioned_reason})"
        return (f"{self.documents_seen} document(s), {len(self.disclosures)} disclosure(s), "
                f"{len(self.parse_errors)} parse error(s)"
                + (f", {len(self.notes)} note(s)" if self.notes else ""))


@runtime_checkable
class DisclosureProvider(Protocol):
    """One chamber's disclosure feed.

    ``fetch`` takes the window it should cover and returns what it found. It NEVER raises for
    an unprovisioned feed, a dead counterparty or an unreadable document — those are the three
    states the result type exists to express. It may raise on a programming error, and the
    poller isolates that per provider so one chamber's bug cannot silence the other's.
    """

    name: str

    def fetch(self, *, since: date, today: date) -> ProviderResult: ...


# ---------------------------------------------------------------- parse-error classification

# A parse error is STRUCTURAL when it is deterministic in the document or in the configuration:
# retrying it produces the identical failure forever. It is RETRIABLE otherwise (a transport
# fault, a 5xx, a truncated download).
#
# THE DISTINCTION IS THE CURSOR'S, AND IT IS THE ONE ``ingest/edgar_brew.py`` SPENT SIX ROUNDS
# ON. A retriable error is a BARRIER: the cursor may not advance past a window that holds one,
# because the evidence is good and will arrive on the next cycle. A structural error is
# TERMINAL: holding the cursor for it would wedge the feed forever on a document that can never
# be read, and a permanent wedge is how a feed dies quietly.
#
# THE DEFAULT IS RETRIABLE — i.e. HOLD. An unknown reason code must fail CLOSED toward keeping
# evidence re-fetchable, because holding the cursor costs one re-fetch while advancing past
# unread evidence loses it forever.
STRUCTURAL_PARSE_REASONS = frozenset({
    "pdf_extractor_unavailable",   # no text extractor wired — deterministic in CONFIG
    "paper_ptr_unsupported",       # a scanned image; no text layer exists to extract
    "unparsed_row",                # the row does not match any known PTR grammar
    "unknown_tx_type",             # a transaction code outside KNOWN_TX_TYPES
    "unknown_amount",              # an amount outside the STOCK Act tiers
    "bad_tx_date",
    "bad_filed_date",
    "tx_after_filed",              # disclosed before it happened: the document is wrong
    "empty_description",
    "senate_table_headers_missing",
    "bad_doc_id",
    "index_entry_invalid",
})


def is_retriable_reason(reason: str) -> bool:
    """True when a later cycle might succeed where this one failed (see above)."""
    return str(reason).split(":", 1)[0] not in STRUCTURAL_PARSE_REASONS


# ------------------------------------------------------------------------------- throttling

class RequestThrottle:
    """A TokenBucket plus a BOUNDED wait, with both clocks injected.

    ``TokenBucket`` answers "may I go now?" and starts EMPTY (no birth burst), so the first
    request of a cycle waits ``1/rate`` seconds by construction. Something has to do that
    waiting, and it cannot be the poller: a "rate-denied cycle returns 0" pattern would make a
    throttled feed masquerade as a quiet one, which ``ingest/edgar_brew.py`` names as exactly
    the anti-pattern. So the throttle SLEEPS — through an injected ``sleep`` and an injected
    ``monotonic``, so the whole package still runs offline and instantly under test.

    The wait is BOUNDED. After ``max_waits`` sleeps ``acquire`` returns False and the caller
    stops fetching for this cycle and NOTES it: a bounded short cycle whose remainder is picked
    up next time is always better than an unbounded block inside a scheduled job.
    """

    def __init__(self, bucket: TokenBucket | None = None, *, rate_per_sec: float | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 max_waits: int = 10, wait_seconds: float | None = None):
        rate = float(rate_per_sec) if rate_per_sec else DEFAULT_RATE_PER_SEC
        if rate <= 0:
            raise ValueError("rate_per_sec must be positive")
        self._bucket = bucket if bucket is not None else TokenBucket(rate_per_sec=rate,
                                                                     capacity=1.0)
        self._monotonic = monotonic
        self._sleep = sleep
        self._max_waits = max(0, int(max_waits))
        self._wait = float(wait_seconds) if wait_seconds else min(5.0, max(0.05, 1.0 / rate))
        self.waits = 0
        self.denials = 0

    def acquire(self) -> bool:
        """Block (bounded) until one request is permitted. False => give up this cycle."""
        for _ in range(self._max_waits + 1):
            if self._bucket.allow(self._monotonic()):
                return True
            self.waits += 1
            self._sleep(self._wait)
        self.denials += 1
        return False


# ------------------------------------------------------------------------ provisioning gates

def resolve_user_agent(settings: Any) -> str | None:
    """``settings.congress_user_agent`` if it is a usable declaration, else None.

    Mirrors ``build_sec_fetcher``'s posture exactly: no UA is not an exception, it is an
    UNPROVISIONED feed. The convention (SEC's published sample, which both chambers' operators
    are used to seeing) is "<App name> <contact email>" — we send whatever the operator
    declares and invent nothing.
    """
    ua = (getattr(settings, "congress_user_agent", None) or "")
    ua = ua.strip() if isinstance(ua, str) else ""
    return ua or None


UNPROVISIONED_NO_UA = (
    "set COCKPIT_CONGRESS_USER_AGENT to a descriptive '<App name> <contact email>' string "
    "(both chambers publish these filings free and without an API key, but an anonymous "
    "scraper is not something this cockpit will be)"
)
UNPROVISIONED_NO_TRANSPORT = (
    "no HTTP transport injected: all network I/O for this feed is injected so tests cannot "
    "reach the internet, and production must pass a real session-scoped transport "
    "(transport=...) or an http_get whose response exposes raw BYTES — this feed downloads "
    "ZIP/PDF/HTML, so a JSON-decoding getter cannot serve it"
)


def unprovisioned(reason: str) -> ProviderResult:
    """The one way a provider reports 'switched off'. Never raises, never looks quiet."""
    return ProviderResult(provisioned=False, unprovisioned_reason=reason)


# --------------------------------------------------------------- adapting an injected getter

# A response object we did not author may spell its status and its body in several ways. These
# are the spellings the stdlib, urllib3/requests and httpx actually use; anything outside them
# is UNKNOWN and is refused rather than guessed at. Order matters only in that `body`/`content`
# are cheap attributes while `read()` is a one-shot consuming call, so it is tried LAST.
_STATUS_ATTRS = ("status", "status_code", "code")
_BODY_ATTRS = ("body", "content", "data")

# The status a synthesised response carries when the transport produced no usable answer. It is
# deliberately NOT an HTTP status: 0 can never be confused with something a server said, and
# ``ok`` is False for it, so every existing "did this succeed?" test refuses it unchanged.
STATUS_TRANSPORT_UNUSABLE = 0


def normalize_response(obj) -> TransportResponse | None:
    """Coerce whatever a transport/getter returned into a :class:`TransportResponse`, or None.

    None means "this object carries no byte body we can read", and the caller turns that into a
    COUNTED ``transport_unusable`` — never into an empty document. The distinction is the whole
    point: an empty body parses to zero rows and reads as a quiet filing, while a refusal reads
    as a broken transport. This feed's counters exist to keep those apart.

    A ``str`` body is accepted and UTF-8 encoded, because an HTML page handed back as text is a
    legitimate shape for a hand-written transport. Bytes are preferred and never re-encoded.
    """
    if obj is None:
        return None
    if isinstance(obj, TransportResponse):
        return obj
    status = None
    for attr in _STATUS_ATTRS:
        value = getattr(obj, attr, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            status = int(value)
            break
    body = None
    for attr in _BODY_ATTRS:
        value = getattr(obj, attr, None)
        if isinstance(value, (bytes, bytearray)):
            body = bytes(value)
            break
        if isinstance(value, str):
            body = value.encode("utf-8", "surrogatepass")
            break
    if body is None:
        reader = getattr(obj, "read", None)
        if callable(reader):
            try:
                raw = reader()
            except Exception:                      # noqa: BLE001 - an unreadable body, not a crash
                return None
            if isinstance(raw, (bytes, bytearray)):
                body = bytes(raw)
            elif isinstance(raw, str):
                body = raw.encode("utf-8", "surrogatepass")
    if body is None:
        # The object exposes no bytes at all — the ``.json()``-only shape the module docstring
        # names. Refuse it; do NOT re-encode a decoded payload back into "the document".
        return None
    headers = getattr(obj, "headers", None)
    return TransportResponse(
        status=status if status is not None else 200,
        body=body[:MAX_RESPONSE_BYTES],
        headers=dict(headers) if isinstance(headers, Mapping) else {},
    )


def transport_from_http_get(http_get) -> Callable[..., TransportResponse] | None:
    """Wrap the composition root's ``http_get`` into this package's transport shape, or None.

    Returns None for a missing getter so the caller reports NOT PROVISIONED with its own
    reason. The wrapper is GET-only by construction: it refuses a POST rather than silently
    issuing a GET, because the Senate handshake's whole purpose is the POST that accepts the
    terms page, and a handshake that quietly degraded to a GET would leave the session
    un-authorised and every later fetch failing for a reason that pointed at the wrong wire.
    """
    if http_get is None:
        return None

    def transport(method: str, url: str, *, headers=None, data=None,
                  timeout: float | None = None) -> TransportResponse:
        if str(method).upper() != "GET":
            # Named, not silent: the caller counts it and the operator sees which capability is
            # missing rather than a mysterious 403 from a site we never accepted the terms of.
            return TransportResponse(status=STATUS_TRANSPORT_UNUSABLE, body=b"")
        response = normalize_response(
            http_get(url, headers=dict(headers or {}), timeout=timeout)
        )
        if response is None:
            return TransportResponse(status=STATUS_TRANSPORT_UNUSABLE, body=b"")
        return response

    return transport


def http_failure_reason(response) -> str:
    """The counted reason code for a non-OK response. ONE spelling, both providers.

    ``transport_unusable`` is kept DISTINCT from an HTTP status because they demand different
    operator actions: a 503 is the counterparty having a bad minute, while
    ``transport_unusable`` means the wire we were handed cannot carry this feed's documents at
    all. Flattening the second into ``http_0`` would send an operator to read house.gov's
    status page about a bug in their own composition root.
    """
    status = getattr(response, "status", None)
    if status == STATUS_TRANSPORT_UNUSABLE:
        return "transport_unusable"
    return f"http_{status if status is not None else 'unknown'}"


def resolve_transport(transport, http_get):
    """The ONE place a provider decides what its network seam is. Explicit beats implicit.

    An explicitly injected ``transport`` always wins — production and every fixture replay pass
    one. ``http_get`` is the composition root's shared getter and is only adapted when no real
    transport was given. Both absent -> None, and the provider reports NOT PROVISIONED at fetch
    time where the reason is counted, rather than at construction where nothing counts it.
    """
    if transport is not None:
        return transport
    return transport_from_http_get(http_get)
