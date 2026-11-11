"""The paper panel's read client: Recursion's control API, read-only (item 5; #318, CSL-736).

Lucas, Nov 11, 2:30am PST: "I agree with each of your picks" (the panel's operator.*.inspect reads go
through the Recursion control API). The API is recursion PR #327 (CSL-798), still a draft, coded here
against its head ``1580d103fb7c0f5983b9fd67d89892f28f215c78``: an in-process object whose
``call(method, params) -> dict`` answers with a typed ``code``. At that head it serves ``run.list``,
``run.status``, ``run.start`` and ``run.stop`` and answers ``method_unknown`` to anything else; it has
no daemon adapter or HTTP transport yet (its README, "Candidate and unwired").

This client is read-only. It sends only the panel's ``operator.<name>.inspect`` commands, with empty
O7 arguments, and refuses anything else before calling the API, so the panel can never reach
``run.start``, ``run.stop`` or an O7 intervention. The API's answer is shown as the read result with
its ``code`` copied to ``status_code`` (a non-ok ``code`` wins over a ``status_code`` in the reply), so ``method_unknown``, ``registry_unavailable`` or a missing
API (``control_api_unavailable``, the same code #327's CLI uses) stays visible (PRODUCT.md 3.5).
Paper only: PRODUCT.md 5.2 / CP0-v2 (pin d84cdd26, CSLR-D-012).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = ["CONTROL_API_PIN", "ControlApiPanelReader", "ReadRefused"]

CONTROL_API_PIN = "recursion#327@1580d103fb7c0f5983b9fd67d89892f28f215c78"
_INSPECT_READ = re.compile(r"operator\.[a-z][a-z0-9_]*\.inspect")


class ReadRefused(ValueError):
    """The command is not an ``operator.<name>.inspect`` read; nothing was sent."""


def _is_inspect_read(name: object) -> bool:
    return type(name) is str and _INSPECT_READ.fullmatch(name) is not None


class ControlApiPanelReader:
    """``reader(command) -> dict`` over an object with ``call(method, params)`` (#327 ControlAPI)."""

    def __init__(self, control: Any | None) -> None:
        if control is not None and not callable(getattr(control, "call", None)):
            raise TypeError("control must have call(method, params), the recursion #327 ControlAPI shape")
        self._control = control

    def __call__(self, command: str) -> dict:
        if not _is_inspect_read(command):
            raise ReadRefused(f"{command!r} is not an operator.*.inspect read")
        if self._control is None:
            return {"status_code": "control_api_unavailable"}
        reply = self._control.call(command, {})
        if not isinstance(reply, Mapping):
            return {"status_code": "read_failed",
                    "errors": [{"code": "read_failed", "message": "reply_not_a_mapping"}]}
        out = dict(reply)
        # The API's own code wins when it is not "ok" (#327 answers with ``code``), so a transport
        # error can never be masked by a status_code inside the reply.
        api_code = out.get("code")
        code = api_code if type(api_code) is str and api_code != "ok" else out.get("status_code", api_code)
        out["status_code"] = code if type(code) is str else "status_unknown"
        return out
