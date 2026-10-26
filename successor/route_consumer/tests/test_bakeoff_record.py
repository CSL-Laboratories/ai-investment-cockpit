"""The committed X2 record is exactly what the in-process run produces today."""

from __future__ import annotations

import json
import unittest

from successor.route_consumer import pin
from successor.route_consumer.tests.support import bakeoff


class BakeoffRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.committed = bakeoff.RECORD_PATH.read_text(encoding="utf-8")
        cls.fresh = bakeoff.run()

    def test_committed_record_equals_a_fresh_run_byte_for_byte(self) -> None:
        self.assertEqual(bakeoff.render(self.fresh), self.committed)

    def test_record_is_labeled_deferred_and_makes_no_live_claim(self) -> None:
        record = json.loads(self.committed)
        self.assertEqual("X2 live integration deferred", record["label"])
        self.assertEqual(("fake", False), (record["adapter_kind"], record["live_calls"]))
        self.assertIn("no verdict", record["kind"])
        self.assertEqual(pin.pinned_head(), record["contract"]["head_sha"])

    def test_record_shows_the_inv_2_pin_on_the_wire(self) -> None:
        cells = json.loads(self.committed)["cells"]
        self.assertEqual(20, len(cells))
        for cell in cells:
            expected_ok = cell["mode"] == "paper" or cell["engine_id"] == "claude"
            self.assertEqual("accepted" if expected_ok else "fail_closed", cell["disposition"], cell)
            if not expected_ok:
                self.assertEqual(("engine_not_permitted_in_mode", 0),
                                 (cell["reason"], cell["route_calls"]))
            else:
                self.assertEqual(1, cell["route_calls"])

    def test_record_carries_digests_only(self) -> None:
        blob = self.committed.lower()
        for word in ("synthetic prompt", "prompt\":", "secret", "canary"):
            self.assertNotIn(word, blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
