"""The X2 record is rebuilt in memory. This repo keeps only the vault pin."""

from __future__ import annotations

import hashlib
import json
import unittest

from successor.route_consumer import pin
from successor.route_consumer.tests.support import bakeoff

# Published evidence. csl-research commit 4aa2190d (blob b36a5b11) stores the
# same bytes as bakeoff.render(bakeoff.run()) on this tree. The suite locks that
# digest offline and does not fetch the vault.
VAULT = {
    "repository": "CSL-Laboratories/csl-research",
    "commit": "4aa2190d0ee198b7735d4396c0fb3e2a4763c3b2",
    "path": "contracts/ai-investment-cockpit/evidence/x2-fake-driver-bakeoff.json",
    "sha256": "0d94f066256ded639ddae11a5122b8e88ba9f29609e6dbcebbd6a55f201619b3",
    "git_blob": "b36a5b119d8df40fd485079d564fdc2c4cc5d2f0",
    "url": (
        "https://github.com/CSL-Laboratories/csl-research/blob/"
        "4aa2190d0ee198b7735d4396c0fb3e2a4763c3b2/"
        "contracts/ai-investment-cockpit/evidence/x2-fake-driver-bakeoff.json"
    ),
    "claim": "X2 live integration deferred. Plumbing record on the fake driver.",
    "limitations": "No model comparison, no live provider call, no CP7 result, and no verdict.",
}


class BakeoffRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = json.loads(bakeoff.REF_PATH.read_text(encoding="utf-8"))
        cls.fresh = bakeoff.run()
        cls.rendered = bakeoff.render(cls.fresh)

    def test_reference_points_at_the_vaulted_record(self) -> None:
        self.assertEqual(VAULT, self.reference)
        self.assertEqual(
            f"https://github.com/{VAULT['repository']}/blob/{VAULT['commit']}/{VAULT['path']}",
            self.reference["url"],
        )
        self.assertEqual(40, len(self.reference["commit"]))
        self.assertEqual(64, len(self.reference["sha256"]))
        self.assertEqual(40, len(self.reference["git_blob"]))
        self.assertFalse((pin.PACKAGE_DIR / "records" / "x2-fake-driver-bakeoff.json").exists())

    def test_write_back_into_the_repo_is_refused(self) -> None:
        self.assertEqual(2, bakeoff.main(["--write"]))
        self.assertFalse((pin.PACKAGE_DIR / "records" / "x2-fake-driver-bakeoff.json").exists())

    def test_fresh_run_canonical_sha256_is_stable(self) -> None:
        encoded = self.rendered.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        self.assertEqual(VAULT["sha256"], digest)
        self.assertEqual(self.reference["sha256"], digest)
        self.assertEqual(7478, len(encoded))

    def test_record_is_labeled_deferred_and_makes_no_live_claim(self) -> None:
        record = self.fresh
        self.assertEqual("X2 live integration deferred", record["label"])
        self.assertEqual(("fake", False), (record["adapter_kind"], record["live_calls"]))
        self.assertIn("no verdict", record["kind"])
        self.assertEqual(pin.pinned_head(), record["contract"]["head_sha"])

    def test_record_shows_the_inv_2_pin_on_the_wire(self) -> None:
        cells = self.fresh["cells"]
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
        blob = self.rendered.lower()
        for word in ("synthetic prompt", "prompt\":", "secret", "canary"):
            self.assertNotIn(word, blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
