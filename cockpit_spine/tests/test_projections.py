"""Every displayed number links to a verified published-tier projection."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cockpit_spine import projections as proj
from cockpit_spine import regen_projections as regen


def _scratch() -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp(prefix="proj-"))
    source = root / "src.json"
    source.write_text(json.dumps({"n": 7, "rows": [{"k": "a"}, {"k": "b"}, {"k": "a"}]}))
    return root, source


def _registry_for(root: Path, **overrides: object) -> Path:
    data = (root / "src.json").read_bytes()
    entry = proj.make_entry(
        projection_id=str(overrides.get("projection_id", "p1")), label="label", value=2,
        unit="rows", repository="org/repo", commit="a" * 40, path="src.json",
        sha256=hashlib.sha256(data).hexdigest(),
        derivation={"kind": "count_where", "pointer": "/rows", "match": {"k": "a"}},
    )
    path = root / "registry.json"
    path.write_text(json.dumps(proj.registry_document([entry])))
    return path


class CommittedRegistryTests(unittest.TestCase):
    def test_committed_registry_loads_and_every_value_is_recomputed(self) -> None:
        registry = proj.load_registry()
        self.assertEqual(3, len(registry.ids()))
        self.assertEqual((20, 15, 5), tuple(
            registry.get(i).value for i in (
                "x2-fake-driver-cells-total", "x2-fake-driver-cells-accepted",
                "x2-fake-driver-cells-fail-closed")))

    def test_committed_registry_is_exactly_what_regeneration_produces(self) -> None:
        committed = proj.REGISTRY_PATH.read_text(encoding="utf-8")
        self.assertEqual(regen.render(regen.build()), committed)

    def test_source_blob_at_the_cited_commit_has_the_pinned_bytes(self) -> None:
        entry = proj.load_registry().get("x2-fake-driver-cells-total")
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{entry.source_commit}:{entry.source_path}"],
            cwd=proj.REPO_ROOT, capture_output=True, check=False,
        )
        if probe.returncode != 0:
            self.skipTest("cited commit is not in this clone; byte check not run")
        blob = subprocess.run(
            ["git", "show", f"{entry.source_commit}:{entry.source_path}"],
            cwd=proj.REPO_ROOT, capture_output=True, check=True,
        ).stdout
        self.assertEqual(entry.source_sha256, hashlib.sha256(blob).hexdigest())

    def test_a_figure_carries_its_link_and_renders_with_it(self) -> None:
        figure = proj.load_registry().figure("x2-fake-driver-cells-accepted")
        self.assertIn(figure.link(), proj.render_figure(figure))
        self.assertTrue(figure.link().startswith("projection:x2-fake-driver-cells-accepted@sha256:"))
        self.assertTrue(proj.render_figure(figure).startswith("15 cells "))


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root, self.source = _scratch()
        self.addCleanup(shutil.rmtree, self.root, True)

    def load(self, path: Path | None = None) -> proj.Registry:
        return proj.load_registry(path or self.root / "registry.json", self.root)

    def test_scratch_registry_loads(self) -> None:
        _registry_for(self.root)
        self.assertEqual(2, self.load().get("p1").value)

    def test_changed_source_bytes_are_refused(self) -> None:
        path = _registry_for(self.root)
        self.source.write_text(json.dumps({"rows": [{"k": "a"}, {"k": "a"}]}))
        with self.assertRaisesRegex(proj.ProjectionError, "differ from the pinned sha256"):
            self.load(path)

    def test_stated_value_that_the_source_does_not_give_is_refused(self) -> None:
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        entry = doc["projections"][0]
        entry["value"] = 3
        entry["digest"] = proj._entry_digest(entry)
        path.write_text(json.dumps(proj.registry_document(doc["projections"])))
        with self.assertRaisesRegex(proj.ProjectionError, "stated 3 but the source gives 2"):
            self.load(path)

    def test_entry_edited_without_its_digest_is_refused(self) -> None:
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        doc["projections"][0]["label"] = "edited"
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(proj.ProjectionError, "entry digest"):
            self.load(path)

    def test_registry_digest_must_match_the_entries(self) -> None:
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        doc["registry_digest"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(proj.ProjectionError, "registry digest"):
            self.load(path)

    def _mutated(self, mutate) -> Path:
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        entry = copy.deepcopy(doc["projections"][0])
        mutate(entry)
        entry["digest"] = proj._entry_digest(entry)
        path.write_text(json.dumps(proj.registry_document([entry])))
        return path

    def test_anything_but_published_tier_is_refused(self) -> None:
        path = self._mutated(lambda e: e.update(tier="fixture"))
        with self.assertRaisesRegex(proj.ProjectionError, "tier"):
            self.load(path)

    def test_short_or_symbolic_commits_are_refused(self) -> None:
        for bad in ("main", "abc1234", "A" * 40):
            path = self._mutated(lambda e, b=bad: e["source"].update(commit=b))
            with self.assertRaisesRegex(proj.ProjectionError, "40-hex"):
                self.load(path)

    def test_path_leaving_the_repository_is_refused(self) -> None:
        path = self._mutated(lambda e: e["source"].update(path="../outside.json"))
        with self.assertRaisesRegex(proj.ProjectionError, "leaves the repository"):
            self.load(path)

    def test_extra_keys_are_refused(self) -> None:
        path = self._mutated(lambda e: e.update(note="hidden"))
        with self.assertRaisesRegex(proj.ProjectionError, "unexpected"):
            self.load(path)

    def test_non_numbers_are_refused(self) -> None:
        for bad in (True, float("nan"), float("inf"), "2", None):
            path = self._mutated(lambda e, b=bad: e.update(value=b))
            with self.assertRaises(proj.ProjectionError, msg=repr(bad)):
                self.load(path)

    def test_duplicate_ids_are_refused(self) -> None:
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        path.write_text(json.dumps(proj.registry_document(doc["projections"] * 2)))
        with self.assertRaisesRegex(proj.ProjectionError, "duplicate"):
            self.load(path)

    def test_missing_registry_and_wrong_version_fail_closed(self) -> None:
        with self.assertRaisesRegex(proj.ProjectionError, "unreadable"):
            self.load(self.root / "absent.json")
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        doc["registry_version"] = 2
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(proj.ProjectionError, "version"):
            self.load(path)


class DeriveTests(unittest.TestCase):
    DOC = {"a": {"b": [1, {"c": 5}]}, "xs": [{"k": 1}, {"k": 2}, {"k": 1}], "s": "text"}

    def test_value_len_and_count(self) -> None:
        self.assertEqual(5, proj.derive(self.DOC, {"kind": "value_at", "pointer": "/a/b/1/c"}))
        self.assertEqual(3, proj.derive(self.DOC, {"kind": "len_at", "pointer": "/xs"}))
        self.assertEqual(2, proj.derive(
            self.DOC, {"kind": "count_where", "pointer": "/xs", "match": {"k": 1}}))

    def test_bad_derivations_raise(self) -> None:
        for bad in (
            {"kind": "eval", "pointer": "/xs"},
            {"kind": "value_at", "pointer": "/missing"},
            {"kind": "value_at", "pointer": "/s"},
            {"kind": "len_at", "pointer": "/s"},
            {"kind": "count_where", "pointer": "/xs", "match": {}},
            {"kind": "value_at", "pointer": "no-slash"},
        ):
            with self.assertRaises(proj.ProjectionError, msg=str(bad)):
                proj.derive(self.DOC, bad)


class UnlinkedNumberTests(unittest.TestCase):
    def test_unknown_projection_id_raises_unlinked(self) -> None:
        with self.assertRaises(proj.UnlinkedNumberError):
            proj.load_registry().figure("no-such-projection")

    def test_anything_that_is_not_a_figure_renders_as_withheld(self) -> None:
        for value in (None, 15, "15 cells", 1.5, {"value": 15}):
            self.assertEqual(proj.WITHHELD, proj.render_figure(value))
        self.assertNotIn("15", proj.render_figure(15))


if __name__ == "__main__":
    unittest.main(verbosity=2)
