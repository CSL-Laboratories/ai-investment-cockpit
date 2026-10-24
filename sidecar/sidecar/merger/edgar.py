"""SEC EDGAR submissions reader.

The client talks only to ``data.sec.gov``. It does not parse merger consideration out of
HTML. Deal terms stay in operator cards; filings only prove what was public as of a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
MERGER_FORMS = frozenset({
    "S-4",
    "S-4/A",
    "DEFM14A",
    "DEFA14A",
    "SC TO-T",
    "SC TO-T/A",
    "SC 14D9",
    "SC 14D9/A",
    "425",
})


class EdgarError(ValueError):
    """A submissions payload or request cannot be used."""


@dataclass(frozen=True)
class Filing:
    cik: str
    form: str
    accession: str
    filed_on: date
    accepted_at: datetime | None

    def visible_at(self, as_of: datetime) -> bool:
        """Whether this filing was public at ``as_of``.

        An acceptance timestamp is used when EDGAR provides one. A date-only filing is
        treated as public on the next UTC date, which avoids assuming it was available at
        the open of the filing date.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise EdgarError("as_of must be timezone-aware")
        moment = as_of.astimezone(timezone.utc)
        if self.accepted_at is not None:
            return self.accepted_at <= moment
        return moment.date() > self.filed_on


def _cik(value: Any) -> str:
    text = str(value).strip()
    if text.isdigit():
        text = text.zfill(10)
    if len(text) != 10 or not text.isdigit():
        raise EdgarError("CIK must be 10 digits")
    return text


def _accepted(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EdgarError("acceptanceDateTime is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_submissions(payload: dict) -> list[Filing]:
    if not isinstance(payload, dict):
        raise EdgarError("submissions payload must be an object")
    try:
        recent = payload["filings"]["recent"]
        forms = recent["form"]
        accessions = recent["accessionNumber"]
        filed = recent["filingDate"]
    except (KeyError, TypeError) as exc:
        raise EdgarError("submissions payload is missing filings.recent") from exc
    accepted = recent.get("acceptanceDateTime") or [None] * len(forms)
    if not (len(forms) == len(accessions) == len(filed) == len(accepted)):
        raise EdgarError("submissions columns have different lengths")
    cik = _cik(payload.get("cik"))
    rows = []
    for form, accession, day, stamp in zip(forms, accessions, filed, accepted, strict=True):
        try:
            filed_on = date.fromisoformat(str(day))
        except ValueError as exc:
            raise EdgarError(f"bad filing date {day!r}") from exc
        rows.append(
            Filing(
                cik=cik,
                form=str(form).strip(),
                accession=str(accession).strip(),
                filed_on=filed_on,
                accepted_at=_accepted(stamp),
            )
        )
    return rows


def fetch_submissions(cik: str, *, user_agent: str, transport=None) -> dict:
    """GET the public submissions JSON. ``user_agent`` must identify the operator.

    SEC asks for a descriptive User-Agent with contact information and a modest rate.
    ``transport`` is an httpx client or a callable compatible with ``httpx.Client.get``.
    """
    if not isinstance(user_agent, str) or len(user_agent.strip()) < 12 or "@" not in user_agent:
        raise EdgarError(
            "set SIDECAR_SEC_USER_AGENT to a descriptive User-Agent that contains a contact email"
        )
    import httpx

    url = SUBMISSIONS_URL.format(cik=_cik(cik))
    headers = {"User-Agent": user_agent.strip(), "Accept": "application/json"}
    if transport is None:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            response = client.get(url)
    else:
        response = transport.get(url, headers=headers)
    if response.status_code != 200:
        raise EdgarError(f"EDGAR submissions request failed with status {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise EdgarError("EDGAR submissions body was not an object")
    return payload
