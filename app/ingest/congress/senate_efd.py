"""RW-16 — the Senate Electronic Financial Disclosure (eFD) provider.

WHAT THE SENATE PUBLISHES, AND WHY IT COSTS THREE REQUESTS INSTEAD OF ONE
--------------------------------------------------------------------------
The House Clerk hands you a per-year ZIP over an anonymous GET. The Senate does not. Its
search endpoint is behind a PUBLIC TERMS PAGE, and the sequence is:

  1. ``GET  https://efdsearch.senate.gov/search/home/`` — the terms page. It sets a session
     cookie and embeds a Django ``csrfmiddlewaretoken`` in a hidden input.
  2. ``POST https://efdsearch.senate.gov/search/home/`` with ``prohibition_agreement=1`` plus
     that token — this is the acceptance. The SAME session is now allowed to search.
  3. ``POST https://efdsearch.senate.gov/search/report/data/`` with the token and a date
     window — returns JSON rows: ``{"data": [[first, last, office, <anchor html>, filed], ...]}``.
     The anchor's href locates the report; ``/search/view/ptr/<uuid>/`` is an
     ELECTRONICALLY-FILED report whose transactions are an HTML table, and
     ``/search/view/paper/<uuid>/`` is a SCAN with no text layer at all.
  4. ``GET  https://efdsearch.senate.gov/search/view/ptr/<uuid>/`` per report — the table.

STEP 2 IS NOT A BYPASS AND IS NOT PRETENDING TO BE A HUMAN. It is the documented public entry
to a public record, performed once per session, under the operator's own declared User-Agent,
at the self-imposed rate in ``provider.RequestThrottle``. What this module will NOT do is fetch
without that identification: no UA -> UNPROVISIONED, returned, never raised.

THE SESSION LIVES IN THE TRANSPORT, NOT HERE
---------------------------------------------
Steps 1-4 only work if the cookie set in step 1 is carried by steps 2-4, so the production
transport MUST be session-scoped (``provider.Transport``). This module deliberately parses no
cookies and stores none: cookie handling is a property of an HTTP client, and a feed module
that re-implemented it would be a second, worse client. A STATELESS transport does not crash
anything — the search POST simply comes back unauthorised and is COUNTED as a fetch fault,
which is the honest report of "the wire we were given cannot hold a session".

FAIL-CLOSED, PER STAGE — and the stages are NOT interchangeable
----------------------------------------------------------------
A failed handshake kills the CYCLE (there is nothing to search with) and is reported as ONE
counted error, not as an empty search. A failed search kills the cycle likewise. A failed or
unreadable REPORT kills only that document; the rest of the batch continues, exactly as the
House provider isolates one bad PDF. ``documents_seen`` rises even when ``disclosures`` does
not, which is what lets the poller tell "the Senate filed nothing this week" from "we opened
nine reports and parsed none of them".

PAPER FILINGS ARE A FIRST-CLASS, NAMED OUTCOME
-----------------------------------------------
A meaningful share of Senate PTRs are still scans behind ``/search/view/paper/``. There is no
text layer to extract, so there is nothing this package can parse — ever, not "not yet". They
are counted as ``paper_ptr_unsupported`` (a STRUCTURAL reason, so they can never wedge the
cursor) and NEVER fetched: spending a request on a document we know we cannot read takes
bandwidth from one we can.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from html.parser import HTMLParser

from copytrade.models import CHAMBER_SENATE
from copytrade.policy import normalize_filer
from ingest.congress import ptr_parse
from ingest.congress.provider import (
    DEFAULT_MAX_DOCUMENTS_PER_CYCLE,
    DEFAULT_TIMEOUT_SECONDS,
    UNPROVISIONED_NO_TRANSPORT,
    UNPROVISIONED_NO_UA,
    ProviderResult,
    RequestThrottle,
    http_failure_reason,
    resolve_transport,
    unprovisioned,
)
from ingest.normalize import escape_untrusted

log = logging.getLogger(__name__)

NAME = "senate"

SENATE_HOME_URL = "https://efdsearch.senate.gov/search/home/"
SENATE_SEARCH_URL = "https://efdsearch.senate.gov/search/report/data/"
SENATE_REPORT_URL = "https://efdsearch.senate.gov/search/view/ptr/{doc_id}/"

# eFD's report-type selector for "Periodic Transaction Report". OBSERVED on the live search
# form, NOT published as an API contract anywhere — which is why it is a constructor knob and
# not a constant baked into the request. If the Senate renumbers it, the search returns rows of
# the WRONG type; every one of those rows then fails ``parse_senate_ptr_html``'s header check
# and is COUNTED, so the failure mode is a visibly empty feed rather than a silently wrong one.
SENATE_REPORT_TYPE_PTR = "11"

# Untrusted-input bounds. Neither is a tuning knob: they bound what ONE hostile or corrupt
# response can cost inside a scheduled job.
MAX_SEARCH_ROWS = 5_000
MAX_HANDSHAKE_CHARS = 4 * 1024 * 1024

# How far back a cold start may reach — same rationale and same number as the House provider:
# the staleness gate refuses anything older than ``CopyPolicy.max_age_days`` anyway, so a wider
# window only costs bandwidth, but a corrupt persisted cursor must not ask eFD for a decade.
MAX_WINDOW_DAYS = 400

# A Django CSRF token is base64url-ish and fixed-length in practice. It is interpolated into a
# POST body, so it is pattern-validated rather than trusted: a token containing a delimiter
# would let a tampered terms page steer the search request's other fields.
_CSRF_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,128}$", re.ASCII)

# The report link inside a search row's anchor cell. Anchored to the eFD path shape and to an
# ASCII id charset for the same reason ``ptr_parse._DOC_ID`` is: this value is interpolated
# into a URL PATH and into ``Disclosure.raw_ref``, and a "cleaned" id points at a DIFFERENT
# document than the row meant.
_REPORT_HREF = re.compile(
    r"""href\s*=\s*["']/search/view/(?P<kind>[a-z-]{1,16})/(?P<doc_id>[0-9A-Za-z_-]{1,64})/?["']""",
    re.ASCII,
)

# eFD's own name for a scanned filing. Everything else that is not ``ptr`` is a report kind we
# have no grammar for; both are refused, and they are counted APART so an operator can tell
# "the Senate still files on paper" from "eFD published a shape we do not understand".
_KIND_ELECTRONIC = "ptr"
_KIND_PAPER = "paper"


class _CsrfFinder(HTMLParser):
    """Pull the ``csrfmiddlewaretoken`` value out of the terms page. Stdlib only.

    An HTML parser rather than a regex because attribute ORDER and quoting are not guaranteed,
    and a regex that is nearly right here fails as a silent empty token — i.e. as a handshake
    that reports success and produces a session that cannot search. It executes nothing,
    follows nothing and builds no DOM.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.token: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "input" or self.token is not None:
            return
        attributes = dict(attrs)
        if attributes.get("name") == "csrfmiddlewaretoken":
            value = attributes.get("value")
            # FIRST token wins and is validated HERE: a page carrying two differing tokens is
            # either corrupt or hostile, and picking "the last one" would let appended markup
            # decide which credential we send.
            if isinstance(value, str) and _CSRF_TOKEN.match(value):
                self.token = value


def extract_csrf_token(html_text: str) -> str | None:
    """The terms page's CSRF token, or None. PURE — no I/O, testable from a fixture page."""
    if not isinstance(html_text, str) or not html_text.strip():
        return None
    parser = _CsrfFinder()
    try:
        parser.feed(html_text[:MAX_HANDSHAKE_CHARS])
        parser.close()
    except Exception:                              # noqa: BLE001 - malformed markup, not a crash
        return None
    return parser.token


class SearchRow:
    """One decoded eFD search hit: WHO filed WHAT and WHEN — never a transaction.

    Kept deliberately distinct from :class:`~copytrade.models.Disclosure` for the same reason
    ``ptr_parse.HouseIndexEntry`` is: a search row carries no amount, no ticker and no
    transaction, and treating one as a trade signal is the most dangerous misreading available
    in this feed.
    """

    __slots__ = ("doc_id", "kind", "filer_display", "filer_slug", "filed_date")

    def __init__(self, *, doc_id: str, kind: str, filer_display: str, filer_slug: str,
                 filed_date: date):
        self.doc_id = doc_id
        self.kind = kind
        self.filer_display = filer_display
        self.filer_slug = filer_slug
        self.filed_date = filed_date

    @property
    def is_electronic(self) -> bool:
        """Only an electronically-filed report has a table we can read (see the docstring)."""
        return self.kind == _KIND_ELECTRONIC


def parse_search_payload(text: str) -> tuple[list[SearchRow], list[tuple[str, str]]]:
    """Decode the search response into ``(rows, errors)``. PURE — no I/O.

    Lives here rather than in ``ptr_parse`` for the same reason ``house_clerk._unzip_index``
    does: it decodes ONE endpoint's transport envelope, not the STOCK Act filing grammar that
    both chambers share. The grammar lives in ``ptr_parse`` and is called from step 4.

    Every refusal is a NAMED error, never a dropped row. A search response we half-understand
    is the shape that quietly halves a feed, so a row that fails any gate is counted with the
    ordinal that lets an operator find it in the primary source.
    """
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return [], [("search", "search_payload_malformed")]
    if not isinstance(payload, dict):
        return [], [("search", "search_payload_not_an_object")]
    data = payload.get("data")
    if not isinstance(data, list):
        # An error object, a login redirect rendered as JSON, or a schema change. NOT an empty
        # result set: a failed search that read as "the Senate filed nothing" is precisely the
        # silence this package refuses.
        return [], [("search", "search_payload_missing_data")]

    rows: list[SearchRow] = []
    errors: list[tuple[str, str]] = []
    for ordinal, raw in enumerate(data[:MAX_SEARCH_ROWS]):
        if not isinstance(raw, list) or len(raw) < 5:
            errors.append((f"search:{ordinal}", "search_row_shape"))
            continue
        first, last, _office, anchor, filed_text = (str(raw[0]), str(raw[1]), str(raw[2]),
                                                    str(raw[3]), str(raw[4]))
        match = _REPORT_HREF.search(anchor)
        if match is None:
            errors.append((f"search:{ordinal}", "search_row_no_link"))
            continue
        doc_id = ptr_parse.validate_doc_id(match.group("doc_id"))
        if doc_id is None:
            # Counted, never sanitised into something we then fetch.
            errors.append((f"search:{ordinal}", "bad_doc_id"))
            continue
        filed = ptr_parse.parse_filing_date(filed_text)
        if filed is None:
            errors.append((doc_id, "bad_filed_date"))
            continue
        display = " ".join(f"{first} {last}".split())
        slug = normalize_filer(f"{last}, {first}" if last.strip() else display)
        if not slug:
            # An unparseable name fails CLOSED at the policy layer anyway (an empty slug matches
            # no follow entry), but it is recorded so the operator sees a filing skipped for a
            # NAME reason rather than silently never appearing.
            errors.append((doc_id, "index_entry_invalid"))
            continue
        rows.append(SearchRow(
            doc_id=doc_id,
            kind=match.group("kind"),
            # SEC-02 at the boundary where the value becomes a stored one, not at some later
            # display site a future caller might bypass. ``build_disclosure`` escapes this same
            # value again downstream; that is safe because ``escape_untrusted`` is a FIXED POINT
            # (``f(f(x)) == f(x)``), NOT because "``&`` is escaped first" — that older claim was
            # false and double-encoded every "&" in a filer name ("Bob &amp;amp; Sons"). Do not
            # reintroduce ``&`` entity-encoding there without removing one of these two calls.
            filer_display=escape_untrusted(display)[:200],
            filer_slug=slug,
            filed_date=filed,
        ))
    if len(data) > MAX_SEARCH_ROWS:
        errors.append(("search", "search_row_cap"))
    return rows, errors


class SenateEfdProvider:
    """``DisclosureProvider`` for the Senate's electronic financial disclosure search.

    Every network call goes through the injected ``transport`` and the shared
    :class:`RequestThrottle`; there is no HTTP client in this module's import graph, so no test
    can reach efdsearch.senate.gov even by accident.

    ``rate_per_sec`` / ``http_get`` mirror :class:`~ingest.congress.house_clerk
    .HouseClerkProvider` because ``main.build_congress_providers`` offers the same kwargs to
    both and silently drops any a constructor does not declare — see that class for why a
    dropped kwarg is worse than a rejected one.
    """

    name = NAME

    def __init__(self, *, transport=None, http_get=None, user_agent: str | None = None,
                 rate_per_sec: float | None = None,
                 throttle: RequestThrottle | None = None,
                 max_documents_per_cycle: int = DEFAULT_MAX_DOCUMENTS_PER_CYCLE,
                 document_filter=None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 home_url: str = SENATE_HOME_URL, search_url: str = SENATE_SEARCH_URL,
                 report_url: str = SENATE_REPORT_URL,
                 report_type: str = SENATE_REPORT_TYPE_PTR):
        self._transport = resolve_transport(transport, http_get)
        self._user_agent = (user_agent or "").strip() or None
        self._throttle = (throttle if throttle is not None
                          else RequestThrottle(rate_per_sec=rate_per_sec))
        self._max_documents = max(1, int(max_documents_per_cycle))
        self._document_filter = document_filter
        self._timeout = float(timeout)
        self._home_url = home_url
        self._search_url = search_url
        self._report_url = report_url
        self._report_type = str(report_type)
        # The token is per SESSION, and the session lives in the transport. Cached across
        # cycles because re-accepting the terms page on every poll would triple this feed's
        # request count against a small .gov host for no new information; dropped the moment a
        # search comes back unauthorised, so a rotated session re-handshakes rather than
        # retrying a dead credential forever.
        self._csrf: str | None = None

    # ------------------------------------------------------------------ the provider contract

    def fetch(self, *, since: date, today: date) -> ProviderResult:
        if self._user_agent is None:
            return unprovisioned(UNPROVISIONED_NO_UA)
        if self._transport is None:
            return unprovisioned(UNPROVISIONED_NO_TRANSPORT)

        result = ProviderResult()
        window_start = max(since, today - timedelta(days=MAX_WINDOW_DAYS))
        if window_start != since:
            result.notes.append(
                f"window clamped to {MAX_WINDOW_DAYS} days (cursor was {since})")

        token, reason = self._ensure_session()
        if token is None:
            # ONE counted error for the whole cycle. NOT an empty search: a handshake failure
            # means we never asked the question, and "we never asked" must never be recorded in
            # the same shape as "the answer was nothing".
            result.parse_errors.append(("handshake", reason or "handshake_failed"))
            return result

        rows, reason = self._search(window_start, today, token)
        if rows is None:
            result.parse_errors.append(("search", reason or "search_failed"))
            return result
        parsed, errors = parse_search_payload(rows)
        result.parse_errors.extend(errors)

        # DETERMINISTIC ORDER (Inv 2). The cursor is a filed-date high-water mark, so the
        # per-cycle document cap must cut the OLDEST-first list — cutting an arbitrary order
        # would leave holes below the cursor that no later cycle can reach.
        in_window = sorted(
            (r for r in parsed if window_start <= r.filed_date <= today),
            key=lambda r: (r.filed_date, r.doc_id),
        )
        skipped = 0
        for row in in_window[:self._max_documents]:
            if self._document_filter is not None and not self._document_filter(row.doc_id):
                skipped += 1
                continue                           # already fully consumed — do not re-fetch
            result.documents_seen += 1
            if not row.is_electronic:
                # A scan has no text layer. STRUCTURAL and permanent, so it can never wedge the
                # cursor — and never fetched, because a request we know we cannot read is a
                # request taken from a document we can.
                result.parse_errors.append((
                    row.doc_id,
                    "paper_ptr_unsupported" if row.kind == _KIND_PAPER else "unknown_report_kind",
                ))
                continue
            self._fetch_one_report(row, result)
        if len(in_window) > self._max_documents:
            result.notes.append(
                f"document cap reached: {len(in_window) - self._max_documents} report(s) in "
                f"the window were not read this cycle (cap {self._max_documents}); they remain "
                "in the window because the cursor cannot pass an unread document")
        if skipped:
            result.notes.append(f"{skipped} document(s) already consumed, not re-fetched")
        return result

    # ------------------------------------------------------------------------ stages 1 and 2

    def _ensure_session(self) -> tuple[str | None, str | None]:
        """Accept the public terms page once per session. ``(token, None)`` or ``(None, why)``."""
        if self._csrf is not None:
            return self._csrf, None
        page, reason = self._get_text(self._home_url)
        if page is None:
            return None, reason
        token = extract_csrf_token(page)
        if token is None:
            # A terms page with no token is a page we do not recognise (a maintenance notice, a
            # WAF interstitial, a redesign). Refusing beats POSTing an acceptance blind.
            return None, "handshake_no_csrf_token"
        if not self._throttle.acquire():
            return None, "rate_limited"
        try:
            response = self._transport(
                "POST", self._home_url, headers=self._headers(form=True),
                data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
                timeout=self._timeout,
            )
        except Exception as exc:                   # noqa: BLE001 - a dead feed, not a dead run
            log.warning("Senate eFD handshake failed (%s)", type(exc).__name__)
            return None, f"transport_error:{type(exc).__name__}"
        if not getattr(response, "ok", False):
            return None, f"handshake_{http_failure_reason(response)}"
        self._csrf = token
        return token, None

    # ------------------------------------------------------------------------------- stage 3

    def _search(self, since: date, today: date, token: str) -> tuple[str | None, str | None]:
        """POST the date-windowed PTR search. Returns the raw response text, or ``(None, why)``."""
        if not self._throttle.acquire():
            return None, "rate_limited"
        payload = {
            "start": "0",
            "length": str(MAX_SEARCH_ROWS),
            "report_types": f"[{self._report_type}]",
            "filer_types": "[]",
            "submitted_start_date": f"{since.month:02d}/{since.day:02d}/{since.year} 00:00:00",
            "submitted_end_date": f"{today.month:02d}/{today.day:02d}/{today.year} 23:59:59",
            "candidate_state": "",
            "senator_state": "",
            "office_id": "",
            "first_name": "",
            "last_name": "",
            "csrfmiddlewaretoken": token,
        }
        try:
            response = self._transport("POST", self._search_url,
                                       headers=self._headers(form=True), data=payload,
                                       timeout=self._timeout)
        except Exception as exc:                   # noqa: BLE001 - a dead feed, not a dead run
            log.warning("Senate eFD search failed (%s)", type(exc).__name__)
            return None, f"transport_error:{type(exc).__name__}"
        if not getattr(response, "ok", False):
            status = getattr(response, "status", None)
            if status in (401, 403):
                # The session went away (rotated cookie, expired token). Drop the cached
                # credential so the NEXT cycle re-handshakes instead of retrying a dead one
                # forever — a feed that fails identically every poll is a feed nobody fixes.
                self._csrf = None
            return None, f"search_{http_failure_reason(response)}"
        try:
            return response.text(), None
        except Exception:                          # noqa: BLE001 - undecodable body, not a crash
            return None, "search_body_unreadable"

    # ------------------------------------------------------------------------------- stage 4

    def _fetch_one_report(self, row: SearchRow, result: ProviderResult) -> None:
        """Fetch ONE electronically-filed report and parse its table. Per-document isolation.

        Any failure here is attributed to THIS document and counted; the batch continues. One
        unreadable report must never cost the other filings in the same cycle their evidence —
        the rule ``ingest/edgar_pipeline.py`` applies per filing, applied here per report.
        """
        if not self._throttle.acquire():
            result.parse_errors.append((row.doc_id, "rate_limited"))
            return
        url = self._report_url.format(doc_id=row.doc_id)
        try:
            response = self._transport("GET", url, headers=self._headers(), data=None,
                                       timeout=self._timeout)
        except Exception as exc:                   # noqa: BLE001 - one document, not the run
            result.parse_errors.append((row.doc_id, f"transport_error:{type(exc).__name__}"))
            return
        if not getattr(response, "ok", False):
            result.parse_errors.append((row.doc_id, http_failure_reason(response)))
            return
        body = getattr(response, "body", b"") or b""
        try:
            html_text = response.text()
        except Exception:                          # noqa: BLE001 - undecodable body, not a crash
            result.parse_errors.append((row.doc_id, "report_body_unreadable"))
            return
        parsed = ptr_parse.parse_senate_ptr_html(
            html_text,
            doc_id=row.doc_id,
            filer_display=row.filer_display,
            filer_slug=row.filer_slug,
            filed_date=row.filed_date,
            # raw_ref is COMPOSED from a constant template and a pattern-validated doc id
            # (``validate_doc_id``) — no untrusted byte reaches it, which is what makes it safe
            # to carry into citation text downstream (the BUG-085 lesson).
            raw_ref=url,
            # Hashed over the bytes AS FETCHED, not over the decoded text: the decode is lossy
            # (``errors="replace"``), so a hash of the text could not be reproduced by an
            # operator re-fetching the primary document. Invariant 8 / SEC-13.
            doc_hash=ptr_parse.document_hash(body),
        )
        result.disclosures.extend(parsed.disclosures)
        result.parse_errors.extend(parsed.row_errors)
        if not parsed.disclosures and not parsed.row_errors:
            # A report with no transaction rows is legal (an amendment cover page, a "no
            # reportable transactions" filing) but it is also what a page redesign looks like.
            # Say so; do not let it read as a clean document.
            result.parse_errors.append((row.doc_id, "no_transaction_rows"))

    # ------------------------------------------------------------------------------- plumbing

    def _get_text(self, url: str) -> tuple[str | None, str | None]:
        if not self._throttle.acquire():
            return None, "rate_limited"
        try:
            response = self._transport("GET", url, headers=self._headers(), data=None,
                                       timeout=self._timeout)
        except Exception as exc:                   # noqa: BLE001 - a dead feed, not a dead run
            return None, f"transport_error:{type(exc).__name__}"
        if not getattr(response, "ok", False):
            return None, http_failure_reason(response)
        try:
            return response.text(), None
        except Exception:                          # noqa: BLE001 - undecodable body, not a crash
            return None, "body_unreadable"

    def _headers(self, *, form: bool = False) -> dict[str, str]:
        headers = {"User-Agent": self._user_agent, "Accept": "*/*"}
        if form:
            # Django's CSRF middleware checks the Referer on same-origin POSTs over HTTPS. It is
            # our OWN previous request's URL, not a forged origin — omitting it makes the
            # acceptance POST fail with a 403 that looks exactly like a block.
            headers["Referer"] = self._home_url
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return headers


def build_senate_provider(settings, *, transport=None, http_get=None, throttle=None,
                          document_filter=None) -> SenateEfdProvider:
    """Compose the provider from ``Settings``. ALWAYS returns a provider.

    It never returns None and never raises on a missing User-Agent: an unprovisioned feed
    reports itself at ``fetch`` time, where the reason is visible in the cycle's result, rather
    than disappearing at composition time where nothing counts it.
    """
    from ingest.congress.provider import resolve_user_agent

    return SenateEfdProvider(
        transport=transport,
        http_get=http_get,
        user_agent=resolve_user_agent(settings),
        throttle=throttle if throttle is not None else RequestThrottle(
            rate_per_sec=float(getattr(settings, "congress_rate_per_sec", 0) or 0) or None),
        max_documents_per_cycle=int(
            getattr(settings, "congress_max_documents_per_cycle",
                    DEFAULT_MAX_DOCUMENTS_PER_CYCLE)),
        document_filter=document_filter,
    )


__all__ = [
    "NAME",
    "SENATE_HOME_URL",
    "SENATE_REPORT_TYPE_PTR",
    "SENATE_REPORT_URL",
    "SENATE_SEARCH_URL",
    "SearchRow",
    "SenateEfdProvider",
    "build_senate_provider",
    "extract_csrf_token",
    "parse_search_payload",
]

# CHAMBER_SENATE is imported for the same reason house_clerk imports CHAMBER_HOUSE: the chamber
# a provider speaks for is a DOMAIN constant, and ``parse_senate_ptr_html`` stamps it on every
# row it builds. Referenced here so the binding is explicit rather than implied by a name.
assert NAME == CHAMBER_SENATE, "provider name must be the chamber it files for"
