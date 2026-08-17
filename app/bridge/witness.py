"""W10 — concrete external-witness adapters for the SEC-11 audit anchor.

``bridge/anchor.py`` defines the ``ExternalWitnessAdapter`` seam (``publish`` + ``read_latest``)
and ``GitAuditLogAnchorStore`` that wraps one or more backends. This module supplies the two
CONCRETE backends the docstring promised, replacing the deferred SKETCH:

  * ``GitCommitWitness`` — maintains a machine-local ``(seq, head_hash)`` mirror.
    Production passes ``commit_fn=False`` so a running cockpit never stages or commits onto a
    development branch; the file is ignored and untracked. The class retains an injectable
    commit seam for explicit tooling/tests, but that same-domain history is never trusted by
    the runtime gate.
  * ``DiscordAuditLogWitness`` — posts ``(seq, head_hash)`` to the private ``#audit-log``
    channel and reads the highest post back. The guarantee is TECHNICAL: the local writer
    cannot backdate a server-stamped message, and (with a channel ACL that denies the bot
    manage-messages) cannot delete history either. This is the strongest single witness.

``GitAuditLogAnchorStore.latest()`` considers only out-of-domain backends, so the local mirror
can never unlock auto/live. Lowering the trusted witness requires defeating the server-held
Discord history, which a keyless local writer cannot do (SEC-11).

The out-of-domain Discord adapter and explicit git-history mode are APPEND-ONLY + MONOTONIC.
Production's untrusted file-only mirror may resynchronise across a branch/DB change and cannot
veto Discord. All external I/O is INJECTABLE (``commit_fn`` / ``post_fn`` / ``fetch_fn``)
so the adapters are fully testable without a live git repo or Discord bot; ``main.compose()``
wires the production functions from config + keyring.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bridge.anchor import AnchorError, _guard_monotonic


def _dump(seq: int, head_hash: str) -> str:
    return json.dumps({"seq": int(seq), "head_hash": head_hash},
                      sort_keys=True, separators=(",", ":"))


# ---- machine-local head file (MIRROR — NOT a runtime trust source) ---------------------
class GitCommitWitness:
    """Maintain a local ``(seq, head_hash)`` mirror. ``read_latest`` reads the head
    file and ``publish`` updates it. ``commit_fn(head_file, seq, head_hash)`` remains injectable
    for explicit tooling/tests; the class default stages + commits via git, while production
    composition passes ``commit_fn=False`` and never mutates a development branch.

    **``out_of_domain = False`` — this is a same-domain MIRROR, NOT a runtime trust source
    (SEC-11, corrected by the G10 red-team 2026-07-08).** Explicit commit mode can provide a
    reviewed-history detection layer, but production file-only mode does not. ``read_latest``
    reads the WORKING-TREE head file, which lives in the local writer's OWN trust domain: a
    keyless writer who can ``DELETE`` the DB tail can equally overwrite ``data/audit-head.json``
    to a captured lower ``(N, H_N)``. The git-commit guarantee is PROCEDURAL (Lucas notices a
    rewrite of committed history in the diff before push) and OUT-OF-BAND — it is NOT enforced
    on the runtime read path. So ``GitAuditLogAnchorStore.latest()`` (the value the gate/mode/
    gateway/budget consult to fail closed) EXCLUDES this backend; only a genuinely out-of-domain
    witness (``DiscordAuditLogWitness``) counts. Production still publishes to this adapter as
    a file-only local cross-check, but never trusts it for the auto/live unlock decision."""

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
        if self._commit_fn is not False:
            # Explicit git-history mode retains the append-only contract. Production's
            # file-only mirror is deliberately non-gating: a stale/ahead local file must not
            # veto the out-of-domain Discord witness and is resynchronised to the current DB.
            _guard_monotonic(cur[0] if cur else None, cur[1] if cur else None, seq, head_hash)
        if cur == (seq, head_hash):
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


# ---- prospective strategy commitment (server-stamped, exact-message lookup) -----------
@dataclass(frozen=True)
class SemanticWitnessReceipt:
    message_id: str
    server_ts: datetime


class DiscordSemanticWitness:
    """Witness one outcome-free selection-manifest hash in Discord's trust domain.

    Unlike the rolling audit-head adapter, semantic commitments are addressed by exact message
    id.  A later calibration must fetch that same immutable server-stamped post; looking only at
    the latest 100 channel messages would make old cohorts unverifiable and would not bind a
    particular pre-outcome timestamp.
    """

    out_of_domain = True
    _PREFIX = "merger-selection-commitment "
    _HASH = re.compile(r"^[0-9a-f]{64}$")

    def __init__(self, *, post_fn, fetch_fn, channel_id=None) -> None:
        self._post = post_fn
        self._fetch = fetch_fn
        self._channel_id = channel_id

    @classmethod
    def _content(cls, content_hash: str) -> str:
        if cls._HASH.fullmatch(str(content_hash)) is None:
            raise AnchorError("semantic commitment hash is invalid")
        return f"{cls._PREFIX}{content_hash}"

    @staticmethod
    def _server_time(value) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AnchorError("semantic witness returned an invalid server timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise AnchorError("semantic witness timestamp is timezone-naive")
        return parsed.astimezone(timezone.utc)

    def publish(self, content_hash: str) -> SemanticWitnessReceipt:
        expected = self._content(content_hash)
        try:
            message = self._post(expected)
        except Exception as exc:
            raise AnchorError(f"semantic witness publish failed: {type(exc).__name__}") from exc
        if not isinstance(message, dict):
            raise AnchorError("semantic witness publish returned no message receipt")
        message_id = str(message.get("id") or "").strip()
        if not message_id or message.get("content") != expected:
            raise AnchorError("semantic witness receipt does not bind the commitment")
        return SemanticWitnessReceipt(
            message_id=message_id,
            server_ts=self._server_time(message.get("timestamp")),
        )

    def verify(self, message_id: str, content_hash: str) -> datetime:
        expected_id = str(message_id).strip()
        if not expected_id:
            raise AnchorError("semantic witness message id is missing")
        try:
            message = self._fetch(expected_id)
        except Exception as exc:
            raise AnchorError(f"semantic witness read failed: {type(exc).__name__}") from exc
        if (
            not isinstance(message, dict)
            or str(message.get("id") or "") != expected_id
            or message.get("content") != self._content(content_hash)
        ):
            raise AnchorError("semantic witness message does not bind the commitment")
        return self._server_time(message.get("timestamp"))


def discord_rest_semantic_witness(
    channel_id, bot_token: str, *, timeout: float = 10.0
) -> DiscordSemanticWitness:
    """Build the exact-message Discord witness using the existing keyring-held bot token."""
    import urllib.request

    base = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
        "User-Agent": "cockpit-merger-semantic-witness (https://localhost, 1.0)",
    }

    def post_fn(text: str):
        body = json.dumps({"content": text}).encode("utf-8")
        req = urllib.request.Request(base, data=body, headers=headers, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    def fetch_fn(message_id: str):
        if not message_id.isdigit():
            raise AnchorError("Discord semantic witness message id is invalid")
        req = urllib.request.Request(f"{base}/{message_id}", headers=headers, method="GET")
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    return DiscordSemanticWitness(post_fn=post_fn, fetch_fn=fetch_fn, channel_id=channel_id)
