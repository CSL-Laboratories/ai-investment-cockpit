"""Item 10 GUI: door, no order path, forwarded Stop, O7 parity, beta rubric, coverage, a11y.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/gui/tests -t engine -v``.
Everything goes through WSGI fakes; no socket is opened, no provider or venue is called.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

from cockpit.gui.client import ACTIONS, COMMANDS, READS, ControlClient, ControlError, o7_command
from cockpit.gui.inventory import BARS, REQUIRED, assess, coverage_gaps, project
from cockpit.gui.surface import Application, withhold_venue_data
from cockpit.gui.tests.fakes import O7, FakeControlAPI, broken_transport, wsgi_transport

HOST = "127.0.0.1:8765"
ORIGIN = "http://127.0.0.1:8765"
GUI_DIR = Path(__file__).resolve().parents[1]


def _bars(**overrides) -> dict:
    bars = {bar: {"pass": True, "evidence": "fixture-evidence-" + bar} for bar in BARS}
    bars.update(overrides)
    return bars


def _route(rid: str, cls: str, maturity: str = "beta", bars=None, **extra) -> dict:
    return {"id": rid, "class": cls, "maturity": maturity, "bars": bars if bars is not None else {},
            "configured": True, "blocker": "", **extra}


class _Door:
    def __init__(self, ok: bool = True) -> None:
        self.ok, self.seen = ok, 0

    def __call__(self, env: dict) -> bool:
        self.seen += 1
        return self.ok


class GuiHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeControlAPI(routes=[_route("engine:codex:chatgpt", "engine", engine="codex", sign_in="chatgpt")])
        self.door = _Door()
        self.app = Application(ControlClient(wsgi_transport(self.api)), self.door,
                               frozenset({HOST}), frozenset({ORIGIN}))

    def request(self, method: str, path: str, form: dict | list | None = None, *, query: str = "",
                headers: dict | None = None, raw: bytes | None = None) -> tuple[str, dict, str]:
        body = raw if raw is not None else (urlencode(form, doseq=True).encode() if form is not None else b"")
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, "HTTP_HOST": HOST,
               "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/x-www-form-urlencoded",
               "wsgi.input": io.BytesIO(body), "wsgi.errors": sys.stderr}
        if method == "POST":
            env["HTTP_ORIGIN"] = ORIGIN
            env["HTTP_SEC_FETCH_SITE"] = "same-origin"
        for key, value in (headers or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        status, hdrs = [], {}
        out = b"".join(self.app(env, lambda s, h: (status.append(s), hdrs.update(h))))
        return status[0], hdrs, out.decode("utf-8")


class DoorTests(GuiHarness):
    def test_get_without_origin_is_a_normal_navigation(self) -> None:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, "200 OK")
        self.assertEqual(headers["Referrer-Policy"], "same-origin")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.door.seen, 1)

    def test_refusals(self) -> None:
        cases = [
            ("GET", {"HTTP_HOST": "evil.example"}, "403", "host_not_allowed"),
            ("GET", {"HTTP_ORIGIN": "http://evil.example"}, "403", "origin_not_allowed"),
            ("GET", {"HTTP_ORIGIN": "null"}, "403", "origin_not_allowed"),
            ("POST", {"HTTP_ORIGIN": ORIGIN + ", http://evil.example"}, "403", "origin_not_allowed"),
            ("POST", {"HTTP_ORIGIN": None}, "403", "origin_required"),
            ("POST", {"HTTP_SEC_FETCH_SITE": "cross-site"}, "403", "cross_site_request"),
            ("GET", {"HTTP_SEC_FETCH_SITE": "same-site"}, "403", "cross_site_request"),
        ]
        for method, headers, status, code in cases:
            with self.subTest(headers=headers):
                path = "/" if method == "GET" else "/stop"
                form = None if method == "GET" else {"run_id": "run-1", "confirm": "explicit"}
                got, _, body = self.request(method, path, form, headers=headers)
                self.assertTrue(got.startswith(status), got)
                self.assertIn(code, body)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.door.seen, 0)  # refused before the device check

    def test_device_door_belongs_to_the_core_adapter(self) -> None:
        self.door.ok = False
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, "401 Unauthorized")
        self.assertIn("device_not_authenticated", body)
        self.assertEqual(self.api.calls, [])
        source = (GUI_DIR / "surface.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"HTTP_AUTHORIZATION|HTTP_COOKIE|token\s*=")

    def test_no_socket_binding_or_deploy_code(self) -> None:
        for path in GUI_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"^\s*(import|from)\s+(socket|wsgiref|http\.server|socketserver)",)
                self.assertNotIn("0.0.0.0", text)


class NoOrderPathTests(GuiHarness):
    def test_command_set_is_the_o7_contract(self) -> None:
        matrix = json.loads((O7 / "parity-matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(COMMANDS), sorted(row["command"] for row in matrix["rows"]))
        self.assertEqual((len(READS), len(ACTIONS)), (11, 3))
        self.assertTrue(all(row["order_path"] == "none" for row in matrix["rows"]))

    def test_disallowed_orders_and_commands_refused_before_the_api(self) -> None:
        for command in ("operator.orders.submit", "place_order", "operator.approve", "submit", ""):
            with self.subTest(command=command):
                status, _, _ = self.request("POST", "/action", {"command": command, "confirm": "explicit"})
                self.assertEqual(status, "400 Bad Request")
                self.assertRaises(ControlError, o7_command, command, "gui", True)
        for path in ("/order", "/orders", "/approve", "/submit"):
            self.assertEqual(self.request("POST", path, {"x": "1"})[0], "404 Not Found")
        status, _, _ = self.request("GET", "/read", query="command=operator.interventions.request_stop")
        self.assertEqual(status, "400 Bad Request")  # actions are never a GET
        self.assertEqual([c for c in self.api.calls if c[1] != "/v1/routes"], [])

    def test_gui_imports_no_order_or_paper_code(self) -> None:
        for path in GUI_DIR.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(GUI_DIR))):
                self.assertNotRegex(text, r"(import|from)\s+\S*(paper|gateway|broker|order)")

    def test_interventions_need_explicit_confirmation(self) -> None:
        for form in ({"command": ACTIONS[0]}, {"command": ACTIONS[0], "confirm": "yes"}):
            with self.subTest(form=form):
                self.assertEqual(self.request("POST", "/action", form)[0], "400 Bad Request")
        status, _, body = self.request("POST", "/action", {"command": ACTIONS[0], "confirm": "explicit"})
        self.assertEqual(status, "200 OK")
        self.assertIn("<strong>ok</strong>", body)
        sent = self.api.calls[-1][2]
        self.assertEqual((sent["surface"], sent["confirmation"], sent["arguments"]),
                         ("gui", "explicit", {"scope": "operator_session"}))

    def test_strict_form_parsing(self) -> None:
        cases = [
            dict(form=[("run_id", "a"), ("run_id", "b"), ("confirm", "explicit")]),
            dict(form={"run_id": "a", "confirm": "explicit", "extra": "1"}),
            dict(form={"run_id": "a", "confirm": "explicit"}, headers={"CONTENT_TYPE": "text/plain"}),
            dict(raw=b"run_id=" + b"a" * 5000 + b"&confirm=explicit"),
            dict(raw=b"%%%"),
        ]
        for case in cases:
            with self.subTest(case=str(case)[:60]):
                self.assertEqual(self.request("POST", "/stop", **case)[0], "400 Bad Request")
        self.assertEqual([c for c in self.api.calls if c[1] == "/v1/runs/stop"], [])


class StopTests(GuiHarness):
    def test_stop_forwards_and_shows_pending_unchanged(self) -> None:
        status, _, body = self.request("POST", "/stop", {"run_id": "run-42", "confirm": "explicit"})
        self.assertEqual(status, "200 OK")
        self.assertEqual(self.api.calls[-1], ("POST", "/v1/runs/stop", {"run_id": "run-42"}))
        self.assertIn("Stop state reported by the control API: <strong>pending</strong>", body)
        main = body.split("<main", 1)[1].split("</main>", 1)[0].lower()
        for word in ("terminated", "stopped", "killed", "confirmed"):
            self.assertNotIn(word, main)

    def test_stop_state_is_never_invented(self) -> None:
        self.api.stop_result = {"detail": "accepted"}
        body = self.request("POST", "/stop", {"run_id": "run-1", "confirm": "explicit"})[2]
        self.assertIn("not reported by the control API (unknown)", body)
        self.api.stop_result = {"state": "terminated", "exit_observed": True}
        body = self.request("POST", "/stop", {"run_id": "run-1", "confirm": "explicit"})[2]
        self.assertIn("<strong>terminated</strong>", body)  # shown only because the API said so
        down = Application(ControlClient(broken_transport), _Door(), frozenset({HOST}), frozenset({ORIGIN}))
        self.app = down
        body = self.request("POST", "/stop", {"run_id": "run-1", "confirm": "explicit"})[2]
        self.assertIn("Stop state: unknown", body)
        self.assertNotIn("terminated", body.lower())

    def test_stop_needs_confirmation_and_a_valid_run_id(self) -> None:
        self.assertEqual(self.request("POST", "/stop", {"run_id": "run-1", "confirm": "no"})[0], "400 Bad Request")
        body = self.request("POST", "/stop", {"run_id": "bad id;", "confirm": "explicit"})[2]
        self.assertIn("run_id_invalid", body)
        self.assertEqual([c for c in self.api.calls if c[1] == "/v1/runs/stop"], [])


class ParityTests(GuiHarness):
    _SEMANTIC = ("api_version", "profile", "enforcement_route", "authority_effect", "order_path", "status_code",
                 "visibility", "result", "errors", "command")

    def test_gui_and_cli_get_the_same_semantics_for_every_command(self) -> None:
        transport = wsgi_transport(self.api)
        gui = ControlClient(transport)
        matrix = json.loads((O7 / "parity-matrix.json").read_text(encoding="utf-8"))
        for row in matrix["rows"]:
            command = row["command"]
            with self.subTest(command=command):
                confirmed = command in ACTIONS
                status, raw = transport("POST", "/v1/operator", json.dumps(
                    o7_command(command, "cli", confirmed)).encode())
                cli = json.loads(raw)
                g = gui.operator(command, confirmed=confirmed)
                self.assertEqual((status, cli["surface"], g["surface"]), (200, "cli", "gui"))
                self.assertEqual({k: cli.get(k) for k in self._SEMANTIC}, {k: g.get(k) for k in self._SEMANTIC})
                for field in row["must_show"]:
                    self.assertIn(field, json.dumps(g))
                if command in READS:
                    body = self.request("GET", "/read", query=urlencode({"command": command}))[2]
                    self.assertIn("<strong>%s</strong>" % cli["status_code"], body)


class RubricTests(unittest.TestCase):
    def test_any_single_failing_or_missing_bar_gives_beta(self) -> None:
        self.assertEqual(assess(_route("r", "engine", "mature", _bars()))["maturity"], "mature")
        for bar in BARS:
            for broken in ({"pass": False, "evidence": "x"}, {"pass": True, "evidence": ""},
                           {"pass": True}, {"pass": "true", "evidence": "x"}, None):
                with self.subTest(bar=bar, broken=broken):
                    bars = _bars(**{bar: broken}) if broken is not None else {
                        k: v for k, v in _bars().items() if k != bar}
                    row = assess(_route("r", "engine", "mature", bars))
                    self.assertEqual((row["maturity"], row["claim"]), ("beta", "mature_claim_rejected"))
                    self.assertEqual(row["failed_bars"], [bar])

    def test_missing_evidence_or_maturity_field_is_beta(self) -> None:
        self.assertEqual(assess(_route("r", "engine", "beta", {}))["failed_bars"], list(BARS))
        record = _route("r", "engine", bars=_bars())
        del record["maturity"]
        self.assertEqual((assess(record)["maturity"], assess(record)["claim"]), ("beta", "missing_maturity"))
        self.assertEqual(assess("not a record")["maturity"], "beta")
        self.assertEqual(assess(_route("r", "engine", "MATURE", _bars()))["maturity"], "beta")

    def test_every_projection_carries_maturity(self) -> None:
        rows = [assess(_route("engine:%d" % i, "engine", m, b, engine="codex"))
                for i, (m, b) in enumerate([("beta", {}), ("mature", _bars()), ("mature", {})])]
        rows += coverage_gaps([])
        for row in rows:
            with self.subTest(row=row["id"]):
                self.assertIn("maturity=" + row["maturity"], project(row, "cli"))
                self.assertEqual(project(row, "mcp")["maturity"], row["maturity"])
                self.assertEqual(project(row, "gui"), row["maturity"].upper())

    def test_coverage_gaps_cover_every_required_class(self) -> None:
        gaps = coverage_gaps([])
        self.assertEqual(len(gaps), sum(len(v) for v in REQUIRED.values()))
        self.assertEqual(len(REQUIRED["engine"]), 9)
        self.assertEqual(REQUIRED["mcp"], ("2025-06-18", "2026-07-28"))
        self.assertTrue(all(g["maturity"] == "beta" and g["blocker"].startswith("upstream") for g in gaps))
        reported = [_route("e", "engine", engine="codex"), _route("m", "mcp", mcp_version="2025-06-18"),
                    _route("v", "venue_use", venue="kalshi", use="model_fed_read")]
        ids = {g["id"] for g in coverage_gaps(reported)}
        self.assertNotIn("gap:engine:codex", ids)
        self.assertNotIn("gap:mcp:2025-06-18", ids)
        self.assertIn("gap:mcp:2026-07-28", ids)
        self.assertIn("gap:venue_use:kalshi:model_fed_propose", ids)


class PageTests(GuiHarness):
    def test_routes_shown_with_beta_badges_and_gaps(self) -> None:
        self.api.routes = [_route("engine:codex:chatgpt", "engine", "mature", _bars(xf_verify={"pass": False}),
                                  engine="codex", sign_in="chatgpt"),
                           _route("engine:claude_code:subscription", "engine", "mature", _bars(),
                                  engine="claude_code", sign_in="subscription")]
        body = self.request("GET", "/")[2]
        self.assertIn("mature_claim_rejected", body)
        self.assertIn("gap:engine:hermes", body)
        self.assertIn("upstream: control API did not report this route", body)
        self.assertEqual(body.count('<span class="badge">MATURE</span>'), 1)
        self.assertEqual(body.count('<span class="badge">BETA</span>'),
                         1 + sum(len(v) for v in REQUIRED.values()) - 2)  # two engines reported

    def test_routes_unavailable_is_reported(self) -> None:
        self.app = Application(ControlClient(broken_transport), _Door(), frozenset({HOST}), frozenset({ORIGIN}))
        body = self.request("GET", "/")[2]
        self.assertIn("Routes unavailable: control_api_unavailable", body)
        self.assertNotIn("MATURE</span>", body)

    def test_venue_data_withheld_while_switch_off(self) -> None:
        secret_number = "4242.17"
        self.api.extra["operator.evidence.inspect"] = {"result": {
            **self.api.responses["operator.evidence.inspect"]["result"],
            "provenance": {"venue_data": {"kalshi_yes_price": secret_number}, "venue_data_switch": "off"}}}
        body = self.request("GET", "/read", query="command=operator.evidence.inspect")[2]
        self.assertNotIn(secret_number, body)
        self.assertIn("withheld: venue data switch is off", body)
        self.assertEqual(withhold_venue_data({"venue_data": 1})["venue_data"], "[withheld: venue data switch is off]")
        self.assertEqual(withhold_venue_data({"venue_data": 1, "venue_data_switch": "on"})["venue_data"], 1)
        self.assertEqual(withhold_venue_data([{"venue_data": 1, "venue_data_switch": True}])[0]["venue_data"],
                         "[withheld: venue data switch is off]")

    def test_semantic_landmarks_labels_and_keyboard(self) -> None:
        status, headers, body = self.request("GET", "/")
        audit = _Audit()
        audit.feed(body)
        self.assertEqual(audit.lang, "en")
        for tag in ("header", "nav", "main", "footer"):
            self.assertEqual(audit.counts.get(tag), 1, tag)
        self.assertTrue(audit.skip_link)
        self.assertEqual(audit.counts.get("h1"), 1)
        unlabeled = [i for i in audit.inputs if i.get("type") != "hidden" and i.get("id") not in audit.label_for]
        self.assertEqual(unlabeled, [])
        self.assertTrue(all(text.strip() for text in audit.button_text))
        self.assertEqual(audit.counts.get("script", 0), 0)
        self.assertNotIn("tabindex=\"1", body)  # no positive tabindex; DOM order is the keyboard order
        style = re.search(r"<style>(.*?)</style>", body, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        self.assertIn("'sha256-%s'" % digest, headers["Content-Security-Policy"])
        self.assertIn("min-height:24px", style)
        self.assertIn(":focus{outline", style)


class _Audit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = {}
        self.inputs: list[dict] = []
        self.label_for: set = set()
        self.button_text: list[str] = []
        self.lang = None
        self.skip_link = False
        self._in_button = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.counts[tag] = self.counts.get(tag, 0) + 1
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "input":
            self.inputs.append(a)
        if tag == "label" and a.get("for"):
            self.label_for.add(a["for"])
        if tag == "a" and a.get("href") == "#main":
            self.skip_link = True
        if tag == "button":
            self._in_button = True
            self.button_text.append("")

    def handle_endtag(self, tag):
        if tag == "button":
            self._in_button = False

    def handle_data(self, data):
        if self._in_button:
            self.button_text[-1] += data


if __name__ == "__main__":
    unittest.main()
