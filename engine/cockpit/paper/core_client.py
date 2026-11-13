"""Client for the Recursion core's ``csl-core/v0`` boundary (bytes in, bytes out).

Cockpit does not import the Recursion core. It receives a transport, a callable that
takes canonical request bytes and returns canonical response bytes, bound by the core
to this plugin's identity. The encoder below follows the same restricted canonical
form the core publishes (``csl-cj/v0``: sorted ASCII keys, no whitespace, integers
within 2**53 - 1, no floats). It is a deliberate copy at a protocol boundary, so both
sides can be checked against the same vectors; integration with the core's published
``protocol.json`` is a follow-up, not assumed here.
"""

from __future__ import annotations

import json
from typing import Callable

__all__ = ["CoreClient", "CoreRefused", "canonical"]

PROTOCOL = "csl-core/v0"
MAX_INT = 2**53 - 1


class CoreRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _check(value: object) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if -MAX_INT <= value <= MAX_INT:
            return
        raise ValueError("integer out of range")
    if type(value) is str and value.isascii():
        return
    if type(value) is list:
        for item in value:
            _check(item)
        return
    if type(value) is dict and all(type(k) is str and k.isascii() for k in value):
        for item in value.values():
            _check(item)
        return
    raise ValueError("noncanonical value: " + type(value).__name__)


def canonical(value: object) -> bytes:
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


class CoreClient:
    def __init__(self, transport: Callable[[bytes], bytes]) -> None:
        self._transport = transport

    def call(self, op: str, **args: object) -> dict:
        raw = self._transport(canonical({"protocol": PROTOCOL, "op": op, "args": args}))
        try:
            response = json.loads(raw)
        except (TypeError, ValueError):
            raise CoreRefused("core_response_invalid") from None
        if type(response) is not dict or response.get("protocol") != PROTOCOL:
            raise CoreRefused("core_response_invalid")
        if response.get("ok") is True and type(response.get("result")) is dict:
            return response["result"]
        refusal = response.get("refusal")
        code = refusal.get("code") if type(refusal) is dict else None
        raise CoreRefused(code if type(code) is str else "core_response_invalid")

    def request_approval(self, effect: dict) -> dict:
        return self.call("effect.request", effect=effect)

    def authorize(self, effect: dict, approval_id: str, authority: dict | None) -> dict:
        result = self.call("effect.authorize", effect=effect, approval_id=approval_id, authority=authority)
        if result.get("authorized") is not True:
            raise CoreRefused("core_response_invalid")
        return result
