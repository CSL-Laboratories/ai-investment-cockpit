"""One-shot GET-only capture of the five already identified RW-21 primaries.

Run explicitly with COCKPIT_SEC_USER_AGENT declared by the operator. No retries,
403/429 recovery, discovery, ingest, audit writes, or overwrite of captured bytes.
Compressed fixtures preserve each original response body byte-for-byte.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone

import httpx


SOURCES = (
    ("SAFT", "0001172052-26-000023", "10-Q", "2026-08-07",
     "1172052/000117205226000023/saft-20260630x10q.htm"),
    ("FSEA", "0001193125-26-206551", "8-K", None,
     "1943802/000119312526206551/d74001d8k.htm"),
    ("SLP", "0001023459-26-000024", "8-K", "2026-06-17",
     "1023459/000102345926000024/slp-20260615.htm"),
    ("CCO", "0001334978-26-000025", "10-Q", "2026-08-05",
     "1334978/000133497826000025/cco-20260630.htm"),
    ("BHF", "0001685040-26-000028", "10-Q", "2026-08-06",
     "1685040/000168504026000028/bhf-20260630.htm"),
)
MAX_BYTES = 10 * 1024 * 1024


def main() -> int:
    user_agent = os.environ.get("COCKPIT_SEC_USER_AGENT", "").strip()
    if not user_agent:
        print("REFUSE: operator-declared COCKPIT_SEC_USER_AGENT required; zero SEC requests.")
        return 2
    destination = Path(__file__).resolve().parent
    manifest_path = destination / "capture-manifest.json"
    if manifest_path.exists() or any(destination.glob("*.html.gz")):
        print("REFUSE: capture destination already contains evidence; no overwrite or replay.")
        return 2
    manifest = {
        "schema": "rw21-exact-sec-fixtures.v1",
        "purpose": "Historical development regressions; no selection or held-out authority",
        "method": "GET",
        "sec_user_agent_declared": True,
        "retry_count": 0,
        "sources": [],
    }
    with httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
        timeout=45.0,
        follow_redirects=False,
    ) as client:
        for symbol, accession, form, filed_on, path in SOURCES:
            if manifest["sources"]:
                time.sleep(1.1)
            url = "https://www.sec.gov/Archives/edgar/data/" + path
            record = {
                "symbol": symbol,
                "accession": accession,
                "form": form,
                "filing_date": filed_on,
                "url": url,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }
            manifest["sources"].append(record)
            try:
                with client.stream("GET", url) as response:
                    record["http_status"] = response.status_code
                    record["content_type"] = response.headers.get("content-type", "")
                    if response.status_code != 200:
                        raise ValueError("SEC response was not 200; collection stopped without retry")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError("SEC primary exceeds fixed per-document capture bound")
                raw = bytes(body)
                if b"<html" not in raw.lower() or "html" not in record["content_type"].lower():
                    raise ValueError("SEC response is not a primary HTML document")
                raw.decode("utf-8")
                filename = f"{symbol}__{accession}.html.gz"
                artifact = gzip.compress(raw, mtime=0)
                with (destination / filename).open("xb") as output:
                    output.write(artifact)
                record.update({
                    "file": filename,
                    "raw_size": len(raw),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
                    "status": "captured",
                })
                print(f"{symbol}: captured {len(raw)} exact document bytes")
            except (httpx.HTTPError, OSError, ValueError) as exc:
                record.update({"status": "refused", "error_type": type(exc).__name__})
                manifest["complete"] = False
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                print(f"{symbol}: REFUSE; stopped without retry ({type(exc).__name__})")
                return 1
    manifest["complete"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
