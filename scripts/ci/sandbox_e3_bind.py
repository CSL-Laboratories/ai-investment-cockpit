#!/usr/bin/env python3
"""E3-0-early I3/I4 bind-mount order, pin hashes, and U-16 record hooks (CSL-474 D10).

Reviewed grammar for the mention-job 0b prefix. Hosted 0b embeds this file from
the workflow YAML (not PR-tree ``app/``) and runs it with system
``/usr/bin/python3 -I``. E3-1 (signed CLI pin under ``/opt/csl-claude/<ver>``)
is CSL-473's PR; this module does not download or install that binary.

U-16/U-18 hosted values stay ``[A]``: this records files and parent pids; it
does not claim the GitHub-hosted image matches the actions/runner Dockerfile.
"""
from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

ACTION_PIN = "a5a423f75c251dcbff8a74e99f83b2cb03c72bfa"
ACTION_DIR = (
    "/home/runner/work/_actions/anthropics/claude-code-action/" + ACTION_PIN
)
SETUP_BUN_PIN = "0c5077e51419868618aeaa5fe8019c62421857d6"
SETUP_BUN_USES = f"oven-sh/setup-bun@{SETUP_BUN_PIN}"
SETUP_BUN_DIR = (
    "/home/runner/work/_actions/oven-sh/setup-bun/" + SETUP_BUN_PIN
)
BUN_VERSION = "1.3.14"
CHECKOUT_PIN = "11d5960a326750d5838078e36cf38b85af677262"
CHECKOUT_USES = f"actions/checkout@{CHECKOUT_PIN}"
BUN_EXE = "/opt/csl-bun/bun"
BUN_HOME_BIN = "/home/runner/.bun/bin"
RELAY_STATE = "/var/lib/csl-relay"
LIB_DIR = "/opt/csl-claude/lib"

# D10 §1.3 / E3-5. Same constants as sandbox_e3_record.py.
ACTION_YML_SHA256 = "87b706489e63483c9c32c12af36467dd34e309becca35f7a8ead4d1e0664c4df"
SDK_MJS_SHA256 = "48480d49d4493e2aec28227d8db8f8432743c7aa32facbdb9a7ce0a0363f7503"

# J2: outermost first; $AP last, then remount ro. Ancestors stay rw.
BIND_ORDER: Tuple[str, ...] = (
    "/home/runner/work",
    "/home/runner/work/_actions",
    "/home/runner/work/_actions/anthropics",
    "/home/runner/work/_actions/anthropics/claude-code-action",
    ACTION_DIR,
)

class BindError(RuntimeError):
    """Fail closed. Callers must not copy untrusted paths into the public log."""


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_sdk_mjs(action_dir: str) -> str:
    """Exactly one ``sdk.mjs`` under the pinned action dir, or fail closed."""
    matches: List[str] = []
    root = Path(action_dir)
    if not root.is_dir():
        raise BindError("action_dir")
    for path in root.rglob("sdk.mjs"):
        if path.is_file() and not path.is_symlink():
            matches.append(str(path))
    if len(matches) != 1:
        raise BindError("sdk_mjs")
    return matches[0]


def verify_pins(
    *,
    action_dir: str,
    action_yml_sha256: str = ACTION_YML_SHA256,
    sdk_mjs_sha256: str = SDK_MJS_SHA256,
) -> Dict[str, str]:
    yml = os.path.join(action_dir, "action.yml")
    if not os.path.isfile(yml):
        raise BindError("action_yml")
    got_yml = sha256_file(yml)
    if got_yml != action_yml_sha256:
        raise BindError("action_yml_hash")
    sdk = find_sdk_mjs(action_dir)
    got_sdk = sha256_file(sdk)
    if got_sdk != sdk_mjs_sha256:
        raise BindError("sdk_mjs_hash")
    return {
        "action_yml_sha256": got_yml,
        "sdk_mjs_sha256": got_sdk,
        "sdk_mjs_path": sdk,
        "sdk_source": "downloaded-action-dir-frozen-lockfile-ignore-scripts",
        "sdk_disk_match": "true",
    }


def inode_of(path: str) -> Tuple[int, int]:
    info = os.lstat(path)
    return (info.st_dev, info.st_ino)


def parse_mountinfo(text: str) -> List[Dict[str, str]]:
    """Minimal ``/proc/self/mountinfo`` parse: mount_id, parent_id, target, options."""
    rows: List[Dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        left, _sep, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 6:
            raise BindError("mountinfo")
        rows.append(
            {
                "mount_id": fields[0],
                "parent_id": fields[1],
                "target": fields[4],
                "options": fields[5],
            }
        )
    return rows


def bind_ids_for(targets: Sequence[str], rows: Sequence[Mapping[str, str]]) -> Dict[str, str]:
    """Last mountinfo row whose target matches, per D10 J2 record."""
    found: Dict[str, str] = {}
    for row in rows:
        if row["target"] in targets:
            found[row["target"]] = row["mount_id"]
    missing = [path for path in targets if path not in found]
    if missing:
        raise BindError("mount_ids")
    return found


def nsswitch_sudoers_ok(text: str) -> bool:
    """D10 0b step 5: fail closed on sudoers: ldap/sss."""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.lower().startswith("sudoers:"):
            continue
        rest = line.split(":", 1)[1].lower()
        if "ldap" in rest or "sss" in rest:
            return False
    return True


def sudo_conf_plugin_ok(text: str) -> bool:
    """D10 0b step 5: fail closed if sudo.conf loads a Plugin that overrides policy."""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.lower().startswith("plugin"):
            return False
    return True


def record_u16(state_dir: str, sudoers: str, nsswitch: str, sudo_conf: str) -> str:
    """Write hashes and a marker. Does not claim the hosted image matches [A]."""
    os.makedirs(state_dir, exist_ok=True)
    payload = (
        "u16_status=[A]\n"
        f"sudoers_sha256={hashlib.sha256(sudoers.encode('utf-8', 'replace')).hexdigest()}\n"
        f"nsswitch_sha256={hashlib.sha256(nsswitch.encode('utf-8', 'replace')).hexdigest()}\n"
        f"sudo_conf_sha256={hashlib.sha256(sudo_conf.encode('utf-8', 'replace')).hexdigest()}\n"
        "image_match=unproven\n"
        "see=f-u16.md\n"
    )
    path = os.path.join(state_dir, "u16.record")
    with open(path, "w", encoding="ascii") as handle:
        handle.write(payload)
    os.chmod(path, 0o600)
    return path


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def check_u16_policy(*, nsswitch_path: str = "/etc/nsswitch.conf", sudo_conf_path: str = "/etc/sudo.conf") -> None:
    nsswitch = _read_text(nsswitch_path)
    if not nsswitch_sudoers_ok(nsswitch):
        raise BindError("nsswitch")
    sudo_conf = _read_text(sudo_conf_path)
    if not sudo_conf_plugin_ok(sudo_conf):
        raise BindError("sudo_conf")


def record_path_inode(path: str, dest: str) -> None:
    dev, ino = inode_of(path)
    Path(dest).write_text(f"dev={dev}\nino={ino}\nmode={stat.S_IMODE(os.lstat(path).st_mode):o}\n", encoding="ascii")


def empty_root_owned_dir(
    path: str, *, uid: int | None = None, gid: int | None = None, mode: int = 0o755
) -> None:
    """K1: ``~/.bun/bin`` empty, root-owned 0755 after copying bun to /opt/csl-bun.

    Hosted 0b is already root, so euid/egid are 0. Tests pass through the
    current ids so they can run unprivileged.
    """
    if uid is None:
        uid = os.geteuid()
    if gid is None:
        gid = os.getegid()
    os.makedirs(path, exist_ok=True)
    for name in os.listdir(path):
        target = os.path.join(path, name)
        if os.path.isdir(target) and not os.path.islink(target):
            raise BindError("bun_bin_nested")
        os.remove(target)
    os.chown(path, uid, gid)
    os.chmod(path, mode)
    if os.listdir(path):
        raise BindError("bun_bin_not_empty")


def git_blob_sha1(path: str) -> str:
    data = Path(path).read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def parse_ls_tree(text: str) -> Dict[str, str]:
    """``git ls-tree -r`` → path to blob sha1."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        _mode, kind, sha = meta.split()
        if kind != "blob":
            continue
        out[path] = sha
    return out


def _rel_posix(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _rel_under_root(root_abs: str, dest: str) -> str:
    dest = os.path.normpath(dest)
    try:
        common = os.path.commonpath([root_abs, dest])
    except ValueError as exc:
        raise BindError("symlink_escape") from exc
    if common != root_abs:
        raise BindError("symlink_escape")
    rel = os.path.relpath(dest, root_abs).replace("\\", "/")
    if rel.startswith(".."):
        raise BindError("symlink_escape")
    return rel


def lexical_in_tree_dest(root: str, link_path: str, target: str) -> str:
    """Join ``target`` onto the link without following any symlink.

    ``Path.resolve()`` follows intermediates, so a relative link that
    lexically leaves ``root`` and re-enters through an outside symlink
    would pass and later redirect without changing the hashed text.
    ``abspath``+``normpath`` stay lexical.
    """
    if not target or "\0" in target or "\n" in target or "\r" in target:
        raise BindError("symlink")
    if os.path.isabs(target):
        raise BindError("symlink_abs")
    root_abs = os.path.normpath(os.path.abspath(root))
    dest = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(link_path)), target)
    )
    return _rel_under_root(root_abs, dest)


def require_in_tree_symlink(root: str, link_path: str, target: str) -> str:
    """Allow bun's in-tree ``node_modules/.bin`` links; refuse abs/escape.

    Lexical containment and followed resolution must both stay under
    ``root``. Lexical-only missed ``sub/a -> ..`` plus ``cli ->
    sub/a/../../outside`` (Devin SEC / Codex P1 on #281). Returns
    ``lexical\\0resolved`` relative paths for the digest. Callers must
    not put the path into the public log.
    """
    lexical = lexical_in_tree_dest(root, link_path, target)
    # Followed dest uses realpath(root): abspath leaves a symlinked ancestor
    # unresolved (macOS /var → /private/var, or a linked work dir), so every
    # in-tree link compared as symlink_escape (Devin BUG on #281).
    root_real = os.path.realpath(root)
    try:
        resolved = str((Path(os.path.abspath(link_path)).parent / target).resolve())
    except OSError as exc:
        raise BindError("symlink") from exc
    resolved_rel = _rel_under_root(root_real, resolved)
    return lexical + "\0" + resolved_rel


def walk_files(root: str, *, skip_dirs: Sequence[str] = ()) -> List[str]:
    """Regular files only. Any symlink in the git-tracked action tree fails closed."""
    skip = set(skip_dirs)
    found: List[str] = []
    base = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames):
            if (Path(dirpath) / name).is_symlink():
                raise BindError("symlink")
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                raise BindError("symlink")
            rel = _rel_posix(base, full)
            found.append(rel)
    return sorted(found)


def check_git_tree(action_dir: str, ls_tree: str, *, skip_dirs: Sequence[str] = ("node_modules",)) -> Dict[str, str]:
    """Fail closed on blob mismatch or extra files (D10:204). ``bin/`` is not excluded."""
    expected = parse_ls_tree(ls_tree)
    on_disk = walk_files(action_dir, skip_dirs=skip_dirs)
    extra = [p for p in on_disk if p not in expected]
    missing = [p for p in expected if p not in on_disk]
    if extra or missing:
        raise BindError("git_tree")
    for rel, sha in expected.items():
        got = git_blob_sha1(os.path.join(action_dir, rel))
        if got != sha:
            raise BindError("git_tree_hash")
    return {"files": str(len(expected))}


def iter_tree_entries(root: str) -> List[Tuple[str, str, str]]:
    """Regular files and in-tree relative symlinks. Does not follow dir links."""
    base = Path(root)
    entries: List[Tuple[str, str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames):
            full = Path(dirpath) / name
            if not full.is_symlink():
                continue
            rel = _rel_posix(base, full)
            target = os.readlink(full)
            lexical = require_in_tree_symlink(root, str(full), target)
            entries.append((rel, "symlink", target + "\0" + lexical))
            dirnames.remove(name)
        for name in filenames:
            full = Path(dirpath) / name
            rel = _rel_posix(base, full)
            if full.is_symlink():
                target = os.readlink(full)
                lexical = require_in_tree_symlink(root, str(full), target)
                entries.append((rel, "symlink", target + "\0" + lexical))
            elif full.is_file():
                entries.append((rel, "file", sha256_file(str(full))))
            else:
                raise BindError("tree")
    entries.sort(key=lambda item: item[0])
    return entries


def tree_digest(root: str) -> str:
    """Ordered digest of regular files and in-tree relative symlink targets.

    File records stay ``rel\\0sha256\\n`` so a symlink-free tree matches the
    pre-40262408416 hasher. Symlinks append
    ``rel\\0symlink\\0target\\0lexical_rel\\0resolved_rel\\n``.
    """
    digest = hashlib.sha256()
    base = Path(root)
    if base.is_symlink() or not base.is_dir():
        raise BindError("tree")
    for rel, kind, payload in iter_tree_entries(root):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        if kind == "symlink":
            digest.update(b"symlink\0")
            digest.update(os.fsencode(payload))
        else:
            digest.update(payload.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def worker_ns_mnt(path: str, expected: str) -> None:
    got = Path(path).read_text(encoding="ascii").strip()
    if not got or got != expected.strip():
        raise BindError("ns_mnt")


_HEX64 = frozenset("0123456789abcdef")


def check_node_modules_digest(
    action_dir: str = ACTION_DIR, state_dir: str = RELAY_STATE
) -> str:
    """Fail closed unless ``$AP/node_modules`` matches the 0b digest.

    Hosted 40359709793: the action's ``bun install --production`` collided
    (EEXIST) with the already-populated, remount-ro tree. The action-PATH
    wrapper calls this instead of the ELF; it must not fall through to
    ``bun install``.
    """
    digest_path = Path(state_dir, "node_modules.digest")
    try:
        expected = digest_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise BindError("node_modules") from exc
    if len(expected) != 64 or any(char not in _HEX64 for char in expected):
        raise BindError("node_modules")
    got = tree_digest(os.path.join(action_dir, "node_modules"))
    if got != expected:
        raise BindError("node_modules")
    return got


def main(argv: Sequence[str]) -> int:
    sys.tracebacklimit = 0
    cmd = argv[1] if len(argv) > 1 else ""
    action_dir = ACTION_DIR
    state_dir = RELAY_STATE
    if cmd == "empty-bun-bin":
        empty_root_owned_dir(BUN_HOME_BIN)
        return 0
    if cmd == "verify-pins":
        pins = verify_pins(action_dir=action_dir)
        sys.stdout.write(f"sdk_disk_match={pins['sdk_disk_match']}\n")
        return 0
    if cmd == "check-u16":
        check_u16_policy()
        record_u16(
            state_dir,
            _read_text("/etc/sudoers"),
            _read_text("/etc/nsswitch.conf"),
            _read_text("/etc/sudo.conf"),
        )
        return 0
    if cmd == "record-inodes":
        os.makedirs(state_dir, exist_ok=True)
        record_path_inode(BUN_EXE, os.path.join(state_dir, "bun.inode"))
        record_path_inode(BUN_HOME_BIN, os.path.join(state_dir, "bun-bin.inode"))
        record_path_inode(action_dir, os.path.join(state_dir, "ap.inode"))
        return 0
    if cmd == "hash-git-tree":
        ls_tree = Path(os.path.join(state_dir, "cca.ls-tree")).read_text(encoding="utf-8")
        check_git_tree(action_dir, ls_tree)
        return 0
    if cmd == "hash-node-modules":
        digest = tree_digest(os.path.join(action_dir, "node_modules"))
        Path(state_dir, "node_modules.digest").write_text(digest + "\n", encoding="ascii")
        return 0
    if cmd == "check-node-modules":
        check_node_modules_digest()
        sys.stdout.write("node_modules_digest_ok\n")
        return 0
    if cmd == "hash-setup-bun":
        digest = tree_digest(SETUP_BUN_DIR)
        Path(state_dir, "setup-bun.digest").write_text(digest + "\n", encoding="ascii")
        return 0
    if cmd == "observe":
        pins = verify_pins(action_dir=action_dir)
        sys.stdout.write(f"sdk_disk_match={pins['sdk_disk_match']}\n")
        check_node_modules_digest(action_dir=action_dir, state_dir=state_dir)
        bun = Path(state_dir, "setup-bun.digest").read_text(encoding="ascii").strip()
        if tree_digest(SETUP_BUN_DIR) != bun:
            raise BindError("setup_bun")
        return 0
    raise BindError("argv")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
