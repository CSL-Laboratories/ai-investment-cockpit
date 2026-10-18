"""OCC option symbol parser. Alpaca symbols are unpadded, for example SPY251219C00500000."""

from __future__ import annotations

import re
from datetime import date

from sidecar.flow.config import FlowError

_OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str) -> tuple[str, date, str, float]:
    match = _OCC.fullmatch(symbol)
    if match is None:
        raise FlowError(f"unrecognized option symbol {symbol}")
    root, ymd, right, strike = match.groups()
    try:
        expiration = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError as exc:
        raise FlowError(f"unrecognized option symbol {symbol}") from exc
    side = "call" if right == "C" else "put"
    return root, expiration, side, int(strike) / 1000.0
