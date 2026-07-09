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

import json

import pytest

from bridge.anchor import AnchorError, AnchorRewind, GitAuditLogAnchorStore
from bridge.witness import DiscordAuditLogWitness, GitCommitWitness


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


# ---- GitAuditLogAnchorStore delegating across BOTH backends ----------------------------
def test_store_delegates_across_both_backends_and_takes_max(tmp_path):
    git, gcommits = _git(tmp_path)
    disc, dposts = _discord()
    store = GitAuditLogAnchorStore(git, disc)
    assert store.latest() is None
    store.append(1, "h1")
    assert store.latest() == (1, "h1")
    assert git.read_latest() == (1, "h1") and disc.read_latest() == (1, "h1")   # published to both
    store.append(2, "h2")
    assert store.latest() == (2, "h2")
    with pytest.raises(AnchorRewind):
        store.append(1, "hx")                      # the store refuses a rewind before any publish


def test_store_latest_is_the_max_when_backends_diverge(tmp_path):
    """If the out-of-domain backend is ahead (Discord posted seq 3), latest() reports it — so an
    attacker who lowers the same-domain mirror cannot lower the witness."""
    git, _ = _git(tmp_path)
    disc, _ = _discord()
    git.publish(9, "h9")            # mirror ahead but SAME-DOMAIN -> excluded from the trust read
    disc.publish(3, "h3")           # the genuine out-of-domain witness
    store = GitAuditLogAnchorStore(git, disc)
    assert store.latest() == (3, "h3")   # trust read = out-of-domain backend, NOT the higher mirror


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
    assert DiscordAuditLogWitness.out_of_domain is True  # server-stamped runtime trust source


def test_rewriting_the_git_mirror_cannot_lower_the_witness(tmp_path):
    """With a genuine out-of-domain backend present, overwriting the same-domain git head file
    (the exact keyless-writer move) does NOT move the trust read — Discord still holds seq 4."""
    git, _ = _git(tmp_path)
    disc, _ = _discord()
    store = GitAuditLogAnchorStore(git, disc)
    store.append(4, "h4")
    assert store.latest() == (4, "h4")
    # keyless writer rewrites the working-tree head file to a rolled-back (1, hx)
    (tmp_path / "head.json").write_text('{"head_hash":"hx","seq":1}', encoding="utf-8")
    assert git.read_latest() == (1, "hx")     # the mirror was successfully rewound...
    assert store.latest() == (4, "h4")        # ...but the out-of-domain witness is unmoved
