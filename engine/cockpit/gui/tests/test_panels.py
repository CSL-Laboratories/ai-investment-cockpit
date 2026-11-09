"""Item 10 Cockpit plugin: venue-class mapping and panel registry only.

Run: ``PYTHONPATH=engine python3 -B -m unittest discover -s engine/cockpit/gui/tests -t engine -v``.
The shared page is Recursion's and is tested there. Plain data; no socket, provider or venue.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import cockpit.gui
from cockpit.gui.panels import PANELS, REQUIRED, registry, withhold_venue_data

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


class VenueDataTests(unittest.TestCase):
    def test_venue_data_withheld_unless_switch_is_on(self) -> None:
        hidden = "[withheld: venue data switch is off]"
        self.assertEqual(withhold_venue_data({"venue_data": 1})["venue_data"], hidden)
        self.assertEqual(withhold_venue_data({"venue_data": 1, "venue_data_switch": "on"})["venue_data"], 1)
        self.assertEqual(withhold_venue_data([{"venue_data": 1, "venue_data_switch": True}])[0]["venue_data"],
                         hidden)
        nested = {"result": {"provenance": {"venue_data": {"kalshi_yes_price": "4242.17"},
                                            "venue_data_switch": "off"}}}
        self.assertNotIn("4242.17", repr(withhold_venue_data(nested)))


class BoundaryTests(unittest.TestCase):
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
