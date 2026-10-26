#!/usr/bin/env python3
"""Unit tests for D10 E3-0-early helpers (environ, record, bind pins).

Run: python3 -X utf8 -B tools/test_sandbox_e3.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "ci"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


e3 = load("sandbox_e3_record")
environ = load("sandbox_e3_environ")
bind = load("sandbox_e3_bind")


class Set2PinTests(unittest.TestCase):
    def test_set2_is_81_unique_names_in_d10_order(self) -> None:
        self.assertEqual(len(environ.SET2_NAMES), 81)
        self.assertEqual(len(environ.SET2), 81)
        self.assertEqual(len(set(environ.SET2_NAMES)), 81)
        self.assertIn("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", environ.SET2)
        self.assertIn("GITHUB_RUN_ID", environ.SET2)
        self.assertIn("INPUT_PATH_TO_CLAUDE_CODE_EXECUTABLE", environ.SET2)
        self.assertEqual(bind.ACTION_YML_SHA256, e3.ACTION_YML_SHA256)
        self.assertEqual(bind.SDK_MJS_SHA256, e3.SDK_MJS_SHA256)

    def test_set2_bun_input_is_the_action_wrapper_not_the_elf(self) -> None:
        self.assertEqual(environ.BUN_EXE, "/opt/csl-bun/bun")
        self.assertEqual(environ.BUN_ACTION, "/opt/csl-bun-action/bun")
        self.assertEqual(
            environ.SET2_VALUE_RULES["INPUT_PATH_TO_BUN_EXECUTABLE"],
            environ.BUN_ACTION,
        )
        self.assertNotEqual(
            environ.SET2_VALUE_RULES["INPUT_PATH_TO_BUN_EXECUTABLE"],
            environ.BUN_EXE,
        )
        worker = {"PATH": "/usr/bin", "FOO": "1"}
        # Action GITHUB_PATH prepends dirname(path_to_bun_executable).
        action_path = (
            "/bin:/usr/bin:/opt/csl-bun-action:/home/runner/.bun/bin:/usr/bin"
        )
        self.assertEqual(environ.expected_job_path("/usr/bin"), action_path)
        stale_path = "/bin:/usr/bin:/opt/csl-bun:/home/runner/.bun/bin:/usr/bin"
        b_env = {"PATH": action_path, "FOO": "1"}
        x_ok = {
            "PATH": action_path,
            "FOO": "1",
            "PWD": "/workspace",
            "SHLVL": "1",
            "_": environ.BUN_EXE,
            "INPUT_PATH_TO_BUN_EXECUTABLE": environ.BUN_ACTION,
        }
        self.assertTrue(
            environ.check_environ(
                proc_env=x_ok,
                worker_env=worker,
                b_env=b_env,
                on_x=True,
                workspace="/workspace",
                worker_path="/usr/bin",
            ).ok
        )
        x_elf = dict(x_ok)
        x_elf["INPUT_PATH_TO_BUN_EXECUTABLE"] = environ.BUN_EXE
        self.assertFalse(
            environ.check_environ(
                proc_env=x_elf,
                worker_env=worker,
                b_env=b_env,
                on_x=True,
                workspace="/workspace",
                worker_path="/usr/bin",
            ).ok
        )
        x_stale = dict(x_ok)
        x_stale["PATH"] = stale_path
        stale = environ.check_environ(
            proc_env=x_stale,
            worker_env=worker,
            b_env=b_env,
            on_x=True,
            workspace="/workspace",
            worker_path="/usr/bin",
        )
        self.assertFalse(stale.ok)
        self.assertEqual(stale.reason, "path")


class E3RecordTests(unittest.TestCase):
    def _fields(self, **overrides: str) -> Dict[str, str]:
        fields = {
            "claude_sha256": e3.CLAUDE_SHA256,
            "claude_realpath_class": "pinned",
            "sdk_mjs_sha256": e3.SDK_MJS_SHA256,
            "sdk_source": e3.SDK_SOURCE,
            "sdk_disk_match": "true",
            "bun_tofu_sha256": "a" * 64,
            "bwrap_sha256": "b" * 64,
            "socat_sha256": "c" * 64,
            "bash_sha256": "d" * 64,
            "match": "true",
            "handoff_count": "1",
            "handoff_conflict": "false",
        }
        fields.update(overrides)
        return fields

    def test_reconstruct_uses_constants_and_hex_grammar(self) -> None:
        text = e3.reconstruct_e3_record(self._fields())
        self.assertIn(f"claude_sha256={e3.CLAUDE_SHA256}", text)
        self.assertIn("claude_realpath_class=pinned", text)
        self.assertIn(f"sdk_source={e3.SDK_SOURCE}", text)
        self.assertNotIn("\r", text)
        self.assertTrue(e3.compare_pinned_cli(e3.CLAUDE_SHA256, e3.CLAUDE_SIZE))
        self.assertFalse(e3.compare_pinned_cli("0" * 64))

    def test_rejects_capture_smuggled_text(self) -> None:
        with self.assertRaises(e3.E3RecordError):
            e3.reconstruct_e3_record(self._fields(match="true\n[Sandbox Linux] Wrapped"))
        with self.assertRaises(e3.E3RecordError):
            e3.reconstruct_e3_record(self._fields(claude_sha256="not-hex"))
        with self.assertRaises(e3.E3RecordError):
            e3.parse_e3_text("claude_sha256=" + e3.CLAUDE_SHA256 + "\nextra=1\n")

    def test_bootstrap_handoff_fields_are_placeholders(self) -> None:
        self.assertFalse(hasattr(e3, "apply_observed_handoff"))
        bootstrap = e3.reconstruct_e3_record(self._fields(handoff_count="0"))
        self.assertIn("handoff_count=0", bootstrap)
        self.assertIn("handoff_conflict=false", bootstrap)
        src = (SCRIPTS / "sandbox_e3_record.py").read_text(encoding="utf-8")
        self.assertIn("ok= / extra=", src)


class EnvironK2Tests(unittest.TestCase):
    def test_k1_path_newest_first_keeps_worker_suffix(self) -> None:
        worker = "/usr/bin:/bin:/snap/bin"
        got = environ.expected_job_path(worker)
        self.assertTrue(
            got.startswith("/bin:/usr/bin:/opt/csl-bun-action:/home/runner/.bun/bin:")
        )
        self.assertNotIn(":/opt/csl-bun:", f":{got}:")
        self.assertTrue(got.endswith(worker))
        self.assertEqual(got.count("/usr/bin"), 2)

    def test_l1_oldpwd_absent_or_equal_to_worker(self) -> None:
        worker = {"PATH": "/usr/bin", "FOO": "1"}
        b_env = {"PATH": environ.expected_job_path("/usr/bin"), "FOO": "1"}
        x_ok = {
            "PATH": environ.expected_job_path("/usr/bin"),
            "FOO": "1",
            "PWD": "/workspace",
            "SHLVL": "1",
            "_": "/opt/csl-bun/bun",
        }
        self.assertTrue(
            environ.check_environ(
                proc_env=x_ok,
                worker_env=worker,
                b_env=b_env,
                on_x=True,
                workspace="/workspace",
                worker_path="/usr/bin",
            ).ok
        )
        x_eq = dict(x_ok)
        x_eq["OLDPWD"] = "/home/runner"
        worker2 = dict(worker)
        worker2["OLDPWD"] = "/home/runner"
        self.assertTrue(
            environ.check_environ(
                proc_env=x_eq,
                worker_env=worker2,
                b_env=b_env,
                on_x=True,
                workspace="/workspace",
                worker_path="/usr/bin",
            ).ok
        )
        x_bad = dict(x_ok)
        x_bad["OLDPWD"] = "/tmp/evil"
        self.assertFalse(
            environ.check_environ(
                proc_env=x_bad,
                worker_env=worker,
                b_env=b_env,
                on_x=True,
                workspace="/workspace",
                worker_path="/usr/bin",
            ).ok
        )

    def test_l2_b_may_carry_pwd_as_set1(self) -> None:
        worker = {"PATH": "/usr/bin", "PWD": "/workspace", "SHLVL": "1", "_": "/bin/bash"}
        path = environ.expected_job_path("/usr/bin")
        b_env = {"PATH": path, "PWD": "/workspace", "SHLVL": "1", "_": "/bin/bash"}
        verdict = environ.check_environ(
            proc_env=b_env,
            worker_env=worker,
            b_env=b_env,
            on_x=False,
            workspace="/workspace",
            worker_path="/usr/bin",
        )
        self.assertTrue(verdict.ok, verdict.reason)

    def test_run_step_fixture_diffs_parent_and_child_environ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "child.py"
            parent_dump = Path(tmp) / "parent.env"
            child_dump = Path(tmp) / "child.env"
            script = Path(tmp) / "step.sh"
            stub.write_text(
                "from pathlib import Path\n"
                f"Path({str(child_dump)!r}).write_bytes(Path('/proc/self/environ').read_bytes())\n",
                encoding="utf-8",
            )
            script.write_text(
                "#!/bin/bash\n"
                f"cat /proc/$$/environ > {parent_dump}\n"
                f"{sys.executable} {stub}\n",
                encoding="utf-8",
            )
            argv = list(environ.run_step_argv(str(script), bash="/bin/bash"))
            proc = subprocess.run(argv, check=False, cwd=tmp)
            self.assertEqual(proc.returncode, 0)
            parent = environ.parse_environ_bytes(parent_dump.read_bytes())
            child = environ.parse_environ_bytes(child_dump.read_bytes())
            self.assertIn("PWD", child)
            self.assertEqual(child["PWD"], tmp)
            parent_lvl = int(parent["SHLVL"]) if parent.get("SHLVL", "").isdigit() else 0
            self.assertEqual(int(child["SHLVL"]), parent_lvl + 1)
            self.assertTrue(child["_"].endswith("python3") or "python" in child["_"])
            if "OLDPWD" in child:
                self.assertEqual(child["OLDPWD"], parent.get("OLDPWD", child["OLDPWD"]))


class BindPinTests(unittest.TestCase):
    def test_bind_order_is_outermost_first_ap_last(self) -> None:
        self.assertEqual(bind.BIND_ORDER[0], "/home/runner/work")
        self.assertEqual(bind.BIND_ORDER[-1], bind.ACTION_DIR)
        self.assertIn(bind.ACTION_PIN, bind.ACTION_DIR)
        self.assertEqual(len(bind.BIND_ORDER), 5)

    def test_verify_pins_accepts_matching_hashes_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yml = Path(tmp) / "action.yml"
            sdk_dir = Path(tmp) / "node_modules" / "pkg"
            sdk_dir.mkdir(parents=True)
            sdk = sdk_dir / "sdk.mjs"
            yml.write_text("name: pin\n", encoding="utf-8")
            sdk.write_text("export {}\n", encoding="utf-8")
            pins = bind.verify_pins(
                action_dir=tmp,
                action_yml_sha256=bind.sha256_file(str(yml)),
                sdk_mjs_sha256=bind.sha256_file(str(sdk)),
            )
            self.assertEqual(pins["sdk_disk_match"], "true")
            with self.assertRaises(bind.BindError):
                bind.verify_pins(
                    action_dir=tmp,
                    action_yml_sha256="0" * 64,
                    sdk_mjs_sha256=pins["sdk_mjs_sha256"],
                )

    def test_u16_nsswitch_and_sudo_conf_hooks(self) -> None:
        self.assertTrue(bind.nsswitch_sudoers_ok("passwd: files\nsudoers: files\n"))
        self.assertFalse(bind.nsswitch_sudoers_ok("sudoers: ldap files\n"))
        self.assertFalse(bind.nsswitch_sudoers_ok("sudoers: sss\n"))
        self.assertTrue(bind.sudo_conf_plugin_ok("# empty\n"))
        self.assertFalse(bind.sudo_conf_plugin_ok("Plugin sudoers_policy sudoers.so\n"))
        with tempfile.TemporaryDirectory() as tmp:
            path = bind.record_u16(tmp, "root ALL=(ALL) ALL\n", "passwd: files\n", "")
            text = Path(path).read_text(encoding="ascii")
            self.assertIn("u16_status=[A]", text)
            self.assertIn("image_match=unproven", text)
            self.assertIn("see=f-u16.md", text)

    def test_empty_bun_bin_and_mountinfo_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bun_bin = Path(tmp) / "bin"
            bun_bin.mkdir()
            (bun_bin / "bun").write_text("stale", encoding="utf-8")
            bind.empty_root_owned_dir(str(bun_bin))
            self.assertEqual(os.listdir(bun_bin), [])
            rows = bind.parse_mountinfo(
                "99 1 8:1 / /home/runner/work rw - ext4 /dev/sda1 rw\n"
                "100 99 8:1 / /home/runner/work/_actions rw - ext4 /dev/sda1 rw\n"
                "101 100 8:1 / /home/runner/work/_actions/anthropics rw - ext4 /dev/sda1 rw\n"
                "102 101 8:1 / /home/runner/work/_actions/anthropics/claude-code-action rw - ext4 /dev/sda1 rw\n"
                f"103 102 8:1 / {bind.ACTION_DIR} ro - ext4 /dev/sda1 rw\n"
            )
            ids = bind.bind_ids_for(bind.BIND_ORDER, rows)
            self.assertEqual(ids[bind.ACTION_DIR], "103")
            self.assertEqual(ids["/home/runner/work"], "99")


class EnvironM1Tests(unittest.TestCase):
    def test_ld_library_path_must_be_empty_or_absent(self) -> None:
        worker = {"PATH": "/usr/bin", "FOO": "1"}
        path = environ.expected_job_path("/usr/bin")
        b_env = {"PATH": path, "FOO": "1"}
        x_ok = {"PATH": path, "FOO": "1", "PWD": "/workspace", "SHLVL": "1", "_": "/opt/csl-bun/bun"}
        self.assertTrue(
            environ.check_environ(
                proc_env=x_ok, worker_env=worker, b_env=b_env, on_x=True,
                workspace="/workspace", worker_path="/usr/bin",
            ).ok
        )
        x_empty = dict(x_ok)
        x_empty["LD_LIBRARY_PATH"] = ""
        self.assertTrue(
            environ.check_environ(
                proc_env=x_empty, worker_env=worker, b_env=b_env, on_x=True,
                workspace="/workspace", worker_path="/usr/bin",
            ).ok
        )
        x_bad = dict(x_ok)
        x_bad["LD_LIBRARY_PATH"] = "/opt/hostedtoolcache/Python/3.12/lib"
        self.assertFalse(
            environ.check_environ(
                proc_env=x_bad, worker_env=worker, b_env=b_env, on_x=True,
                workspace="/workspace", worker_path="/usr/bin",
            ).ok
        )

    def test_bun_star_only_recorded_names(self) -> None:
        worker = {"PATH": "/usr/bin"}
        path = environ.expected_job_path("/usr/bin")
        b_env = {"PATH": path}
        x = {"PATH": path, "PWD": "/workspace", "SHLVL": "1", "_": "/opt/csl-bun/bun", "BUN_INSTALL": "/opt"}
        self.assertFalse(
            environ.check_environ(
                proc_env=x, worker_env=worker, b_env=b_env, on_x=True,
                workspace="/workspace", worker_path="/usr/bin",
            ).ok
        )
        self.assertTrue(
            environ.check_environ(
                proc_env=x, worker_env=worker, b_env=b_env, on_x=True,
                workspace="/workspace", worker_path="/usr/bin",
                recorded_bun={"BUN_INSTALL": "/opt"},
            ).ok
        )


class GitTreeTests(unittest.TestCase):
    def test_git_tree_rejects_extra_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "action.yml").write_text("name: pin\n", encoding="utf-8")
            blob = bind.git_blob_sha1(str(Path(tmp) / "action.yml"))
            ls = f"100644 blob {blob}\taction.yml\n"
            bind.check_git_tree(tmp, ls, skip_dirs=())
            (Path(tmp) / "extra.txt").write_text("x\n", encoding="utf-8")
            with self.assertRaises(bind.BindError):
                bind.check_git_tree(tmp, ls, skip_dirs=())
            extra = bind.tree_digest(tmp)
            self.assertEqual(len(extra), 64)

    def test_git_tree_rejects_bin_bun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "action.yml").write_text("name: pin\n", encoding="utf-8")
            blob = bind.git_blob_sha1(str(Path(tmp) / "action.yml"))
            ls = f"100644 blob {blob}\taction.yml\n"
            bind.check_git_tree(tmp, ls, skip_dirs=())
            (Path(tmp) / "bin").mkdir()
            (Path(tmp) / "bin" / "bun").write_bytes(b"bun")
            with self.assertRaises(bind.BindError):
                bind.check_git_tree(tmp, ls, skip_dirs=())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_git_tree_still_rejects_source_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "action.yml").write_text("name: pin\n", encoding="utf-8")
            os.symlink("action.yml", str(Path(tmp) / "link.yml"))
            blob = bind.git_blob_sha1(str(Path(tmp) / "action.yml"))
            ls = f"100644 blob {blob}\taction.yml\n"
            with self.assertRaises(bind.BindError) as ctx:
                bind.check_git_tree(tmp, ls, skip_dirs=())
            self.assertEqual(str(ctx.exception), "symlink")


class NodeModulesDigestTests(unittest.TestCase):
    """Hosted 40262408416: bun's node_modules/.bin links tripped BindError: symlink."""

    def _bun_style_node_modules(self, tmp: str) -> Path:
        nm = Path(tmp) / "node_modules"
        pkg = nm / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "cli.js").write_text("console.log(1)\n", encoding="utf-8")
        (pkg / "index.js").write_text("export {}\n", encoding="utf-8")
        binv = nm / ".bin"
        binv.mkdir()
        os.symlink("../pkg/cli.js", binv / "cli")
        return nm

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_bun_style_dotbin_relative_symlink_is_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nm = self._bun_style_node_modules(tmp)
            digest = bind.tree_digest(str(nm))
            self.assertEqual(len(digest), 64)
            self.assertEqual(digest, bind.tree_digest(str(nm)))
            os.remove(nm / ".bin" / "cli")
            os.symlink("../pkg/index.js", nm / ".bin" / "cli")
            self.assertNotEqual(digest, bind.tree_digest(str(nm)))

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_file_only_tree_digest_matches_pre_symlink_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "action.yml"
            path.write_text("name: pin\n", encoding="utf-8")
            rel = "action.yml"
            expected = hashlib.sha256()
            expected.update(rel.encode("utf-8"))
            expected.update(b"\0")
            expected.update(bind.sha256_file(str(path)).encode("ascii"))
            expected.update(b"\n")
            self.assertEqual(bind.tree_digest(tmp), expected.hexdigest())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_absolute_and_escaping_symlinks_still_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nm = Path(tmp) / "node_modules"
            nm.mkdir()
            (nm / "ok.txt").write_text("x\n", encoding="utf-8")
            os.symlink("/etc/passwd", nm / "abs")
            with self.assertRaises(bind.BindError) as ctx:
                bind.tree_digest(str(nm))
            self.assertEqual(str(ctx.exception), "symlink_abs")
            os.remove(nm / "abs")
            os.symlink("../../etc/passwd", nm / "escape")
            with self.assertRaises(bind.BindError) as ctx:
                bind.tree_digest(str(nm))
            self.assertEqual(str(ctx.exception), "symlink_escape")

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_lexical_bounce_through_outside_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nm = Path(tmp) / "node_modules"
            pkg = nm / "pkg"
            pkg.mkdir(parents=True)
            (pkg / "cli.js").write_text("console.log(1)\n", encoding="utf-8")
            (nm / ".bin").mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            os.symlink(pkg, outside / "back")
            bounce = os.path.relpath(outside / "back" / "cli.js", nm / ".bin")
            self.assertFalse(os.path.isabs(bounce))
            os.symlink(bounce, nm / ".bin" / "cli")
            # Following intermediates would land back in pkg/; lexical path leaves nm.
            with self.assertRaises(bind.BindError) as ctx:
                bind.tree_digest(str(nm))
            self.assertEqual(str(ctx.exception), "symlink_escape")
            bind.require_in_tree_symlink(
                str(nm), str(nm / ".bin" / "ok"), "../pkg/cli.js"
            )

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_dotdot_after_in_tree_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nm = Path(tmp) / "node_modules"
            sub = nm / "sub"
            sub.mkdir(parents=True)
            os.symlink("..", sub / "a")
            os.symlink("sub/a/../../outside.js", nm / "cli")
            (Path(tmp) / "outside.js").write_text("pwned\n", encoding="utf-8")
            with self.assertRaises(bind.BindError) as ctx:
                bind.tree_digest(str(nm))
            self.assertEqual(str(ctx.exception), "symlink_escape")

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_symlinked_ancestor_of_root_still_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            nm = real / "nm"
            pkg = nm / "pkg"
            pkg.mkdir(parents=True)
            (pkg / "cli.js").write_text("console.log(1)\n", encoding="utf-8")
            (nm / ".bin").mkdir()
            os.symlink("../pkg/cli.js", nm / ".bin" / "cli")
            alias = Path(tmp) / "alias"
            os.symlink(real, alias)
            via_alias = alias / "nm"
            digest = bind.tree_digest(str(nm))
            self.assertEqual(digest, bind.tree_digest(str(via_alias)))
            self.assertEqual(len(digest), 64)

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_dotbin_through_in_tree_dir_symlink_is_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nm = Path(tmp) / "node_modules"
            real = nm / ".bun" / "pkg@1"
            real.mkdir(parents=True)
            (real / "cli.js").write_text("console.log(1)\n", encoding="utf-8")
            os.symlink(".bun/pkg@1", nm / "pkg")
            (nm / ".bin").mkdir()
            os.symlink("../pkg/cli.js", nm / ".bin" / "cli")
            digest = bind.tree_digest(str(nm))
            self.assertEqual(len(digest), 64)
            self.assertEqual(digest, bind.tree_digest(str(nm)))

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_directory_symlink_is_recorded_and_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nm = Path(tmp) / "node_modules"
            real = nm / ".bun" / "pkg@1"
            real.mkdir(parents=True)
            (real / "index.js").write_text("export {}\n", encoding="utf-8")
            alias = nm / "pkg"
            os.symlink(".bun/pkg@1", alias)
            digest = bind.tree_digest(str(nm))
            self.assertEqual(len(digest), 64)
            os.remove(alias)
            os.symlink("/tmp", alias)
            with self.assertRaises(bind.BindError) as ctx:
                bind.tree_digest(str(nm))
            self.assertEqual(str(ctx.exception), "symlink_abs")


class BootstrapContractTests(unittest.TestCase):
    def test_sudoers_and_negative_controls_are_in_0b(self) -> None:
        text = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn('Defaults env_reset', text)
        self.assertIn('Defaults secure_path="/usr/sbin:/usr/bin:/sbin:/bin"', text)
        self.assertIn("runner ALL=(root) NOPASSWD: /opt/csl-relay/bin/relay", text)
        self.assertNotIn("%sudo   ALL=(ALL:ALL) NOPASSWD:ALL", text)
        self.assertIn("NOPASSWD:ALL remains", text)  # fail-closed check, not a grant
        self.assertIn("runuser -u runner --", text)
        self.assertIn("sudo -n /bin/true", text)
        self.assertIn("sudo -n -u nobody /bin/true", text)
        self.assertIn("sudo -n -u nobody /opt/csl-relay/bin/relay", text)
        self.assertIn("sudo -n -E /opt/csl-relay/bin/relay", text)
        self.assertIn("sudo -n FOO=1 /opt/csl-relay/bin/relay", text)
        self.assertIn("sudo -n /opt/csl-relay/bin/filter", text)
        self.assertIn("sudo -n /opt/csl-claude/launch", text)
        self.assertIn("cmp -s /etc/sudoers", text)
        self.assertIn("Runner.Worker", text)
        self.assertIn("mcp.empty.json", text)
        self.assertNotIn("worker.environ", text)
        self.assertNotIn("grep -qv 'NOPASSWD:ALL'", text)
        self.assertIn("sandbox_e3_record.py\" write", text)
        self.assertIn("check-ld", text)
        self.assertIn("kernel.yama.ptrace_scope=2", text)
        self.assertIn("systemctl stop", text)
        self.assertIn("systemctl mask", text)
        self.assertIn("-static", text)
        self.assertIn('"$GCC" -static', text)
        self.assertIn("/opt/csl-relay/bin/capture-start", text)
        self.assertIn("visudo -cf", text)
        self.assertNotIn("CSL_ACTION_DIR", text)
        self.assertNotIn("CSL_RELAY_STATE", text)
        self.assertIn("hash-git-tree", text)
        self.assertIn("hash-node-modules", text)
        self.assertIn("hash-setup-bun", text)
        self.assertIn("sandbox_e3_cli_pin.sh", text)
        self.assertIn("sandbox_e3_action_bun.sh", text)
        self.assertIn("/opt/csl-bun-action/bun", text)
        self.assertIn("check-node-modules", (SCRIPTS / "sandbox_e3_action_bun.sh").read_text(encoding="utf-8"))
        self.assertIn('readlink /proc/self/ns/mnt', text)
        self.assertIn('"$STATE/launch.sha256"', text)
        self.assertIn('GUARD="/home/runner/work/_temp/claude-fixer-guard"', text)
        self.assertNotIn('"$PYTHON" -I "$GUARD/model-api" install', text)
        self.assertIn('test -f "$GUARD/context.json"', text)
        self.assertNotIn("${RUNNER_TEMP:?}", text)
        self.assertNotIn("${GITHUB_WORKSPACE:-}", text)
        self.assertNotIn("command -v bun", text)
        self.assertIn('BUN_SRC="/home/runner/.bun/bin/bun"', text)
        self.assertIn("/home/runner/work/_temp/csl-e3-bun-path", text)
        self.assertIn('"$AP/bin/bun"', text)
        self.assertIn("1.3.14", text)
        self.assertIn(
            "/home/runner/work/ai-investment-cockpit/ai-investment-cockpit",
            text,
        )
        self.assertNotIn(
            'readlink /proc/"$WORKER_PID"/ns/mnt > "$STATE/ns.mnt"',
            text,
        )

    def test_cli_pin_uses_compiled_in_runner_temp(self) -> None:
        text = (SCRIPTS / "sandbox_e3_cli_pin.sh").read_text(encoding="utf-8")
        self.assertIn('STAGING="/home/runner/work/_temp/csl-claude-staging"', text)
        self.assertNotIn("${RUNNER_TEMP:?}", text)

    def test_gnu_install_refuses_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sudoers.new"
            path.write_text("runner ALL=(root) NOPASSWD: /opt/csl-relay/bin/relay\n", encoding="utf-8")
            proc = subprocess.run(
                ["install", "-m0440", str(path), str(path)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("same file", proc.stderr.lower())

    def test_bootstrap_installs_sudoers_from_a_temp_source(self) -> None:
        text = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        self.assertNotIn(
            "install -m0440 -o root -g root /etc/sudoers.new /etc/sudoers.new",
            text,
        )
        self.assertIn('SUDOERS_SRC="$(mktemp)"', text)
        self.assertIn(
            'install -m0440 -o root -g root "$SUDOERS_SRC" /etc/sudoers.new',
            text,
        )
        self.assertIn("visudo -cf /etc/sudoers.new", text)
        self.assertLess(text.find("mktemp"), text.find('install -m0440'))

    def test_install_from_temp_source_sets_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "sudoers.new"
            src.write_text("x\n", encoding="utf-8")
            subprocess.run(["install", "-m0440", str(src), str(dst)], check=True)
            self.assertEqual(stat.S_IMODE(dst.stat().st_mode), 0o440)

    def test_cli_pin_checks_writability_as_runner(self) -> None:
        text = (SCRIPTS / "sandbox_e3_cli_pin.sh").read_text(encoding="utf-8")
        self.assertNotIn('if [ -w "$DEST" ] || [ -w "$DEST_DIR" ]; then', text)
        self.assertIn('runuser -u runner -- test -w "$DEST"', text)
        self.assertIn('runuser -u runner -- test -w "$DEST_DIR"', text)

    def test_dash_w_is_true_for_the_owner_of_a_755_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "claude"
            dest.write_bytes(b"x")
            dest.chmod(0o755)
            proc = subprocess.run(
                ["bash", "-c", 'test -w "$1"', "--", str(dest)],
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            mode = dest.stat().st_mode
            self.assertFalse(mode & 0o022)

    def test_bootstrap_seeds_ap_bin_bun_after_hash_git_tree(self) -> None:
        text = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        hash_at = text.find('sandbox_e3_bind.py" hash-git-tree')
        bun_at = text.find(
            'install -D -m0755 -o root -g root "$BUN_DST" "$AP/bin/bun"'
        )
        remount_at = text.find('mount -o remount,bind,ro "$AP"')
        self.assertGreater(hash_at, 0)
        self.assertGreater(bun_at, hash_at)
        self.assertGreater(remount_at, bun_at)
        self.assertNotIn("still P9", text)
        touch_at = text.find('touch "$AP/csl-e3-ro-probe"')
        mkdir_at = text.find('mkdir "$AP/csl-e3-ro-dir"')
        self.assertGreater(touch_at, remount_at)
        self.assertGreater(mkdir_at, remount_at)

    def test_bootstrap_precreates_refuse_and_f1_miss(self) -> None:
        text = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn(
            'install -m0600 -o runner -g runner /dev/null "$STATE/launch.refuse"',
            text,
        )
        self.assertIn(
            'install -m0600 -o root -g root /dev/null "$STATE/f1.miss"',
            text,
        )
        mkdir_at = text.find('chmod 0711 "$STATE"')
        refuse_at = text.find("$STATE/launch.refuse")
        miss_at = text.find("$STATE/f1.miss")
        self.assertGreater(refuse_at, mkdir_at)
        self.assertGreater(miss_at, mkdir_at)

    def test_bootstrap_git_credential_overlay_after_hash_git_tree(self) -> None:
        text = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        hash_at = text.find('sandbox_e3_bind.py" hash-git-tree')
        bun_at = text.find(
            'install -D -m0755 -o root -g root "$BUN_DST" "$AP/bin/bun"'
        )
        placeholder_at = text.find(': > "$AP/.git-credential-gh-token"')
        remount_at = text.find('mount -o remount,bind,ro "$AP"')
        bind_at = text.find(
            'mount --bind "$STATE/git-credential.backing" "$AP/.git-credential-gh-token"'
        )
        writable_at = text.find("git-credential overlay not runner-writable")
        parent_at = text.find("$AP writable after credential overlay")
        self.assertGreater(hash_at, 0)
        self.assertGreater(bun_at, hash_at)
        self.assertGreater(placeholder_at, bun_at)
        self.assertGreater(remount_at, placeholder_at)
        self.assertGreater(bind_at, remount_at)
        self.assertGreater(writable_at, bind_at)
        self.assertGreater(parent_at, bind_at)
        self.assertNotIn("remount,bind,rw", text)
        self.assertNotIn("mount -o remount,rw", text)
        self.assertIn(
            'install -m0600 -o runner -g runner /dev/null "$STATE/git-credential.backing"',
            text,
        )

    def test_nested_file_bind_stays_writable_on_ro_parent(self) -> None:
        """File bind over remount-ro parent stays rw; parent stays ro."""
        script = r"""
set -euo pipefail
root="$1"
mkdir -p "$root/ap" "$root/state"
: > "$root/ap/.git-credential-gh-token"
: > "$root/state/git-credential.backing"
chmod 0600 "$root/ap/.git-credential-gh-token" "$root/state/git-credential.backing"
mount --bind "$root/ap" "$root/ap"
mount -o remount,bind,ro "$root/ap"
if touch "$root/ap/probe" 2>/dev/null; then
  echo "parent still rw" >&2
  exit 2
fi
mount --bind "$root/state/git-credential.backing" "$root/ap/.git-credential-gh-token"
if ! test -w "$root/ap/.git-credential-gh-token"; then
  echo "overlay not writable" >&2
  exit 3
fi
printf 'helper\n' > "$root/ap/.git-credential-gh-token"
got="$(cat "$root/state/git-credential.backing")"
test "$got" = "helper"
if touch "$root/ap/probe2" 2>/dev/null; then
  echo "parent became rw" >&2
  exit 4
fi
"""
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                ["unshare", "-rm", "bash", "-c", script, "nested", tmp],
                check=False,
                capture_output=True,
                text=True,
            )
            # Hosted runners often block unprivileged user namespaces
            # (`uid_map` EPERM). Production overlay runs as root in 0b, so
            # the contract test above is the required CI gate; this proves
            # the mount flags only where userns is allowed.
            if proc.returncode != 0 and "Operation not permitted" in (proc.stderr or ""):
                self.skipTest("unprivileged user namespaces are disabled")
            self.assertEqual(
                proc.returncode,
                0,
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}",
            )

    def test_bootstrap_installs_action_bun_wrapper_after_digest_before_remount(self) -> None:
        text = (SCRIPTS / "sandbox_e3_bootstrap.sh").read_text(encoding="utf-8")
        digest_at = text.find('sandbox_e3_bind.py" hash-node-modules')
        wrapper_at = text.find(
            'install -D -m0755 -o root -g root "$LIB/sandbox_e3_action_bun.sh" /opt/csl-bun-action/bun'
        )
        remount_at = text.find('mount -o remount,bind,ro "$AP"')
        elf_at = text.find(
            'install -D -m0755 -o root -g root "$BUN_DST" "$AP/bin/bun"'
        )
        self.assertGreater(digest_at, 0)
        self.assertGreater(wrapper_at, digest_at)
        self.assertGreater(elf_at, wrapper_at)
        self.assertGreater(remount_at, elf_at)
        self.assertIn("action bun wrapper collides with ELF", text)
        self.assertIn('BUN_DST=/opt/csl-bun/bun', text)
        self.assertNotIn("remount,bind,rw", text)


class CheckNodeModulesDigestTests(unittest.TestCase):
    """Hosted 40359709793: action bun install EEXIST vs E3 pre-populated tree."""

    def _tree(self, tmp: str) -> Path:
        nm = Path(tmp) / "node_modules"
        pkg = nm / "@anthropic-ai" / "sdk"
        pkg.mkdir(parents=True)
        (pkg / "index.js").write_text("export {}\n", encoding="utf-8")
        return nm

    def test_matching_digest_ok_mismatch_and_missing_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp)
            state = Path(tmp) / "state"
            state.mkdir()
            digest = bind.tree_digest(str(Path(tmp) / "node_modules"))
            (state / "node_modules.digest").write_text(digest + "\n", encoding="ascii")
            self.assertEqual(
                bind.check_node_modules_digest(action_dir=tmp, state_dir=str(state)),
                digest,
            )
            (state / "node_modules.digest").write_text("0" * 64 + "\n", encoding="ascii")
            with self.assertRaises(bind.BindError) as ctx:
                bind.check_node_modules_digest(action_dir=tmp, state_dir=str(state))
            self.assertEqual(str(ctx.exception), "node_modules")
            (state / "node_modules.digest").unlink()
            with self.assertRaises(bind.BindError) as ctx:
                bind.check_node_modules_digest(action_dir=tmp, state_dir=str(state))
            self.assertEqual(str(ctx.exception), "node_modules")

    def test_rejects_non_hex_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp)
            state = Path(tmp) / "state"
            state.mkdir()
            (state / "node_modules.digest").write_text("not-a-digest\n", encoding="ascii")
            with self.assertRaises(bind.BindError) as ctx:
                bind.check_node_modules_digest(action_dir=tmp, state_dir=str(state))
            self.assertEqual(str(ctx.exception), "node_modules")


class ActionBunWrapperTests(unittest.TestCase):
    """Rewrite compiled-in paths so the wrapper can run off the hosted layout."""

    def _layout(self, tmp: str, *, python_rc: int = 0) -> dict:
        root = Path(tmp)
        ap = root / "ap"
        ap.mkdir()
        workspace = root / "workspace"
        workspace.mkdir()
        bun_dir = root / "opt-csl-bun"
        bun_dir.mkdir()
        bun_elf = bun_dir / "bun"
        marker = root / "marker"
        bun_elf.write_text(
            "#!/bin/bash\n"
            f'printf "elf %s\\n" "$*" >> "{marker}"\n',
            encoding="utf-8",
        )
        bun_elf.chmod(0o755)
        py = root / "python3"
        py.write_text(
            "#!/bin/bash\n"
            f'printf "check %s\\n" "$*" >> "{marker}"\n'
            f"exit {python_rc}\n",
            encoding="utf-8",
        )
        py.chmod(0o755)
        bind_py = root / "sandbox_e3_bind.py"
        bind_py.write_text("# stub; wrapper execs python -I this check-node-modules\n", encoding="utf-8")
        src = (SCRIPTS / "sandbox_e3_action_bun.sh").read_text(encoding="utf-8")
        src = src.replace("/opt/csl-bun/bun", str(bun_elf))
        src = src.replace(
            "/home/runner/work/_actions/anthropics/claude-code-action/"
            "a5a423f75c251dcbff8a74e99f83b2cb03c72bfa",
            str(ap),
        )
        src = src.replace("/usr/bin/python3", str(py))
        src = src.replace("/opt/csl-claude/lib/sandbox_e3_bind.py", str(bind_py))
        wrapper = root / "wrapper"
        wrapper.write_text(src, encoding="utf-8")
        wrapper.chmod(0o755)
        return {
            "wrapper": str(wrapper),
            "ap": str(ap),
            "workspace": str(workspace),
            "marker": marker,
        }

    def _run(self, layout: dict, cwd: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [layout["wrapper"], *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def test_action_install_checks_digest_and_does_not_exec_elf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(tmp)
            proc = self._run(layout, layout["ap"], ["install", "--production"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = layout["marker"].read_text(encoding="utf-8")
            self.assertIn("check ", text)
            self.assertIn("check-node-modules", text)
            self.assertNotIn("elf ", text)

    def test_action_install_mismatch_does_not_fall_through_to_elf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(tmp, python_rc=1)
            proc = self._run(layout, layout["ap"], ["install", "--production"])
            self.assertNotEqual(proc.returncode, 0)
            text = layout["marker"].read_text(encoding="utf-8")
            self.assertIn("check ", text)
            self.assertNotIn("elf ", text)

    def test_workspace_install_and_non_install_exec_elf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(tmp)
            proc = self._run(layout, layout["workspace"], ["install", "--production"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            proc = self._run(layout, layout["ap"], ["--version"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = layout["marker"].read_text(encoding="utf-8")
            self.assertIn("elf install --production", text)
            self.assertIn("elf --version", text)
            self.assertNotIn("check ", text)

    def test_wrapper_keeps_compiled_in_hosted_paths(self) -> None:
        text = (SCRIPTS / "sandbox_e3_action_bun.sh").read_text(encoding="utf-8")
        self.assertIn("BUN_ELF=/opt/csl-bun/bun", text)
        self.assertIn(
            "AP=/home/runner/work/_actions/anthropics/claude-code-action/"
            "a5a423f75c251dcbff8a74e99f83b2cb03c72bfa",
            text,
        )
        self.assertNotIn("${CSL_", text)
        self.assertIn('exec "$PYTHON" -I "$BIND" check-node-modules', text)
        self.assertIn('exec "$BUN_ELF" "$@"', text)
        self.assertLess(
            text.find('exec "$PYTHON" -I "$BIND" check-node-modules'),
            text.find('exec "$BUN_ELF" "$@"'),
        )


if __name__ == "__main__":
    unittest.main()
