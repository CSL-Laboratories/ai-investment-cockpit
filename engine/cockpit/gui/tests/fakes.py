"""Test doubles: a fake control API (WSGI) and the core adapter's transport. Not product code.

The fake answers O7 commands with the checked-in O7 oracle responses
(``docs/replacement/outcomes/O7/fixtures/oracle/responses.json``, read only) and answers
a run Stop with ``pending``, the way an unconfirmed Stop must be reported.
"""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
O7 = ROOT / "docs" / "replacement" / "outcomes" / "O7"
DEVICE = "Bearer device-token-fake"


def oracle_responses() -> dict:
    data = json.loads((O7 / "fixtures" / "oracle" / "responses.json").read_text(encoding="utf-8"))
    items = data["responses"] if isinstance(data.get("responses"), list) else []
    out: dict = {}
    for item in items:
        out.setdefault(item["command"], item)
    return out


class FakeControlAPI:
    def __init__(self, routes=None, stop_result=None) -> None:
        self.routes = routes if routes is not None else []
        self.stop_result = stop_result if stop_result is not None else {"state": "pending",
                                                                       "detail": "stop requested; exit not yet observed"}
        self.responses = oracle_responses()
        self.calls: list[tuple[str, str, object]] = []
        self.extra: dict = {}

    def __call__(self, env, start_response):
        def send(status: str, payload: object):
            raw = json.dumps(payload).encode()
            start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(raw)))])
            return [raw]
        if env.get("HTTP_AUTHORIZATION") != DEVICE:
            return send("401 Unauthorized", {"error": "device_token_required"})
        method, path = env["REQUEST_METHOD"], env["PATH_INFO"]
        body = None
        if method == "POST":
            body = json.loads(env["wsgi.input"].read(int(env["CONTENT_LENGTH"])))
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/v1/routes"):
            return send("200 OK", {"routes": self.routes})
        if (method, path) == ("POST", "/v1/operator"):
            command = body.get("command")
            if command not in self.responses:
                return send("400 Bad Request", {"error": "usage"})
            action = command.startswith("operator.interventions.")
            if body["confirmation"] != ("explicit" if action else "not_required") or \
                    body["arguments"] != ({"scope": "operator_session"} if action else {}):
                return send("400 Bad Request", {"error": "usage"})
            response = copy.deepcopy(self.responses[command])
            response.update(surface=body["surface"], request_id=body["request_id"])
            response.update(copy.deepcopy(self.extra.get(command, {})))
            return send("200 OK", response)
        if (method, path) == ("POST", "/v1/runs/stop"):
            return send("200 OK", {"run_id": body["run_id"], **self.stop_result})
        return send("404 Not Found", {"error": "not_found"})


def wsgi_transport(app, credential: str = DEVICE):
    """The core adapter's transport: it, not the GUI, attaches the device credential."""
    def transport(method: str, path: str, body: bytes | None):
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "HTTP_AUTHORIZATION": credential,
               "CONTENT_LENGTH": str(len(body or b"")), "wsgi.input": io.BytesIO(body or b""),
               "wsgi.errors": sys.stderr}
        status = []
        raw = b"".join(app(env, lambda s, h: status.append(s)))
        return int(status[0].split()[0]), raw
    return transport


def broken_transport(method: str, path: str, body: bytes | None):
    raise ConnectionError("control API down")
