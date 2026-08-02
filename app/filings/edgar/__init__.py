"""WP3 — the EDGAR ingest layer.

``fetch.py`` is the ONE module allowed to touch sec.gov (Invariant 4): it holds the
declared User-Agent, the token-bucket rate limiter (strictly < 10 req/s), the distinct
error taxonomy (transport error vs. HTTP error vs. fair-access halt), and every sec.gov
URL string. ``cik_map.py``, ``submissions_poll.py``, and ``companyfacts.py`` build on it
and never open a connection or name a sec.gov host themselves.
"""
from __future__ import annotations
