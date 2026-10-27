"""Every displayed number links to a verified published-tier projection."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from cockpit_spine import projections as proj
from cockpit_spine import regen_projections as regen
from cockpit_spine.tests.gitpin import blob_sha256_at

REF_PATH = (
    proj.REPO_ROOT / "successor" / "route_consumer" / "records"
    / "x2-fake-driver-bakeoff.ref.json"
)
RECORDS_JSON = (
    proj.REPO_ROOT / "successor" / "route_consumer" / "records"
    / "x2-fake-driver-bakeoff.json"
)
SYNTHETIC_COMMIT = "c" * 40
SYNTHETIC_PATH = "synthetic/cells.json"
_SYNTHETIC_SPECS = (
    ("x2-fake-driver-cells-total", "Cells in the synthetic record", "cells",
     {"kind": "len_at", "pointer": "/cells"}),
    ("x2-fake-driver-cells-accepted", "Synthetic cells accepted", "cells",
     {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "accepted"}}),
    ("x2-fake-driver-cells-fail-closed", "Synthetic cells that failed closed", "cells",
     {"kind": "count_where", "pointer": "/cells", "match": {"disposition": "fail_closed"}}),
)


def _synthetic_record() -> bytes:
    """A tiny stand-in for the vault record. Not the vault bytes."""

    document = {
        "kind": "synthetic-projection-record",
        "cells": [{"disposition": "accepted"}] * 15 + [{"disposition": "fail_closed"}] * 5,
    }
    return (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")


def _write_vault(data: bytes, relative: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="vault-synth-"))
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return root


def _registry_from(
    data: bytes, *, relative: str = SYNTHETIC_PATH, commit: str = SYNTHETIC_COMMIT,
) -> Path:
    document = json.loads(data.decode("utf-8"))
    sha = hashlib.sha256(data).hexdigest()
    entries = [
        proj.make_entry(
            projection_id=pid, label=label, value=proj.derive(document, derivation),
            unit=unit, repository=proj.VAULT_REPOSITORY, commit=commit, path=relative,
            sha256=sha, derivation=derivation,
        )
        for pid, label, unit, derivation in _SYNTHETIC_SPECS
    ]
    directory = Path(tempfile.mkdtemp(prefix="reg-synth-"))
    path = directory / "registry.json"
    path.write_text(json.dumps(proj.registry_document(entries)), encoding="utf-8")
    return path


def _assert_cited_blobs(test: unittest.TestCase, registry: proj.Registry, vault: Path) -> None:
    """Fail unless each cited commit's git blob matches the pinned sha256."""

    for projection_id in registry.ids():
        entry = registry.get(projection_id)
        pin_root = vault if entry.source_repository == proj.VAULT_REPOSITORY else proj.REPO_ROOT
        if not (pin_root / ".git").exists():
            test.fail(f"{projection_id}: {pin_root} is not a git checkout")
        blob = blob_sha256_at(pin_root, entry.source_commit, entry.source_path)
        test.assertIsNotNone(blob, f"{projection_id}: cited commit is not in this checkout")
        test.assertEqual(entry.source_sha256, blob, projection_id)


class _SyntheticVault:
    """A temp vault and a temp registry built with ``make_entry``. No vault bytes."""

    def setUp(self) -> None:
        super().setUp()
        self.data = _synthetic_record()
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.assertNotEqual(regen.X2_SHA256, self.sha)
        self.assertNotEqual(7478, len(self.data))
        self.vault = _write_vault(self.data, SYNTHETIC_PATH)
        self.addCleanup(shutil.rmtree, self.vault, True)
        self.registry_path = _registry_from(self.data)
        self.addCleanup(shutil.rmtree, self.registry_path.parent, True)


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


class CitationTests(unittest.TestCase):
    def test_committed_registry_cites_the_vault_and_stores_no_evidence_bytes(self) -> None:
        ref = json.loads(REF_PATH.read_text(encoding="utf-8"))
        self.assertEqual(regen.REPOSITORY, ref["repository"])
        self.assertEqual(regen.X2_COMMIT, ref["commit"])
        self.assertEqual(regen.X2_PATH, ref["path"])
        self.assertEqual(regen.X2_SHA256, ref["sha256"])
        self.assertEqual("4aa2190d0ee198b7735d4396c0fb3e2a4763c3b2", ref["commit"])
        self.assertEqual(
            "0d94f066256ded639ddae11a5122b8e88ba9f29609e6dbcebbd6a55f201619b3",
            ref["sha256"],
        )
        self.assertEqual(40, len(ref["commit"]))
        self.assertEqual(64, len(ref["sha256"]))
        self.assertFalse(RECORDS_JSON.exists())
        self.assertFalse((Path(__file__).resolve().parent / "fixtures").exists())
        raw = json.loads(proj.REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(3, len(raw["projections"]))
        for entry in raw["projections"]:
            source = entry["source"]
            self.assertEqual(ref["repository"], source["repository"])
            self.assertEqual(ref["commit"], source["commit"])
            self.assertEqual(ref["path"], source["path"])
            self.assertEqual(ref["sha256"], source["sha256"])
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=proj.REPO_ROOT, capture_output=True, check=True,
        )
        suspects = [
            text for raw_path in listed.stdout.split(b"\0")
            if raw_path and not (text := raw_path.decode()).endswith(".ref.json")
            and (text.endswith(".json") or "bakeoff" in text or "x2-fake" in text)
        ]
        if not suspects:
            return
        batched = subprocess.run(
            ["git", "cat-file", "--batch"], cwd=proj.REPO_ROOT, capture_output=True, check=True,
            input="".join(f":{path}\n" for path in suspects).encode(),
        ).stdout
        cursor = 0
        for path in suspects:
            newline = batched.index(b"\n", cursor)
            _sha, kind, size_text = batched[cursor:newline].decode().split()
            cursor = newline + 1
            size = int(size_text)
            content = batched[cursor:cursor + size]
            cursor += size + 1
            if kind != "blob":
                continue
            self.assertNotEqual(regen.X2_SHA256, hashlib.sha256(content).hexdigest(), path)


class SyntheticLoaderTests(_SyntheticVault, unittest.TestCase):
    def test_default_load_path_reads_the_synthetic_record(self) -> None:
        with mock.patch.dict(os.environ, {proj.VAULT_CHECKOUT_ENV: str(self.vault)}):
            via_env = proj.load_registry(self.registry_path)
        with mock.patch.dict(os.environ):
            os.environ.pop(proj.VAULT_CHECKOUT_ENV, None)
            via_argument = proj.load_registry(self.registry_path, vault_root=self.vault)
        self.assertEqual(via_env.ids(), via_argument.ids())
        self.assertEqual((20, 15, 5), tuple(
            via_env.get(i).value for i in (
                "x2-fake-driver-cells-total", "x2-fake-driver-cells-accepted",
                "x2-fake-driver-cells-fail-closed")))
        for projection_id in via_env.ids():
            entry = via_env.get(projection_id)
            self.assertEqual(proj.VAULT_REPOSITORY, entry.source_repository)
            self.assertEqual(SYNTHETIC_COMMIT, entry.source_commit)
            self.assertEqual(SYNTHETIC_PATH, entry.source_path)
            self.assertEqual(self.sha, entry.source_sha256)
            self.assertNotEqual(regen.X2_SHA256, entry.source_sha256)
            self.assertEqual(entry.digest, via_argument.get(projection_id).digest)
        figure = via_env.figure("x2-fake-driver-cells-accepted")
        text = via_env.render(figure)
        self.assertIn(figure.link(), text)
        self.assertEqual(text, proj.render_figure(figure, via_env))
        self.assertEqual(text, via_env.render_id("x2-fake-driver-cells-accepted"))
        link = "projection:x2-fake-driver-cells-accepted@sha256:"
        self.assertTrue(figure.link().startswith(link))
        self.assertTrue(text.startswith("15 cells "))

    def test_absent_checkout_missing_file_or_bad_digest_fails_closed(self) -> None:
        before = proj.REGISTRY_PATH.read_bytes()
        with mock.patch.dict(os.environ):
            os.environ.pop(proj.VAULT_CHECKOUT_ENV, None)
            with self.assertRaisesRegex(proj.ProjectionError, "not configured"):
                proj.load_registry(self.registry_path)
            with self.assertRaisesRegex(proj.ProjectionError, "not configured"):
                proj.load_registry()
            with self.assertRaisesRegex(proj.ProjectionError, "not configured"):
                regen.build()
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(1, regen.main(["--write"]))
            self.assertIn("not configured", stderr.getvalue())
        with self.assertRaisesRegex(proj.ProjectionError, "empty"):
            proj.require_vault_checkout("")
        with self.assertRaisesRegex(proj.ProjectionError, "empty"):
            proj.require_vault_checkout("   ")
        with self.assertRaisesRegex(proj.ProjectionError, "empty"):
            proj.require_vault_checkout(Path(""))
        self.assertEqual(before, proj.REGISTRY_PATH.read_bytes())

        missing = self.vault / "no-such-checkout"
        with self.assertRaisesRegex(proj.ProjectionError, "absent"):
            proj.load_registry(self.registry_path, vault_root=missing)
        with self.assertRaisesRegex(proj.ProjectionError, "absent"):
            regen.build(missing)
        empty = Path(tempfile.mkdtemp(prefix="vault-empty-"))
        self.addCleanup(shutil.rmtree, empty, True)
        with self.assertRaisesRegex(proj.ProjectionError, "unreadable"):
            proj.load_registry(self.registry_path, vault_root=empty)

        flipped = bytearray(self.data)
        flipped[-1] ^= 0x01
        mutated = _write_vault(bytes(flipped), SYNTHETIC_PATH)
        self.addCleanup(shutil.rmtree, mutated, True)
        with self.assertRaisesRegex(proj.ProjectionError, "pinned sha256"):
            proj.load_registry(self.registry_path, vault_root=mutated)

        wrong_pin = _write_vault(self.data, regen.X2_PATH)
        self.addCleanup(shutil.rmtree, wrong_pin, True)
        with self.assertRaisesRegex(proj.ProjectionError, "pinned sha256"):
            regen.build(wrong_pin)

    def test_vault_path_leaving_the_checkout_is_refused(self) -> None:
        document = json.loads(self.data.decode("utf-8"))
        derivation = {"kind": "len_at", "pointer": "/cells"}
        entry = proj.make_entry(
            projection_id="p-escape", label="label",
            value=proj.derive(document, derivation), unit="cells",
            repository=proj.VAULT_REPOSITORY, commit=SYNTHETIC_COMMIT,
            path="../outside.json", sha256=self.sha, derivation=derivation,
        )
        path = self.registry_path.parent / "escape.json"
        path.write_text(json.dumps(proj.registry_document([entry])), encoding="utf-8")
        with self.assertRaisesRegex(proj.ProjectionError, "leaves the repository"):
            proj.load_registry(path, vault_root=self.vault)


class RealVaultCheckoutTests(unittest.TestCase):
    """Committed registry against a real csl-research checkout.

    Skips when ``CSL_RESEARCH_CHECKOUT`` is unset or not a directory. Putting
    that checkout on CI is a workflow change and is not done here.
    """

    def setUp(self) -> None:
        raw = os.environ.get(proj.VAULT_CHECKOUT_ENV, "").strip()
        vault = Path(raw) if raw else None
        if vault is None or not vault.is_dir():
            self.skipTest("CSL_RESEARCH_CHECKOUT does not point at a checkout")
        self.vault = vault
        self.ref = json.loads(REF_PATH.read_text(encoding="utf-8"))

    def test_committed_registry_matches_the_pinned_vault_bytes(self) -> None:
        data = (self.vault / self.ref["path"]).read_bytes()
        self.assertEqual(7478, len(data))
        self.assertEqual(self.ref["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(regen.X2_SHA256, hashlib.sha256(data).hexdigest())
        self.assertEqual(regen.REPOSITORY, self.ref["repository"])
        self.assertEqual(regen.X2_COMMIT, self.ref["commit"])
        self.assertEqual(regen.X2_PATH, self.ref["path"])
        registry = proj.load_registry(vault_root=self.vault)
        self.assertEqual((20, 15, 5), tuple(
            registry.get(i).value for i in (
                "x2-fake-driver-cells-total", "x2-fake-driver-cells-accepted",
                "x2-fake-driver-cells-fail-closed")))
        for projection_id in registry.ids():
            entry = registry.get(projection_id)
            self.assertEqual(self.ref["sha256"], entry.source_sha256)
            self.assertEqual(self.ref["commit"], entry.source_commit)
            self.assertEqual(self.ref["path"], entry.source_path)
        via_env = proj.load_registry()
        self.assertEqual(
            registry.get("x2-fake-driver-cells-accepted").digest,
            via_env.get("x2-fake-driver-cells-accepted").digest,
        )

    def test_committed_registry_is_exactly_what_regeneration_produces(self) -> None:
        committed = proj.REGISTRY_PATH.read_text(encoding="utf-8")
        self.assertEqual(regen.render(regen.build(self.vault)), committed)
        self.assertEqual(regen.render(regen.build()), committed)

    def test_source_blob_at_the_cited_commit_has_the_pinned_bytes(self) -> None:
        if not (self.vault / ".git").exists():
            self.skipTest("CSL_RESEARCH_CHECKOUT is not a git checkout")
        _assert_cited_blobs(self, proj.load_registry(vault_root=self.vault), self.vault)


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

    def test_cited_blob_check_verifies_a_git_checkout_and_rejects_a_forged_commit(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="pin-reg-"))
        self.addCleanup(shutil.rmtree, root, True)
        data = _synthetic_record()
        target = root / SYNTHETIC_PATH
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q"], cwd=root, check=True)
        subprocess.run([*git, "add", SYNTHETIC_PATH], cwd=root, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "x"], cwd=root, check=True)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=True, text=True,
        ).stdout.strip()
        registry_path = _registry_from(data, commit=commit)
        self.addCleanup(shutil.rmtree, registry_path.parent, True)
        _assert_cited_blobs(self, proj.load_registry(registry_path, vault_root=root), root)

        forged = _registry_from(data, commit="f" * 40)
        self.addCleanup(shutil.rmtree, forged.parent, True)
        loaded = proj.load_registry(forged, vault_root=root)
        with self.assertRaises(AssertionError):
            _assert_cited_blobs(self, loaded, root)


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


class UnlinkedNumberTests(_SyntheticVault, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = proj.load_registry(self.registry_path, vault_root=self.vault)

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
