"""Precursor regression (folded into the WP5 phase): the citation EDGAR URL must use the
REAL primary-document filename.

Forward-flag from the WP4 handoff: ``Filing`` had no filename column, so
``filings.delta.engine._primary_filename`` synthesized ``{accession-nodash}.htm`` — a
well-FORMED but non-WORKING URL (the real primary doc is e.g. ``nvda-20260426.htm``). WP5's
digest is the first artifact whose EDGAR links Lucas actually clicks, so the link must
resolve. The fix adds ``Filing.primary_doc`` (captured by the WP3 poller from the SEC
submissions feed) and has the engine prefer it, falling back to the synthesized name only
when it is absent.

These tests are deliberately NOT part of the separately-authored WP5 ``cite/`` acceptance
suite — they certify the engine/data-model half of the fix (Delta.edgar_url), independent of
the citation machinery built on top of it. Offline fixtures only; no LLM, no network.
"""
from __future__ import annotations

from datetime import UTC, datetime

from filings.delta import compute_deltas
from schemas.models.filing import Filing
from filings.raw_store import RawStore
from tests.filings._wp4_support import META, raw_bytes_for, xbrl_facts_for


def _filing(key: str, *, primary_doc: str | None) -> Filing:
    m = META[key]
    return Filing(
        cik=m.cik,
        accession_no=m.accession,
        form_type=m.form,
        filing_date=m.filed,
        detection_ts=datetime(2026, 7, 8, 9, 30, tzinfo=UTC),
        raw_ref=m.accession,
        primary_doc=primary_doc,
    )


def _store(tmp_path, *keys: str) -> RawStore:
    store = RawStore(tmp_path)
    for key in keys:
        store.write(META[key].accession, raw_bytes_for(META[key].accession))
    return store


def _deltas_for_nvda_pair(tmp_path, *, cur_doc, prior_doc):
    store = _store(tmp_path, "NVDA_NEW", "NVDA_OLD")
    return compute_deltas(
        _filing("NVDA_NEW", primary_doc=cur_doc),
        _filing("NVDA_OLD", primary_doc=prior_doc),
        store,
        xbrl_facts_for(META["NVDA_NEW"].accession, META["NVDA_NEW"].cik),
        xbrl_facts_for(META["NVDA_OLD"].accession, META["NVDA_OLD"].cik),
    )


def test_edgar_url_uses_the_real_primary_document_filename(tmp_path):
    # With Filing.primary_doc set, every delta's URL ends in the REAL doc name for the
    # filing it cites (current deltas -> nvda-20260426.htm, removed-passage deltas cited to
    # the prior filing -> nvda-20251026.htm), and NEVER the synthesized {accession}.htm.
    cur_doc = META["NVDA_NEW"].filename       # "nvda-20260426.htm"
    prior_doc = META["NVDA_OLD"].filename     # "nvda-20251026.htm"
    deltas = _deltas_for_nvda_pair(tmp_path, cur_doc=cur_doc, prior_doc=prior_doc)
    assert deltas, "engine produced no deltas for the NVDA pair (interface/fixture gap)"

    by_doc = {
        META["NVDA_NEW"].accession: cur_doc,
        META["NVDA_OLD"].accession: prior_doc,
    }
    for d in deltas:
        expected_doc = by_doc[d.accession_no]
        assert d.edgar_url.endswith("/" + expected_doc), (
            f"delta cites {d.accession_no} but URL does not end in the real doc "
            f"{expected_doc!r}: {d.edgar_url!r}"
        )
        # The synthesized fallback must NOT leak through when a real name is available.
        synthesized = f"{d.accession_no.replace('-', '')}.htm"
        assert not d.edgar_url.endswith("/" + synthesized), (
            f"delta {d.accession_no} still uses the synthesized filename {synthesized!r}"
        )


def test_falls_back_to_synthesized_name_when_primary_doc_absent(tmp_path):
    # A Filing ingested before the column existed (primary_doc=None) still yields a
    # well-formed, accession-pinned URL — the fallback path, backward compatible.
    deltas = _deltas_for_nvda_pair(tmp_path, cur_doc=None, prior_doc=None)
    assert deltas
    for d in deltas:
        synthesized = f"{d.accession_no.replace('-', '')}.htm"
        assert d.edgar_url.endswith("/" + synthesized), (
            f"fallback URL for {d.accession_no} is not the synthesized name: {d.edgar_url!r}"
        )
        # Accession still pins the citation (the WP4 structural guarantee).
        assert d.accession_no.replace("-", "") in d.edgar_url
