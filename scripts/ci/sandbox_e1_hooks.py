#!/usr/bin/python3 -I
"""U-16 / U-18 hooks for the first E1 job (CSL-474 D4).

These record hosted facts. They do not invent image, sudo, or parent-shape
values. U-16: repo-level Actions variable names are empty on cockpit and
recursion; org-level names were 403 (unreadable residual). U-18 accepts both
F4a sudo shapes; the hosted pick stays ``[A]``.
"""
from __future__ import annotations

import os
from pathlib import Path
STATE_DIR = "/var/lib/csl-relay"
RELAY_PATH = "/opt/csl-relay/bin/relay"
SUDO_EXES = frozenset({"/usr/bin/sudo", "/usr/sbin/sudo", "/bin/sudo"})
U15_NAMES = ("SRT_DEBUG", "DEBUG_CLAUDE_AGENT_SDK", "ACTIONS_STEP_DEBUG")
ACCEPTED_SHAPES = frozenset({"sudo_fork", "sudo_exec"})


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _exists(path: str) -> bool:
    return os.path.exists(path)


def run0_status() -> str:
    """Ubuntu 24.04 ships systemd 255; run0 needs 256+. Absent ≠ denied."""
    for candidate in ("/usr/bin/run0", "/bin/run0"):
        if os.path.isfile(candidate):
            return "present_unrun"
    return "absent"


def classify_parent(
    *,
    uid: int,
    euid: int,
    ppid_exe: str,
    self_exe: str,
) -> str:
    """F4a: accept sudo-fork or sudo-exec. Do not pick the hosted shape here."""
    if uid != 0 or euid != 0:
        return "bad_parent"
    ppid_real = os.path.realpath(ppid_exe) if ppid_exe else ""
    self_real = os.path.realpath(self_exe) if self_exe else ""
    if ppid_real in SUDO_EXES or os.path.basename(ppid_real) == "sudo":
        return "sudo_fork"
    if self_real == RELAY_PATH or os.path.basename(self_real) == "relay":
        return "sudo_exec"
    return "unproven"


def accept_parent_shape(shape: str) -> bool:
    return shape in ACCEPTED_SHAPES or shape == "unproven"


def u16_inventory() -> dict[str, str]:
    nsswitch = _read("/etc/nsswitch.conf")
    sudo_conf = _read("/etc/sudo.conf")
    ldap = "yes" if "sudoers:" in nsswitch and ("ldap" in nsswitch or "sss" in nsswitch) else "no"
    plugin = "yes" if any(
        line.split("#", 1)[0].strip().lower().startswith("plugin")
        for line in sudo_conf.splitlines()
    ) else "no"
    return {
        "u16_status": "[A]",
        "see": "f-u16.md",
        "image_match": "unproven",
        "org_variable_names": "unreadable",
        "repo_variable_names": "empty",
        "nsswitch_sudoers_ldap_or_sss": ldap,
        "sudo_conf_plugin": plugin,
        "docker_sock": "present" if _exists("/var/run/docker.sock") else "absent",
        "run0": run0_status(),
        "u15_inherited": "unproven_org_names",
    }


def u18_record(shape: str) -> dict[str, str]:
    return {
        "u18_status": "[A]",
        "see": "f-u18.md",
        "accepted_shapes": "sudo_fork,sudo_exec",
        "observed_shape": shape,
        "hosted_pick": "unproven",
    }


def write_records(state_dir: str, *, shape: str = "unproven") -> None:
    os.makedirs(state_dir, exist_ok=True)
    u16 = u16_inventory()
    Path(state_dir, "u16.record").write_text(
        "".join(f"{key}={value}\n" for key, value in u16.items()),
        encoding="ascii",
    )
    u18 = u18_record(shape)
    Path(state_dir, "u18.record").write_text(
        "".join(f"{key}={value}\n" for key, value in u18.items()),
        encoding="ascii",
    )


def live_parent_shape() -> str:
    ppid = os.getppid()
    ppid_exe = ""
    try:
        ppid_exe = os.readlink(f"/proc/{ppid}/exe")
    except OSError:
        ppid_exe = ""
    self_exe = ""
    try:
        self_exe = os.readlink("/proc/self/exe")
    except OSError:
        self_exe = ""
    return classify_parent(
        uid=os.getuid(),
        euid=os.geteuid(),
        ppid_exe=ppid_exe,
        self_exe=self_exe,
    )


def main(argv: list[str]) -> int:
    del argv
    write_records(os.environ.get("CSL_RELAY_STATE", STATE_DIR), shape=live_parent_shape())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
