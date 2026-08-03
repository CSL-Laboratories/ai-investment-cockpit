"""WP3 test support — offline SEC replay (NEVER a live sec.gov call).

Shared by the WP3 test modules. Nothing here is collected by pytest (leading underscore,
no ``test_`` prefix). It provides:

* :class:`VirtualTime` — an injectable monotonic/sleep pair so the token-bucket rate
  limiter and the fetcher's backoff run deterministically and instantly (no wall-clock
  sleeping in the suite).
* :class:`FixtureTransport` — an injectable :class:`filings.edgar.fetch.Transport` that maps the
  EXACT SEC URLs (built by ``fetch.py``'s own URL builders) to the recorded fixture bytes
  under ``app/tests/fixtures/sec/``. Unknown URLs return HTTP 404. Every request is
  recorded so tests can assert the declared User-Agent rode on it.
* small purpose-built transports for error injection (403/429/500/timeout).
"""
from __future__ import annotations

from pathlib import Path

from filings.edgar.fetch import (
    HttpResponse,
    archive_document_url,
    companyfacts_url,
    submissions_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sec"

# The two captured registrants (fixture dir -> CIK). Matches MANIFEST.md.
FIXTURE_CIKS = {
    "NVDA": "1045810",
    "AAPL": "320193",
}


class VirtualTime:
    """A deterministic monotonic clock whose ``sleep`` advances it (no real waiting)."""

    def __init__(self, start: float = 0.0):
        self.t = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.t += dt


def build_registry() -> dict[str, tuple[int, bytes]]:
    """Map every real SEC URL we can serve from the fixtures -> (status, body bytes)."""
    reg: dict[str, tuple[int, bytes]] = {}
    for ticker, cik in FIXTURE_CIKS.items():
        d = FIXTURES / ticker
        reg[submissions_url(cik)] = (200, (d / "submissions.json").read_bytes())
        reg[companyfacts_url(cik)] = (200, (d / "companyfacts.json").read_bytes())
        for accdir in sorted(d.iterdir()):
            if accdir.is_dir():
                for doc in sorted(accdir.iterdir()):
                    url = archive_document_url(cik, accdir.name, doc.name)
                    reg[url] = (200, doc.read_bytes())
    return reg


class FixtureTransport:
    """Replays recorded fixtures for known SEC URLs; 404 for anything else.

    Records every ``(method, url, headers)`` so a test can assert the User-Agent header.
    ``timestamps`` (when a ``time`` source is given) records the monotonic time of each call
    for the rate-limit-under-load assertion.
    """

    def __init__(self, registry: dict[str, tuple[int, bytes]] | None = None, time=None):
        self.registry = registry if registry is not None else build_registry()
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.timestamps: list[float] = []
        self._time = time

    def __call__(self, method: str, url: str, headers) -> HttpResponse:
        self.requests.append((method, url, dict(headers)))
        if self._time is not None:
            self.timestamps.append(self._time.monotonic())
        status, body = self.registry.get(url, (404, b""))
        return HttpResponse(status=status, body=body, url=url)


class StatusTransport:
    """Always returns a fixed HTTP status with an empty body (403/429/404/500 injection)."""

    def __init__(self, status: int, body: bytes = b"", time=None):
        self.status = status
        self.body = body
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self._time = time

    def __call__(self, method: str, url: str, headers) -> HttpResponse:
        self.requests.append((method, url, dict(headers)))
        return HttpResponse(status=self.status, body=self.body, url=url)


class RaisingTransport:
    """Always raises a transport-level failure (timeout / connection error)."""

    def __init__(self, exc: BaseException | None = None):
        self.exc = exc or TimeoutError("simulated SEC read timeout")
        self.calls = 0

    def __call__(self, method: str, url: str, headers) -> HttpResponse:
        self.calls += 1
        raise self.exc


class SequenceTransport:
    """Returns/raises each queued item in turn (list of HttpResponse or Exception)."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def __call__(self, method: str, url: str, headers) -> HttpResponse:
        item = self.items[self.calls]
        self.calls += 1
        if isinstance(item, BaseException):
            raise item
        return item
