"""Join operator deal cards to filings that were already public."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sidecar.merger.edgar import MERGER_FORMS, Filing

CONSIDERATIONS = frozenset({"cash", "stock", "mixed"})
OUTCOMES = frozenset({"completed", "broken", "open"})


class DealDataError(ValueError):
    """A deal card is not usable as a point-in-time row."""


def _dt(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DealDataError(f"{field} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DealDataError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _dec(value, field: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DealDataError(f"{field} is not a decimal") from exc
    if not number.is_finite() or number <= 0:
        raise DealDataError(f"{field} must be positive")
    return number


def _day(value, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DealDataError(f"{field} is not a date") from exc


def build_observations(cards: list[dict], filings: list[Filing]) -> list[dict]:
    """Return one feature row per card.

    Features use only filings visible at ``observed_at``. Outcome fields are kept for later
    scoring and are not model inputs by themselves; ``fit_calibration`` drops unresolved and
    future outcomes from training.
    """
    if not isinstance(cards, list):
        raise DealDataError("cards must be a list")
    by_cik: dict[str, list[Filing]] = {}
    for filing in filings:
        by_cik.setdefault(filing.cik, []).append(filing)
    rows = []
    seen = set()
    for card in cards:
        if not isinstance(card, dict):
            raise DealDataError("each card must be an object")
        deal_id = str(card.get("deal_id", "")).strip()
        if not deal_id or deal_id in seen:
            raise DealDataError("deal_id must be present and unique")
        seen.add(deal_id)
        consideration = str(card.get("consideration", "")).strip()
        outcome = str(card.get("outcome", "")).strip()
        if consideration not in CONSIDERATIONS:
            raise DealDataError(f"{deal_id} consideration is not cash, stock, or mixed")
        if outcome not in OUTCOMES:
            raise DealDataError(f"{deal_id} outcome is not completed, broken, or open")
        symbol = str(card.get("symbol", "")).strip().upper()
        if not symbol:
            raise DealDataError(f"{deal_id} symbol is empty")
        cik = str(card.get("target_cik", "")).strip().zfill(10)
        if len(cik) != 10 or not cik.isdigit():
            raise DealDataError(f"{deal_id} target_cik is invalid")
        observed = _dt(card.get("observed_at"), "observed_at")
        announced = _dt(card.get("announced_at"), "announced_at")
        if announced > observed:
            raise DealDataError(f"{deal_id} was observed before it was announced")
        outcome_at = None
        outcome_price = None
        if outcome != "open":
            outcome_at = _dt(card.get("outcome_at"), "outcome_at")
            if outcome_at <= observed:
                raise DealDataError(f"{deal_id} outcome is not strictly after observed_at")
            outcome_price = _dec(card.get("outcome_price"), "outcome_price")
        offer = _dec(card.get("offer_price"), "offer_price")
        market = _dec(card.get("market_price"), "market_price")
        outside = _day(card.get("outside_date"), "outside_date")
        hostile = card.get("hostile")
        regulatory = card.get("regulatory_review")
        if type(hostile) is not bool or type(regulatory) is not bool:
            raise DealDataError(f"{deal_id} hostile and regulatory_review must be booleans")
        visible = [
            filing
            for filing in by_cik.get(cik, [])
            if filing.form in MERGER_FORMS and filing.visible_at(observed)
        ]
        spread = (offer - market) / market
        days = (outside - observed.date()).days
        rows.append(
            {
                "deal_id": deal_id,
                "symbol": symbol,
                "target_cik": cik,
                "synthetic": bool(card.get("synthetic", False)),
                "consideration": consideration,
                "hostile": hostile,
                "regulatory_review": regulatory,
                "announced_at": announced.isoformat(),
                "observed_at": observed.isoformat(),
                "outside_date": outside.isoformat(),
                "offer_price": format(offer, "f"),
                "market_price": format(market, "f"),
                "spread": format(spread, "f"),
                "days_to_outside": days,
                "merger_filing_count": len(visible),
                "pit_filing_accessions": sorted(filing.accession for filing in visible),
                "outcome": outcome,
                "outcome_at": None if outcome_at is None else outcome_at.isoformat(),
                "outcome_price": None if outcome_price is None else format(outcome_price, "f"),
            }
        )
    return rows
