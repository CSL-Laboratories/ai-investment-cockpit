"""Item 5 verifier (Rule CA): the app-side paper panel wiring of #318 (CSL-736).

Lucas, Nov 11, 12:52am PST: "Lift the #318 hold and have new app/ code wire in #318 and #319".
Paper only: PRODUCT.md 5.2 / CP0-v2 (pin d84cdd26, CSLR-D-012). These tests check that the app
module ``app/orchestrator/paper_panel.py`` serves only the one read-only paper panel, refuses
any engine panel that is not that panel, never offers an order, cancel or Stop verb or a
non-paper venue, and drops other venues' data, including data nested in a paper row.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[3] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import engine_path  # noqa: E402
from orchestrator import paper_panel as pp  # noqa: E402
from orchestrator import recursion_control as rc  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from orchestrator.auth import ApiKeyAuth  # noqa: E402

from cockpit.gui import panels as gp  # noqa: E402

R1 = "PAPER (SIMULATED) \u2014 NOT REAL MONEY"
OTHER_VENUES = ("kalshi_demo", "kalshi", "coinbase", "gemini")


class Rigged(str):
    """A str subclass that claims to equal anything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = str.__hash__


def _good() -> dict:
    return gp.registry()["panels"][0]


def _with(**changes: object) -> dict:
    panel = _good()
    panel.update(changes)
    return panel


def _registry_returning(panels: object):
    return mock.patch.object(gp, "registry", lambda: {"required": {}, "panels": panels})


def _allow() -> None:
    return None


AUTH = [Depends(_allow)]


def _mixed_read(command: str) -> dict:
    return {
        "status_code": "ok",
        "visibility": "operator",
        "result": [
            {"venue": "paper", "instrument_id": "MSFT", "qty": "3",
             "evidence": {"venue": "kalshi_demo", "yes_price": "0.42"},
             "kalshi_yes_price": "0.42", "paper_cash_minor": 100,
             "detail": {"note": "paper fill", "price": "410.00"}},
            {"venue": "stock", "instrument_id": "AAPL", "qty": "1"},
            {"venue": "kalshi_demo", "ticker": "KXDEMO-1", "yes_price": "0.42"},
            {"venue": "coinbase", "instrument": "BTC-USD", "price": "1"},
            {"venue": "gemini", "instrument": "ETH-USD", "price": "1"},
            {"ticker": "UNLABELLED", "price": "9"},
        ],
        "errors": [{"code": "stale_quote", "message": "paper book stale"}],
        "stale": True,
        "unreconciled": [],
        "coinbase_balance": "5",
        "switch_states": {"kalshi.model_input": "on"},
        "command": command,
    }


def _venues(value: object) -> set:
    found: set = set()
    if isinstance(value, dict):
        if "venue" in value:
            found.add(value["venue"])
        for item in value.values():
            found |= _venues(item)
    elif isinstance(value, list):
        for item in value:
            found |= _venues(item)
    return found


def _keys(value: object) -> set:
    found: set = set()
    if isinstance(value, dict):
        found |= set(value)
        for item in value.values():
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


class PaperPanelCheckTests(unittest.TestCase):
    def test_the_engine_panel_is_the_one_paper_panel(self) -> None:
        panel = pp.paper_panel()
        self.assertEqual(panel["id"], "cockpit.paper_own_trading")
        self.assertIn(panel["venue"], gp.PAPER_BOOK)
        self.assertEqual(panel["venue_use"], "paper:own_trading")
        self.assertTrue(panel["title"].startswith(R1))
        self.assertIs(panel["withhold_venue_data"], False)
        for read in panel["reads"]:
            self.assertRegex(read, r"^operator\.[a-z_]+\.inspect$")
        with self.assertRaises(TypeError):
            panel["venue"] = "kalshi_demo"  # type: ignore[index]

    def test_a_non_paper_panel_refuses_the_whole_panel(self) -> None:
        cases = {
            "kalshi panel exported too": [_good(), dict(gp.KALSHI_PANELS[0], reads=["operator.evidence.inspect"])],
            "no panel": [],
            "not a list": (_good(),),
            "two copies": [_good(), _good()],
        }
        for venue in OTHER_VENUES + ("paper_live", "", "STOCK"):
            cases[f"venue {venue!r}"] = [_with(venue=venue)]
        cases.update({
            "venue not a str": [_with(venue=["stock"])],
            "venue rigged str": [_with(venue=Rigged("kalshi_demo"))],
            "id rigged str": [_with(id=Rigged("cockpit.kalshi_model_fed_read"))],
            "other id": [_with(id="cockpit.kalshi_model_fed_read")],
            "use kalshi": [_with(venue_use="kalshi:model_fed_read")],
            "use rigged": [_with(venue_use=Rigged("kalshi:model_fed_propose"))],
            "behind a switch": [_with(withhold_venue_data=True)],
            "withhold falsy not False": [_with(withhold_venue_data=0)],
            "no R1 title": [_with(title="paper own trading")],
            "extra order key": [_with(order_verb="operator.orders.submit")],
            "missing reads": [{k: v for k, v in _good().items() if k != "reads"}],
            "empty reads": [_with(reads=[])],
            "repeated read": [_with(reads=["operator.positions.inspect"] * 2)],
        })
        for verb in ("operator.orders.submit", "operator.stop", "operator.interventions.pause",
                     "operator.positions.inspect.submit", "paper.orders.inspect", "operator..inspect",
                     "operator.positions.Inspect", "operator.kill switch.inspect", 7, None):
            cases[f"read {verb!r}"] = [_with(reads=["operator.positions.inspect", verb])]
        for name, panels in cases.items():
            with self.subTest(name), _registry_returning(panels):
                with self.assertRaises(pp.PanelRefused):
                    pp.paper_panel()
                with self.assertRaises(pp.PanelRefused):
                    pp.build_paper_panel_router(lambda c: {}, dependencies=AUTH)
                with self.assertRaises(pp.PanelRefused):
                    pp.render_paper_panel(lambda c: {})


class PaperPanelRenderTests(unittest.TestCase):
    def test_other_venues_never_reach_the_panel(self) -> None:
        view = pp.render_paper_panel(_mixed_read)
        self.assertIs(view["paper_only"], True)
        self.assertTrue(view["title"].startswith(R1))
        for command, data in view["reads"].items():
            with self.subTest(command):
                self.assertLessEqual(_venues(data), gp.PAPER_BOOK)
                self.assertEqual(_venues(data), {"paper", "stock"})
                keys = _keys(data)
                for venue in OTHER_VENUES:
                    self.assertFalse(any(k.startswith(venue + "_") for k in keys), keys)
                self.assertNotIn("yes_price", keys)  # nested Kalshi evidence in the paper row
                rows = {r["instrument_id"] for r in data["result"]}
                self.assertEqual(rows, {"MSFT", "AAPL"})
                self.assertEqual(data["status_code"], "ok")
                self.assertIs(data["stale"], True)
                self.assertEqual(data["errors"], [{"code": "stale_quote", "message": "paper book stale"}])
                msft = next(r for r in data["result"] if r["instrument_id"] == "MSFT")
                self.assertEqual(msft["paper_cash_minor"], 100)
                self.assertNotIn("detail", msft)  # unlabelled venue-like child of a kept row

    def test_a_payload_cannot_switch_kalshi_on(self) -> None:
        view = pp.render_paper_panel(_mixed_read)
        dumped = repr(view)
        self.assertNotIn("KXDEMO-1", dumped)
        self.assertNotIn("0.42", dumped)

    def test_a_failed_read_is_shown_not_hidden(self) -> None:
        def read(command: str) -> dict:
            if command == "operator.budgets.inspect":
                raise ConnectionError("down")
            return {"status_code": "ok", "result": []}
        view = pp.render_paper_panel(read)
        self.assertEqual(view["reads"]["operator.budgets.inspect"]["status_code"], "read_failed")
        self.assertEqual(view["reads"]["operator.budgets.inspect"]["errors"][0]["message"], "ConnectionError")
        self.assertEqual(view["reads"]["operator.positions.inspect"], {"status_code": "ok", "result": []})

    def test_a_caller_cannot_render_another_panel(self) -> None:
        with self.assertRaises(TypeError):
            pp.render_paper_panel(_mixed_read, gp.KALSHI_PANELS[0])  # type: ignore[call-arg]

    def test_non_dict_containers_are_normalised_before_the_filter(self) -> None:
        from collections import OrderedDict

        def read(command: str) -> object:
            if command == "operator.positions.inspect":
                return {"result": ({"venue": "kalshi_demo", "ticker": "KXDEMO-1"}, {"venue": "paper", "qty": "1"})}
            if command == "operator.decisions.inspect":
                return OrderedDict(result=[OrderedDict(venue="coinbase", price="1")])
            return {"result": [types.SimpleNamespace(venue="kalshi_demo")]}
        view = pp.render_paper_panel(read)["reads"]
        self.assertEqual(view["operator.positions.inspect"], {"result": [{"venue": "paper", "qty": "1"}]})
        self.assertEqual(view["operator.decisions.inspect"], {"result": []})
        self.assertEqual(view["operator.budgets.inspect"]["status_code"], "read_failed")
        self.assertNotIn("kalshi", repr(view))

    def test_only_the_named_inspect_reads_are_called(self) -> None:
        calls: list = []
        pp.render_paper_panel(lambda c: calls.append(c) or {})
        self.assertEqual(calls, list(pp.paper_panel()["reads"]))


class PaperPanelRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list = []

        def read(command: str) -> dict:
            self.calls.append(command)
            return _mixed_read(command)

        self.router = pp.build_paper_panel_router(read, dependencies=AUTH)
        app = FastAPI()
        app.include_router(self.router)
        self.client = TestClient(app)

    def test_the_router_has_get_routes_only(self) -> None:
        routes = list(self.router.routes)
        self.assertEqual(sorted(r.path for r in routes), ["/panels", "/panels/{panel_id}"])
        for route in routes:
            self.assertEqual(route.methods, {"GET"})
        for path in ("/panels", "/panels/cockpit.paper_own_trading"):
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path).status_code, 405)

    def test_listing_offers_only_the_paper_panel(self) -> None:
        body = self.client.get("/panels").json()
        self.assertEqual([p["id"] for p in body["panels"]], ["cockpit.paper_own_trading"])
        self.assertEqual(body["panels"][0]["venue_use"], "paper:own_trading")
        self.assertIs(body["panels"][0]["paper_only"], True)
        self.assertEqual(self.calls, [])

    def test_other_panels_are_404_and_query_params_change_nothing(self) -> None:
        for panel_id in ("cockpit.kalshi_model_fed_read", "cockpit.kalshi_model_fed_propose", "kalshi", "x"):
            with self.subTest(panel_id):
                self.assertEqual(self.client.get(f"/panels/{panel_id}").status_code, 404)
        self.assertEqual(self.calls, [])
        plain = self.client.get("/panels/cockpit.paper_own_trading").json()
        rigged = self.client.get("/panels/cockpit.paper_own_trading",
                                 params={"venue": "kalshi_demo", "kalshi.model_input": "on",
                                         "switch_states": "on"}).json()
        self.assertEqual(plain, rigged)
        for data in rigged["reads"].values():
            self.assertEqual(_venues(data), {"paper", "stock"})

    def test_a_mount_without_auth_is_refused(self) -> None:
        for deps in ((), [], "x"):
            with self.subTest(deps=deps), self.assertRaises(pp.PanelRefused):
                pp.build_paper_panel_router(_mixed_read, dependencies=deps)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            pp.build_paper_panel_router(_mixed_read)  # type: ignore[call-arg]

    def test_auth_dependencies_are_applied(self) -> None:
        from fastapi import HTTPException

        def deny() -> None:
            raise HTTPException(status_code=401)

        app = FastAPI()
        app.include_router(pp.build_paper_panel_router(_mixed_read, dependencies=[Depends(deny)]))
        client = TestClient(app)
        self.assertEqual(client.get("/panels").status_code, 401)
        self.assertEqual(client.get("/panels/cockpit.paper_own_trading").status_code, 401)

    def test_a_non_callable_read_refuses_the_mount(self) -> None:
        with self.assertRaises(pp.PanelRefused):
            pp.build_paper_panel_router(None, dependencies=AUTH)  # type: ignore[arg-type]


class EngineImportTests(unittest.TestCase):
    def test_only_the_panel_and_keys_packages_are_importable(self) -> None:
        for name in ("cockpit", "cockpit.paper", "cockpit.paper.gateway", "cockpit.guix", "os", "", None):
            with self.subTest(name):
                with self.assertRaises(engine_path.EngineImportRefused):
                    engine_path.import_engine(name)  # type: ignore[arg-type]
        self.assertIs(engine_path.import_engine("cockpit.gui.panels"), gp)

    def test_a_cockpit_package_from_elsewhere_is_refused(self) -> None:
        fake = types.ModuleType("cockpit")
        fake.__file__ = "/elsewhere/cockpit/__init__.py"
        with mock.patch.dict(sys.modules, {"cockpit": fake}):
            with self.assertRaises(engine_path.EngineImportRefused):
                engine_path.import_engine("cockpit.gui.panels")

    def test_a_foreign_cockpit_on_the_path_is_refused_before_it_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "cockpit"
            (pkg / "gui").mkdir(parents=True)
            marker = Path(tmp) / "ran"
            (pkg / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
            (pkg / "gui" / "__init__.py").write_text("")
            saved = {k: v for k, v in sys.modules.items() if k == "cockpit" or k.startswith("cockpit.")}
            for key in saved:
                del sys.modules[key]
            sys.path.insert(0, tmp)
            try:
                with self.assertRaises(engine_path.EngineImportRefused):
                    engine_path.import_engine("cockpit.gui.panels")
                self.assertFalse(marker.exists())
            finally:
                sys.path.remove(tmp)
                for key in [k for k in sys.modules if k == "cockpit" or k.startswith("cockpit.")]:
                    del sys.modules[key]
                sys.modules.update(saved)

    def test_a_foreign_subpackage_already_loaded_is_refused(self) -> None:
        fake = types.ModuleType("cockpit.gui")
        fake.__file__ = "/elsewhere/cockpit/gui/__init__.py"
        with mock.patch.dict(sys.modules, {"cockpit.gui": fake}):
            with self.assertRaises(engine_path.EngineImportRefused):
                engine_path.import_engine("cockpit.gui.panels")

    def test_engine_is_appended_not_prepended(self) -> None:
        entry = str(engine_path.ENGINE_DIR)
        engine_path.import_engine("cockpit.gui")
        app_core = sys.modules["core"].__file__
        self.assertTrue(Path(app_core).resolve().is_relative_to(APP_DIR.resolve()))
        self.assertIn(entry, sys.path)


class NestedVenueDataTests(unittest.TestCase):
    """#318 review 6752409146 finding 1: a kept paper row must not carry Kalshi data."""

    def test_nested_other_venue_data_in_a_paper_row_is_dropped(self) -> None:
        row = {"venue": "paper", "instrument_id": "MSFT", "evidence": {"venue": "kalshi_demo", "yes_price": "0.42"},
               "kalshi_yes_price": "0.42", "lots": [{"venue": "coinbase", "price": "1"}, {"qty": "1", "price": "2"}]}
        kept = gp.keep_venue_rows({"result": [row]}, "stock")
        self.assertEqual(kept, {"result": [{"venue": "paper", "instrument_id": "MSFT", "lots": []}]})

    def test_unlabelled_venue_like_children_of_a_kept_row_are_dropped(self) -> None:
        """Lucas, Nov 11 2:30am PST (Q4): drop them; they cannot be attributed to the paper book."""
        row = {"venue": "paper", "instrument_id": "MSFT", "qty": "3",
               "evidence": {"ticker": "KXDEMO-1", "yes_price": "0.42"},
               "quotes": [{"symbol": "MSFT", "bid": "1"}, {"note": "kept"}],
               "fill": {"filled_at": "t", "detail": {"market": "x"}},
               "status": {"stale": True}}
        kept = gp.keep_venue_rows({"result": [row]}, "stock")
        self.assertEqual(kept, {"result": [{"venue": "paper", "instrument_id": "MSFT", "qty": "3",
                                            "quotes": [{"note": "kept"}], "fill": {"filled_at": "t"},
                                            "status": {"stale": True}}]})
        for key in gp.VENUE_ROW_KEYS:
            with self.subTest(key=key):
                nested = gp.keep_venue_rows({"result": [{"venue": "stock", "child": {key: "1"}}]}, "stock")
                self.assertEqual(nested, {"result": [{"venue": "stock"}]})

    def test_a_key_named_for_another_venue_is_dropped(self) -> None:
        row = {"venue": "paper", "instrument_id": "MSFT", "kalshi": {"yes_price": "0.42"}, "coinbase": [1],
               "stock": {"qty": "1"}, "paper": {"qty": "2"}}
        kept = gp.keep_venue_rows({"result": [row], "gemini": {"x": 1}}, "stock")
        self.assertEqual(kept, {"result": [{"venue": "paper", "instrument_id": "MSFT",
                                            "stock": {"qty": "1"}, "paper": {"qty": "2"}}]})

    def test_an_errors_item_labelled_paper_loses_its_kalshi_price(self) -> None:
        kept = gp.keep_venue_rows({"errors": [{"venue": "paper", "code": "stale_quote", "kalshi_yes_price": "0.42"}]},
                                  "stock")
        self.assertEqual(kept, {"errors": [{"venue": "paper", "code": "stale_quote"}]})


class _ControlSpy:
    """The recursion #327 ControlAPI shape (``call(method, params) -> dict`` with ``code``)."""

    def __init__(self, serve_o7: bool) -> None:
        self.serve_o7, self.calls = serve_o7, []

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method.startswith("run."):
            return {"code": "ok", "runs": []}
        if not self.serve_o7:
            return {"code": "method_unknown", "detail": method}  # #327 at 1580d103 for operator.*
        return _mixed_read(method)


class ControlApiReaderTests(unittest.TestCase):
    def test_only_inspect_reads_are_sent(self) -> None:
        spy = _ControlSpy(serve_o7=True)
        reader = rc.ControlApiPanelReader(spy)
        for method in ("run.stop", "run.start", "run.list", "run.status", "operator.interventions.request_stop",
                       "operator.interventions.request_revocation", "operator.audit.export", "operator.export.bundle",
                       "operator.positions.inspect.x", "operator.\u00e9.inspect", "operator.Positions.inspect",
                       "operator..inspect", "", None, 3):
            with self.subTest(method=method), self.assertRaises(rc.ReadRefused):
                reader(method)  # type: ignore[arg-type]
        self.assertEqual(spy.calls, [])
        reader("operator.positions.inspect")
        self.assertEqual(spy.calls, [("operator.positions.inspect", {})])

    def test_answers_stay_visible(self) -> None:
        self.assertEqual(rc.ControlApiPanelReader(None)("operator.positions.inspect"),
                         {"status_code": "control_api_unavailable"})
        unknown = rc.ControlApiPanelReader(_ControlSpy(serve_o7=False))("operator.positions.inspect")
        self.assertEqual(unknown, {"code": "method_unknown", "detail": "operator.positions.inspect",
                                   "status_code": "method_unknown"})
        masked = types.SimpleNamespace(call=lambda m, p: {"code": "method_unknown", "status_code": "ok"})
        self.assertEqual(rc.ControlApiPanelReader(masked)("operator.positions.inspect")["status_code"],
                         "method_unknown")
        stale = types.SimpleNamespace(call=lambda m, p: {"code": "ok", "status_code": "stale_quote"})
        self.assertEqual(rc.ControlApiPanelReader(stale)("operator.positions.inspect")["status_code"], "stale_quote")
        odd = types.SimpleNamespace(call=lambda m, p: ["x"])
        self.assertEqual(rc.ControlApiPanelReader(odd)("operator.budgets.inspect")["status_code"], "read_failed")
        with self.assertRaises(TypeError):
            rc.ControlApiPanelReader(object())
        self.assertEqual(rc.CONTROL_API_PIN, "recursion#327@1580d103fb7c0f5983b9fd67d89892f28f215c78")


class CreateAppMountTests(unittest.TestCase):
    """The mount lines in app/orchestrator/app.py create_app (item 5, Lucas Nov 11 2:30am PST)."""

    KEY = "item5-smoke-key"

    def _client(self, control):
        state = types.SimpleNamespace(settings=types.SimpleNamespace(watchlist_symbols=frozenset()))
        app = create_app(state, auth=ApiKeyAuth(key_provider=lambda: self.KEY), paper_panel_control=control)
        return TestClient(app)

    def test_the_panel_is_mounted_behind_the_key_and_read_only(self) -> None:
        spy = _ControlSpy(serve_o7=True)
        client = self._client(spy)
        path = "/panels/cockpit.paper_own_trading"
        self.assertEqual(client.get(path).status_code, 401)
        self.assertEqual(client.get(path, headers={"X-API-Key": "wrong"}).status_code, 401)
        ok = client.get(path, headers={"X-API-Key": self.KEY})
        self.assertEqual(ok.status_code, 200)
        for data in ok.json()["reads"].values():
            self.assertEqual(_venues(data), {"paper", "stock"})
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(client, method)(path, headers={"X-API-Key": self.KEY}).status_code, 405)
        self.assertEqual(client.get("/panels/cockpit.kalshi_model_fed_read",
                                    headers={"X-API-Key": self.KEY}).status_code, 404)
        self.assertEqual({m for m, _ in spy.calls}, set(pp.paper_panel()["reads"]))
        # Plain routes, so app code that walks app.routes by path keeps working.
        panel_routes = {r.path: r.methods for r in client.app.routes if r.path.startswith("/panels")}
        self.assertEqual(panel_routes, {"/panels": {"GET"}, "/panels/{panel_id}": {"GET"}})

    def test_without_a_control_api_the_panel_says_so(self) -> None:
        body = self._client(None).get("/panels/cockpit.paper_own_trading", headers={"X-API-Key": self.KEY}).json()
        self.assertEqual({v["status_code"] for v in body["reads"].values()}, {"control_api_unavailable"})


class ComposeMountTests(unittest.TestCase):
    """The mount line in app/main.py compose(): the control API passes through to the panel."""

    def test_compose_passes_the_control_api_to_the_panel(self) -> None:
        import keyring
        from keyring.backend import KeyringBackend

        from core.secrets import set_secret
        from main import compose
        from orchestrator.auth import API_KEY_SECRET_NAME

        class _InMemory(KeyringBackend):
            priority = 1  # type: ignore[assignment]

            def __init__(self) -> None:
                super().__init__()
                self.store: dict = {}

            def get_password(self, service, username):
                return self.store.get((service, username))

            def set_password(self, service, username, password):
                self.store[(service, username)] = password

            def delete_password(self, service, username):
                self.store.pop((service, username), None)

        previous, cwd = keyring.get_keyring(), os.getcwd()
        keyring.set_keyring(_InMemory())
        spy = _ControlSpy(serve_o7=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"COCKPIT_HALT_PATH": os.path.join(tmp, "HALT")}):
                    _, app, _ = compose(db_url=f"sqlite:///{Path(tmp, 'c.db').as_posix()}",
                                        parquet_root=os.path.join(tmp, "raw"), paper_panel_control=spy)
                    set_secret(API_KEY_SECRET_NAME, self.KEY)
                    client = TestClient(app)
                    path = "/panels/cockpit.paper_own_trading"
                    self.assertEqual(client.get(path).status_code, 401)
                    self.assertEqual(client.post(path, headers={"X-API-Key": self.KEY}).status_code, 405)
                    body = client.get(path, headers={"X-API-Key": self.KEY}).json()
                    self.assertEqual({v["status_code"] for v in body["reads"].values()}, {"ok"})
                    self.assertEqual({m for m, _ in spy.calls}, set(pp.paper_panel()["reads"]))
            finally:
                os.chdir(cwd)
                keyring.set_keyring(previous)

    KEY = "item5-compose-key"


if __name__ == "__main__":
    unittest.main()
