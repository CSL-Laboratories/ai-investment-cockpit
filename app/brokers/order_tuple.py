"""WP7 shared seam — the canonical *resolved-order tuple* + its hash (SEC-07).

The single fact that binds a human decision to the exact order it authorized. The
``ApprovalRecord`` (and its covering ``AuditEntry`` payload) store
``order_tuple_hash = sha256(canonical {symbol, side, qty, order_type, limit_price, tif})``;
``brokers/`` refuses any submit whose recomputed hash != the approved hash — so a
Modify / re-price yields a DIFFERENT tuple -> DIFFERENT hash -> the old approval cannot
authorize it (a NEW proposal + approval is required), and a single-use approval cannot be
replayed against a mutated order.

This module is the ONE place ``bridge/`` (mint) and ``brokers/`` (verify) agree on
canonicalization, so the two sides never disagree on what a given order hashes to.
Canonicalization is pinned and fail-closed:
  * ``symbol``     — upper-cased, non-empty;
  * ``side``       — one of {buy, sell} (lower-cased);
  * ``order_type`` — one of {market, limit, stop, stop_limit};
  * ``qty``        — a POSITIVE decimal, normalized to a canonical fixed-point string (a
                     resolved order has a concrete share/contract count by approval time);
  * ``limit_price``— required (positive) for ``limit``/``stop_limit``, forbidden otherwise;
  * ``tif``        — one of {day, gtc, ioc, fok, opg, cls} (lower-cased).

Any deviation raises ``ValueError`` (fail closed) rather than producing a hash for an
ill-formed order — an unhashable order can never be approved or submitted.

The hash is a plain SHA-256 (an equality tag, not a tamper seal): its INTEGRITY comes from
the covering ``AuditEntry`` HMAC that records the same hash in the adjudication payload, so
a keyless local writer cannot silently repoint an approval at a different order without
breaking the audit chain (SEC-12).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

_SIDES = frozenset({"buy", "sell"})
_ORDER_TYPES = frozenset({"market", "limit", "stop", "stop_limit"})
_TIFS = frozenset({"day", "gtc", "ioc", "fok", "opg", "cls"})
_NEEDS_LIMIT = frozenset({"limit", "stop_limit"})

# The pinned field order of the tuple (SEC-07). Kept explicit so the canonical dict is
# always the same shape regardless of caller kwarg order.
FIELDS = ("symbol", "side", "qty", "order_type", "limit_price", "tif")


def _decimal(value, *, field: str) -> Decimal:
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"order tuple {field!r} is not a valid number: {value!r}") from None
    if not d.is_finite():
        raise ValueError(f"order tuple {field!r} must be finite, got {value!r}")
    return d


def _canonical_decimal_str(d: Decimal) -> str:
    """A canonical fixed-point string so ``5``, ``5.0`` and ``Decimal('5.00')`` all collapse
    to one representation (no exponent form, no trailing-zero ambiguity)."""
    normalized = d.normalize()
    if normalized == 0:                    # normalize() renders 0 as '0E+2' etc.
        normalized = Decimal(0)
    return format(normalized, "f")


def canonical_order_tuple(
    *,
    symbol: str,
    side: str,
    qty,
    order_type: str,
    limit_price=None,
    tif: str = "day",
) -> dict:
    """Validate + normalize a resolved order into its canonical dict form. Raises
    ``ValueError`` on any ill-formed field (fail closed)."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"order tuple symbol must be a non-empty string, got {symbol!r}")
    sym = symbol.strip().upper()

    side_l = str(side).strip().lower()
    if side_l not in _SIDES:
        raise ValueError(f"order tuple side must be one of {sorted(_SIDES)}, got {side!r}")

    otype_l = str(order_type).strip().lower()
    if otype_l not in _ORDER_TYPES:
        raise ValueError(
            f"order tuple order_type must be one of {sorted(_ORDER_TYPES)}, got {order_type!r}"
        )

    tif_l = str(tif).strip().lower()
    if tif_l not in _TIFS:
        raise ValueError(f"order tuple tif must be one of {sorted(_TIFS)}, got {tif!r}")

    q = _decimal(qty, field="qty")
    if q <= 0:
        raise ValueError(f"order tuple qty must be positive (resolved order), got {qty!r}")

    if otype_l in _NEEDS_LIMIT:
        if limit_price is None:
            raise ValueError(f"order tuple order_type {otype_l!r} requires a limit_price")
        lp = _decimal(limit_price, field="limit_price")
        if lp <= 0:
            raise ValueError(f"order tuple limit_price must be positive, got {limit_price!r}")
        lp_str = _canonical_decimal_str(lp)
    else:
        if limit_price is not None:
            raise ValueError(
                f"order tuple order_type {otype_l!r} must not carry a limit_price"
            )
        lp_str = None

    return {
        "symbol": sym,
        "side": side_l,
        "qty": _canonical_decimal_str(q),
        "order_type": otype_l,
        "limit_price": lp_str,
        "tif": tif_l,
    }


def order_tuple_hash(tuple_or_fields: dict) -> str:
    """Canonical SHA-256 of a resolved order. Accepts either an already-canonical dict (only
    the ``FIELDS`` keys) or a raw field dict, and re-canonicalizes either way so both sides
    of the mint/verify boundary always agree. Fail-closed on ill-formed input."""
    if not isinstance(tuple_or_fields, dict):
        raise ValueError(f"order tuple must be a dict, got {type(tuple_or_fields).__name__}")
    fields = {k: tuple_or_fields.get(k) for k in FIELDS}
    if "tif" not in tuple_or_fields or tuple_or_fields.get("tif") is None:
        fields["tif"] = "day"
    canon = canonical_order_tuple(
        symbol=fields["symbol"],
        side=fields["side"],
        qty=fields["qty"],
        order_type=fields["order_type"],
        limit_price=fields["limit_price"],
        tif=fields["tif"],
    )
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
