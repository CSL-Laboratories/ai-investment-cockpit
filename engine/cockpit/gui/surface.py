"""Minimal server-side HTML operator surface (build-order item 10). WSGI, no JavaScript.

A presentation adapter over the control API, like the CLI: it reads routes, runs the
11 O7 reads, sends the three O7 interventions and forwards a run Stop. It has no order
path and no Stop of its own, and it shows every result as the control API returned it
(a Stop that is ``pending`` is shown as pending; the page never says "terminated" on
its own).

The door, in order, on every request:

1. ``Host`` must be one of the configured hosts;
2. any ``Origin`` must be exactly one configured origin (a list or ``null`` is
   refused); a POST must carry one. ``Sec-Fetch-Site``, when sent, must be
   ``same-origin`` (or ``none`` for a typed-in GET);
3. the core's device-token adapter (``door``) must accept the request. Authentication
   belongs to the core adapter; this module never reads or stores a token.

``Referrer-Policy: same-origin`` is deliberate: ``no-referrer`` makes browsers send
``Origin: null`` on form posts, which broke every verb in Cockpit's earlier browser
operator (csl512 gap6 record). Binding and remote pairing are the composition root's;
nothing here opens a socket. Accessibility: landmarks, labels, a skip link, visible
focus, text status, 24 px minimum targets. No WCAG conformance is claimed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from html import escape
from typing import Callable
from urllib.parse import parse_qs, quote

from .client import ACTIONS, READS, ControlClient, ControlError
from .inventory import BARS, assess, coverage_gaps, project

__all__ = ["Application", "withhold_venue_data"]

_CSS = ("body{font:16px/1.5 system-ui,sans-serif;margin:0 auto;max-width:72rem;padding:0 1rem}"
        ".skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:#fff;padding:.5rem}"
        "a,button,input{min-height:24px;min-width:24px}button{padding:.25rem .75rem}"
        ":focus{outline:3px solid #1a5fb4;outline-offset:2px}table{border-collapse:collapse}"
        "th,td{border:1px solid #666;padding:.25rem .5rem;text-align:left}"
        ".badge{font-weight:700;border:2px solid currentColor;padding:0 .25rem}")
_CSS_HASH = base64.b64encode(hashlib.sha256(_CSS.encode()).digest()).decode()
_CSP = ("default-src 'none'; style-src 'sha256-%s'; form-action 'self'; frame-ancestors 'none'; "
        "base-uri 'none'" % _CSS_HASH)
_MAX_BODY = 4096
_LABELS = {
    "operator.evidence.inspect": "Evidence and provenance", "operator.strategies.inspect": "Strategies",
    "operator.positions.inspect": "Positions and collateral", "operator.decisions.inspect": "Decisions and refusals",
    "operator.uncertainty.inspect": "Uncertainty", "operator.health.inspect": "Health",
    "operator.budgets.inspect": "Budgets", "operator.profiles.inspect": "Compliance profiles",
    "operator.audit.export": "Audit export", "operator.recovery.inspect": "Recovery",
    "operator.export.bundle": "Export bundle",
    "operator.interventions.request_stop": "Stop request (operator intervention)",
    "operator.interventions.request_reconciliation": "Reconciliation request",
    "operator.interventions.request_revocation": "Revocation request",
}


def withhold_venue_data(value: object) -> object:
    """Hide venue data unless the control API marks its switch exactly ``on``."""
    if type(value) is dict:
        out = {k: withhold_venue_data(v) for k, v in value.items()}
        if "venue_data" in value and value.get("venue_data_switch") != "on":
            out["venue_data"] = "[withheld: venue data switch is off]"
        return out
    if type(value) is list:
        return [withhold_venue_data(v) for v in value]
    return value


class Application:
    def __init__(self, client: ControlClient, door: Callable[[dict], bool], hosts: frozenset,
                 origins: frozenset) -> None:
        self._client = client
        self._door = door
        self._hosts = frozenset(hosts)
        self._origins = frozenset(origins)

    # -- door ---------------------------------------------------------------------------
    def _refusal(self, env: dict) -> str | None:
        method = env.get("REQUEST_METHOD")
        if env.get("HTTP_HOST") not in self._hosts:
            return "host_not_allowed"
        origin = env.get("HTTP_ORIGIN")
        if origin is not None and ("," in origin or origin not in self._origins):
            return "origin_not_allowed"
        if method == "POST" and origin is None:
            return "origin_required"
        site = env.get("HTTP_SEC_FETCH_SITE")
        if site is not None and site != "same-origin" and not (method == "GET" and site == "none"):
            return "cross_site_request"
        if self._door(env) is not True:
            return "device_not_authenticated"
        return None

    def __call__(self, env: dict, start_response) -> list[bytes]:
        refusal = self._refusal(env)
        if refusal is not None:
            status = "401 Unauthorized" if refusal == "device_not_authenticated" else "403 Forbidden"
            return self._send(start_response, status, self._page("Refused", "<p>Refused: %s</p>" % escape(refusal)))
        method, path = env.get("REQUEST_METHOD"), env.get("PATH_INFO", "")
        routes = {("GET", "/"): self._home, ("GET", "/read"): self._read,
                  ("POST", "/action"): self._action, ("POST", "/stop"): self._stop}
        handler = routes.get((method, path))
        if handler is None:
            known = path in {p for _, p in routes}
            return self._send(start_response, "405 Method Not Allowed" if known else "404 Not Found",
                              self._page("Not available", "<p>Not available.</p>"))
        try:
            title, body = handler(env)
            return self._send(start_response, "200 OK", self._page(title, body))
        except _BadRequest as exc:
            return self._send(start_response, "400 Bad Request",
                              self._page("Bad request", "<p>Bad request: %s</p>" % escape(str(exc))))

    @staticmethod
    def _send(start_response, status: str, html: str) -> list[bytes]:
        raw = html.encode("utf-8")
        start_response(status, [
            ("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(raw))),
            ("Cache-Control", "no-store"), ("Content-Security-Policy", _CSP),
            ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin"),
            ("X-Frame-Options", "DENY")])
        return [raw]

    # -- pages --------------------------------------------------------------------------
    @staticmethod
    def _page(title: str, main: str) -> str:
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>%s - Cockpit operator</title><style>%s</style></head><body>"
                '<a class="skip" href="#main">Skip to main content</a>'
                "<header><h1>Cockpit operator</h1><p>Every route is BETA unless all six bars pass with "
                "recorded evidence. No live demos. This page cannot place, approve or size orders.</p></header>"
                '<nav aria-label="Sections"><ul><li><a href="/">Overview</a></li>'
                '<li><a href="/#routes-h">Routes</a></li><li><a href="/#reads-h">Reads</a></li>'
                '<li><a href="/#actions-h">Interventions</a></li><li><a href="/#stop-h">Stop a run</a></li>'
                '</ul></nav><main id="main" tabindex="-1">%s</main>'
                "<footer><p>Results are shown as the control API returned them. A stop that is not "
                "confirmed shows as pending.</p></footer></body></html>") % (escape(title), _CSS, main)

    def _routes_table(self) -> str:
        try:
            records = self._client.routes()
        except ControlError as exc:
            records, error = [], exc.code
        else:
            error = None
        rows = [assess(r) for r in records] + coverage_gaps(records)
        body = "".join(
            "<tr><th scope=\"row\">%s</th><td>%s</td><td>%s</td><td>%s</td><td><span class=\"badge\">%s</span>"
            " %s</td><td>%s</td><td>%s</td></tr>" % (
                escape(r["id"]), escape(r["class"]), escape(r["subject"]), escape(r["sign_in"] or "-"),
                project(r, "gui"), escape("" if r["claim"] == "ok" else "(" + r["claim"] + ")"),
                escape(", ".join(r["failed_bars"]) or "none"), escape(r["blocker"] or "-"))
            for r in rows)
        note = "" if error is None else "<p role=\"status\">Routes unavailable: %s</p>" % escape(error)
        return ('<section aria-labelledby="routes-h"><h2 id="routes-h">Routes</h2>%s<table><caption>'
                "Routes from the control API plus required routes it did not report. Bars: %s.</caption>"
                '<thead><tr><th scope="col">Route</th><th scope="col">Class</th><th scope="col">Covers</th>'
                '<th scope="col">Sign-in</th><th scope="col">Maturity</th><th scope="col">Failed bars</th>'
                '<th scope="col">Blocker</th></tr></thead><tbody>%s</tbody></table></section>'
                % (note, escape(", ".join(BARS)), body))

    def _home(self, env: dict) -> tuple[str, str]:
        reads = "".join('<li><a href="/read?command=%s">%s</a></li>' % (quote(c), escape(_LABELS[c]))
                        for c in READS)
        actions = "".join(
            '<form method="post" action="/action"><fieldset><legend>%s</legend>'
            '<input type="hidden" name="command" value="%s"><p>Scope: this operator session only.</p>'
            '<input type="checkbox" id="confirm-%d" name="confirm" value="explicit" required>'
            '<label for="confirm-%d">I confirm this request</label> <button type="submit">Send: %s</button>'
            "</fieldset></form>" % (escape(_LABELS[c]), escape(c), i, i, escape(_LABELS[c]))
            for i, c in enumerate(ACTIONS))
        stop = ('<section aria-labelledby="stop-h"><h2 id="stop-h">Stop a run</h2>'
                '<form method="post" action="/stop"><p><label for="run-id">Run id</label> '
                '<input id="run-id" name="run_id" required autocomplete="off" maxlength="128" '
                'pattern="[A-Za-z0-9_.:\\-]{1,128}" aria-describedby="run-id-help"></p>'
                '<p id="run-id-help">The control API stops the run; the result shows as it reports it.</p>'
                '<p><input type="checkbox" id="confirm-stop" name="confirm" value="explicit" required>'
                '<label for="confirm-stop">I confirm stopping this run</label></p>'
                '<button type="submit">Request stop</button></form></section>')
        main = (self._routes_table() + '<section aria-labelledby="reads-h"><h2 id="reads-h">Reads</h2><ul>'
                + reads + '</ul></section><section aria-labelledby="actions-h"><h2 id="actions-h">'
                "Interventions</h2>" + actions + "</section>" + stop)
        return "Overview", main

    @staticmethod
    def _query(env: dict) -> dict:
        try:
            form = parse_qs(env.get("QUERY_STRING", ""), strict_parsing=True)
        except ValueError:
            raise _BadRequest("query") from None
        return form

    def _form(self, env: dict, fields: set) -> dict:
        try:
            length = int(env.get("CONTENT_LENGTH") or "0")
        except ValueError:
            raise _BadRequest("length") from None
        if not 0 < length <= _MAX_BODY:
            raise _BadRequest("length")
        if (env.get("CONTENT_TYPE") or "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            raise _BadRequest("content type")
        try:
            raw = env["wsgi.input"].read(length).decode("utf-8")
            form = parse_qs(raw, strict_parsing=True)
        except (KeyError, UnicodeError, ValueError):
            raise _BadRequest("form") from None
        if set(form) != fields or any(len(v) != 1 for v in form.values()):
            raise _BadRequest("fields")
        return {k: v[0] for k, v in form.items()}

    @staticmethod
    def _response_view(data: object) -> str:
        shown = withhold_venue_data(data)
        return "<pre>%s</pre>" % escape(json.dumps(shown, indent=2, sort_keys=True))

    def _o7_view(self, command: str, data: dict) -> str:
        status = data.get("status_code") if type(data.get("status_code")) is str else "not reported"
        result = data.get("result") if type(data.get("result")) is dict else {}
        warn = ""
        if result.get("order_submitted") is not False and command in ACTIONS:
            warn = '<p role="alert">Contract problem: the response does not state order_submitted=false.</p>'
        return ('<p role="status">Status reported by the control API: <strong>%s</strong></p>%s%s'
                % (escape(status), warn, self._response_view(data)))

    def _read(self, env: dict) -> tuple[str, str]:
        form = self._query(env)
        if set(form) != {"command"} or len(form["command"]) != 1 or form["command"][0] not in READS:
            raise _BadRequest("command_not_in_operator_contract")
        command = form["command"][0]
        try:
            data = self._client.operator(command)
        except ControlError as exc:
            return _LABELS[command], '<p role="status">Unavailable: %s</p>' % escape(exc.code)
        return _LABELS[command], "<h2>%s</h2>%s" % (escape(_LABELS[command]), self._o7_view(command, data))

    def _action(self, env: dict) -> tuple[str, str]:
        form = self._form(env, {"command", "confirm"})
        if form["command"] not in ACTIONS or form["confirm"] != "explicit":
            raise _BadRequest("command_not_in_operator_contract")
        try:
            data = self._client.operator(form["command"], confirmed=True)
        except ControlError as exc:
            return "Request not sent", '<p role="status">Not confirmed: %s</p>' % escape(exc.code)
        return _LABELS[form["command"]], "<h2>%s</h2>%s" % (escape(_LABELS[form["command"]]),
                                                             self._o7_view(form["command"], data))

    def _stop(self, env: dict) -> tuple[str, str]:
        form = self._form(env, {"run_id", "confirm"})
        if form["confirm"] != "explicit":
            raise _BadRequest("confirmation_required")
        try:
            data = self._client.stop_run(form["run_id"])
        except ControlError as exc:
            return "Stop not confirmed", ('<p role="status">Stop state: unknown. The control API did not '
                                          "answer (%s). Check the run before assuming it stopped.</p>"
                                          % escape(exc.code))
        state = data.get("state") if type(data.get("state")) is str else None
        line = ("Stop state reported by the control API: <strong>%s</strong>" % escape(state) if state
                else "Stop state: not reported by the control API (unknown)")
        return "Stop", '<h2>Stop</h2><p role="status">%s</p>%s' % (line, self._response_view(data))


class _BadRequest(Exception):
    pass
