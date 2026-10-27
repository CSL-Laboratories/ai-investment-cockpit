"""The loopback view: hardened, and the same service the CLI uses."""

from __future__ import annotations

import http.client
import io
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path

from cockpit_spine import cli, web
from cockpit_spine import projections as proj
from cockpit_spine import service as svc
from cockpit_spine import session as sess
from cockpit_spine.fake_source import FakeProposalSource
from cockpit_spine.tests.support.scripted import (
    ScriptedSource, accepted, counter_clock, proposal_payload,
)

REGISTRY = proj.load_registry()


class Texts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append(tag)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.nodes.append(data.strip())


class ViewCase(unittest.TestCase):
    def start(self, source=None, **options) -> web.LoopbackView:
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source or FakeProposalSource(),
            clock=counter_clock())
        view = web.LoopbackView(svc.OperatorService(session, REGISTRY), **options)
        thread = threading.Thread(target=view.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(view.close)
        return view

    def request(self, view: web.LoopbackView, method: str, path: str, body: str = "",
                headers: dict | None = None, host: str | None = None) -> tuple[int, dict, str]:
        conn = http.client.HTTPConnection(web.LOOPBACK, view.port, timeout=5)
        merged = {"Host": host or f"{web.LOOPBACK}:{view.port}", **(headers or {})}
        conn.request(method, path, body=body or None, headers=merged)
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        result = response.status, dict(response.getheaders()), data
        conn.close()
        return result

    def api(self, view: web.LoopbackView, command: str, **args: str) -> dict:
        body = json.dumps({"api": svc.API_VERSION, "command": command, "args": args})
        status, _, text = self.request(view, "POST", "/api", body, {
            "Content-Type": "application/json", "X-Operator-Token": view.token})
        self.assertEqual(200, status)
        return json.loads(text)


class HardeningTests(ViewCase):
    def test_binds_loopback_only(self) -> None:
        view = self.start()
        self.assertEqual(web.LOOPBACK, view.server.server_address[0])

    def test_page_headers_and_status_endpoint(self) -> None:
        view = self.start()
        status, headers, body = self.request(view, "GET", "/")
        self.assertEqual(200, status)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertNotIn("<script", body)
        status, _, text = self.request(view, "GET", "/api/status")
        self.assertEqual("status", json.loads(text)["code"])
        self.assertEqual(404, self.request(view, "GET", "/etc/passwd")[0])

    def test_foreign_host_or_origin_is_refused(self) -> None:
        view = self.start()
        self.assertEqual(403, self.request(view, "GET", "/", host="evil.example")[0])
        self.assertEqual(403, self.request(view, "GET", "/", host=f"127.0.0.1:{view.port + 1}")[0])
        self.assertEqual(403, self.request(
            view, "GET", "/", headers={"Origin": "http://evil.example"})[0])
        self.assertEqual(200, self.request(
            view, "GET", "/", host=f"localhost:{view.port}")[0])

    def test_posts_need_the_token_the_type_and_a_sane_size(self) -> None:
        view = self.start()
        body = json.dumps({"api": svc.API_VERSION, "command": "status", "args": {}})
        json_type = {"Content-Type": "application/json"}
        self.assertEqual(403, self.request(view, "POST", "/api", body, json_type)[0])
        self.assertEqual(403, self.request(
            view, "POST", "/api", body, {**json_type, "X-Operator-Token": "wrong"})[0])
        self.assertEqual(415, self.request(
            view, "POST", "/api", body, {"Content-Type": "text/plain",
                                         "X-Operator-Token": view.token})[0])
        self.assertEqual(413, self.request(
            view, "POST", "/api", "x" * (web.MAX_BODY + 1),
            {**json_type, "X-Operator-Token": view.token})[0])
        self.assertEqual(400, self.request(
            view, "POST", "/api", "{not json", {**json_type, "X-Operator-Token": view.token})[0])
        self.assertEqual(403, self.request(view, "POST", "/act", "command=stop")[0])
        self.assertEqual(403, self.request(
            view, "POST", "/api", body, {**json_type, "X-Operator-Token": view.token},
            host="evil.example")[0])
        self.assertEqual(404, self.request(view, "POST", "/elsewhere", "x")[0])
        self.assertEqual(405, self.request(view, "PUT", "/api", "x")[0])
        self.assertEqual("session_opened", view.service.session.records()[-1]["kind"])

    def test_form_with_a_bad_command_is_refused(self) -> None:
        view = self.start()
        self.assertEqual(400, self.request(
            view, "POST", "/act", f"token={view.token}&command=approve_everything")[0])

    def test_model_text_is_escaped_in_the_page(self) -> None:
        payload = proposal_payload(thesis="<script>alert(1)</script> & more")
        view = self.start(ScriptedSource(accepted(payload)))
        self.api(view, "propose", request_id="req-1")
        body = self.request(view, "GET", "/")[2]
        self.assertNotIn("<script", body)
        self.assertNotIn("alert(1)", body)


class FlowTests(ViewCase):
    def test_form_flow_approve_then_veto_then_stop(self) -> None:
        view = self.start(ScriptedSource(
            accepted(proposal_payload("prop-1")), accepted(proposal_payload("prop-2"))))
        for n in (1, 2):
            self.api(view, "propose", request_id=f"req-{n}")
        page = self.request(view, "GET", "/")[2]
        self.assertEqual(2, page.count('value="approve"'))
        digest = view.service.session.proposal("prop-1")["payload_digest"]
        form = (f"token={view.token}&command=approve&proposal_id=prop-1&operator=op"
                f"&confirm_digest={digest}")
        status, _, body = self.request(view, "POST", "/act", form)
        self.assertEqual(200, status)
        self.assertIn("result: approved", body)
        self.assertEqual("approved", view.service.session.proposal("prop-1")["state"])
        form = (f"token={view.token}&command=veto&proposal_id=prop-2&operator=op&reason=nope")
        self.assertIn("result: vetoed", self.request(view, "POST", "/act", form)[2])
        form = f"token={view.token}&command=stop&operator=op&reason=done"
        stopped = self.request(view, "POST", "/act", form)[2]
        self.assertIn("result: stopped", stopped)
        self.assertNotIn("<form", stopped)
        again = self.request(
            view, "POST", "/act", f"token={view.token}&command=propose&request_id=r9")[2]
        self.assertIn("result: session_stopped", again)

    def test_a_wrong_digest_from_the_form_does_not_approve(self) -> None:
        view = self.start(ScriptedSource(accepted(proposal_payload())))
        self.api(view, "propose", request_id="req-1")
        form = (f"token={view.token}&command=approve&proposal_id=prop-1&operator=op"
                "&confirm_digest=sha256:" + "0" * 64)
        self.assertIn("result: digest_mismatch", self.request(view, "POST", "/act", form)[2])
        self.assertEqual("pending", view.service.session.proposal("prop-1")["state"])

    def test_visible_numbers_are_exactly_validated_lines(self) -> None:
        view = self.start()
        self.api(view, "propose", request_id="req-1")
        reply = self.api(view, "status")
        allowed = {f"{i['label']}: {i['value']}" for i in reply["lines"]
                   if i["kind"] in ("id", "time", "digest", "term", "figure")}
        proposal_ids = {i["value"] for i in reply["lines"] if i["label"] == "proposal"}
        parser = Texts()
        parser.feed(self.request(view, "GET", "/")[2])
        for node in parser.nodes:
            if any(c.isdigit() for c in node):
                if any(node.endswith(" " + pid) for pid in proposal_ids):
                    continue
                self.assertIn(node, allowed, node)
        self.assertTrue(any("[projection:" in n for n in parser.nodes))


class ParityTests(ViewCase):
    def test_cli_and_loopback_view_produce_the_same_transcript(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="parity-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        log = tmp / "t.jsonl"
        clock = counter_clock()

        def run(*args: str) -> dict:
            out = io.StringIO()
            cli.main(["--log", str(log), "--json", *args], out=out, clock=clock)
            return json.loads(out.getvalue())

        run("open", "--session-id", "s-1", "--mode", "paper")
        cli_steps = [run("propose", "--request-id", "r1"), run("propose", "--request-id", "r2")]
        digest = next(i["value"] for i in cli_steps[0]["lines"] if i["label"] == "confirm digest")
        cli_steps += [
            run("approve", "--proposal-id", "prop-r1", "--operator", "op",
                "--confirm-digest", digest),
            run("veto", "--proposal-id", "prop-r2", "--operator", "op", "--reason", "no"),
            run("stop", "--operator", "op", "--reason", "end"),
            run("propose", "--request-id", "r3"),
        ]

        view = self.start()
        web_steps = [
            self.api(view, "propose", request_id="r1"), self.api(view, "propose", request_id="r2"),
            self.api(view, "approve", proposal_id="prop-r1", operator="op",
                     confirm_digest=digest),
            self.api(view, "veto", proposal_id="prop-r2", operator="op", reason="no"),
            self.api(view, "stop", operator="op", reason="end"),
            self.api(view, "propose", request_id="r3"),
        ]
        self.assertEqual(cli_steps, web_steps)
        self.assertEqual(cli.read_log(log), view.service.session.records())


STOP_FORM = "command=stop&operator=op&reason=done"


class AvailabilityTests(ViewCase):
    """One stuck connection must never take the stop control away."""

    def idle_socket(self, view: web.LoopbackView, send: bytes = b"") -> socket.socket:
        conn = socket.create_connection((web.LOOPBACK, view.port), timeout=5)
        self.addCleanup(conn.close)
        if send:
            conn.sendall(send)
        return conn

    def stop_within(self, view: web.LoopbackView, bound: float = 3.0) -> float:
        started = time.monotonic()
        status, _, body = self.request(
            view, "POST", "/act", f"token={view.token}&{STOP_FORM}")
        elapsed = time.monotonic() - started
        self.assertEqual(200, status)
        self.assertIn("result: stopped", body)
        self.assertTrue(view.service.session.stopped)
        self.assertLess(elapsed, bound)
        return elapsed

    def test_an_idle_connection_does_not_block_stop(self) -> None:
        view = self.start()
        self.idle_socket(view)
        self.stop_within(view)

    def test_many_idle_connections_do_not_block_stop(self) -> None:
        view = self.start()
        for _ in range(8):
            self.idle_socket(view)
        self.stop_within(view)

    def test_a_stalled_request_body_does_not_block_stop(self) -> None:
        view = self.start()
        head = (f"POST /act HTTP/1.1\r\nHost: {web.LOOPBACK}:{view.port}\r\n"
                "Content-Length: 100\r\n\r\npartial").encode()
        self.idle_socket(view, head)
        self.stop_within(view)

    def test_a_half_sent_request_line_does_not_block_stop(self) -> None:
        view = self.start()
        self.idle_socket(view, b"GET / HT")
        self.stop_within(view)

    def test_idle_connections_are_closed_after_the_timeout(self) -> None:
        view = self.start(socket_timeout=0.3)
        conn = self.idle_socket(view)
        conn.settimeout(3)
        started = time.monotonic()
        self.assertEqual(b"", conn.recv(1))
        self.assertLess(time.monotonic() - started, 3)

    def test_the_timeout_is_set_on_the_handler_and_bounded(self) -> None:
        view = self.start()
        self.assertEqual(web.SOCKET_TIMEOUT, view.server.RequestHandlerClass.timeout)
        for bad in (0, -1, 61):
            with self.assertRaises(ValueError):
                web.LoopbackView(view.service, socket_timeout=bad)

    def test_concurrent_commands_do_not_corrupt_the_session(self) -> None:
        view = self.start(ScriptedSource(*[accepted(proposal_payload(f"prop-{n}"))
                                           for n in range(1, 7)]))
        results: list[dict] = []

        def go(n: int) -> None:
            results.append(self.api(view, "propose", request_id=f"req-{n}"))

        threads = [threading.Thread(target=go, args=(n,)) for n in range(1, 7)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(6, len(results))
        self.assertTrue(all(r["ok"] for r in results))
        records = view.service.session.records()
        from cockpit_spine.audit import verify_chain

        self.assertEqual(view.service.session.head, verify_chain(records))
        sess.OperatorSession.replay(records, source=ScriptedSource())


class PageStateTests(ViewCase):
    def test_only_pending_proposals_get_approve_and_veto_forms(self) -> None:
        view = self.start(ScriptedSource(
            accepted(proposal_payload("prop-1")), accepted(proposal_payload("prop-2")),
            accepted(proposal_payload("prop-3"))))
        for n in (1, 2, 3):
            self.api(view, "propose", request_id=f"req-{n}")
        digest = view.service.session.proposal("prop-1")["payload_digest"]
        self.api(view, "approve", proposal_id="prop-1", operator="op", confirm_digest=digest)
        self.api(view, "veto", proposal_id="prop-2", operator="op", reason="no")
        page = self.request(view, "GET", "/")[2]
        self.assertEqual(1, page.count('value="approve"'))
        self.assertEqual(1, page.count('value="veto"'))
        self.assertIn('name="proposal_id" value="prop-3"', page)
        self.assertIn(">approve prop-3<", page)
        self.assertIn(">veto prop-3<", page)
        self.assertNotIn('name="proposal_id" value="prop-1"', page)
        self.assertNotIn('name="proposal_id" value="prop-2"', page)
        self.assertNotIn(">approve prop-1<", page)

    def test_a_non_ascii_token_is_forbidden(self) -> None:
        view = self.start()
        status, _, _ = self.request(view, "POST", "/act", "token=%C3%A9&command=stop")
        self.assertEqual(403, status)
        header = self.request(
            view, "POST", "/api", "{}",
            {"Content-Type": "application/json", "X-Operator-Token": "é"})
        self.assertEqual(403, header[0])

    def test_labels_and_values_are_escaped(self) -> None:
        view = self.start(ScriptedSource(accepted(proposal_payload(
            thesis="<img src=x onerror=alert(1)>"))))
        self.api(view, "propose", request_id="req-1")
        page = self.request(view, "GET", "/")[2]
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img", page)

    def test_persist_runs_after_each_api_and_form_command_but_not_on_reads(self) -> None:
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=ScriptedSource(accepted(proposal_payload())),
            clock=counter_clock())
        seen: list[int] = []
        view = web.LoopbackView(
            svc.OperatorService(session, REGISTRY),
            persist=lambda service: seen.append(len(service.session.records())))
        thread = threading.Thread(target=view.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(view.close)
        self.request(view, "GET", "/")
        self.request(view, "GET", "/api/status")
        self.assertEqual([], seen)
        self.api(view, "propose", request_id="req-1")
        self.request(view, "POST", "/act", f"token={view.token}&{STOP_FORM}")
        self.assertEqual(2, len(seen))
        self.assertLess(seen[0], seen[1])


class CaptionAndFieldTests(unittest.TestCase):
    def service(self, source=None) -> svc.OperatorService:
        session = sess.OperatorSession.open(
            session_id="s-1", mode="paper", source=source or FakeProposalSource(),
            clock=counter_clock())
        return svc.OperatorService(session, REGISTRY)

    def test_a_non_identifier_proposal_id_is_withheld_on_the_button(self) -> None:
        service = self.service(ScriptedSource(accepted(proposal_payload("1234567"))))
        service.handle({
            "api": svc.API_VERSION, "command": "propose", "args": {"request_id": "req-1"}})
        page = web.render_page(service, "tok")
        self.assertEqual(2, page.count('value="1234567"'))
        self.assertIn(">approve withheld id<", page)
        self.assertIn(">veto withheld id<", page)
        self.assertNotIn("approve 1234567", page)
        self.assertNotIn("veto 1234567", page)

    def test_the_prompt_field_allows_2000_characters(self) -> None:
        page = web.render_page(self.service(), "tok")
        self.assertLess(2000, web.MAX_BODY)
        self.assertIn('name="prompt" maxlength="2000"', page)
        self.assertEqual(1, page.count('maxlength="2000"'))
        for name in ("request_id", "operator", "reason"):
            self.assertIn(f'name="{name}" maxlength="200"', page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
