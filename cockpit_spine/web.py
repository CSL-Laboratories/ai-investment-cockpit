"""A loopback-only view of the operator service: the Linux GUI stand-in.

The GUI toolkit is not chosen (CR8 is open), so this is a plain HTML page on
``127.0.0.1`` that renders the same typed lines as the CLI and posts the same
service commands. When a toolkit is chosen, it replaces this file and nothing
below the service API changes.

Hardening, all tested: the listener binds ``127.0.0.1`` and takes no host
argument; a request must carry a loopback ``Host`` (no DNS-rebinding reads);
every POST needs the per-start token; a cross-origin ``Origin`` is refused;
bodies are capped; every value is HTML-escaped; the page sets a no-script
content security policy. It makes no outbound call.

Availability: each connection runs on its own thread and has a socket timeout, so
an idle or stalled connection (a browser's speculative preconnect, or a client
that sends a Content-Length and no body) cannot block another request, including
stop. Access to the one session is serialised by a lock.

Limits, stated plainly. The web approve form carries the confirm digest as a
hidden field, so the web operator does not type it back the way a CLI user does;
it still binds the approval to the payload that was rendered, but it is not
equivalent to typing the digest. The per-start token defends against browser
CSRF only: any local process can ``GET /`` and read it from the page, so this is
a stand-in for a single-user loopback, not a boundary against other local users.
"""

from __future__ import annotations

import html
import json
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from cockpit_spine.service import API_VERSION, OperatorService
from cockpit_spine.session import PENDING

LOOPBACK = "127.0.0.1"
MAX_BODY = 8192
SOCKET_TIMEOUT = 5.0
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
FORM_FIELDS = {
    "propose": ("request_id", "prompt"),
    "approve": ("proposal_id", "operator", "confirm_digest"),
    "veto": ("proposal_id", "operator", "reason"),
    "stop": ("operator", "reason"),
}


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _form(token: str, command: str, hidden: dict[str, str], visible: tuple[str, ...]) -> str:
    fields = [f'<input type="hidden" name="token" value="{_esc(token)}">',
              f'<input type="hidden" name="command" value="{command}">']
    fields += [f'<input type="hidden" name="{k}" value="{_esc(v)}">' for k, v in hidden.items()]
    fields += [f'<label>{name} <input type="text" name="{name}" maxlength="200"></label>'
               for name in visible]
    return f'<form method="post" action="/act">{"".join(fields)}<button>{command}</button></form>'


def render_page(service: OperatorService, token: str, response: dict | None = None) -> str:
    shown = response or service.handle({"api": API_VERSION, "command": "status", "args": {}})
    body = [f"<li>{_esc(item['label'])}: {_esc(item['value'])}</li>" for item in shown["lines"]]
    result = f"<p>result: {_esc(shown['code'])}</p>" if response else ""
    forms = []
    if not service.session.stopped:
        forms.append(_form(token, "propose", {}, ("request_id", "prompt")))
        for item in service.session.proposals():
            if item["state"] == PENDING:
                hidden = {"proposal_id": item["proposal_id"],
                          "confirm_digest": item["payload_digest"]}
                forms.append(_form(token, "approve", hidden, ("operator",)))
                forms.append(_form(
                    token, "veto", {"proposal_id": item["proposal_id"]}, ("operator", "reason")))
        forms.append(_form(token, "stop", {}, ("operator", "reason")))
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Operator</title></head>"
        f"<body><h1>Operator view</h1>{result}<ul>{''.join(body)}</ul>{''.join(forms)}"
        "</body></html>"
    )


class LoopbackView:
    def __init__(
        self, service: OperatorService, *, port: int = 0,
        persist: Callable[[OperatorService], None] | None = None,
        socket_timeout: float = SOCKET_TIMEOUT,
    ) -> None:
        if not 0 < socket_timeout <= 60:
            raise ValueError("socket_timeout must be in (0, 60] seconds")
        self.service = service
        self.token = secrets.token_urlsafe(18)
        self._persist = persist
        self._lock = threading.Lock()
        view = self

        class Handler(BaseHTTPRequestHandler):
            timeout = socket_timeout

            def handle(self) -> None:
                try:
                    super().handle()
                except (ConnectionError, TimeoutError):
                    self.close_connection = True

            def log_message(self, *args: object) -> None:
                return

            def _send(self, status: int, body: str, content_type: str = "text/html") -> None:
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for name, value in HEADERS.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def _local(self) -> bool:
                port_now = self.server.server_address[1]
                allowed = {f"{LOOPBACK}:{port_now}", f"localhost:{port_now}"}
                origin = self.headers.get("Origin")
                origin_ok = origin is None or origin in {f"http://{h}" for h in allowed}
                return self.headers.get("Host") in allowed and origin_ok

            def do_GET(self) -> None:
                if not self._local():
                    return self._send(403, "forbidden")
                if self.path == "/":
                    with view._lock:
                        page = render_page(view.service, view.token)
                    return self._send(200, page)
                if self.path == "/api/status":
                    with view._lock:
                        reply = view.service.handle(
                            {"api": API_VERSION, "command": "status", "args": {}})
                    return self._send(200, json.dumps(reply, sort_keys=True), "application/json")
                self._send(404, "not found")

            def _body(self) -> str | None:
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    length = -1
                if length < 0:
                    self._send(411, "length required")
                    return None
                if length > MAX_BODY:
                    self._send(413, "too large")
                    return None
                return self.rfile.read(length).decode("utf-8", "replace")

            def do_POST(self) -> None:
                if not self._local():
                    return self._send(403, "forbidden")
                if self.path not in ("/api", "/act"):
                    return self._send(404, "not found")
                raw = self._body()
                if raw is None:
                    return
                if self.path == "/api":
                    self._api(raw)
                else:
                    self._act(raw)

            def _api(self, raw: str) -> None:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self._send(415, "json only")
                if not secrets.compare_digest(
                        self.headers.get("X-Operator-Token", ""), view.token):
                    return self._send(403, "forbidden")
                try:
                    request = json.loads(raw)
                except ValueError:
                    return self._send(400, "bad json")
                reply = view._run(request)
                self._send(200, json.dumps(reply, sort_keys=True), "application/json")

            def _act(self, raw: str) -> None:
                form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
                if not secrets.compare_digest(form.pop("token", ""), view.token):
                    return self._send(403, "forbidden")
                command = form.pop("command", "")
                if command not in FORM_FIELDS:
                    return self._send(400, "bad command")
                args = {k: form.get(k, "") for k in FORM_FIELDS[command]}
                with view._lock:
                    reply = view._run_locked(
                        {"api": API_VERSION, "command": command, "args": args})
                    page = render_page(view.service, view.token, reply)
                self._send(200, page)

            def do_PUT(self) -> None:
                self._send(405, "method not allowed")

            do_DELETE = do_PATCH = do_PUT

        self.server = ThreadingHTTPServer((LOOPBACK, port), Handler)
        self.server.daemon_threads = True

    def _run_locked(self, request: object) -> dict:
        reply = self.service.handle(request)
        if self._persist is not None:
            self._persist(self.service)
        return reply

    def _run(self, request: object) -> dict:
        with self._lock:
            return self._run_locked(request)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
