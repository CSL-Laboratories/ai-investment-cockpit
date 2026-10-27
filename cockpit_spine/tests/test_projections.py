"""Every displayed number links to a verified published-tier projection."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cockpit_spine import projections as proj
from cockpit_spine import regen_projections as regen
from cockpit_spine.tests.gitpin import blob_sha256_at


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
        registry = proj.load_registry()
        for projection_id in registry.ids():
            entry = registry.get(projection_id)
            blob_sha = blob_sha256_at(proj.REPO_ROOT, entry.source_commit, entry.source_path)
            if blob_sha is None:
                if os.environ.get("COCKPIT_REQUIRE_COMMIT_PIN") == "1":
                    self.fail(f"{projection_id}: cited commit is not in this clone")
                self.skipTest("cited commit is not in this clone; byte check not run")
            self.assertEqual(entry.source_sha256, blob_sha, projection_id)

    def test_a_figure_carries_its_link_and_renders_through_the_registry(self) -> None:
        registry = proj.load_registry()
        figure = registry.figure("x2-fake-driver-cells-accepted")
        text = registry.render(figure)
        self.assertIn(figure.link(), text)
        self.assertEqual(text, proj.render_figure(figure, registry))
        self.assertEqual(text, registry.render_id("x2-fake-driver-cells-accepted"))
        self.assertTrue(figure.link().startswith("projection:x2-fake-driver-cells-accepted@sha256:"))
        self.assertTrue(text.startswith("15 cells "))


class CommitPinHelperTests(unittest.TestCase):
    def test_helper_finds_real_bytes_and_refuses_a_forged_commit(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="pin-"))
        self.addCleanup(shutil.rmtree, root, True)
        (root / "f.json").write_bytes(b'{"n": 1}')
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q"], cwd=root, check=True)
        subprocess.run([*git, "add", "f.json"], cwd=root, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "x"], cwd=root, check=True)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                check=True, text=True).stdout.strip()
        self.assertEqual(hashlib.sha256(b'{"n": 1}').hexdigest(),
                         blob_sha256_at(root, commit, "f.json"))
        self.assertIsNone(blob_sha256_at(root, "f" * 40, "f.json"))
        self.assertIsNone(blob_sha256_at(root, commit, "absent.json"))


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

    def test_stated_value_must_match_the_recomputed_type(self) -> None:
        path = _registry_for(self.root)
        doc = json.loads(path.read_text())
        entry = doc["projections"][0]
        entry["value"] = 2.0
        entry["digest"] = proj._entry_digest(entry)
        path.write_text(json.dumps(proj.registry_document(doc["projections"])))
        with self.assertRaisesRegex(proj.ProjectionError, "stated 2.0 but the source gives 2"):
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
    def setUp(self) -> None:
        self.registry = proj.load_registry()

    def test_unknown_projection_id_raises_unlinked(self) -> None:
        with self.assertRaises(proj.UnlinkedNumberError):
            self.registry.figure("no-such-projection")

    def test_anything_that_is_not_a_figure_renders_as_withheld(self) -> None:
        for value in (None, 15, "15 cells", 1.5, {"value": 15}):
            self.assertEqual(proj.WITHHELD, proj.render_figure(value, self.registry))
        self.assertNotIn("15", proj.render_figure(15, self.registry))
        self.assertEqual(proj.WITHHELD, self.registry.render_id("no-such-projection"))
        self.assertEqual(proj.WITHHELD, self.registry.render_id(None))

    def test_a_hand_built_registry_cannot_render(self) -> None:
        pid = "x2-fake-driver-cells-accepted"
        real = self.registry.get(pid)
        forged = dataclasses.replace(real, value=99)
        bad = proj.Registry({pid: forged}, self.registry.digest)
        shown = bad.render_id(pid)
        self.assertEqual(proj.WITHHELD, shown)
        self.assertNotIn("99", shown)
        figure = proj.Figure(pid, 99, real.unit, real.label, real.digest)
        self.assertEqual(proj.WITHHELD, bad.render(figure))
        with self.assertRaises(proj.UnlinkedNumberError):
            bad.figure(pid)

    def test_recomputed_digests_do_not_let_a_hand_built_registry_render(self) -> None:
        pid = "x2-fake-driver-cells-accepted"
        real = self.registry.get(pid)
        forged_entry = real.entry()
        forged_entry["value"] = 99
        forged_entry["digest"] = proj._entry_digest(forged_entry)
        forged = dataclasses.replace(real, value=99, digest=forged_entry["digest"])
        projections = {
            name: forged if name == pid else self.registry.get(name)
            for name in self.registry.ids()
        }
        digest = proj.registry_document(
            [projections[name].entry() for name in sorted(projections)]
        )["registry_digest"]
        bad = proj.Registry(projections, digest)
        shown = bad.render_id(pid)
        self.assertEqual(proj.WITHHELD, shown)
        self.assertNotIn("99", shown)
        self.assertNotIn("15", shown)

    def test_mutating_the_caller_dict_after_construction_stays_withheld(self) -> None:
        pid = "x2-fake-driver-cells-accepted"
        caller = dict(self.registry._projections)
        built = proj.Registry(caller, self.registry.digest)
        caller[pid] = dataclasses.replace(self.registry.get(pid), value=77)
        shown = built.render_id(pid)
        self.assertEqual(proj.WITHHELD, shown)
        self.assertNotIn("77", shown)

    def test_a_loaded_registry_keeps_a_private_immutable_copy(self) -> None:
        pid = "x2-fake-driver-cells-accepted"
        caller = dict(self.registry._projections)
        loaded = proj.Registry(caller, self.registry.digest, _from_loader=proj._LOAD_TOKEN)
        self.assertIn("15 cells", loaded.render_id(pid))
        caller[pid] = dataclasses.replace(caller[pid], value=99)
        shown = loaded.render_id(pid)
        self.assertTrue(shown.startswith("15 cells "))
        self.assertNotIn("99 cells", shown)
        with self.assertRaises(TypeError):
            loaded._projections[pid] = caller[pid]
        self.assertIn("15 cells", self.registry.render_id(pid))

    def test_a_hand_built_figure_does_not_render(self) -> None:
        forged = proj.Figure("x2-fake-driver-cells-accepted", 99, "cells", "forged",
                             "sha256:" + "0" * 64)
        self.assertEqual(proj.WITHHELD, self.registry.render(forged))
        self.assertEqual(proj.WITHHELD, proj.render_figure(forged, self.registry))

    def test_each_field_of_a_figure_is_checked_against_the_entry(self) -> None:
        real = self.registry.figure("x2-fake-driver-cells-accepted")
        for change in (
            {"value": 99}, {"value": 15.0}, {"value": True}, {"unit": "rows"},
            {"label": "other"}, {"projection_digest": "sha256:" + "0" * 64},
            {"projection_id": "x2-fake-driver-cells-total"}, {"projection_id": "absent"},
        ):
            figure = dataclasses.replace(real, **change)
            self.assertEqual(proj.WITHHELD, self.registry.render(figure), str(change))

    def test_a_figure_from_another_registry_is_stale_and_withheld(self) -> None:
        root, _ = _scratch()
        self.addCleanup(shutil.rmtree, root, True)
        other = proj.load_registry(_registry_for(root), root)
        figure = other.figure("p1")
        self.assertEqual(proj.WITHHELD, self.registry.render(figure))
        self.assertNotEqual(proj.WITHHELD, other.render(figure))

    def test_rendering_prints_the_registry_text_not_the_figure_text(self) -> None:
        real = self.registry.figure("x2-fake-driver-cells-accepted")
        self.assertEqual("15 cells [" + real.link() + "]", self.registry.render(real))

    def test_there_is_no_registry_free_way_to_render(self) -> None:
        figure = self.registry.figure("x2-fake-driver-cells-accepted")
        with self.assertRaises(TypeError):
            proj.render_figure(figure)  # type: ignore[call-arg]
        self.assertFalse(hasattr(figure, "text"))


class StrictDerivationTests(unittest.TestCase):
    def test_count_where_does_not_match_across_types(self) -> None:
        doc = {"xs": [{"k": 1}, {"k": True}, {"k": 1.0}]}
        self.assertEqual(1, proj.derive(doc, {"kind": "count_where", "pointer": "/xs",
                                              "match": {"k": 1}}))
        self.assertEqual(1, proj.derive(doc, {"kind": "count_where", "pointer": "/xs",
                                              "match": {"k": True}}))

    def test_count_where_compares_nested_values_by_exact_type(self) -> None:
        # A nested True must not match a filter containing 1 (and the reverse).
        doc = {"rows": [{"k": {"v": True}}, {"k": {"v": 1}}, {"k": {"v": 1.0}},
                        {"k": [True]}, {"k": [1]}, {"k": [1, True]}]}
        spec = {"kind": "count_where", "pointer": "/rows"}
        self.assertEqual(1, proj.derive(doc, {**spec, "match": {"k": {"v": 1}}}))
        self.assertEqual(1, proj.derive(doc, {**spec, "match": {"k": {"v": True}}}))
        self.assertEqual(1, proj.derive(doc, {**spec, "match": {"k": {"v": 1.0}}}))
        self.assertEqual(1, proj.derive(doc, {**spec, "match": {"k": [1]}}))
        self.assertEqual(1, proj.derive(doc, {**spec, "match": {"k": [True]}}))
        self.assertEqual(1, proj.derive(doc, {**spec, "match": {"k": [1, True]}}))

    def test_integer_beyond_float_range_is_a_projection_error(self) -> None:
        huge = 10 ** 400
        with self.assertRaisesRegex(proj.ProjectionError, "finite number"):
            proj._is_number(huge)
        with self.assertRaisesRegex(proj.ProjectionError, "finite number"):
            proj.derive({"n": huge}, {"kind": "value_at", "pointer": "/n"})
        self.assertEqual(10 ** 20, proj.derive(
            {"n": 10 ** 20}, {"kind": "value_at", "pointer": "/n"}))

    def test_count_where_requires_the_key_to_exist(self) -> None:
        doc = {"xs": [{"a": 1}, {"k": None}]}
        self.assertEqual(1, proj.derive(doc, {"kind": "count_where", "pointer": "/xs",
                                              "match": {"k": None}}))

    def test_unused_derivation_keys_are_refused(self) -> None:
        doc = {"xs": [1, 2], "n": 3}
        for bad in (
            {"kind": "len_at", "pointer": "/xs", "match": {"k": 1}},
            {"kind": "value_at", "pointer": "/n", "match": {"k": 1}},
            {"kind": "count_where", "pointer": "/xs", "match": {"k": 1}, "extra": 1},
            {"kind": "len_at", "pointer": "/xs", "note": "x"},
        ):
            with self.assertRaisesRegex(proj.ProjectionError, "unexpected derivation keys"):
                proj.derive(doc, bad)

    def test_pointer_indexes_are_plain_ascii_without_leading_zeros(self) -> None:
        doc = {"xs": [10, 20, 30]}
        self.assertEqual(10, proj.derive(doc, {"kind": "value_at", "pointer": "/xs/0"}))
        self.assertEqual(30, proj.derive(doc, {"kind": "value_at", "pointer": "/xs/2"}))
        for bad in ("/xs/01", "/xs/00", "/xs/\u0661", "/xs/\u0660", "/xs/-1", "/xs/ 1", "/xs/3"):
            with self.assertRaises(proj.ProjectionError, msg=bad):
                proj.derive(doc, {"kind": "value_at", "pointer": bad})


if __name__ == "__main__":
    unittest.main(verbosity=2)
