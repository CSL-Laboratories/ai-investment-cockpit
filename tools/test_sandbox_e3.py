#!/usr/bin/env python3
"""Unit tests for D10 E3-0-early helpers (environ, record, bind pins).

Run: python3 -X utf8 -B tools/test_sandbox_e3.py
"""
from __future__ import annotations

import importlib.util
import os
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


class EnvironK2Tests(unittest.TestCase):
    def test_k1_path_newest_first_keeps_worker_suffix(self) -> None:
        worker = "/usr/bin:/bin:/snap/bin"
        got = environ.expected_job_path(worker)
        self.assertTrue(got.startswith("/bin:/usr/bin:/opt/csl-bun:/home/runner/.bun/bin:"))
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


if __name__ == "__main__":
    unittest.main()
