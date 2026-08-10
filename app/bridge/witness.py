"""W10 — concrete external-witness adapters for the SEC-11 audit anchor.

``bridge/anchor.py`` defines the ``ExternalWitnessAdapter`` seam (``publish`` + ``read_latest``)
and ``GitAuditLogAnchorStore`` that wraps one or more backends. This module supplies the two
CONCRETE backends the docstring promised, replacing the deferred SKETCH:

  * ``GitCommitWitness`` — writes ``(seq, head_hash)`` to a git-TRACKED head file and commits
    it. The out-of-domain guarantee is the git HISTORY Lucas reviews via GitHub Desktop before
    pushing: a later rewrite of a committed anchor shows in the diff, and once pushed it is
    immutable on the remote. Its strength is PROCEDURAL (it holds insofar as the history is
    reviewed/pushed) — strictly stronger than ``FileAnchorStore`` (a plain local JSONL has no
    review surface and no remote), but on its own it is a reviewed-git checkpoint, not a
    server-enforced one.
  * ``DiscordAuditLogWitness`` — posts ``(seq, head_hash)`` to the private ``#audit-log``
    channel and reads the highest post back. The guarantee is TECHNICAL: the local writer
    cannot backdate a server-stamped message, and (with a channel ACL that denies the bot
    manage-messages) cannot delete history either. This is the strongest single witness.

``GitAuditLogAnchorStore`` takes ``latest()`` as the MAX across backends, so to LOWER the
witness a keyless writer must defeat BOTH the reviewed git history AND the server-held Discord
history at once — which it cannot. Neither backend is ``FileAnchorStore`` (same-trust-domain
theater; never a witness — SEC-11).

Both adapters are APPEND-ONLY + MONOTONIC: ``publish`` refuses a rewind (lower seq) or a fork
(same seq, different hash) via ``bridge.anchor._guard_monotonic``, and re-publishing the same
head is idempotent. All external I/O is INJECTABLE (``commit_fn`` / ``post_fn`` / ``fetch_fn``)
so the adapters are fully testable without a live git repo or Discord bot; ``main.compose()``
wires the production functions from config + keyring.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bridge.anchor import AnchorError, _guard_monotonic


def _dump(seq: int, head_hash: str) -> str:
    return json.dumps({"seq": int(seq), "head_hash": head_hash},
                      sort_keys=True, separators=(",", ":"))


# ---- git-tracked head file (reviewed-history MIRROR — NOT a runtime trust source) ------
class GitCommitWitness:
    """Anchor ``(seq, head_hash)`` into git-tracked history. ``read_latest`` reads the head
    file; ``publish`` writes it (monotonic) then commits it so the anchor lands in the history
    Lucas reviews. ``commit_fn(head_file, seq, head_hash)`` is injectable (tests pass a fake);
    the default stages + commits the head file via ``git`` (no push — Lucas pushes after
    review, per the repo rule). ``commit_fn=False`` disables committing (file-only).

    **``out_of_domain = False`` — this is a MIRROR + a REVIEWED-HISTORY DETECTION layer, NOT a
    runtime trust source (SEC-11, corrected by the G10 red-team 2026-07-08).** ``read_latest``
    reads the WORKING-TREE head file, which lives in the local writer's OWN trust domain: a
    keyless writer who can ``DELETE`` the DB tail can equally overwrite ``data/audit-head.json``
    to a captured lower ``(N, H_N)``. The git-commit guarantee is PROCEDURAL (Lucas notices a
    rewrite of committed history in the diff before push) and OUT-OF-BAND — it is NOT enforced
    on the runtime read path. So ``GitAuditLogAnchorStore.latest()`` (the value the gate/mode/
    gateway/budget consult to fail closed) EXCLUDES this backend; only a genuinely out-of-domain
    witness (``DiscordAuditLogWitness``) counts. This adapter is still published-to (local
    cross-check + the reviewed-history detection layer), just never trusted for the auto/live
    unlock decision."""

    out_of_domain = False

    def __init__(self, head_file, *, repo_dir=None, commit_fn=None,
                 committer: str = "cockpit-anchor") -> None:
        self._file = Path(head_file)
        self._repo = Path(repo_dir) if repo_dir is not None else self._file.parent
        self._commit_fn = commit_fn
        self._committer = committer

    def read_latest(self) -> "tuple[int, str] | None":
        if not self._file.exists():
            return None
        try:
            rec = json.loads(self._file.read_text(encoding="utf-8"))
            return (int(rec["seq"]), str(rec["head_hash"]))
        except (ValueError, OSError, KeyError, TypeError):
            return None                                  # unreadable/corrupt head => treated as absent

    def publish(self, seq: int, head_hash: str) -> None:
        seq = int(seq)
        if not head_hash:
            raise AnchorError("cannot anchor an empty head hash")
        cur = self.read_latest()
        _guard_monotonic(cur[0] if cur else None, cur[1] if cur else None, seq, head_hash)
        if cur is not None and cur[0] == seq:
            return                                       # idempotent re-anchor of the same head
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(_dump(seq, head_hash) + "\n", encoding="utf-8")
        self._commit(seq, head_hash)

    def _commit(self, seq: int, head_hash: str) -> None:
        if self._commit_fn is False:
            return                                       # file-only mode (explicit opt-out)
        if self._commit_fn is not None:
            self._commit_fn(self._file, seq, head_hash)
            return
        msg = f"audit-anchor: seq {seq} {head_hash[:12]}"
        try:
            # BUG-056: git resolves a relative pathspec against the ``-C`` directory, and
            # ``_repo`` defaults to the head file's PARENT — so passing the caller-relative
            # path doubled the prefix (``git -C data add data/audit-head.json`` looks for
            # ``data/data/audit-head.json``, exit 128) and every real anchor commit failed.
            # The resolved absolute path is unambiguous regardless of ``-C``/CWD; ``--``
            # pins it as a pathspec. (``-C`` on a subdirectory still commits to the
            # enclosing repo — git discovers the work tree upward.)
            subprocess.run(["git", "-C", str(self._repo), "add", "--",
                            str(self._file.resolve())],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(self._repo), "commit", "-m", msg,
                            "--author", f"{self._committer} <noreply@localhost>"],
                           check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            # Fail closed: an un-committed head file is only a same-box mirror, not the
            # reviewed-history witness — do not silently downgrade the guarantee.
            raise AnchorError(f"git commit of the audit head failed: {exc}") from exc


# ---- #audit-log Discord post (server-stamped — the genuine runtime out-of-domain witness) --
class DiscordAuditLogWitness:
    """Anchor ``(seq, head_hash)`` by posting to the private ``#audit-log`` channel and read
    the highest server-recorded post back. ``post_fn(text)`` posts one message; ``fetch_fn()``
    returns the recent messages as ``[(server_ts, content), ...]`` (order-agnostic — the
    highest parsed seq wins, so the server's held history, not the local writer, decides the
    witness). Both are injectable; ``discord_rest_witness`` wires the production Discord REST
    calls.

    **``out_of_domain = True`` — this is THE runtime trust source (SEC-11).** A keyless local
    writer holds no bot token (Inv 4: keyring-only) so it cannot post/backdate a server-stamped
    message, and — with a channel ACL that denies the bot manage-messages — cannot delete
    history either. So it cannot LOWER this witness: after a replayed-watermark rollback of the
    DB, ``read_latest`` still returns the highest seq the SERVER recorded, above the rolled-back
    head, and ``verify_against_anchor`` fails closed. This is the backend ``latest()`` trusts."""

    out_of_domain = True

    _PREFIX = "audit-anchor "

    def __init__(self, *, post_fn, fetch_fn, channel_id=None) -> None:
        self._post = post_fn
        self._fetch = fetch_fn
        self._channel_id = channel_id

    @classmethod
    def _parse(cls, text) -> "tuple[int, str] | None":
        if not isinstance(text, str) or not text.startswith(cls._PREFIX):
            return None
        try:
            rec = json.loads(text[len(cls._PREFIX):])
            return (int(rec["seq"]), str(rec["head_hash"]))
        except (ValueError, KeyError, TypeError):
            return None

    def read_latest(self) -> "tuple[int, str] | None":
        try:
            posts = self._fetch() or []
        except Exception:
            # A witness we cannot READ (Discord unreachable / API error) is treated as ABSENT so
            # the consults fail CLOSED (return None), never raising into the caller. A transient
            # outage therefore blocks auto/live/deep-budget rather than crashing the loop.
            return None
        best: tuple[int, str] | None = None
        for _ts, content in posts:
            rec = self._parse(content)
            if rec is None:
                continue
            if best is None or rec[0] > best[0]:
                best = rec
        return best

    def publish(self, seq: int, head_hash: str) -> None:
        seq = int(seq)
        if not head_hash:
            raise AnchorError("cannot anchor an empty head hash")
        cur = self.read_latest()
        _guard_monotonic(cur[0] if cur else None, cur[1] if cur else None, seq, head_hash)
        if cur is not None and cur[0] == seq:
            return                                       # idempotent re-anchor of the same head
        self._post(f"{self._PREFIX}{_dump(seq, head_hash)}")


def discord_rest_witness(channel_id, bot_token: str,
                         *, timeout: float = 10.0) -> DiscordAuditLogWitness:
    """Build a ``DiscordAuditLogWitness`` bound to a channel via the Discord REST API using a
    keyring-held bot token (Inv 4 — the token never leaves this closure into a log/prompt).
    Stdlib ``urllib`` only (no async client); invoked by ``main.compose()`` ONLY when the token
    + channel are provisioned (deferred pending bot creds)."""
    import urllib.request

    base = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
        "User-Agent": "cockpit-audit-anchor (https://localhost, 1.0)",
    }

    def post_fn(text: str) -> None:
        body = json.dumps({"content": text}).encode("utf-8")
        req = urllib.request.Request(base, data=body, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=timeout).read()

    def fetch_fn():
        req = urllib.request.Request(f"{base}?limit=100", headers=headers, method="GET")
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        return [(m.get("timestamp"), m.get("content", "")) for m in data]

    return DiscordAuditLogWitness(post_fn=post_fn, fetch_fn=fetch_fn, channel_id=channel_id)
