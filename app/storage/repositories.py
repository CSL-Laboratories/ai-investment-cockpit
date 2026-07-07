"""WP2 — a thin repository over a SQLAlchemy model (add / get / list / count)."""
from __future__ import annotations

from sqlalchemy import func, select


class Repo:
    def __init__(self, session_factory, model):
        self._sf = session_factory
        self._model = model

    def add(self, **fields):
        with self._sf() as s, s.begin():
            row = self._model(**fields)
            s.add(row)
            s.flush()
            s.expunge(row)
            return row

    def get(self, id_):
        with self._sf() as s:
            return s.get(self._model, id_)

    def list(self, limit: int = 100):
        with self._sf() as s:
            return list(s.execute(select(self._model).limit(limit)).scalars().all())

    def count(self) -> int:
        with self._sf() as s:
            return int(s.execute(select(func.count()).select_from(self._model)).scalar_one())
