"""Install the Rust book when the extension module is present.

Imported by ``lob.py``. A missing extension is the Python implementation, not a
failure: hosted Python gates do not build Rust. A present-but-broken extension
is a failure and is not swallowed.
"""
from __future__ import annotations

def maybe_install(
    order_not_found: type,
    integrity_error: type,
    book_snapshot: type,
    level_snapshot: type,
    resting_order: type,
) -> tuple[type, type] | None:
    try:
        from quant.book._rust_backend import bind
    except ImportError:
        return None
    return bind(
        order_not_found,
        integrity_error,
        book_snapshot,
        level_snapshot,
        resting_order,
    )
