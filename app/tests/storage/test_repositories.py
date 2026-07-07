"""WP2 — repository add/get/list/count round-trip."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemas.models.mode_state import ModeState
from storage.engine import create_all, make_engine, make_session_factory
from storage.repositories import Repo


@pytest.fixture
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    create_all(engine)
    return make_session_factory(engine)


def test_repo_add_get_list_count(sf):
    repo = Repo(sf, ModeState)
    now = datetime.now(timezone.utc)
    repo.add(id="m1", mode="read_only", created_at=now)
    repo.add(id="m2", mode="paper", created_at=now)
    assert repo.count() == 2
    assert repo.get("m1").mode == "read_only"
    assert {m.id for m in repo.list()} == {"m1", "m2"}
