"""RW-16 — the House Clerk disclosure provider.

WHAT THE HOUSE ACTUALLY PUBLISHES (and what it does not)
---------------------------------------------------------
Two artifacts, and confusing them is the mistake this module exists to prevent:

  1. ``.../public_disc/financial-pdfs/<YEAR>FD.zip`` — a ZIP containing ``<YEAR>FD.xml``, an
     INDEX of every financial-disclosure filing that year: Prefix/Last/First, FilingType,
     StateDst, Year, FilingDate, DocID. ``FilingType == "P"`` is a Periodic Transaction
     Report. **The index carries no transactions at all** — it tells you WHO filed and WHEN.
  2. ``.../public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf`` — the report itself, where the
     transactions live, as a PDF.

So the House feed is inherently two-stage, and the second stage needs PDF text. This module
does not extract PDF text (see ``ptr_parse``'s module docstring: no heavy dependency lands on
the ingest path for RW-16). It takes an injected ``pdf_extractor`` — any callable
``(bytes, doc_id) -> str`` — and when none is wired EVERY PTR is counted as
``pdf_extractor_unavailable`` and NO PDF IS FETCHED. Downloading documents we cannot read
would spend the counterparty's bandwidth on nothing; refusing loudly is both politer and more
honest.

FAIL-CLOSED, PER STAGE
-----------------------
No User-Agent or no transport -> UNPROVISIONED, returned, never raised. A dead index for one
year -> a counted error for that year, with the other year still attempted. A dead PDF -> a
counted error for that document, with the rest of the batch unaffected. Nothing in this
module can turn one bad artifact into a silent zero: ``documents_seen`` rises even when
``disclosures`` does not, which is what lets the poller tell "Congress was quiet" from "we
read forty filings and parsed none of them".

THE ZIP IS UNTRUSTED INPUT
---------------------------
A ZIP fetched over the network is a decompression-bomb vector, so the member count, the
declared uncompressed size and the actual read are all bounded before anything is parsed. A
refusal is a counted error, never an exception and never an unbounded read.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, timedelta

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

log = logging.getLogger(__name__)

NAME = "house"

HOUSE_INDEX_URL = ("https://disclosures-clerk.house.gov/public_disc/financial-pdfs/"
                   "{year}FD.zip")
HOUSE_PTR_PDF_URL = ("https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/"
                     "{year}/{doc_id}.pdf")

# Decompression-bomb bounds for the index ZIP. The real 2026 archive is a handful of MB with a
# single XML member; these leave two orders of magnitude of headroom and still refuse a bomb.
MAX_ZIP_MEMBERS = 16
MAX_ZIP_UNCOMPRESSED_BYTES = 64 * 1024 * 1024

# How far back a cold start may reach. The staleness gate refuses anything older than
# ``CopyPolicy.max_age_days`` (45 by default) anyway, so a longer window only costs bandwidth —
# but it is bounded here so a corrupt persisted cursor cannot ask the Clerk for a decade.
MAX_WINDOW_DAYS = 400


class HouseClerkProvider:
    """``DisclosureProvider`` for the House Clerk's public financial disclosures.

    Every network call goes through the injected ``transport`` and the shared
    :class:`RequestThrottle`; there is no HTTP client in this module's import graph, so no test
    can reach disclosures-clerk.house.gov even by accident.

    ``document_filter(doc_id) -> bool`` is an OPTIONAL seam, defaulting to "fetch everything".
    The poller passes its own "have I already fully consumed this document?" predicate, which
    is what keeps a HELD cursor (see the poller's barrier rules) from re-downloading the same
    PDFs every cycle while it waits for a transient fault to clear.

    ``rate_per_sec`` and ``http_get`` exist because the composition root offers them to every
    ingest factory (``main.build_congress_providers`` -> ``_supported_kwargs``), and a kwarg a
    constructor does not declare is silently DROPPED there. A provider that quietly refused the
    operator's declared rate and the injected getter would then report NOT PROVISIONED forever
    with nobody able to see which wire was missing. See ``provider.transport_from_http_get`` for
    what a JSON-only getter is allowed to become (nothing, loudly).
    """

    name = NAME

    def __init__(self, *, transport=None, http_get=None, user_agent: str | None = None,
                 rate_per_sec: float | None = None, pdf_extractor=None,
                 throttle: RequestThrottle | None = None,
                 max_documents_per_cycle: int = DEFAULT_MAX_DOCUMENTS_PER_CYCLE,
                 document_filter=None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 index_url: str = HOUSE_INDEX_URL, pdf_url: str = HOUSE_PTR_PDF_URL):
        self._transport = resolve_transport(transport, http_get)
        self._user_agent = (user_agent or "").strip() or None
        self._pdf_extractor = pdf_extractor
        self._throttle = (throttle if throttle is not None
                          else RequestThrottle(rate_per_sec=rate_per_sec))
        self._max_documents = max(1, int(max_documents_per_cycle))
        self._document_filter = document_filter
        self._timeout = float(timeout)
        self._index_url = index_url
        self._pdf_url = pdf_url

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
        entries = self._index_entries(window_start, today, result)
        if entries is None:
            return result                          # index unreadable: already counted

        skipped = 0
        for entry in entries[:self._max_documents]:
            if self._document_filter is not None and not self._document_filter(entry.doc_id):
                skipped += 1
                continue                           # already fully consumed — do not re-fetch
            result.documents_seen += 1
            if self._pdf_extractor is None:
                # FIRST-CLASS STATE, not an error we are embarrassed by: the feed is wired and
                # reachable, and the document cannot be read because no extractor exists yet.
                result.parse_errors.append((entry.doc_id, "pdf_extractor_unavailable"))
                continue
            self._fetch_one_ptr(entry, result)
        if len(entries) > self._max_documents:
            result.notes.append(
                f"document cap reached: {len(entries) - self._max_documents} PTR(s) in the "
                f"window were not read this cycle (cap {self._max_documents}); they remain "
                "in the window because the cursor cannot pass an unread document")
        if skipped:
            result.notes.append(f"{skipped} document(s) already consumed, not re-fetched")
        return result

    # ------------------------------------------------------------------------------- stage 1

    def _index_entries(self, since: date, today: date, result: ProviderResult):
        """Every in-window PTR index entry, or None when no index could be read at all.

        Both the current year AND the previous one are read when the window straddles a year
        boundary: a report filed on 2 January covers December trades and lives in the NEW
        year's index, while a late December filing lives in the old one. Reading only
        ``today.year`` would silently lose every filing in the first days of January.
        """
        years = sorted({since.year, today.year})
        entries = []
        read_any = False
        for year in years:
            xml_text, reason = self._read_index(year)
            if xml_text is None:
                result.parse_errors.append((f"{year}FD", reason or "index_unavailable"))
                continue
            read_any = True
            parsed = ptr_parse.parse_house_index(xml_text)
            result.parse_errors.extend(parsed.errors)
            for entry in parsed.entries:
                if not entry.is_ptr:
                    continue                       # annual/candidate filings hold no trades
                if entry.filed_date < since or entry.filed_date > today:
                    continue
                entries.append(entry)
        if not read_any:
            return None
        # DETERMINISTIC ORDER (Inv 2). The cursor is a filed-date high-water mark, so the
        # per-cycle document cap must cut the OLDEST-first list — cutting an arbitrary order
        # would leave holes below the cursor that no later cycle can reach.
        entries.sort(key=lambda e: (e.filed_date, e.doc_id))
        return entries

    def _read_index(self, year: int) -> tuple[str | None, str | None]:
        """Fetch and unzip ``<YEAR>FD.zip`` -> the index XML text, or ``(None, reason)``."""
        if not self._throttle.acquire():
            return None, "rate_limited"
        url = self._index_url.format(year=int(year))
        try:
            response = self._transport("GET", url, headers=self._headers(), data=None,
                                       timeout=self._timeout)
        except Exception as exc:                   # noqa: BLE001 - a dead feed, not a dead run
            log.warning("House index %s unavailable (%s)", year, type(exc).__name__)
            return None, f"transport_error:{type(exc).__name__}"
        if not getattr(response, "ok", False):
            return None, http_failure_reason(response)
        return _unzip_index(getattr(response, "body", b""), year)

    # ------------------------------------------------------------------------------- stage 2

    def _fetch_one_ptr(self, entry, result: ProviderResult) -> None:
        """Fetch ONE PTR PDF, extract its text, parse its rows. Per-document fault isolation.

        Any failure here is attributed to THIS document and counted; the batch continues. One
        unreadable PDF must never cost the other filings in the same cycle their evidence —
        the rule ``ingest/edgar_pipeline.py`` applies per filing, applied here per report.
        """
        if not self._throttle.acquire():
            result.parse_errors.append((entry.doc_id, "rate_limited"))
            return
        url = self._pdf_url.format(year=int(entry.year), doc_id=entry.doc_id)
        try:
            response = self._transport("GET", url, headers=self._headers(), data=None,
                                       timeout=self._timeout)
        except Exception as exc:                   # noqa: BLE001 - one document, not the run
            result.parse_errors.append((entry.doc_id, f"transport_error:{type(exc).__name__}"))
            return
        if not getattr(response, "ok", False):
            result.parse_errors.append((entry.doc_id, http_failure_reason(response)))
            return
        body = getattr(response, "body", b"") or b""
        try:
            text = self._pdf_extractor(body, entry.doc_id)
        except Exception as exc:                   # noqa: BLE001 - an extractor is third-party
            result.parse_errors.append((entry.doc_id, f"pdf_extract_error:{type(exc).__name__}"))
            return
        parsed = ptr_parse.parse_ptr_text(
            text if isinstance(text, str) else "",
            chamber=ptr_parse.CHAMBER_HOUSE,
            doc_id=entry.doc_id,
            filer_display=entry.filer_display,
            filer_slug=entry.filer_slug,
            filed_date=entry.filed_date,
            # raw_ref is COMPOSED from a constant template, an int year and a pattern-validated
            # doc id (``validate_doc_id``) — no untrusted byte reaches it, which is what makes
            # it safe to carry into citation text downstream (the BUG-085 lesson).
            raw_ref=url,
            doc_hash=ptr_parse.document_hash(body),
        )
        result.disclosures.extend(parsed.disclosures)
        result.parse_errors.extend(parsed.row_errors)
        if not parsed.disclosures and not parsed.row_errors:
            # A PTR with no transaction rows at all is legal (an amendment cover page, a
            # "no reportable transactions" report) but it is also what a silently-failing text
            # extractor looks like. Say so; do not let it read as a clean document.
            result.parse_errors.append((entry.doc_id, "no_transaction_rows"))

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept": "*/*"}


def _unzip_index(body: bytes, year: int) -> tuple[str | None, str | None]:
    """Extract ``<YEAR>FD.xml`` from the archive, bounded against a decompression bomb."""
    if not body:
        return None, "index_empty"
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            infos = archive.infolist()
            if not infos:
                return None, "index_zip_empty"
            if len(infos) > MAX_ZIP_MEMBERS:
                return None, "index_zip_suspicious"
            wanted = f"{year}fd.xml"
            member = next((i for i in infos if i.filename.lower().endswith(wanted)), None)
            if member is None:
                xmls = [i for i in infos if i.filename.lower().endswith(".xml")]
                if len(xmls) != 1:
                    return None, "index_member_missing"
                member = xmls[0]
            if member.file_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                return None, "index_zip_too_large"
            with archive.open(member) as handle:
                # Read ONE byte past the cap: a declared size can lie, so the bound has to be
                # enforced on the actual read as well as on the header.
                raw = handle.read(MAX_ZIP_UNCOMPRESSED_BYTES + 1)
            if len(raw) > MAX_ZIP_UNCOMPRESSED_BYTES:
                return None, "index_zip_too_large"
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        return None, f"index_unreadable:{type(exc).__name__}"
    return raw.decode("utf-8", errors="replace"), None


def build_house_provider(settings, *, transport=None, http_get=None, pdf_extractor=None,
                         throttle=None, document_filter=None) -> HouseClerkProvider:
    """Compose the provider from ``Settings``. ALWAYS returns a provider.

    It never returns None and never raises on a missing User-Agent: an unprovisioned feed
    reports itself at ``fetch`` time, where the reason is visible in the cycle's result,
    rather than disappearing at composition time where nothing counts it.
    """
    from ingest.congress.provider import resolve_user_agent

    return HouseClerkProvider(
        transport=transport,
        http_get=http_get,
        user_agent=resolve_user_agent(settings),
        pdf_extractor=pdf_extractor,
        throttle=throttle if throttle is not None else RequestThrottle(
            rate_per_sec=float(getattr(settings, "congress_rate_per_sec", 0) or 0) or None),
        max_documents_per_cycle=int(
            getattr(settings, "congress_max_documents_per_cycle",
                    DEFAULT_MAX_DOCUMENTS_PER_CYCLE)),
        document_filter=document_filter,
    )
