"""W10 SEC-11 (a) REGRESSION — the concrete out-of-domain witness adapters.

``bridge/witness.py`` replaces the deferred ``GitAuditLogAnchorStore`` SKETCH with two CONCRETE
``ExternalWitnessAdapter`` backends: ``GitCommitWitness`` (reviewed git history) +
``DiscordAuditLogWitness`` (server-stamped ``#audit-log`` post). This suite proves both are
APPEND-ONLY + MONOTONIC (a rewind/fork is refused), that read-back comes from the external domain
(the head file / the server's posts), and that ``GitAuditLogAnchorStore`` delegates across both
and takes ``latest()`` as the MAX — so lowering the witness needs defeating every backend at once.
Neither is ``FileAnchorStore`` (same-trust-domain theater, never a witness — SEC-11).
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from bridge.anchor import AnchorError, AnchorRewind, GitAuditLogAnchorStore
from bridge.witness import (
    DiscordAuditLogWitness,
    DiscordSemanticWitness,
    GitCommitWitness,
    S3ObjectLockWitness,
    s3_object_lock_witness,
)


BOT = {"id": "987654321", "bot": True}


def _identity():
    return dict(BOT)


# ---- GitCommitWitness ------------------------------------------------------------------
def _git(tmp_path):
    commits = []
    w = GitCommitWitness(tmp_path / "head.json",
                         commit_fn=lambda f, seq, h: commits.append((seq, h)))
    return w, commits


def test_git_witness_publishes_reads_and_commits(tmp_path):
    w, commits = _git(tmp_path)
    assert w.read_latest() is None
    w.publish(1, "h1")
    assert w.read_latest() == (1, "h1")
    assert commits == [(1, "h1")]                  # the anchor landed in (reviewed) history
    w.publish(2, "h2")
    assert w.read_latest() == (2, "h2") and len(commits) == 2
    # read-back is from the external domain (the head file), not a cached field
    assert json.loads((tmp_path / "head.json").read_text())["seq"] == 2


def test_git_witness_is_append_only_and_monotonic(tmp_path):
    w, commits = _git(tmp_path)
    w.publish(2, "h2")
    w.publish(2, "h2")                             # idempotent re-anchor of the same head
    assert len(commits) == 1
    with pytest.raises(AnchorRewind):
        w.publish(1, "hx")                         # rewind refused
    with pytest.raises(AnchorRewind):
        w.publish(2, "different")                  # fork refused
    with pytest.raises(AnchorError):
        w.publish(3, "")                           # empty head refused
    assert w.read_latest() == (2, "h2")            # nothing moved the witness backward


def test_git_witness_reads_back_across_instances(tmp_path):
    w1, _ = _git(tmp_path)
    w1.publish(5, "h5")
    w2 = GitCommitWitness(tmp_path / "head.json", commit_fn=lambda *a: None)
    assert w2.read_latest() == (5, "h5")           # durable via the head file
    with pytest.raises(AnchorRewind):
        w2.publish(4, "h4")


def test_git_witness_commit_failure_fails_closed(tmp_path):
    def boom(f, seq, h):
        raise RuntimeError("git identity not configured")
    w = GitCommitWitness(tmp_path / "head.json", commit_fn=boom)
    with pytest.raises(RuntimeError):              # an un-committed head is not the witness
        w.publish(1, "h1")


# ---- DiscordAuditLogWitness ------------------------------------------------------------
def _discord():
    posts = []                                     # server-held history
    w = DiscordAuditLogWitness(
        post_fn=lambda text: posts.append(("2026-07-08T00:00:00+00:00", text)),
        fetch_fn=lambda: list(posts),
        channel_id=42,
    )
    return w, posts


def test_discord_witness_publishes_and_reads_highest(tmp_path):
    w, posts = _discord()
    assert w.read_latest() is None
    w.publish(1, "h1")
    w.publish(2, "h2")
    assert w.read_latest() == (2, "h2")            # highest server-recorded post wins
    assert len(posts) == 2 and posts[0][1].startswith("audit-anchor ")


def test_discord_witness_is_append_only_and_monotonic(tmp_path):
    w, posts = _discord()
    w.publish(2, "h2")
    w.publish(2, "h2")                             # idempotent
    assert len(posts) == 1
    with pytest.raises(AnchorRewind):
        w.publish(1, "hx")                         # rewind refused
    with pytest.raises(AnchorRewind):
        w.publish(2, "different")                  # fork refused
    with pytest.raises(AnchorError):
        w.publish(3, "")


def test_discord_witness_read_fails_closed_on_fetch_error(tmp_path):
    """A witness we cannot READ (Discord unreachable) is treated as ABSENT -> read_latest returns
    None (so every consult fails closed), never raising into the caller."""
    def boom():
        raise RuntimeError("discord API unreachable")
    w = DiscordAuditLogWitness(post_fn=lambda t: None, fetch_fn=boom, channel_id=1)
    assert w.read_latest() is None
    store = GitAuditLogAnchorStore(w)
    assert store.latest() is None                 # the store fails closed on an unreadable witness


def test_discord_witness_ignores_foreign_and_malformed_posts(tmp_path):
    w, posts = _discord()
    posts.append(("2026-07-08T00:00:00+00:00", "just some chatter"))
    posts.append(("2026-07-08T00:00:00+00:00", "audit-anchor {not json"))
    assert w.read_latest() is None                 # no valid anchor among noise
    w.publish(3, "h3")
    assert w.read_latest() == (3, "h3")            # a real anchor still parses out of the noise


# ---- S3 Object Lock Compliance witness ------------------------------------------------
NOW = datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)


class FakeS3:
    def __init__(self):
        self.versions = []
        self.delete_markers = []
        self.default_mode = "COMPLIANCE"
        self.default_days = 2_555
        self.calls = []

    def get_object_lock_configuration(self, **kwargs):
        self.calls.append(("lock", kwargs))
        return {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {
                    "DefaultRetention": {
                        "Mode": self.default_mode,
                        "Days": self.default_days,
                    }
                },
            }
        }

    def list_object_versions(self, **kwargs):
        self.calls.append(("list", kwargs))
        prefix = kwargs["Prefix"]
        return {
            "Versions": [
                {"Key": row["Key"], "VersionId": row["VersionId"]}
                for row in self.versions
                if row["Key"].startswith(prefix)
            ],
            "DeleteMarkers": list(self.delete_markers),
            "IsTruncated": False,
        }

    def put_object(self, **kwargs):
        self.calls.append(("put", kwargs))
        if kwargs.get("IfNoneMatch") == "*" and any(
            row["Key"] == kwargs["Key"] for row in self.versions
        ):
            raise RuntimeError("precondition failed")
        version_id = f"v{len(self.versions) + 1}"
        self.versions.insert(
            0,
            {
                "Key": kwargs["Key"],
                "VersionId": version_id,
                "Body": kwargs["Body"],
                "Metadata": dict(kwargs["Metadata"]),
                "Mode": "COMPLIANCE",
                "RetainUntilDate": NOW + timedelta(days=self.default_days),
            },
        )
        return {"VersionId": version_id}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        row = self._version(kwargs["Key"], kwargs["VersionId"])
        checksum = row.get(
            "ChecksumSHA256",
            base64.b64encode(hashlib.sha256(row["Body"]).digest()).decode(),
        )
        return {
            "Body": BytesIO(row["Body"]),
            "ContentLength": len(row["Body"]),
            "ChecksumSHA256": checksum,
            "Metadata": dict(row["Metadata"]),
        }

    def get_object_retention(self, **kwargs):
        self.calls.append(("retention", kwargs))
        row = self._version(kwargs["Key"], kwargs["VersionId"])
        return {
            "Retention": {
                "Mode": row["Mode"],
                "RetainUntilDate": row["RetainUntilDate"],
            }
        }

    def _version(self, key, version_id):
        return next(
            row for row in self.versions
            if row["Key"] == key and row["VersionId"] == version_id
        )


def _worm():
    client = FakeS3()
    witness = S3ObjectLockWitness(
        client=client,
        bucket="cockpit-worm-example",
        expected_bucket_owner="123456789012",
        min_retention_days=365,
        now_fn=lambda: NOW,
    )
    return witness, client


def _inject(client, witness, seq, head_hash, *, key=None, mode="COMPLIANCE", days=2_555):
    payload = witness._payload(seq, head_hash)  # noqa: SLF001 - adversarial fixture
    digest = hashlib.sha256(payload).hexdigest()
    client.versions.insert(
        0,
        {
            "Key": key or witness._key(seq, digest),  # noqa: SLF001
            "VersionId": f"injected-{len(client.versions)}",
            "Body": payload,
            "Metadata": {"payload-sha256": digest, "schema-version": "1"},
            "Mode": mode,
            "RetainUntilDate": NOW + timedelta(days=days),
        },
    )


def test_s3_witness_publishes_and_reverifies_the_exact_compliance_version():
    witness, client = _worm()
    head = "a" * 64

    witness.publish(1, head)

    assert witness.read_latest() == (1, head)
    put = next(kwargs for name, kwargs in client.calls if name == "put")
    assert put["ExpectedBucketOwner"] == "123456789012"
    assert put["IfNoneMatch"] == "*"
    assert put["ChecksumSHA256"]
    assert any(name == "retention" for name, _kwargs in client.calls)


@pytest.mark.parametrize("fault", ["delete-marker", "shadow-version", "conflict"])
def test_s3_witness_shadow_and_conflict_attacks_fail_closed(fault):
    witness, client = _worm()
    witness.publish(9, "a" * 64)
    if fault == "delete-marker":
        client.delete_markers.append(
            {"Key": client.versions[0]["Key"], "VersionId": "delete-1"}
        )
    elif fault == "shadow-version":
        _inject(client, witness, 1, "b" * 64, key=client.versions[0]["Key"])
    else:
        _inject(client, witness, 9, "b" * 64)

    assert witness.read_latest() is None


@pytest.mark.parametrize("fault", ["governance", "short", "bucket-policy"])
def test_s3_witness_noncompliant_or_insufficient_retention_fails_closed(fault):
    witness, client = _worm()
    witness.publish(1, "a" * 64)
    if fault == "governance":
        client.versions[0]["Mode"] = "GOVERNANCE"
    elif fault == "short":
        client.versions[0]["RetainUntilDate"] = NOW + timedelta(days=30)
    else:
        client.default_mode = "GOVERNANCE"

    assert witness.read_latest() is None
    with pytest.raises(AnchorError):
        witness.publish(2, "b" * 64)


@pytest.mark.parametrize("fault", ["checksum", "metadata", "oversize"])
def test_s3_witness_payload_integrity_and_size_faults_fail_closed(fault):
    witness, client = _worm()
    witness.publish(1, "a" * 64)
    if fault == "checksum":
        client.versions[0]["ChecksumSHA256"] = "not-the-server-checksum"
    elif fault == "metadata":
        client.versions[0]["Metadata"].pop("payload-sha256")
    else:
        client.versions[0]["Body"] = b"x" * 4_097

    assert witness.read_latest() is None


def test_s3_factory_never_consults_the_ambient_aws_credential_chain(monkeypatch):
    client = FakeS3()
    captured = {}

    def build(service, **kwargs):
        captured.update({"service": service, **kwargs})
        return client

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=build))
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://attacker.invalid")
    witness = s3_object_lock_witness(
        bucket="cockpit-worm-example",
        region="us-west-2",
        expected_bucket_owner="123456789012",
        access_key_id="AKIAEXPLICITONLY",
        secret_access_key="explicit-secret",
    )

    assert witness._client is client  # noqa: SLF001 - factory contract
    assert captured["service"] == "s3"
    assert captured["region_name"] == "us-west-2"
    assert captured["endpoint_url"] == "https://s3.us-west-2.amazonaws.com"
    assert captured["aws_access_key_id"] == "AKIAEXPLICITONLY"
    assert captured["aws_secret_access_key"] == "explicit-secret"
    assert captured["use_ssl"] is True and captured["verify"] is True
    assert captured["config"].signature_version == "s3v4"


# ---- GitAuditLogAnchorStore delegating across mirrors + WORM ---------------------------
def test_store_publishes_to_git_and_discord_mirrors_without_trusting_them(tmp_path):
    git, gcommits = _git(tmp_path)
    disc, dposts = _discord()
    store = GitAuditLogAnchorStore(git, disc)
    assert store.latest() is None
    store.append(1, "h1")
    assert store.latest() is None
    assert git.read_latest() == (1, "h1") and disc.read_latest() == (1, "h1")   # published to both
    store.append(2, "h2")
    assert store.latest() is None
    with pytest.raises(AnchorRewind):
        store.append(1, "hx")                      # the store refuses a rewind before any publish


def test_store_latest_is_the_max_when_backends_diverge(tmp_path):
    """Only WORM authority counts; higher local/Discord mirror values never set the floor."""
    git, _ = _git(tmp_path)
    disc, _ = _discord()
    git.publish(9, "h9")            # mirror ahead but SAME-DOMAIN -> excluded from the trust read
    disc.publish(8, "h8")
    worm, client = _worm()
    _inject(client, worm, 3, "a" * 64)
    store = GitAuditLogAnchorStore(git, disc, worm)
    assert store.latest() == (3, "a" * 64)


# ---- G10 finding (2026-07-08): a same-domain MIRROR is NOT a trust source -------------
def test_git_only_store_is_never_trusted_for_latest(tmp_path):
    """THE red-team finding: GitCommitWitness reads the working-tree head file (same trust
    domain — a keyless writer overwrites it), so a git-ONLY GitAuditLogAnchorStore must NOT
    satisfy the SEC-11 witness prerequisite: latest() is None even after a head is 'anchored',
    so every consult FAILS CLOSED. FileAnchorStore-in-effect theater is refused."""
    git, _ = _git(tmp_path)
    store = GitAuditLogAnchorStore(git)
    assert store.has_out_of_domain_witness is False
    store.append(5, "h5")                     # publishes to the git mirror...
    assert git.read_latest() == (5, "h5")     # ...the mirror holds it (local cross-check)...
    assert store.latest() is None             # ...but the TRUST read refuses a same-domain-only witness


def test_out_of_domain_flags():
    assert GitCommitWitness.out_of_domain is False       # mirror + reviewed-history detection
    assert DiscordAuditLogWitness.out_of_domain is False  # Administrator-safe mirror only
    assert S3ObjectLockWitness.out_of_domain is True
    assert DiscordSemanticWitness.out_of_domain is True


def test_semantic_witness_binds_exact_message_id_hash_and_server_time():
    messages = {}

    def post(content):
        message = {
            "id": "123456789",
            "channel_id": "42",
            "timestamp": "2026-07-07T14:00:01Z",
            "edited_timestamp": None,
            "author": dict(BOT),
            "content": content,
        }
        messages[message["id"]] = message
        return message

    witness = DiscordSemanticWitness(
        post_fn=post,
        fetch_fn=lambda message_id: messages[message_id],
        identity_fn=_identity,
        channel_id=42,
    )
    digest = "a" * 64
    receipt = witness.publish(digest)

    assert receipt.message_id == "123456789"
    assert receipt.server_ts.isoformat() == "2026-07-07T14:00:01+00:00"
    assert witness.verify(receipt.message_id, digest) == receipt.server_ts

    messages[receipt.message_id]["content"] = "merger-selection-commitment " + "b" * 64
    with pytest.raises(AnchorError, match="does not bind"):
        witness.verify(receipt.message_id, digest)


def test_semantic_witness_rejects_a_foreign_channel_receipt():
    message = {
        "id": "123456789",
        "channel_id": "99",
        "timestamp": "2026-07-07T14:00:01Z",
        "edited_timestamp": None,
        "author": dict(BOT),
        "content": "merger-selection-commitment " + "a" * 64,
    }
    witness = DiscordSemanticWitness(
        post_fn=lambda _content: message,
        fetch_fn=lambda _message_id: message,
        identity_fn=_identity,
        channel_id=42,
    )

    with pytest.raises(AnchorError, match="does not bind"):
        witness.publish("a" * 64)


@pytest.mark.parametrize("fault", ["wrong-id", "wrong-content", "naive-time"])
def test_semantic_witness_rejects_unbound_publish_receipts(fault):
    message = {
        "id": "123",
        "timestamp": "2026-07-07T14:00:01+00:00",
        "edited_timestamp": None,
        "author": dict(BOT),
        "content": "merger-selection-commitment " + "a" * 64,
    }
    if fault == "wrong-id":
        message["id"] = ""
    elif fault == "wrong-content":
        message["content"] = "foreign"
    else:
        message["timestamp"] = "2026-07-07T14:00:01"
    witness = DiscordSemanticWitness(
        post_fn=lambda _text: message,
        fetch_fn=lambda _message_id: message,
        identity_fn=_identity,
    )

    with pytest.raises(AnchorError):
        witness.publish("a" * 64)


@pytest.mark.parametrize(
    "fault", ["edited", "missing-edit-state", "wrong-author", "webhook", "not-bot"]
)
def test_semantic_witness_rejects_mutable_or_foreign_messages(fault):
    message = {
        "id": "123",
        "channel_id": "42",
        "timestamp": "2026-08-17T18:00:00Z",
        "edited_timestamp": None,
        "author": dict(BOT),
        "content": "merger-selection-commitment " + "a" * 64,
    }
    identity = dict(BOT)
    if fault == "edited":
        message["edited_timestamp"] = "2026-08-17T18:01:00Z"
    elif fault == "missing-edit-state":
        message.pop("edited_timestamp")
    elif fault == "wrong-author":
        message["author"]["id"] = "111111111"
    elif fault == "webhook":
        message["webhook_id"] = "222222222"
    else:
        identity["bot"] = False
    witness = DiscordSemanticWitness(
        post_fn=lambda _text: dict(message),
        fetch_fn=lambda _message_id: dict(message),
        identity_fn=lambda: dict(identity),
        channel_id=42,
    )

    with pytest.raises(AnchorError):
        witness.publish("a" * 64)
    with pytest.raises(AnchorError):
        witness.verify("123", "a" * 64)


def test_rewriting_the_git_mirror_cannot_lower_the_witness(tmp_path):
    """Overwriting both mutable mirrors cannot lower the retained S3 witness."""
    git, _ = _git(tmp_path)
    disc, _ = _discord()
    worm, _client = _worm()
    store = GitAuditLogAnchorStore(git, disc, worm)
    store.append(4, "a" * 64)
    assert store.latest() == (4, "a" * 64)
    # keyless writer rewrites the working-tree head file to a rolled-back (1, hx)
    (tmp_path / "head.json").write_text('{"head_hash":"hx","seq":1}', encoding="utf-8")
    assert git.read_latest() == (1, "hx")     # the mirror was successfully rewound...
    assert store.latest() == (4, "a" * 64)    # ...but the WORM witness is unmoved


def test_ahead_file_only_mirror_cannot_veto_trusted_publish(tmp_path):
    """BUG-051: a branch/fresh-DB mismatch left the local head ahead of the DB. Because the
    mirror is attacker-writable and explicitly untrusted, it may resynchronise but must never
    prevent the WORM witness from receiving the verified current head."""
    mirror = GitCommitWitness(tmp_path / "head.json", commit_fn=False)
    mirror.publish(3_532, "stale")
    disc, posts = _discord()
    worm, _client = _worm()
    store = GitAuditLogAnchorStore(mirror, disc, worm)

    store.append(1, "a" * 64)

    assert worm.read_latest() == (1, "a" * 64)
    assert disc.read_latest() == (1, "a" * 64)
    assert len(posts) == 1
    assert mirror.read_latest() == (1, "a" * 64)


# ---- BUG-056: the DEFAULT _commit path, which every other test injects away ------------
def _real_repo(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                   check=True, capture_output=True)
    return repo


def _log(repo):
    import subprocess

    out = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True)
    return out.stdout


def test_default_commit_really_commits_a_relative_subdir_head_file(tmp_path, monkeypatch):
    """BUG-056: the production shape — ``GitCommitWitness("data/audit-head.json")`` with no
    ``repo_dir`` — made ``_repo`` the SUBDIRECTORY and then ran ``git -C data add
    data/audit-head.json``. Git resolves pathspecs relative to the ``-C`` directory, so the
    prefix doubled (``data/data/...``), every real anchor commit exited 128, and the 30-min
    ``run_anchor_head`` raised AnchorError forever. Invisible until now because every other
    test injects ``commit_fn``."""
    from pathlib import Path

    repo = _real_repo(tmp_path)
    monkeypatch.chdir(repo)
    w = GitCommitWitness(Path("data") / "audit-head.json")     # production default: no repo_dir
    w.publish(1, "a" * 64)
    assert "audit-anchor: seq 1" in _log(repo)


def test_default_commit_with_an_explicit_repo_dir_still_works(tmp_path, monkeypatch):
    from pathlib import Path

    repo = _real_repo(tmp_path)
    monkeypatch.chdir(repo)
    w = GitCommitWitness(Path("data") / "audit-head.json", repo_dir=repo)
    w.publish(3, "b" * 64)
    assert "audit-anchor: seq 3" in _log(repo)
