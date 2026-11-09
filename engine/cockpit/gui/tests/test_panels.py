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
from cockpit.gui.panels import (DEFAULT_SWITCH_STATES, KALSHI_PANELS, KNOWN_VENUES, O7_VENUE, PANELS, R1_PAPER,
                                REQUIRED, keep_venue_rows, panel_switch, redact_panel, registry, withhold_venue_data)

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

    def test_declaring_cockpit_paper_would_hide_every_oracle_position(self) -> None:
        # The bug this mapping fixes: rows are labelled "stock", so "paper" matches none.
        for response in _oracle_positions():
            self.assertEqual(_venue_rows(keep_venue_rows(response, "paper")), [])

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
