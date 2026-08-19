"""W10/W-25 — concrete external-witness adapters for the SEC-11 audit anchor.

``bridge/anchor.py`` defines the ``ExternalWitnessAdapter`` seam (``publish`` + ``read_latest``)
and ``GitAuditLogAnchorStore`` that wraps one or more backends. This module supplies three
concrete backends:

  * ``GitCommitWitness`` — maintains a machine-local ``(seq, head_hash)`` mirror.
    Production passes ``commit_fn=False`` so a running cockpit never stages or commits onto a
    development branch; the file is ignored and untracked. The class retains an injectable
    commit seam for explicit tooling/tests, but that same-domain history is never trusted by
    the runtime gate.
  * ``DiscordAuditLogWitness`` — posts ``(seq, head_hash)`` to the private ``#audit-log``
    channel as an operator-visible mirror. It is not trusted because an Administrator bot can
    edit or delete its own history regardless of channel overwrites.
  * ``S3ObjectLockWitness`` — publishes canonical anchor objects to a dedicated, versioned S3
    bucket and trusts only versions with active Compliance-mode retention. It enumerates every
    version and delete marker so a current shadow object cannot hide an older retained head.

``GitAuditLogAnchorStore.latest()`` considers only out-of-domain backends, so neither local git
nor Discord can unlock auto/live. Missing or unverifiable Object Lock evidence returns no
trusted anchor and every dependent gate fails closed.

The S3 adapter and explicit git-history mode are APPEND-ONLY + MONOTONIC. Production's
untrusted mirrors may resynchronise across a branch/DB change and cannot veto the WORM backend.
All external I/O is injectable so the adapters are testable without live services;
``main.compose()`` wires production clients from non-secret config plus keyring.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    witness (``S3ObjectLockWitness``) counts. Production still publishes to this adapter as
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
            # veto the out-of-domain WORM witness and is resynchronised to the current DB.
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


# ---- #audit-log Discord post (operator-visible MIRROR, never authority) ----------------
class DiscordAuditLogWitness:
    """Anchor ``(seq, head_hash)`` by posting to the private ``#audit-log`` channel and read
    the highest server-recorded post back. ``post_fn(text)`` posts one message; ``fetch_fn()``
    returns the recent messages as ``[(server_ts, content), ...]`` (order-agnostic — the
    highest parsed seq wins, so the server's held history, not the local writer, decides the
    witness). Both are injectable; ``discord_rest_witness`` wires the production Discord REST
    calls.

    **``out_of_domain = False``.** Discord authors can edit and delete their own messages, and
    an Administrator bot bypasses channel overwrites. The rolling channel therefore provides
    operator visibility and cross-checking only; it cannot satisfy SEC-11 or set the monotonic
    floor consumed by ``GitAuditLogAnchorStore.latest()``."""

    out_of_domain = False

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


# ---- S3 Object Lock Compliance witness (the SEC-11 authority) -------------------------
class S3ObjectLockWitness:
    """Append/read canonical audit anchors retained by S3 Object Lock Compliance mode.

    A current S3 object is not sufficient: versioning permits a writer to place a new version
    or delete marker above a protected one. Schema v2 encodes the inverted sequence in each key,
    so S3's lexicographic ordering places the highest claimed sequence first. ``read_latest``
    requests one capped page and verifies only the complete highest-sequence group. A page that
    cannot prove that group complete, or any other ambiguity/API fault, returns ``None``.
    """

    out_of_domain = True
    _SCHEMA_VERSION = 2
    _KIND = "audit-anchor"
    _HASH = re.compile(r"^[0-9a-f]{64}$")
    _MAX_SEQ = (10**20) - 1
    _MAX_CANDIDATE_ENTRIES = 128
    _KEY_SUFFIX = re.compile(r"^v2-(\d{20})-([0-9a-f]{64})\.json$")
    _RETENTION_SKEW = timedelta(minutes=5)
    _MAX_OBJECT_BYTES = 4_096

    def __init__(
        self,
        *,
        client,
        bucket: str,
        expected_bucket_owner: str,
        prefix: str = "ai-investment-cockpit/v1",
        min_retention_days: int = 365,
        now_fn=None,
    ) -> None:
        self._client = client
        self._bucket = str(bucket).strip()
        self._owner = str(expected_bucket_owner).strip()
        self._prefix = str(prefix).strip().strip("/")
        self._anchor_prefix = f"{self._prefix}/audit-anchor/"
        self._min_retention_days = int(min_retention_days)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        if (
            re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self._bucket) is None
            or not re.fullmatch(r"\d{12}", self._owner)
        ):
            raise AnchorError("S3 witness bucket identity is invalid")
        if not self._prefix or ".." in self._prefix or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9!_.*'()/=-]*", self._prefix
        ):
            raise AnchorError("S3 witness prefix is invalid")
        if self._min_retention_days < 1:
            raise AnchorError("S3 witness minimum retention must be positive")

    def _request(self, **kwargs) -> dict:
        return {
            "Bucket": self._bucket,
            "ExpectedBucketOwner": self._owner,
            **kwargs,
        }

    def _now(self) -> datetime:
        value = self._now_fn()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise AnchorError("S3 witness clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @classmethod
    def _payload(cls, seq: int, head_hash: str) -> bytes:
        return json.dumps(
            {
                "head_hash": head_hash,
                "kind": cls._KIND,
                "schema_version": cls._SCHEMA_VERSION,
                "seq": int(seq),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _key(self, seq: int, payload_digest: str) -> str:
        seq = int(seq)
        if not 1 <= seq <= self._MAX_SEQ or self._HASH.fullmatch(str(payload_digest)) is None:
            raise AnchorError("S3 witness key identity is invalid")
        inverted = self._MAX_SEQ - seq
        return f"{self._anchor_prefix}v2-{inverted:020d}-{payload_digest}.json"

    def _key_identity(self, key: str) -> tuple[int, str]:
        if not isinstance(key, str) or not key.startswith(self._anchor_prefix):
            raise AnchorError("S3 witness key identity is invalid")
        match = self._KEY_SUFFIX.fullmatch(key[len(self._anchor_prefix):])
        if match is None:
            raise AnchorError("S3 witness key identity is invalid")
        seq = self._MAX_SEQ - int(match.group(1))
        if not 1 <= seq <= self._MAX_SEQ:
            raise AnchorError("S3 witness key sequence is invalid")
        return seq, match.group(2)

    def _require_bucket_lock(self) -> None:
        response = self._client.get_object_lock_configuration(**self._request())
        config = response.get("ObjectLockConfiguration") or {}
        retention = ((config.get("Rule") or {}).get("DefaultRetention") or {})
        if config.get("ObjectLockEnabled") != "Enabled" or retention.get("Mode") != "COMPLIANCE":
            raise AnchorError("S3 witness bucket is not Object Lock Compliance-enabled")
        days = retention.get("Days")
        years = retention.get("Years")
        if (days is None) == (years is None):
            raise AnchorError("S3 witness bucket default retention is ambiguous")
        total_days = int(days) if days is not None else int(years) * 365
        if total_days < self._min_retention_days:
            raise AnchorError("S3 witness bucket default retention is too short")

    def _list_candidate_versions(self) -> tuple[int | None, list[dict], list[dict]]:
        """Return one complete highest-sequence group from one bounded S3 page.

        General-purpose S3 buckets order keys lexicographically and versions of a key newest
        first. The inverted fixed-width sequence therefore makes the first sequence group the
        monotonic authority candidate. If the page ends before a lower sequence appears, a
        truncated response could be hiding another same-sequence key/version and is refused.
        """
        response = self._client.list_object_versions(
            **self._request(Prefix=self._anchor_prefix, MaxKeys=self._MAX_CANDIDATE_ENTRIES)
        )
        truncated = response.get("IsTruncated")
        if not isinstance(truncated, bool):
            raise AnchorError("S3 witness candidate listing truncation state is invalid")
        versions = response.get("Versions", [])
        delete_markers = response.get("DeleteMarkers", [])
        if versions is None:
            versions = []
        if delete_markers is None:
            delete_markers = []
        if not isinstance(versions, list) or not isinstance(delete_markers, list):
            raise AnchorError("S3 witness candidate listing is invalid")
        if len(versions) + len(delete_markers) > self._MAX_CANDIDATE_ENTRIES:
            raise AnchorError("S3 witness candidate listing exceeds its requested bound")

        entries: list[tuple[int, str, dict]] = []
        for kind, rows in (("version", versions), ("delete", delete_markers)):
            for row in rows:
                if not isinstance(row, dict):
                    raise AnchorError("S3 witness candidate entry is invalid")
                key = row.get("Key")
                version_id = row.get("VersionId")
                seq, _digest = self._key_identity(key)
                if not isinstance(version_id, str) or not version_id or version_id == "null":
                    raise AnchorError("S3 witness version identity is invalid")
                entries.append((seq, kind, row))

        if not entries:
            if truncated:
                raise AnchorError("S3 witness empty candidate page is truncated")
            return None, [], []

        candidate_seq = max(seq for seq, _kind, _row in entries)
        if truncated and all(seq == candidate_seq for seq, _kind, _row in entries):
            raise AnchorError("S3 witness highest-sequence group exceeds its proof bound")
        candidate_versions = [
            row for seq, kind, row in entries if seq == candidate_seq and kind == "version"
        ]
        candidate_markers = [
            row for seq, kind, row in entries if seq == candidate_seq and kind == "delete"
        ]
        return candidate_seq, candidate_versions, candidate_markers

    @staticmethod
    def _retention_time(value) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise AnchorError("S3 witness retention timestamp is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise AnchorError("S3 witness retention timestamp is timezone-naive")
        return parsed.astimezone(timezone.utc)

    def _read_version(self, key: str, version_id: str) -> tuple[int, str, bool]:
        if not version_id or version_id == "null":
            raise AnchorError("S3 witness version identity is invalid")
        key_seq, key_digest = self._key_identity(key)
        response = self._client.get_object(
            **self._request(Key=key, VersionId=version_id, ChecksumMode="ENABLED")
        )
        content_length = response.get("ContentLength")
        if (
            not isinstance(content_length, int)
            or isinstance(content_length, bool)
            or content_length < 1
            or content_length > self._MAX_OBJECT_BYTES
        ):
            raise AnchorError("S3 witness object size is invalid")
        body = response.get("Body")
        try:
            raw = body.read() if hasattr(body, "read") else body
        finally:
            if hasattr(body, "close"):
                body.close()
        if not isinstance(raw, bytes) or len(raw) != content_length:
            raise AnchorError("S3 witness object body is invalid")
        try:
            record = json.loads(raw.decode("utf-8"))
            seq = int(record["seq"])
            head_hash = str(record["head_hash"])
        except (UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise AnchorError("S3 witness object is malformed") from exc
        if seq < 1 or self._HASH.fullmatch(head_hash) is None or raw != self._payload(seq, head_hash):
            raise AnchorError("S3 witness object is not canonical")
        digest = hashlib.sha256(raw).hexdigest()
        checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        if response.get("ChecksumSHA256") != checksum:
            raise AnchorError("S3 witness object checksum is missing or invalid")
        if seq != key_seq or digest != key_digest or key != self._key(seq, digest):
            raise AnchorError("S3 witness key does not bind its payload")
        metadata = response.get("Metadata") or {}
        if (
            metadata.get("payload-sha256") != digest
            or metadata.get("schema-version") != str(self._SCHEMA_VERSION)
        ):
            raise AnchorError("S3 witness metadata does not bind its payload")
        retention = self._client.get_object_retention(
            **self._request(Key=key, VersionId=version_id)
        ).get("Retention") or {}
        if retention.get("Mode") != "COMPLIANCE":
            raise AnchorError("S3 witness object is not Compliance-retained")
        retain_until = self._retention_time(retention.get("RetainUntilDate"))
        minimum = self._now() + timedelta(days=self._min_retention_days) - self._RETENTION_SKEW
        return seq, head_hash, retain_until >= minimum

    def _read_latest_strict(
        self, *, expected_version: "tuple[str, str] | None" = None
    ) -> "tuple[int, str] | None":
        self._require_bucket_lock()
        candidate_seq, versions, delete_markers = self._list_candidate_versions()
        if candidate_seq is None:
            return None
        if delete_markers:
            raise AnchorError("S3 witness highest-sequence group contains a delete marker")
        if not versions:
            raise AnchorError("S3 witness highest-sequence group contains no object version")
        if len(versions) != 1:
            raise AnchorError("S3 witness contains conflicting anchors")
        version = versions[0]
        key = str(version.get("Key") or "")
        version_id = str(version.get("VersionId") or "")
        if expected_version is not None and (key, version_id) != expected_version:
            raise AnchorError("S3 witness publication is not the current retained head")
        record = self._read_version(key, version_id)
        if record[0] != candidate_seq:
            raise AnchorError("S3 witness candidate sequence does not bind its key")
        if not record[2]:
            raise AnchorError("S3 witness latest anchor lacks sufficient retention")
        return record[:2]

    def read_latest(self) -> "tuple[int, str] | None":
        try:
            return self._read_latest_strict()
        except Exception:
            return None

    def publish(self, seq: int, head_hash: str) -> None:
        seq = int(seq)
        if not 1 <= seq <= self._MAX_SEQ or self._HASH.fullmatch(str(head_hash)) is None:
            raise AnchorError("S3 witness anchor is invalid")
        try:
            current = self._read_latest_strict()
            _guard_monotonic(
                current[0] if current else None,
                current[1] if current else None,
                seq,
                head_hash,
            )
            if current == (seq, head_hash):
                return
            payload = self._payload(seq, head_hash)
            digest_bytes = hashlib.sha256(payload).digest()
            digest = digest_bytes.hex()
            key = self._key(seq, digest)
            result = self._client.put_object(
                **self._request(
                    Key=key,
                    Body=payload,
                    ContentType="application/json",
                    Metadata={
                        "payload-sha256": digest,
                        "schema-version": str(self._SCHEMA_VERSION),
                    },
                    ChecksumSHA256=base64.b64encode(digest_bytes).decode("ascii"),
                    IfNoneMatch="*",
                )
            )
            version_id = str(result.get("VersionId") or "")
            if not version_id or version_id == "null":
                raise AnchorError("S3 witness publication returned no version identity")
            observed = self._read_latest_strict(expected_version=(key, version_id))
            if observed != (seq, head_hash):
                raise AnchorError("S3 witness publication is not durably retained")
        except AnchorError:
            raise
        except Exception as exc:
            raise AnchorError(f"S3 witness publication failed: {type(exc).__name__}") from exc


def s3_object_lock_witness(
    *,
    bucket: str,
    region: str,
    expected_bucket_owner: str,
    access_key_id: str,
    secret_access_key: str,
    prefix: str = "ai-investment-cockpit/v1",
    min_retention_days: int = 365,
) -> S3ObjectLockWitness:
    """Build a region/endpoint-pinned S3 witness with no ambient AWS credential/config chain."""
    import boto3
    from botocore.config import Config

    if not all(str(value).strip() for value in (region, access_key_id, secret_access_key)):
        raise AnchorError("S3 witness credentials and region must be explicit")
    if (
        re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", str(region)) is None
        or str(region).startswith("cn-")
    ):
        raise AnchorError("S3 witness region is invalid or outside the pinned AWS partition")
    client = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        use_ssl=True,
        verify=True,
        config=Config(
            signature_version="s3v4",
            retries={"mode": "standard", "max_attempts": 3},
            s3={"addressing_style": "virtual"},
        ),
    )
    return S3ObjectLockWitness(
        client=client,
        bucket=bucket,
        expected_bucket_owner=expected_bucket_owner,
        prefix=prefix,
        min_retention_days=min_retention_days,
    )


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

    def __init__(self, *, post_fn, fetch_fn, identity_fn=None, channel_id=None) -> None:
        self._post = post_fn
        self._fetch = fetch_fn
        self._identity = identity_fn
        self._author_id: str | None = None
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

    def _expected_author_id(self) -> str:
        if self._author_id is not None:
            return self._author_id
        if self._identity is None:
            raise AnchorError("semantic witness bot identity is unavailable")
        try:
            identity = self._identity()
        except Exception as exc:
            raise AnchorError(f"semantic witness identity failed: {type(exc).__name__}") from exc
        author_id = str(identity.get("id") or "").strip() if isinstance(identity, dict) else ""
        if not author_id or identity.get("bot") is not True:
            raise AnchorError("semantic witness identity is not an authenticated bot")
        self._author_id = author_id
        return author_id

    def _binds(self, message: dict, *, expected_id: str, expected_content: str) -> bool:
        author = message.get("author")
        return bool(
            str(message.get("id") or "") == expected_id
            and message.get("content") == expected_content
            and "edited_timestamp" in message
            and message.get("edited_timestamp") is None
            and message.get("webhook_id") is None
            and isinstance(author, dict)
            and str(author.get("id") or "") == self._expected_author_id()
            and author.get("bot") is True
            and (
                self._channel_id is None
                or str(message.get("channel_id") or "") == str(self._channel_id)
            )
        )

    def publish(self, content_hash: str) -> SemanticWitnessReceipt:
        expected = self._content(content_hash)
        try:
            message = self._post(expected)
        except Exception as exc:
            raise AnchorError(f"semantic witness publish failed: {type(exc).__name__}") from exc
        if not isinstance(message, dict):
            raise AnchorError("semantic witness publish returned no message receipt")
        message_id = str(message.get("id") or "").strip()
        if not message_id or not self._binds(
            message, expected_id=message_id, expected_content=expected
        ):
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
        if not isinstance(message, dict) or not self._binds(
            message,
            expected_id=expected_id,
            expected_content=self._content(content_hash),
        ):
            raise AnchorError("semantic witness message does not bind the commitment")
        return self._server_time(message.get("timestamp"))


def discord_rest_semantic_witness(
    channel_id, bot_token: str, *, timeout: float = 10.0
) -> DiscordSemanticWitness:
    """Build the exact-message Discord witness using the existing keyring-held bot token."""
    import urllib.request

    base = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    identity_url = "https://discord.com/api/v10/users/@me"
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

    def identity_fn():
        req = urllib.request.Request(identity_url, headers=headers, method="GET")
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    return DiscordSemanticWitness(
        post_fn=post_fn,
        fetch_fn=fetch_fn,
        identity_fn=identity_fn,
        channel_id=channel_id,
    )
