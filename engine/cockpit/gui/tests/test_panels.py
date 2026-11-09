"""Item 10 Cockpit plugin: venue-class mapping and panel registry only.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/gui/tests -t engine -v``.
The shared page is Recursion's and is tested there. Plain data; no socket, provider or venue.
"""

from __future__ import annotations

import importlib
import re
import unittest
from pathlib import Path

import cockpit.gui
from cockpit.gui.panels import (DEFAULT_SWITCH_STATES, PANELS, REQUIRED, panel_switch, redact_panel, registry,
                                withhold_venue_data)

GUI_DIR = Path(__file__).resolve().parents[1]
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
        ids = [p["id"] for p in PANELS]
        self.assertEqual(len(ids), len(set(ids)))
        for panel in PANELS:
            with self.subTest(panel=panel["id"]):
                self.assertRegex(panel["id"], r"^cockpit\.[a-z0-9_]+$")
                self.assertTrue(panel["title"].strip())
                self.assertTrue(panel["reads"])
                # Exactly the keys Recursion #330's panel_registry accepts; venue_use is also the switch id.
                self.assertEqual(set(panel), {"id", "title", "venue", "venue_use", "reads", "withhold_venue_data"})

    def test_every_required_venue_use_has_one_panel(self) -> None:
        self.assertEqual(sorted(p["venue_use"] for p in PANELS), sorted(REQUIRED["venue_use"]))

    def test_panels_declare_the_post_read_venue_filter(self) -> None:
        expected = {"cockpit.paper_own_trading": "paper",
                    "cockpit.kalshi_model_fed_read": "kalshi",
                    "cockpit.kalshi_model_fed_propose": "kalshi"}
        self.assertEqual({p["id"]: p["venue"] for p in PANELS}, expected)
        self.assertEqual({p["id"]: p["venue"] for p in registry()["panels"]}, expected)
        for panel in PANELS:
            self.assertEqual(panel["venue_use"].split(":", 1)[0], panel["venue"])

    def test_panels_only_read(self) -> None:
        for panel in PANELS:
            with self.subTest(panel=panel["id"]):
                self.assertLessEqual(set(panel["reads"]), O7_READS)
                for read in panel["reads"]:
                    self.assertNotRegex(read, r"intervention|stop|order|approve|size")

    def test_kalshi_panels_withhold_venue_data(self) -> None:
        for panel in PANELS:
            with self.subTest(panel=panel["id"]):
                self.assertEqual(panel["withhold_venue_data"], panel["venue_use"].startswith("kalshi:"))

    def test_registry_is_a_plain_copy(self) -> None:
        data = registry()
        self.assertEqual(data["required"], {"venue_use": list(REQUIRED["venue_use"])})
        self.assertEqual([p["id"] for p in data["panels"]], [p["id"] for p in PANELS])
        data["panels"][0]["reads"].append("operator.health.inspect")
        self.assertNotIn("operator.health.inspect", PANELS[0]["reads"])


# A Kalshi-shaped read result that also tries to switch itself on from inside the payload.
SMUGGLED = {"status_code": "ok", "kalshi_yes_price": "4242.17", "venue_data_switch": "on",
            "result": {"rows": [{"venue": "kalshi", "ticker": "KXSECRET", "venue_data_switch": "on",
                                 "venue_data": {"price": "4242.17"}}],
                       "provenance": {"venue_data": "KXSECRET", "venue_data_switch": "on"}}}


def mount(switch_states: object) -> dict:
    """Mount every registry() panel and redact each read result with the caller's switch state."""
    return {p["id"]: redact_panel(p, {r: SMUGGLED for r in p["reads"]}, switch_states)
            for p in registry()["panels"]}


class VenueDataTests(unittest.TestCase):
    def test_each_panel_is_governed_by_its_own_venue_use_switch(self) -> None:
        self.assertEqual({p["id"]: panel_switch(p) for p in registry()["panels"]},
                         {"cockpit.paper_own_trading": None,
                          "cockpit.kalshi_model_fed_read": "kalshi:model_fed_read",
                          "cockpit.kalshi_model_fed_propose": "kalshi:model_fed_propose"})

    def test_kalshi_panels_show_no_venue_data_with_switches_off(self) -> None:
        for states in (DEFAULT_SWITCH_STATES, {}, None, "on", ["kalshi:model_fed_read"],
                       {"kalshi:model_fed_read": "off", "kalshi:model_fed_propose": True},
                       {"kalshi:model_fed_read": "ON", "kalshi:model_fed_propose": "on "},
                       {"venue_data_switch": "on", "kalshi": "on"}):
            views = mount(states)
            for pid in ("cockpit.kalshi_model_fed_read", "cockpit.kalshi_model_fed_propose"):
                with self.subTest(states=states, panel=pid):
                    shown = repr(views[pid])
                    self.assertNotIn("4242.17", shown)
                    self.assertNotIn("KXSECRET", shown)
                    for view in views[pid].values():
                        self.assertEqual(set(view), {"venue_data", "status_code"})
                        self.assertIn("[withheld:", view["venue_data"])
                        self.assertEqual(view["status_code"], "ok")
            self.assertEqual(views["cockpit.paper_own_trading"],
                             {r: SMUGGLED for r in PANELS[0]["reads"]})

    def test_clearing_read_does_not_clear_propose(self) -> None:
        views = mount({"kalshi:model_fed_read": "on"})
        self.assertEqual(views["cockpit.kalshi_model_fed_read"],
                         {r: SMUGGLED for r in PANELS[1]["reads"]})
        self.assertNotIn("4242.17", repr(views["cockpit.kalshi_model_fed_propose"]))
        views = mount({"kalshi:model_fed_propose": "on"})
        self.assertNotIn("4242.17", repr(views["cockpit.kalshi_model_fed_read"]))
        self.assertEqual(views["cockpit.kalshi_model_fed_propose"],
                         {r: SMUGGLED for r in PANELS[2]["reads"]})

    def test_payload_cannot_switch_itself_on(self) -> None:
        self.assertNotIn("4242.17", repr(withhold_venue_data(SMUGGLED, "kalshi:model_fed_read", {})))
        self.assertNotIn("4242.17", repr(withhold_venue_data([SMUGGLED], "kalshi:model_fed_read",
                                                             {"venue_data_switch": "on"})))
        self.assertIs(withhold_venue_data(SMUGGLED, None, {}), SMUGGLED)

    def test_panel_without_a_switch_id_fails_closed(self) -> None:
        for panel in ({"venue_use": None, "withhold_venue_data": True}, {"withhold_venue_data": "no"}, {}):
            with self.subTest(panel=panel):
                self.assertIsNotNone(panel_switch(panel))
                shown = redact_panel(panel, {"operator.evidence.inspect": SMUGGLED}, {"None": "on", "": "on"})
                self.assertNotIn("4242.17", repr(shown))


class BoundaryTests(unittest.TestCase):
    def test_daemon_plugin_flag_resolves(self) -> None:
        # The daemon is configured with --plugin cockpit.gui:registry (CSL-802 dry run).
        module, _, attr = "cockpit.gui:registry".partition(":")
        factory = getattr(importlib.import_module(module), attr)
        self.assertIs(factory, registry)
        self.assertEqual(factory(), registry())

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
