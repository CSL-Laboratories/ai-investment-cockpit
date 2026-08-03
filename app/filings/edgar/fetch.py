"""WP3 — ``fetch.py``: the ONLY module allowed to touch sec.gov (Invariant 4).

Everything that reaches the SEC network flows through :class:`SecFetcher`. It is the single
chokepoint so the fair-access invariant can be enforced (and grep-tested) in one place:

* **Declared User-Agent on EVERY request.** The UA comes from the single settings field
  (``settings.sec_user_agent``). A request built without a User-Agent RAISES before any
  network call — a UA-less request is a bug, never a silent anonymous fetch.
* **Token-bucket rate limiter, strictly < 10 req/s.** The SEC ceiling is 10 req/s; the
  configured rate (default ~2) is validated below it, and the bucket is additionally
  clamped so it can NEVER exceed the ceiling even if mis-configured. The token bucket is
  ported from the cockpit ``ingest/ratelimit.py`` (re-implemented, not copied): pure,
  injectable ``now``, **starts empty** (no birth burst).
* **Distinct error surfacing — never coerced into an empty feed.** A throttled or
  unreachable EDGAR must be distinguishable from a quiet filing day:
    - ``403``/``429`` -> :class:`SecFairAccessHalt` — HALTS the run and alerts, with
      **zero immediate retries** (a hot retry after a fair-access block is exactly what
      loses the free data source).
    - a transport failure (timeout / connection error) -> bounded exponential backoff
      **under the token bucket**, then :class:`SecTransportError` — a raised error,
      distinguishable from a normal empty result.
    - other ``4xx`` -> :class:`SecHttpError` (client error, distinct, no retry);
      ``5xx`` -> transient, bounded backoff, then :class:`SecHttpError` if exhausted.

The HTTP layer is INJECTABLE (:class:`Transport` protocol) so tests replay recorded
fixtures with zero live sec.gov calls; :class:`UrllibTransport` is the production default
and is never exercised by the test suite.

Every sec.gov URL string in the whole app lives here too (the URL builders below), so the
Invariant-4 grep — "no module outside ``edgar/fetch.py`` names sec.gov" — is exact.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("filings.edgar.fetch")

# SEC fair-access hard ceiling (requests/second). The configured rate MUST stay strictly
# below this, and the bucket is clamped so it can never exceed it.
# https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
SEC_RATE_HARD_CEILING: float = 10.0
DEFAULT_MAX_RESPONSE_BYTES: int = 32 * 1024 * 1024
DEFAULT_MAX_RUN_BYTES: int = 128 * 1024 * 1024

# HTTP statuses that mean "you are being throttled / blocked for fair-access reasons".
# These HALT the run with an alert and are NEVER hot-retried.
_FAIR_ACCESS_STATUSES = frozenset({403, 429})


# ---------------------------------------------------------------------------------------
# SEC URL builders — the ONLY place a sec.gov host string appears in the whole app.
# cik_map / submissions_poll / companyfacts import these; they never spell a host.
# ---------------------------------------------------------------------------------------
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def pad_cik(cik: str | int) -> str:
    """Zero-pad a CIK to the canonical 10 digits (e.g. ``320193`` -> ``"0000320193"``)."""
    digits = str(cik).strip().lstrip("CIK").lstrip("cik").strip()
    if not digits.isdigit():
        raise ValueError(f"CIK must be numeric, got {cik!r}")
    return digits.zfill(10)


def submissions_url(cik: str | int) -> str:
    """data.sec.gov submissions API for one CIK."""
    return f"https://data.sec.gov/submissions/CIK{pad_cik(cik)}.json"


def companyfacts_url(cik: str | int) -> str:
    """data.sec.gov companyfacts (XBRL) API for one CIK."""
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{pad_cik(cik)}.json"


# The one canonical EDGAR archive base — the sole home of this sec.gov literal (Invariant 4:
# only this module holds a requestable sec.gov URL). Both the builder and the validator below
# derive from it, so a caller elsewhere never re-literalizes it.
_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/"


def archive_document_url(cik: str | int, accession: str, filename: str) -> str:
    """Raw EDGAR Archives path for a filing's primary document (never the /ix? viewer).

    Pattern (fixture MANIFEST): ``/Archives/edgar/data/<cik-int>/<accession-no-dashes>/<file>``.
    The CIK segment is the integer form (no leading zeros); the accession segment strips
    the dashes.
    """
    cik_int = str(int(pad_cik(cik)))
    acc_nodash = accession.replace("-", "")
    return f"{_ARCHIVE_BASE}{cik_int}/{acc_nodash}/{filename}"


def is_archive_document_url(
    url: object, accession: object, *, cik: object = None, filename: object = None
) -> bool:
    """True IFF ``url`` is the canonical EDGAR archive URL for this exact filing.

    The companion validator to :func:`archive_document_url`, living here so the sec.gov URL
    knowledge stays in the single fair-access module (Invariant 4). It is SCHEME+HOST-anchored
    (``startswith`` the canonical base — NOT a substring search anywhere, which a phishing host
    embedding ``/Archives/edgar/data/…`` would satisfy). The CIK segment must be all-digits and,
    when the source ``cik`` is given, equal it numerically (rejecting a wrong-CIK URL for the
    right accession); the dash-stripped accession must be the next path segment; the filename
    must be non-empty and, when the source ``filename`` (``Filing.primary_doc``) is given, match
    exactly.
    """
    if not isinstance(url, str) or not isinstance(accession, str):
        return False
    if not url.startswith(_ARCHIVE_BASE):
        return False
    parts = url[len(_ARCHIVE_BASE):].split("/")
    if len(parts) < 3:
        return False
    cik_seg, acc_seg = parts[0], parts[1]
    file_seg = "/".join(parts[2:])  # lenient on the filename, strict on prefix/cik/accession
    if not cik_seg.isdigit():
        return False
    if cik is not None:
        try:
            if int(cik_seg) != int(pad_cik(cik)):
                return False
        except (TypeError, ValueError):
            return False
    if acc_seg != accession.replace("-", ""):
        return False
    if not file_seg:
        return False
    if filename is not None and file_seg != str(filename):
        return False
    return True


# ---------------------------------------------------------------------------------------
# Error taxonomy — each surfaced DISTINCTLY (never as an empty feed).
# ---------------------------------------------------------------------------------------
class SecFetchError(RuntimeError):
    """Base class for every error raised by the SEC fetcher."""


class SecTransportError(SecFetchError):
    """A network-level failure (timeout, connection refused, DNS) after bounded retries.

    Distinguishable from a zero-filing day: this is a raised error, not an empty result.
    """

    def __init__(self, url: str, cause: BaseException, attempts: int):
        super().__init__(f"SEC transport failure after {attempts} attempt(s): {url} ({cause!r})")
        self.url = url
        self.cause = cause
        self.attempts = attempts


class SecHttpError(SecFetchError):
    """A non-2xx HTTP response that is NOT a fair-access block (4xx client error, or a
    5xx that survived bounded retries). Carries the status for distinct handling."""

    def __init__(self, url: str, status: int):
        super().__init__(f"SEC HTTP {status}: {url}")
        self.url = url
        self.status = status


class SecFairAccessHalt(SecFetchError):
    """A 403/429 fair-access block. HALTS the run — the caller must NOT hot-retry; a run
    that trips this alerts and stops so the host is not blocked from the free data source."""

    def __init__(self, url: str, status: int):
        super().__init__(
            f"SEC fair-access HALT (HTTP {status}) on {url} — run stopped, no retry"
        )
        self.url = url
        self.status = status


class SecResourceLimitHalt(SecFetchError):
    """Base class for a finite ingest-budget breach.

    Resource-limit failures halt the run and are never retried.  Treating one as a
    per-item skip would allow the remaining watchlist to continue expanding the blast
    radius after the configured boundary was crossed.
    """


class SecResponseTooLarge(SecResourceLimitHalt):
    """One SEC response exceeded the configured byte ceiling."""

    def __init__(self, url: str, observed_bytes: int, limit_bytes: int):
        super().__init__(
            f"SEC response exceeded {limit_bytes} byte limit: {url} "
            f"({observed_bytes} bytes observed)"
        )
        self.url = url
        self.observed_bytes = observed_bytes
        self.limit_bytes = limit_bytes


class SecRunByteBudgetExceeded(SecResourceLimitHalt):
    """Aggregate SEC bytes for this fetcher/run exceeded its finite budget."""

    def __init__(self, url: str, observed_bytes: int, limit_bytes: int):
        super().__init__(
            f"SEC run byte budget exceeded {limit_bytes} bytes on {url} "
            f"({observed_bytes} aggregate bytes observed)"
        )
        self.url = url
        self.observed_bytes = observed_bytes
        self.limit_bytes = limit_bytes


# ---------------------------------------------------------------------------------------
# Transport seam — injectable so tests replay fixtures with zero live calls.
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HttpResponse:
    """A minimal HTTP response. ``body`` is the raw bytes (byte-identical for the raw store)."""

    status: int
    body: bytes
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    """A callable that performs one HTTP request and returns an :class:`HttpResponse`.

    It MUST return a response for any HTTP status (including 4xx/5xx) and RAISE only on a
    transport-level failure (timeout, connection error) — the fetcher maps statuses and
    transport failures to the distinct error taxonomy above.
    """

    def __call__(self, method: str, url: str, headers: Mapping[str, str]) -> HttpResponse: ...


class UrllibTransport:
    """Production transport over the stdlib ``urllib`` (never used in the test suite).

    An HTTP error status (4xx/5xx) is returned as an :class:`HttpResponse` (urllib's
    ``HTTPError`` is caught and unwrapped); a genuine transport failure (``URLError``,
    ``socket.timeout``) propagates for the fetcher to classify as a transport error.
    """

    def __init__(
        self,
        timeout: float = 20.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ):
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes

    def _read_bounded(self, stream, url: str, status: int) -> bytes:
        """Read at most the configured body size plus one sentinel byte.

        Error response bodies are not application data, so they may be truncated while
        preserving their HTTP status for the fetcher's 403/429/5xx classification.  A 2xx
        response must be complete and therefore fails closed when oversized.
        """
        body = stream.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            if 200 <= status < 300:
                raise SecResponseTooLarge(url, len(body), self._max_response_bytes)
            return body[: self._max_response_bytes]
        return body

    def __call__(self, method: str, url: str, headers: Mapping[str, str]) -> HttpResponse:
        # urllib carries Request headers across its automatic redirect handling, so the
        # declared User-Agent rides on a followed redirect too (the fair-access header must
        # never be dropped mid-redirect). The SEC submissions/companyfacts/Archives paths
        # used here are served directly; a redirect would still keep the UA.
        req = urllib.request.Request(url, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 (https only, via builders)
                return HttpResponse(
                    status=resp.status,
                    body=self._read_bounded(resp, url, resp.status),
                    url=url,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            # A real HTTP status response — NOT a transport failure. Return it so the
            # fetcher can classify 403/429 vs other 4xx/5xx.
            return HttpResponse(
                status=exc.code,
                body=(
                    self._read_bounded(exc, url, exc.code)
                    if hasattr(exc, "read")
                    else b""
                ),
                url=url,
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            )


# ---------------------------------------------------------------------------------------
# Token bucket (ported from cockpit ingest/ratelimit.py — pure, injectable now, empty start)
# ---------------------------------------------------------------------------------------
class TokenBucket:
    """A pure token bucket. Starts EMPTY (no birth burst) so the count in any window can
    never reach ``capacity + rate*T``. ``allow(now)`` refills then consumes one token."""

    def __init__(self, rate_per_sec: float, capacity: float = 1.0, now: float = 0.0):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = 0.0
        self._last = now

    def allow(self, now: float) -> bool:
        elapsed = max(0.0, now - self._last)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Blocks (via an injectable ``sleep``) until the token bucket grants a request.

    Capacity is 1.0 (cockpit value): after any idle the burst is a single request and the
    sustained rate equals the configured rate — so the count in any 1-second window stays
    at the configured rate, well under the SEC ceiling. The rate is validated strictly
    below ``ceiling`` at construction so the bucket can NEVER exceed it.
    """

    def __init__(
        self,
        rate_per_sec: float,
        *,
        ceiling: float = SEC_RATE_HARD_CEILING,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate_per_sec <= 0:
            raise ValueError(f"rate_per_sec must be > 0, got {rate_per_sec}")
        if rate_per_sec >= ceiling:
            raise ValueError(
                f"rate_per_sec={rate_per_sec} must be strictly < the SEC ceiling {ceiling}"
            )
        self._rate = rate_per_sec
        self._monotonic = monotonic
        self._sleep = sleep
        self._bucket = TokenBucket(rate_per_sec=rate_per_sec, capacity=1.0, now=monotonic())

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        # Bounded spin: each miss sleeps exactly one token's worth of time (1/rate), so the
        # next refill grants. Under an injected virtual clock this is deterministic.
        while not self._bucket.allow(self._monotonic()):
            self._sleep(1.0 / self._rate)


# ---------------------------------------------------------------------------------------
# The single SEC client.
# ---------------------------------------------------------------------------------------
class SecFetcher:
    """The one sec.gov client. Rate-limited, UA-enforcing, distinct-error-surfacing.

    :param user_agent: the declared User-Agent (``settings.sec_user_agent``). A blank UA
        makes every ``get`` RAISE before any network call.
    :param rate_per_sec: token-bucket rate; validated strictly < the SEC ceiling.
    :param transport: the injectable HTTP seam (fixtures in tests; urllib in prod).
    :param alert: called once with ``(url, status)`` when a 403/429 halts the run.
    """

    def __init__(
        self,
        user_agent: str,
        rate_per_sec: float = 2.0,
        transport: Transport | None = None,
        *,
        ceiling: float = SEC_RATE_HARD_CEILING,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        alert: Callable[[str, int], None] | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_RUN_BYTES,
    ):
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if max_total_bytes < max_response_bytes:
            raise ValueError("max_total_bytes must be >= max_response_bytes")
        self._user_agent = user_agent
        self._transport: Transport = transport or UrllibTransport(
            max_response_bytes=max_response_bytes
        )
        self._limiter = RateLimiter(
            rate_per_sec, ceiling=ceiling, monotonic=monotonic, sleep=sleep
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._sleep = sleep
        self._max_response_bytes = max_response_bytes
        self._max_total_bytes = max_total_bytes
        self._bytes_received = 0
        self._resource_halt: SecResourceLimitHalt | None = None
        self._budget_lock = threading.Lock()
        self._alert = alert or (lambda url, status: log.critical(
            "SEC fair-access HALT: HTTP %s on %s — stopping run, NO retry", status, url
        ))

    @classmethod
    def from_settings(cls, settings, transport: Transport | None = None, **kwargs) -> SecFetcher:
        """Build from ``config.settings`` — reads the single UA + rate ceiling fields."""
        kwargs.setdefault("max_response_bytes", settings.sec_max_response_bytes)
        kwargs.setdefault("max_total_bytes", settings.sec_max_run_bytes)
        return cls(
            user_agent=settings.sec_user_agent,
            rate_per_sec=settings.sec_rate_limit_per_sec,
            transport=transport,
            **kwargs,
        )

    @property
    def bytes_received(self) -> int:
        """Bytes accounted to this run, exposed for metrics and deterministic tests."""
        with self._budget_lock:
            return self._bytes_received

    def _account_response(self, url: str, response: HttpResponse) -> None:
        body_bytes = len(response.body)
        with self._budget_lock:
            aggregate = self._bytes_received + body_bytes
            self._bytes_received = aggregate
            if body_bytes > self._max_response_bytes:
                halt: SecResourceLimitHalt = SecResponseTooLarge(
                    url, body_bytes, self._max_response_bytes
                )
                self._resource_halt = halt
                raise halt
            if aggregate > self._max_total_bytes:
                halt = SecRunByteBudgetExceeded(url, aggregate, self._max_total_bytes)
                self._resource_halt = halt
                raise halt

    def _headers(self) -> dict[str, str]:
        # The declared User-Agent is MANDATORY on every request. A blank UA is a bug — a
        # UA-less request must never reach the network.
        if not self._user_agent or not self._user_agent.strip():
            raise ValueError(
                "refusing to build a sec.gov request without a declared User-Agent "
                "(settings.sec_user_agent) — SEC fair access requires it (Invariant 4)"
            )
        return {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "identity",  # raw bytes for the write-once raw store
        }

    def _backoff(self, attempt: int) -> None:
        # Bounded exponential backoff (under the token bucket, which the next acquire() re-gates).
        delay = min(self._backoff_cap, self._backoff_base * (2**attempt))
        self._sleep(delay)

    def get(self, url: str) -> HttpResponse:
        """Fetch ``url`` through the single chokepoint. Raises one of the distinct errors
        above on any non-success; returns the :class:`HttpResponse` on 2xx."""
        with self._budget_lock:
            prior_halt = self._resource_halt
        if prior_halt is not None:
            raise prior_halt
        headers = self._headers()  # raises on a UA-less request, before any network call
        attempt = 0
        while True:
            self._limiter.acquire()  # every attempt is gated by the token bucket
            try:
                resp = self._transport("GET", url, headers)
            except SecResourceLimitHalt as exc:
                # The production transport may reject while streaming, before it has
                # materialized an HttpResponse. Latch that halt and never retry it.
                with self._budget_lock:
                    self._resource_halt = exc
                raise
            except Exception as exc:  # transport-level failure (timeout / connection)
                if attempt >= self._max_retries:
                    raise SecTransportError(url, exc, attempt + 1) from exc
                self._backoff(attempt)
                attempt += 1
                continue

            # Account for every body, including HTTP errors and retry responses. Retrying
            # cannot evade the finite aggregate budget.
            self._account_response(url, resp)
            status = resp.status
            if status in _FAIR_ACCESS_STATUSES:
                # HALT — alert once, and NEVER hot-retry.
                self._alert(url, status)
                raise SecFairAccessHalt(url, status)
            if 500 <= status < 600:
                if attempt >= self._max_retries:
                    raise SecHttpError(url, status)  # transient exhausted -> surface distinctly
                self._backoff(attempt)
                attempt += 1
                continue
            if 400 <= status < 500:
                raise SecHttpError(url, status)  # client error, distinct, no retry
            if 200 <= status < 300:
                return resp
            raise SecHttpError(url, status)  # unexpected status -> still distinct, not empty

    def get_bytes(self, url: str) -> bytes:
        """Raw response bytes (for storing a filing document byte-identically)."""
        return self.get(url).body

    def get_json(self, url: str) -> Any:
        """Parsed JSON body (submissions / companyfacts / company_tickers)."""
        return json.loads(self.get(url).body)
