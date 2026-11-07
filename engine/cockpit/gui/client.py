"""Control-API client adapter for the GUI. The GUI's only way to read or act.

The GUI has no order path and no Stop of its own: every read, every O7 intervention
and the run Stop go through this client to the control API, exactly as the CLI does,
and their results come back unchanged. The transport is supplied by the core's adapter,
which attaches the device credential; the GUI never sees or stores it.

Routes on the control API (a proposal for item 8's control API, which does not exist
on any branch this slice could read; integration is open):

* ``GET /v1/routes`` -> ``{"routes": [route record, ...]}``
* ``POST /v1/operator`` with an ``o7.operator.v0`` command -> an O7 response
* ``POST /v1/runs/stop`` with ``{"run_id": ...}`` -> the Stop result as recorded
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Callable

__all__ = ["ACTIONS", "COMMANDS", "READS", "ControlClient", "ControlError", "o7_command"]

READS = tuple("operator." + name for name in (
    "evidence.inspect", "strategies.inspect", "positions.inspect", "decisions.inspect",
    "uncertainty.inspect", "health.inspect", "budgets.inspect", "profiles.inspect",
    "audit.export", "recovery.inspect", "export.bundle"))
ACTIONS = ("operator.interventions.request_stop", "operator.interventions.request_reconciliation",
           "operator.interventions.request_revocation")
COMMANDS = READS + ACTIONS
_RUN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")

Transport = Callable[[str, str, "bytes | None"], "tuple[int, bytes]"]


class ControlError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def o7_command(command: str, surface: str, confirmed: bool, request_id: str | None = None) -> dict:
    """Build the O7 command both surfaces send. Interventions need explicit confirmation."""
    if command not in COMMANDS:
        raise ControlError("command_not_in_operator_contract")
    action = command in ACTIONS
    if action and confirmed is not True:
        raise ControlError("confirmation_required")
    return {
        "api_version": "o7.operator.v0", "candidate_maturity": "researched", "surface": surface,
        "command": command, "request_id": request_id or "req_" + secrets.token_hex(8),
        "arguments": {"scope": "operator_session"} if action else {},
        "confirmation": "explicit" if action else "not_required",
    }


class ControlClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def _call(self, method: str, path: str, payload: object = None) -> object:
        body = None if payload is None else json.dumps(payload, sort_keys=True).encode()
        try:
            status, raw = self._transport(method, path, body)
            data = json.loads(raw)
        except ControlError:
            raise
        except Exception:  # noqa: BLE001 - any transport failure is "unavailable", nothing more
            raise ControlError("control_api_unavailable") from None
        if status != 200:
            code = data.get("error") if type(data) is dict else None
            raise ControlError("control_api_%d:%s" % (status, code if type(code) is str else "error"))
        return data

    def routes(self) -> list:
        data = self._call("GET", "/v1/routes")
        routes = data.get("routes") if type(data) is dict else None
        if type(routes) is not list:
            raise ControlError("control_api_routes_invalid")
        return routes

    def operator(self, command: str, confirmed: bool = False) -> dict:
        data = self._call("POST", "/v1/operator", o7_command(command, "gui", confirmed))
        if type(data) is not dict:
            raise ControlError("control_api_response_invalid")
        return data

    def stop_run(self, run_id: str) -> dict:
        if type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
            raise ControlError("run_id_invalid")
        data = self._call("POST", "/v1/runs/stop", {"run_id": run_id})
        if type(data) is not dict:
            raise ControlError("control_api_response_invalid")
        return data
