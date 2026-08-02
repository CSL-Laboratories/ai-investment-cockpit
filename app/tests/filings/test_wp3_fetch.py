"""WP3 acceptance tests — ``edgar/fetch.py``: the single sec.gov chokepoint (Invariant 4).

Covers the WP3 ACCEPT lines that live at the fetch layer:

* the token bucket NEVER exceeds the ceiling under load, and the ceiling is strictly < 10;
* every outbound request carries the declared User-Agent (session-header assertion), and a
  UA-less request RAISES before any network call;
* transport errors and 4xx/5xx are surfaced DISTINCTLY — never coerced into an empty feed;
* an injected 403/429 HALTS with a distinct alert and ZERO immediate retries;
* an injected timeout backs off within bounds and surfaces a transport error
  DISTINGUISHABLE from a zero-filing day.

All offline: every fetcher is built over an injected transport, and an autouse guard makes
any accidental live ``urlopen`` fail loudly.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from config.settings import Settings
from filings.edgar.fetch import (
    SEC_RATE_HARD_CEILING,
    HttpResponse,
    RateLimiter,
    SecFairAccessHalt,
    SecFetcher,
    SecHttpError,
    SecResponseTooLarge,
    SecRunByteBudgetExceeded,
    SecTransportError,
    TokenBucket,
    UrllibTransport,
    pad_cik,
    submissions_url,
)
from tests.filings._sec_replay import (
    FixtureTransport,
    RaisingTransport,
    SequenceTransport,
    StatusTransport,
    VirtualTime,
)

NVDA = "0001045810"
UA = "Lucas Salvador lucassalvador987@gmail.com (personal research)"


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    """Hard guard: no test in this module may open a live sec.gov connection."""
    import urllib.request

    def _boom(*a, **k):  # pragma: no cover - only fires on a bug
        raise AssertionError("a live urllib.request.urlopen was attempted in a WP3 test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def make_fetcher(transport, rate=2.0, vt=None, **kw):
    vt = vt or VirtualTime()
    return SecFetcher(
        user_agent=UA,
        rate_per_sec=rate,
        transport=transport,
        monotonic=vt.monotonic,
        sleep=vt.sleep,
        **kw,
    )


# --------------------------------------------------------------------------------------
# ceiling — the rate is validated strictly < 10, and the bucket can never exceed it
# --------------------------------------------------------------------------------------
def test_rate_ceiling_is_strictly_below_ten():
    assert SEC_RATE_HARD_CEILING == 10.0
    with pytest.raises(ValueError):
        RateLimiter(10.0)  # == ceiling is not allowed (strictly below)
    with pytest.raises(ValueError):
        RateLimiter(11.0)
    with pytest.raises(ValueError):
        RateLimiter(0)
    RateLimiter(9.99)  # just under the ceiling is fine


def test_settings_rate_default_is_under_ceiling():
    s = Settings(sec_user_agent=UA)
    assert 0 < s.sec_rate_limit_per_sec < SEC_RATE_HARD_CEILING


def test_token_bucket_starts_empty_no_birth_burst():
    b = TokenBucket(rate_per_sec=2.0, capacity=1.0, now=0.0)
    assert b.allow(0.0) is False  # empty at birth -> first call denied, no burst


def test_bucket_never_exceeds_ceiling_under_load():
    # Drive many requests as fast as possible; assert NO 1-second window (inclusive) ever
    # holds more than the SEC ceiling of grants.
    rate = 2.0
    vt = VirtualTime()
    transport = FixtureTransport(registry={}, time=vt)  # every url -> 404, but records time
    fetcher = make_fetcher(transport, rate=rate, vt=vt)
    for _ in range(40):
        with pytest.raises(SecHttpError):  # 404 (empty registry) — we only care about timing
            fetcher.get("https://data.sec.gov/x")
    ts = transport.timestamps
    assert len(ts) == 40
    worst = max(
        sum(1 for t in ts if t0 <= t <= t0 + 1.0) for t0 in ts
    )
    assert worst <= SEC_RATE_HARD_CEILING  # never exceeds the ceiling (the invariant)
    assert worst <= rate + 1  # and tracks the configured rate (tightness)


# --------------------------------------------------------------------------------------
# User-Agent — on EVERY request; a UA-less request raises before any network call
# --------------------------------------------------------------------------------------
def test_every_request_carries_declared_user_agent():
    transport = FixtureTransport()
    fetcher = make_fetcher(transport)
    fetcher.get_json(submissions_url(NVDA))
    assert transport.requests, "no request was made"
    for _method, _url, headers in transport.requests:
        assert headers.get("User-Agent") == UA
        assert headers["User-Agent"].strip()


def test_ua_less_request_raises_before_network():
    transport = StatusTransport(200)
    fetcher = make_fetcher(transport)
    fetcher._user_agent = ""  # simulate a missing UA
    with pytest.raises(ValueError):
        fetcher.get("https://data.sec.gov/x")
    assert transport.requests == []  # never reached the transport


def test_whitespace_only_ua_raises():
    fetcher = make_fetcher(StatusTransport(200))
    fetcher._user_agent = "   "
    with pytest.raises(ValueError):
        fetcher.get("https://data.sec.gov/x")


def test_from_settings_uses_the_single_ua_field():
    s = Settings(sec_user_agent=UA, sec_rate_limit_per_sec=3.0)
    transport = FixtureTransport()
    vt = VirtualTime()
    fetcher = SecFetcher.from_settings(
        s, transport=transport, monotonic=vt.monotonic, sleep=vt.sleep
    )
    fetcher.get_json(submissions_url(NVDA))
    assert all(h.get("User-Agent") == UA for _m, _u, h in transport.requests)


# --------------------------------------------------------------------------------------
# 403/429 — HALT with a distinct alert and ZERO immediate retries (fair-access)
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("status", [403, 429])
def test_fair_access_halts_with_alert_and_no_retry(status):
    transport = StatusTransport(status)
    alerts: list[tuple[str, int]] = []
    fetcher = make_fetcher(transport, alert=lambda url, st: alerts.append((url, st)))
    with pytest.raises(SecFairAccessHalt) as ei:
        fetcher.get("https://data.sec.gov/submissions/x")
    assert ei.value.status == status
    assert len(transport.requests) == 1  # exactly one call — NO hot retry
    assert alerts == [("https://data.sec.gov/submissions/x", status)]  # alerted once


# --------------------------------------------------------------------------------------
# distinct error surfacing — never an empty feed
# --------------------------------------------------------------------------------------
def test_client_4xx_raises_distinct_http_error_no_retry():
    transport = StatusTransport(404)
    fetcher = make_fetcher(transport)
    with pytest.raises(SecHttpError) as ei:
        fetcher.get("https://data.sec.gov/missing")
    assert ei.value.status == 404
    assert len(transport.requests) == 1  # client error is not retried


def test_5xx_is_transient_retries_then_surfaces_distinctly():
    transport = StatusTransport(503)
    fetcher = make_fetcher(transport, max_retries=3)
    with pytest.raises(SecHttpError) as ei:
        fetcher.get("https://data.sec.gov/x")
    assert ei.value.status == 503
    assert len(transport.requests) == 4  # 1 + 3 bounded retries


def test_timeout_backs_off_within_bounds_and_surfaces_transport_error():
    transport = RaisingTransport(TimeoutError("read timed out"))
    vt = VirtualTime()
    fetcher = make_fetcher(
        transport, rate=2.0, vt=vt, max_retries=3, backoff_base=0.5, backoff_cap=8.0
    )
    with pytest.raises(SecTransportError) as ei:
        fetcher.get("https://data.sec.gov/x")
    # a transport error is a RAISED type — distinguishable from a zero-filing day (which
    # would be a normal return with empty content).
    assert isinstance(ei.value, SecTransportError)
    assert not isinstance(ei.value, SecHttpError)
    assert ei.value.attempts == 4  # 1 + 3 bounded retries
    assert transport.calls == 4
    # backoff is BOUNDED: every sleep (rate + backoff) stays within the cap.
    assert vt.sleeps, "expected bounded backoff sleeps"
    assert max(vt.sleeps) <= 8.0


def test_transient_recovers_when_a_retry_succeeds():
    ok = HttpResponse(status=200, body=b'{"ok": true}', url="u")
    transport = SequenceTransport([TimeoutError("t1"), TimeoutError("t2"), ok])
    fetcher = make_fetcher(transport, max_retries=3)
    assert fetcher.get_json("https://data.sec.gov/x") == {"ok": True}
    assert transport.calls == 3


def test_transport_error_is_distinguishable_from_empty_day():
    # empty-but-successful submissions -> a normal (non-raising) return
    empty = HttpResponse(status=200, body=b'{"filings": {"recent": {}}}', url="u")
    good = make_fetcher(SequenceTransport([empty]))
    assert good.get_json("https://data.sec.gov/x") == {"filings": {"recent": {}}}
    # a transport failure -> a RAISED SecTransportError, never an empty dict
    bad = make_fetcher(RaisingTransport(), max_retries=0)
    with pytest.raises(SecTransportError):
        bad.get_json("https://data.sec.gov/x")


# --------------------------------------------------------------------------------------
# finite byte budgets -- hard halts, latched for the run, and never hot-retried
# --------------------------------------------------------------------------------------
def test_single_response_byte_cap_halts_without_retry_and_latches():
    response = HttpResponse(status=200, body=b"12345", url="u")
    transport = SequenceTransport([response, response])
    fetcher = make_fetcher(
        transport,
        max_response_bytes=4,
        max_total_bytes=8,
        max_retries=3,
    )

    with pytest.raises(SecResponseTooLarge) as exc_info:
        fetcher.get("https://data.sec.gov/oversized")
    assert exc_info.value.observed_bytes == 5
    assert transport.calls == 1

    # A caller cannot catch the halt and resume the same run's network activity.
    with pytest.raises(SecResponseTooLarge):
        fetcher.get("https://data.sec.gov/another")
    assert transport.calls == 1


def test_production_transport_stops_streaming_at_limit_plus_sentinel():
    transport = UrllibTransport(max_response_bytes=4)
    stream = BytesIO(b"123456789")
    with pytest.raises(SecResponseTooLarge):
        transport._read_bounded(stream, "https://data.sec.gov/large", 200)
    assert stream.tell() == 5


def test_aggregate_run_byte_budget_counts_each_response_and_latches():
    first = HttpResponse(status=200, body=b"1234", url="one")
    second = HttpResponse(status=200, body=b"5678", url="two")
    transport = SequenceTransport([first, second, first])
    fetcher = make_fetcher(
        transport,
        max_response_bytes=4,
        max_total_bytes=6,
    )

    assert fetcher.get_bytes("https://data.sec.gov/one") == b"1234"
    with pytest.raises(SecRunByteBudgetExceeded) as exc_info:
        fetcher.get("https://data.sec.gov/two")
    assert exc_info.value.observed_bytes == 8
    assert fetcher.bytes_received == 8
    assert transport.calls == 2

    with pytest.raises(SecRunByteBudgetExceeded):
        fetcher.get("https://data.sec.gov/three")
    assert transport.calls == 2


# --------------------------------------------------------------------------------------
# url builders — accession dashes stripped, cik integer-form in Archives path
# --------------------------------------------------------------------------------------
def test_pad_cik_and_url_builders():
    assert pad_cik(320193) == "0000320193"
    assert pad_cik("0001045810") == "0001045810"
    assert submissions_url("1045810").endswith("/submissions/CIK0001045810.json")
    from filings.edgar.fetch import archive_document_url

    url = archive_document_url("0001045810", "0001045810-26-000052", "nvda-20260426.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000052/nvda-20260426.htm"
    )
