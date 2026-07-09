"""WP6 — SEC-11 external witness (anchor store)."""
from __future__ import annotations

import pytest

from bridge.anchor import (
    AnchorError,
    AnchorRewind,
    FileAnchorStore,
    GitAuditLogAnchorStore,
    InMemoryAnchorStore,
    post_head_anchor,
)


def _monotonic_contract(store):
    assert store.latest() is None
    store.append(1, "h1")
    assert store.latest() == (1, "h1")
    store.append(2, "h2")
    assert store.latest() == (2, "h2")
    store.append(2, "h2")                         # idempotent re-anchor of the same head
    assert store.latest() == (2, "h2")
    with pytest.raises(AnchorRewind):
        store.append(1, "hx")                     # rewind refused
    with pytest.raises(AnchorRewind):
        store.append(2, "different")              # fork refused
    with pytest.raises(AnchorError):
        store.append(3, "")                        # empty head refused


def test_in_memory_monotonic():
    _monotonic_contract(InMemoryAnchorStore())


def test_file_monotonic(tmp_path):
    _monotonic_contract(FileAnchorStore(tmp_path / "anchors.jsonl"))


def test_file_persists_across_instances(tmp_path):
    path = tmp_path / "anchors.jsonl"
    a = FileAnchorStore(path)
    a.append(1, "h1")
    a.append(2, "h2")
    b = FileAnchorStore(path)                      # a fresh reader over the same file
    assert b.latest() == (2, "h2")
    with pytest.raises(AnchorRewind):
        b.append(1, "hx")


def test_post_head_anchor_publishes_verified_head(st):
    st.anchor_store = InMemoryAnchorStore()
    st.audit.append("mode_change", payload={"kind": "manual", "from": "read_only", "to": "paper"})
    st.audit.append("drawdown_mark", payload={"drawdown": "0.01"})
    seq, h = post_head_anchor(st)
    head_seq, head_hash = seq, h
    assert st.anchor_store.latest() == (head_seq, head_hash)
    # the chain still verifies against the anchor we just posted
    v = st.audit.verify_against_anchor(seq, h)
    assert v.ok


def test_post_head_anchor_refuses_without_store(st):
    with pytest.raises(AnchorError):
        post_head_anchor(st)


def test_post_head_anchor_is_monotonic_across_calls(st):
    st.anchor_store = InMemoryAnchorStore()
    st.audit.append("mode_change", payload={"kind": "manual", "from": "read_only", "to": "paper"})
    seq1, _ = post_head_anchor(st)
    st.audit.append("drawdown_mark", payload={"drawdown": "0.02"})
    seq2, _ = post_head_anchor(st)
    assert seq2 > seq1


def test_git_auditlog_store_needs_an_adapter():
    with pytest.raises(AnchorError):
        GitAuditLogAnchorStore()


def test_git_auditlog_store_delegates_and_is_monotonic():
    class _Adapter:
        out_of_domain = True                          # a stand-in for a genuine out-of-domain witness

        def __init__(self):
            self.published = []

        def publish(self, seq, head_hash):
            self.published.append((seq, head_hash))

        def read_latest(self):
            return self.published[-1] if self.published else None

    ad = _Adapter()
    store = GitAuditLogAnchorStore(ad)
    store.append(1, "h1")
    assert store.latest() == (1, "h1")
    with pytest.raises(AnchorRewind):
        store.append(1, "hx")
