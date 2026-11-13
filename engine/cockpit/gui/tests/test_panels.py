"""Item 10 Cockpit plugin: venue-class mapping and panel registry only.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/gui/tests -t engine -v``.
The shared page is Recursion's and is tested there. Plain data; no socket, provider or venue.
"""

from __future__ import annotations

import importlib
import json
import re
import unittest
from pathlib import Path

import cockpit.gui
from cockpit.gui.panels import (DEFAULT_SWITCH_STATES, KALSHI_PANELS, KNOWN_VENUES, O7_VENUE, OWN_TRADING_VENUES,
                                PANELS, PAPER_BOOK, R1_PAPER, REQUIRED, keep_venue_rows, panel_switch, redact_panel,
                                registry, withhold_venue_data)

ALL_PANELS = PANELS + KALSHI_PANELS

GUI_DIR = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
ORACLE = REPO / "docs" / "replacement" / "outcomes" / "O7" / "fixtures" / "oracle" / "responses.json"
# The O7 v0 read commands a panel may name (interventions and Stop are the shared page's).
O7_READS = frozenset("operator." + name for name in (
    "evidence.inspect", "strategies.inspect", "positions.inspect", "decisions.inspect",
    "uncertainty.inspect", "health.inspect", "budgets.inspect", "profiles.inspect",
    "audit.export", "recovery.inspect", "export.bundle"))


class RequiredTests(unittest.TestCase):
    def test_required_is_exactly_the_cockpit_venue_classes(self) -> None:
        self.assertEqual(dict(REQUIRED), {
            "venue_use": ("paper:own_trading", "kalshi:model_fed_read", "kalshi:model_fed_propose")})

    def test_required_is_read_only(self) -> None:
        with self.assertRaises(TypeError):
            REQUIRED["engine"] = ("codex",)  # type: ignore[index]


class PanelTests(unittest.TestCase):
    def test_panels_have_unique_ids_titles_and_reads(self) -> None:
        ids = [p["id"] for p in ALL_PANELS]
        self.assertEqual(len(ids), len(set(ids)))
        for panel in ALL_PANELS:
            with self.subTest(panel=panel["id"]):
                self.assertRegex(panel["id"], r"^cockpit\.[a-z0-9_]+$")
                self.assertTrue(panel["title"].strip())
                self.assertTrue(panel["reads"])
                # Exactly the keys Recursion #330's panel_registry accepts.
                self.assertEqual(set(panel), {"id", "title", "venue", "venue_use", "reads", "withhold_venue_data"})

    def test_every_required_venue_use_has_one_defined_panel(self) -> None:
        self.assertEqual(sorted(p["venue_use"] for p in ALL_PANELS), sorted(REQUIRED["venue_use"]))

    def test_panels_declare_the_post_read_venue_filter(self) -> None:
        # Declared venues are O7 venue ids (the row labels O7 reads return), not Cockpit's
        # venue-class names; venue_use keeps the Cockpit class.
        self.assertEqual(dict(O7_VENUE), {"paper": "stock", "kalshi": "kalshi_demo"})
        expected = {"cockpit.paper_own_trading": "stock",
                    "cockpit.kalshi_model_fed_read": "kalshi_demo",
                    "cockpit.kalshi_model_fed_propose": "kalshi_demo"}
        self.assertEqual({p["id"]: p["venue"] for p in ALL_PANELS}, expected)
        self.assertEqual({p["id"]: p["venue"] for p in registry()["panels"]}, {"cockpit.paper_own_trading": "stock"})
        for panel in ALL_PANELS:
            self.assertEqual(O7_VENUE[panel["venue_use"].split(":", 1)[0]], panel["venue"])

    def test_panels_only_read(self) -> None:
        for panel in ALL_PANELS:
            with self.subTest(panel=panel["id"]):
                self.assertLessEqual(set(panel["reads"]), O7_READS)
                for read in panel["reads"]:
                    self.assertNotRegex(read, r"intervention|stop|order|approve|size")

    def test_kalshi_panels_withhold_venue_data(self) -> None:
        for panel in ALL_PANELS:
            with self.subTest(panel=panel["id"]):
                self.assertEqual(panel["withhold_venue_data"], panel["venue"] == O7_VENUE["kalshi"])

    def test_registry_exports_no_kalshi_panel(self) -> None:
        # No switch-aware consumer on the Nov 11 path yet (Recursion #330 compose does not
        # call redact_panel), so Kalshi panels stay defined but are not handed to the daemon.
        panels = registry()["panels"]
        self.assertTrue(panels)
        for panel in panels:
            self.assertNotIn("kalshi", repr(panel).lower())
        self.assertEqual([p["id"] for p in panels], ["cockpit.paper_own_trading"])

    def test_registry_is_a_plain_copy(self) -> None:
        data = registry()
        self.assertEqual(data["required"], {"venue_use": list(REQUIRED["venue_use"])})
        self.assertEqual([p["id"] for p in data["panels"]], [p["id"] for p in PANELS])
        data["panels"][0]["reads"].append("operator.health.inspect")
        self.assertNotIn("operator.health.inspect", PANELS[0]["reads"])


# A mixed read result: a Kalshi row, an unlabelled total and provenance, a paper row, and
# keys that try to switch the payload on from inside.
SMUGGLED = {"status_code": "ok", "kalshi_yes_price": "4242.17", "venue_data_switch": "on",
            "result": {"rows": [{"venue": "kalshi_demo", "ticker": "KXSECRET", "venue_data_switch": "on",
                                 "venue_data": {"price": "4242.17"}},
                                {"venue": "stock", "symbol": "PAPER-OK", "qty": 3}],
                       "total_usd": "10.00",
                       "provenance": {"venue_data": "KXSECRET", "venue_data_switch": "on"}}}
KALSHI_ONLY = {"status_code": "ok", "result": {"rows": [{"venue": "kalshi_demo", "ticker": "KXSHOWN"}]}}
OFF_STATES = (DEFAULT_SWITCH_STATES, {}, None, "on", ["kalshi.model_input"],
              {"kalshi.model_input": "off"}, {"kalshi.model_input": True}, {"kalshi.model_input": "ON"},
              {"kalshi.model_input": "on "}, {"kalshi:model_fed_read": "on", "kalshi:model_fed_propose": "on"},
              {"venue_data_switch": "on", "kalshi": "on"})


def mount(panels, switch_states: object, data: object = SMUGGLED) -> dict:
    """Redact each panel's read results with the caller's switch state."""
    return {p["id"]: redact_panel(p, {r: data for r in p["reads"]}, switch_states) for p in panels}


class VenueDataTests(unittest.TestCase):
    def test_kalshi_panels_share_the_contract_switch(self) -> None:
        self.assertEqual({p["id"]: panel_switch(p) for p in ALL_PANELS},
                         {"cockpit.paper_own_trading": None,
                          "cockpit.kalshi_model_fed_read": "kalshi.model_input",
                          "cockpit.kalshi_model_fed_propose": "kalshi.model_input"})

    def test_paper_panel_shows_no_kalshi_row_with_switches_off(self) -> None:
        for states in OFF_STATES + ({"kalshi.model_input": "on"},):
            with self.subTest(states=states):
                for view in mount(registry()["panels"], states)["cockpit.paper_own_trading"].values():
                    shown = repr(view)
                    self.assertNotIn("kalshi", shown.lower())
                    self.assertNotIn("4242.17", shown)
                    self.assertNotIn("KXSECRET", shown)
                    self.assertEqual(view, {"status_code": "ok", "venue_data_switch": "on",
                                            "result": {"rows": [{"venue": "stock", "symbol": "PAPER-OK", "qty": 3}],
                                                       "total_usd": "10.00"}})

    def test_kalshi_panels_show_no_venue_data_with_switch_off(self) -> None:
        for states in OFF_STATES:
            views = mount(KALSHI_PANELS, states)
            for pid, panel_views in views.items():
                with self.subTest(states=states, panel=pid):
                    self.assertNotIn("4242.17", repr(panel_views))
                    self.assertNotIn("KXSECRET", repr(panel_views))
                    for view in panel_views.values():
                        self.assertEqual(set(view), {"venue_data", "status_code"})
                        self.assertIn("[withheld:", view["venue_data"])

    def test_contract_switch_on_shows_only_kalshi_rows(self) -> None:
        for panel_views in mount(KALSHI_PANELS, {"kalshi.model_input": "on"}).values():
            for view in panel_views.values():
                self.assertIn("KXSECRET", repr(view))
                self.assertNotIn("PAPER-OK", repr(view))
                self.assertNotIn("provenance", view["result"])  # venue_data with no venue label

    def test_o7_states_stay_visible_on_the_paper_panel(self) -> None:
        # Red-team round 3 probe: refusal, stale, unreconciled, uncertainty and empty results
        # must not collapse to a bare "ok" (PRODUCT.md 3.5 "Nothing is hidden"; 5.4).
        budgets = {"status_code": "ok", "result": {"stale": True, "unreconciled": True,
                                                   "refusal": "daily loss limit reached",
                                                   "remaining_usd": "0.00", "uncertainty": "high"}}
        positions = {"status_code": "ok", "result": {"rows": [], "empty_reason": "no fills yet"}}
        decisions = {"status_code": "refused", "errors": [{"code": "stale_read"}], "result": {}}
        panel = registry()["panels"][0]
        for states in OFF_STATES:
            with self.subTest(states=states):
                shown = redact_panel(panel, {"operator.budgets.inspect": budgets,
                                             "operator.positions.inspect": positions,
                                             "operator.decisions.inspect": decisions}, states)
                self.assertEqual(shown, {"operator.budgets.inspect": budgets,
                                         "operator.positions.inspect": positions,
                                         "operator.decisions.inspect": decisions})

    def test_only_other_venue_and_unattributable_rows_are_dropped(self) -> None:
        mixed = {"status_code": "ok", "stale": True,
                 "result": {"rows": [{"venue": "kalshi", "ticker": "KXSECRET"},
                                     {"ticker": "KXUNLABELLED", "price": "0.42"},
                                     {"venue": "paper", "symbol": "PAPER-OK"},
                                     {"qty": 1, "note": "unlabelled non-venue row"}],
                            "kalshi_yes_price": "4242.17", "summary": {"market": "KXNEST"},
                            "n": 4, "uncertainty": "low", "empty_reason": None}}
        self.assertEqual(keep_venue_rows(mixed, "paper"), {
            "status_code": "ok", "stale": True,
            "result": {"rows": [{"venue": "paper", "symbol": "PAPER-OK"},
                                {"qty": 1, "note": "unlabelled non-venue row"}],
                       "n": 4, "uncertainty": "low", "empty_reason": None}})
        kalshi = keep_venue_rows(mixed, "kalshi")
        self.assertEqual(kalshi["result"]["rows"], [{"venue": "kalshi", "ticker": "KXSECRET"},
                                                    {"qty": 1, "note": "unlabelled non-venue row"}])
        self.assertEqual(kalshi["result"]["kalshi_yes_price"], "4242.17")
        self.assertNotIn("KXUNLABELLED", repr(kalshi))
        self.assertNotIn("KX", repr(keep_venue_rows(mixed, None)))
        self.assertEqual(keep_venue_rows({"ticker": "KX", "status_code": "ok"}, "paper"), {"status_code": "ok"})
        self.assertEqual(keep_venue_rows([], "paper"), [])

    def test_payload_cannot_switch_itself_on(self) -> None:
        self.assertNotIn("4242.17", repr(withhold_venue_data(SMUGGLED, "kalshi.model_input", {})))
        self.assertNotIn("4242.17", repr(withhold_venue_data([SMUGGLED], "kalshi.model_input",
                                                             {"venue_data_switch": "on"})))
        self.assertIs(withhold_venue_data(SMUGGLED, None, {}), SMUGGLED)

    def test_panel_without_a_switch_fails_closed(self) -> None:
        for panel in ({"venue": "kalshi", "withhold_venue_data": False}, {"venue": "coinbase"},
                      {"venue": "kalshi_demo", "withhold_venue_data": False}, {"venue": "gemini"},
                      {"venue": "paper", "withhold_venue_data": False},
                      {"venue": "stock", "withhold_venue_data": "no"}, {"venue": None}, {}):
            with self.subTest(panel=panel):
                self.assertIsNotNone(panel_switch(panel))
                shown = redact_panel(panel, {"operator.evidence.inspect": SMUGGLED},
                                     {"None": "on", "": "on", "kalshi": "on", "coinbase": "on", "paper": "on",
                                      "gemini": "on", "kalshi_demo": "on", "stock": "on"})
                self.assertNotIn("4242.17", repr(shown))
                self.assertNotIn("PAPER-OK", repr(shown))


def _oracle_positions() -> list:
    data = json.loads(ORACLE.read_text(encoding="utf-8"))
    return [r for r in data["responses"] if r["command"] == "operator.positions.inspect"]


def _venue_rows(view: object) -> list:
    return [row for row in view.get("result", {}).get("positions", [])] if type(view) is dict else []


class O7OracleTests(unittest.TestCase):
    """Panels against the O7 oracle fixture (PRODUCT.md 3.5 "Nothing is hidden")."""

    def test_oracle_has_positions_and_every_venue_is_known(self) -> None:
        responses = _oracle_positions()
        venues = {row["venue"] for r in responses for row in r["result"]["positions"]}
        self.assertEqual(venues, {"stock", "kalshi_demo"})
        self.assertLessEqual(venues, KNOWN_VENUES)
        self.assertLessEqual({p["venue"] for p in ALL_PANELS}, venues)

    def test_paper_panel_shows_every_oracle_stock_position(self) -> None:
        panel = registry()["panels"][0]
        shown_total = 0
        for response in _oracle_positions():
            for states in OFF_STATES + ({"kalshi.model_input": "on"},):
                with self.subTest(request=response["request_id"], states=states):
                    view = redact_panel(panel, {"operator.positions.inspect": response}, states)
                    shown = view["operator.positions.inspect"]
                    expected = [row for row in response["result"]["positions"] if row["venue"] == "stock"]
                    self.assertEqual(_venue_rows(shown), expected)
                    self.assertNotIn("kalshi_demo", repr(shown))
                    self.assertNotIn("KXDEMO", repr(shown))
                    # O7 status and visibility fields stay visible next to the rows.
                    self.assertEqual(shown["status_code"], response["status_code"])
                    self.assertEqual(shown["visibility"], response["visibility"])
                    self.assertEqual(shown["result"]["collateral"], response["result"]["collateral"])
            shown_total += len(expected)
        self.assertEqual(shown_total, 3)  # AAPL; AAPL and MSFT (signed); empty response shows []

    def test_either_paper_book_id_shows_every_oracle_stock_position(self) -> None:
        # "stock" and "paper" are one book (PaperBookTests), so neither id hides the oracle's
        # "stock" rows. Before the O7 mapping, declaring "paper" matched none of them.
        for response in _oracle_positions():
            expected = [row for row in response["result"]["positions"] if row["venue"] == "stock"]
            for declared in ("stock", "paper"):
                with self.subTest(request=response["request_id"], declared=declared):
                    self.assertEqual(_venue_rows(keep_venue_rows(response, declared)), expected)

    def test_kalshi_panels_show_only_kalshi_demo_rows_behind_the_switch(self) -> None:
        response = _oracle_positions()[0]
        panel = dict(KALSHI_PANELS[0], reads=("operator.positions.inspect",))
        on = redact_panel(panel, {"operator.positions.inspect": response}, {"kalshi.model_input": "on"})
        self.assertEqual([row["instrument_id"] for row in _venue_rows(on["operator.positions.inspect"])],
                         ["KXDEMO-1"])
        for states in OFF_STATES:
            with self.subTest(states=states):
                off = redact_panel(panel, {"operator.positions.inspect": response}, states)
                self.assertNotIn("KXDEMO", repr(off))
                self.assertNotIn("AAPL", repr(off))

    def test_gemini_keys_are_another_venue(self) -> None:
        view = keep_venue_rows({"status_code": "ok", "gemini_balance": "9.99", "coinbase_x": 1, "n": 1}, "stock")
        self.assertEqual(view, {"status_code": "ok", "n": 1})


def _positions(*rows: dict, **extra: object) -> dict:
    """The oracle's first positions response with its rows replaced and ``extra`` keys added."""
    response = json.loads(json.dumps(_oracle_positions()[0]))
    response["result"]["positions"] = [dict(row) for row in rows]
    response.update(extra)
    return response


class PaperBookTests(unittest.TestCase):
    """Lucas's Nov 9 call: "paper" rows, "stock" rows and paper_* keys are the paper panel's book.

    O7 v0 labels paper-book positions "stock"; the paper OrderGateway (#320) keeps venue
    "paper" on its orders. PRODUCT.md 3.5 "Nothing is hidden"; 5.2 paper first.
    """

    ALL_STATES = OFF_STATES + ({"kalshi.model_input": "on"},)
    PAPER_ROW = {"instrument_id": "MSFT", "venue": "paper", "quantity": "2", "currency": "USD"}
    STOCK_ROW = {"instrument_id": "AAPL", "venue": "stock", "quantity": "1", "currency": "USD"}
    KALSHI_ROW = {"instrument_id": "KXDEMO-1", "venue": "kalshi_demo", "quantity": "0", "currency": "USD"}

    def _shown(self, response: dict, states: object = DEFAULT_SWITCH_STATES) -> dict:
        panel = registry()["panels"][0]
        self.assertEqual(panel["id"], "cockpit.paper_own_trading")
        return redact_panel(panel, {"operator.positions.inspect": response}, states)["operator.positions.inspect"]

    def test_the_paper_book_is_paper_and_stock(self) -> None:
        self.assertEqual(PAPER_BOOK, frozenset({"paper", "stock"}))
        self.assertIn(registry()["panels"][0]["venue"], PAPER_BOOK)
        self.assertLessEqual(PAPER_BOOK, KNOWN_VENUES)
        for panel in KALSHI_PANELS:
            self.assertNotIn(panel["venue"], PAPER_BOOK)

    def test_a_paper_row_shows(self) -> None:
        for states in self.ALL_STATES:
            with self.subTest(states=states):
                self.assertEqual(_venue_rows(self._shown(_positions(self.PAPER_ROW), states)), [self.PAPER_ROW])

    def test_a_stock_row_shows(self) -> None:
        for states in self.ALL_STATES:
            with self.subTest(states=states):
                self.assertEqual(_venue_rows(self._shown(_positions(self.STOCK_ROW), states)), [self.STOCK_ROW])

    def test_paper_and_stock_rows_count_as_one_book(self) -> None:
        response = _positions(self.STOCK_ROW, self.KALSHI_ROW, self.PAPER_ROW)
        shown = self._shown(response)
        self.assertEqual(_venue_rows(shown), [self.STOCK_ROW, self.PAPER_ROW])
        # Everything but the Kalshi row is unchanged, and the response is not mutated.
        response["result"]["positions"].pop(1)
        self.assertEqual(shown, response)

    def test_a_paper_key_shows(self) -> None:
        response = _positions(self.PAPER_ROW, paper_cash_minor=100_000)
        response["result"]["paper_open_orders"] = 2
        response["result"]["stock_buying_power_minor"] = 50_000
        for states in self.ALL_STATES:
            with self.subTest(states=states):
                shown = self._shown(response, states)
                self.assertEqual(shown["paper_cash_minor"], 100_000)
                self.assertEqual(shown["result"]["paper_open_orders"], 2)
                self.assertEqual(shown["result"]["stock_buying_power_minor"], 50_000)
                self.assertEqual(shown, response)
        self.assertEqual(keep_venue_rows({"paper_cash_minor": 100, "status_code": "ok"}, "stock"),
                         {"paper_cash_minor": 100, "status_code": "ok"})

    def test_a_kalshi_demo_row_stays_out_of_the_paper_book(self) -> None:
        response = _positions(self.KALSHI_ROW, self.PAPER_ROW, {**self.KALSHI_ROW, "venue": "kalshi"},
                              {**self.KALSHI_ROW, "venue": "coinbase"}, {**self.KALSHI_ROW, "venue": "gemini"},
                              kalshi_demo_yes_price="4242.17", kalshi_yes_price="4242.17")
        for states in self.ALL_STATES:
            with self.subTest(states=states):
                shown = self._shown(response, states)
                self.assertEqual(_venue_rows(shown), [self.PAPER_ROW])
                self.assertNotIn("kalshi", repr(shown).lower())
                self.assertNotIn("KXDEMO", repr(shown))
                self.assertNotIn("4242.17", repr(shown))
        only_kalshi = self._shown(_positions(self.KALSHI_ROW))
        self.assertEqual(_venue_rows(only_kalshi), [])
        self.assertEqual(only_kalshi["status_code"], "ok")
        # A paper_* key is kept, but what sits under it is still filtered row by row.
        self.assertEqual(keep_venue_rows({"paper_rows": [self.KALSHI_ROW, self.PAPER_ROW]}, "stock"),
                         {"paper_rows": [self.PAPER_ROW]})

    def test_a_row_with_no_usable_label_is_not_in_the_book(self) -> None:
        # Near misses and non-string labels are dropped, never matched and never an error.
        for label in ("Paper", "STOCK", "paper ", "paper_", "papers", "", None, True, 1, ["paper"], {"paper": 1}):
            with self.subTest(label=label):
                rows = _venue_rows(self._shown(_positions({**self.PAPER_ROW, "venue": label}, self.STOCK_ROW)))
                self.assertEqual(rows, [self.STOCK_ROW])

    def test_kalshi_panels_do_not_get_the_paper_book(self) -> None:
        response = _positions(self.STOCK_ROW, self.KALSHI_ROW, self.PAPER_ROW, paper_cash_minor=100_000)
        for panel in KALSHI_PANELS:
            with self.subTest(panel=panel["id"]):
                on = redact_panel(panel, {"operator.positions.inspect": response}, {"kalshi.model_input": "on"})
                shown = on["operator.positions.inspect"]
                self.assertEqual(_venue_rows(shown), [self.KALSHI_ROW])
                self.assertNotIn("paper_cash_minor", shown)
                for states in OFF_STATES:
                    off = redact_panel(panel, {"operator.positions.inspect": response}, states)
                    self.assertNotIn("AAPL", repr(off))
                    self.assertNotIn("MSFT", repr(off))
                    self.assertNotIn("100000", repr(off))

    def test_unlabelled_errors_and_provenance_stay_visible(self) -> None:
        # Lucas, Nov 9: response-level errors and provenance with no venue label keep showing
        # on a venue panel. Values are not read as venue ids ("paper_book", "kalshi_demo").
        extra = {"status_code": "refused", "error": {"code": "stale_read", "message": "book read is stale"},
                 "errors": [{"code": "unreconciled", "detail": "kalshi_demo feed lagging"}],
                 "provenance": ["paper_book", {"source": "o7.operator.v0", "read_at": "2026-11-09T21:00:00Z"}]}
        response = _positions(self.PAPER_ROW, self.KALSHI_ROW, self.STOCK_ROW, **extra)
        for panel in (registry()["panels"][0],) + tuple(dict(p, reads=("operator.positions.inspect",))
                                                        for p in KALSHI_PANELS):
            with self.subTest(panel=panel["id"]):
                shown = redact_panel(panel, {"operator.positions.inspect": response},
                                     {"kalshi.model_input": "on"})["operator.positions.inspect"]
                for key, value in extra.items():
                    self.assertEqual(shown[key], value)
                self.assertEqual(shown["visibility"], response["visibility"])
        self.assertEqual(_venue_rows(self._shown(response)), [self.PAPER_ROW, self.STOCK_ROW])

    def test_only_the_o7_id_panel_is_outside_a_switch(self) -> None:
        # Counting "paper" rows does not make a panel declaring "paper" an own-trading panel.
        self.assertEqual(OWN_TRADING_VENUES, frozenset({"stock"}))
        self.assertIsNone(panel_switch(registry()["panels"][0]))
        self.assertIsNotNone(panel_switch({"venue": "paper", "withhold_venue_data": False}))


class BoundaryTests(unittest.TestCase):
    def test_daemon_plugin_flag_resolves(self) -> None:
        # The daemon is configured with --plugin cockpit.gui:registry (CSL-802 dry run).
        module, _, attr = "cockpit.gui:registry".partition(":")
        factory = getattr(importlib.import_module(module), attr)
        self.assertIs(factory, registry)
        self.assertEqual(factory(), registry())

    def test_paper_panel_title_carries_r1(self) -> None:
        # PRODUCT.md 5.4 R1, in the title so #330's fixed panel key set still accepts it.
        self.assertEqual(R1_PAPER, "PAPER (SIMULATED) \u2014 NOT REAL MONEY")
        for panel in registry()["panels"]:
            self.assertTrue(panel["title"].startswith(R1_PAPER), panel["title"])

    def test_no_shared_page_left_here(self) -> None:
        for name in ("client.py", "inventory.py", "surface.py"):
            self.assertFalse((GUI_DIR / name).exists(), name)
        self.assertFalse(hasattr(cockpit.gui, "Application"))

    def test_no_recursion_import_server_or_order_code(self) -> None:
        for path in GUI_DIR.rglob("*.py"):
            if path.parent.name == "tests":
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(GUI_DIR))):
                imports = "\n".join(re.findall(r"^\s*(?:import|from)\s+\S+", text, re.M))
                self.assertNotRegex(imports, r"recursion|paper|gateway|broker|order|wsgi|http|socket")
                self.assertNotIn("<html", text.lower())


if __name__ == "__main__":
    unittest.main()
