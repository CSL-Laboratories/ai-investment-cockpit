"""Restricted canonical JSON for Cockpit's digests and signed files.

The form is the one Recursion names ``csl-cj/v0`` (its ``plugin_security/canonical.py``),
written again here because these packages take no Recursion dependency. It is
deliberately narrower than RFC 8785 so that the two give the same bytes on everything
it accepts; which of the two forms is the one of record is O49, Lucas's question, and
this module does not decide it. ``DIGEST_FORM`` names the form in every record, so a
later change is visible rather than silent.

Accepted: objects (exactly ``dict``) with ASCII string keys, sorted by code point, no
duplicate keys; arrays (exactly ``list``); ASCII strings (exactly ``str``); ``true``,
``false``, ``null``; integers (exactly ``int``, never ``bool`` where a number is
meant) in ``[-(2**53 - 1), 2**53 - 1]``. No floats, NaN, infinities or exponents, so
money is integer minor units. No whitespace. Nesting at most 32 deep.

``parse`` is the strict reader for canonical bytes (files Cockpit wrote or a signer
signed): anything that does not round-trip to the identical bytes is refused.
``loads_untrusted`` is the reader for text from outside (a model's output): it allows
any JSON whitespace but refuses duplicate keys, floats, non-finite numbers, oversized
integers and lone surrogates (raw in the text or written as a JSON escape), and leaves the field rules to the schema.
"""

from __future__ import annotations

import hashlib
import json
import re

from .errors import Refused

__all__ = ["DIGEST_FORM", "MAX_INT", "canonical", "check_value", "digest", "loads_untrusted", "parse"]

DIGEST_FORM = "csl-cj/v0"
MAX_INT = 2**53 - 1
_MAX_DEPTH = 32
_MAX_INT_DIGITS = 16  # 2**53 - 1 has 16 digits; a longer literal is refused before int() runs
_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")


def check_value(value: object, depth: int = 0) -> None:
    """Raise ``Refused('noncanonical_value')`` unless ``value`` fits the form."""
    if depth > _MAX_DEPTH:
        raise Refused("noncanonical_value", "too deep")
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if -MAX_INT <= value <= MAX_INT:
            return
        raise Refused("noncanonical_value", "integer out of range")
    if kind is str:
        if value.isascii():
            return
        raise Refused("noncanonical_value", "non-ASCII text")
    if kind is list:
        for item in value:
            check_value(item, depth + 1)
        return
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str or not key.isascii():
                raise Refused("noncanonical_value", "key")
            check_value(item, depth + 1)
        return
    raise Refused("noncanonical_value", kind.__name__)


def _dumps(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def canonical(value: object) -> bytes:
    """Return the canonical bytes of ``value`` or refuse."""
    try:
        check_value(value)
        raw = _dumps(value)
        # A caller can mutate its containers between the check and the encoding (on a
        # free-threaded build, from another thread). Check the captured bytes too, through
        # a private decoded copy, and refuse if the two encodings differ.
        captured = json.loads(raw, object_pairs_hook=_no_duplicates, parse_float=_no_float,
                              parse_constant=_no_constant, parse_int=_bounded_int)
        check_value(captured)
        if _dumps(captured) != raw:
            raise Refused("noncanonical_value", "changed canonical form")
    except Refused:
        raise
    except (TypeError, ValueError, RecursionError, RuntimeError):
        raise Refused("noncanonical_value", "changed or unencodable value") from None
    return raw


def digest(value: object) -> str:
    """``sha256:<64 hex>`` of the canonical bytes of ``value``."""
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _no_duplicates(pairs: list) -> dict:
    out: dict = {}
    for key, item in pairs:
        if key in out:
            raise Refused("duplicate_key", key if key.isascii() and key.isprintable() else "")
        out[key] = item
    return out


def _no_float(text: str) -> object:
    raise Refused("noncanonical_value", "float")


def _no_constant(text: str) -> object:
    raise Refused("noncanonical_value", "non-finite")


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > _MAX_INT_DIGITS:
        raise Refused("noncanonical_value", "integer out of range")
    value = int(text)
    if not -MAX_INT <= value <= MAX_INT:
        raise Refused("noncanonical_value", "integer out of range")
    return value


def parse(raw: bytes) -> object:
    """Strictly parse canonical bytes; refuse anything that is not exactly canonical."""
    if type(raw) is not bytes:
        raise Refused("noncanonical_value", "not bytes")
    try:
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_float=_no_float,
                           parse_constant=_no_constant, parse_int=_bounded_int)
    except Refused:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refused("noncanonical_value", "not JSON") from None
    if canonical(value) != raw:
        raise Refused("noncanonical_value", "not in canonical form")
    return value


def loads_untrusted(text: object, *, max_chars: int) -> object:
    """Parse JSON text from outside. Whitespace is allowed; nothing else is lenient."""
    if type(text) is not str:
        raise Refused("not_text")
    if len(text) > max_chars:
        raise Refused("too_large")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused("text_invalid", "lone surrogate") from None
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_float=_no_float,
                           parse_constant=_no_constant, parse_int=_bounded_int)
    except Refused:
        raise
    except (ValueError, RecursionError):
        raise Refused("not_json") from None
    _no_lone_surrogates(value)
    return value


def _no_lone_surrogates(value: object) -> None:
    """Refuse a lone surrogate that came in as an escape (the text itself was checked).

    Iterative, so a deeply nested value cannot exhaust the stack here."""
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is str:
            if _SURROGATE.search(item):
                raise Refused("text_invalid", "lone surrogate")
        elif type(item) is list:
            stack.extend(item)
        elif type(item) is dict:
            for key, sub in item.items():
                if _SURROGATE.search(key):
                    raise Refused("text_invalid", "lone surrogate")
                stack.append(sub)
