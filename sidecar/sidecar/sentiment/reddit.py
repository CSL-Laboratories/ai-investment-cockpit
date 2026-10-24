"""Official Reddit script-app client for public r/wallstreetbets posts.

Credentials are SIDECAR_REDDIT_CLIENT_ID, SIDECAR_REDDIT_CLIENT_SECRET, and
SIDECAR_REDDIT_USER_AGENT. If any one is missing the client raises and does not
call the network. Application-only client_credentials can read a public listing.
It cannot post.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from sidecar.sentiment.posts import Post, SentimentError

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
LISTING_URL = "https://oauth.reddit.com/r/wallstreetbets/new"
ENV_ID = "SIDECAR_REDDIT_CLIENT_ID"
ENV_SECRET = "SIDECAR_REDDIT_CLIENT_SECRET"
ENV_AGENT = "SIDECAR_REDDIT_USER_AGENT"


def require_reddit_env() -> tuple[str, str, str]:
    client_id = os.environ.get(ENV_ID, "")
    secret = os.environ.get(ENV_SECRET, "")
    agent = os.environ.get(ENV_AGENT, "")
    if not client_id or not secret or not agent:
        raise SentimentError("reddit credentials are missing; refusing to call the API")
    return client_id, secret, agent


def _assert_reddit_url(url: str, method: str) -> None:
    if url == TOKEN_URL and method == "POST":
        return
    if url == LISTING_URL and method == "GET":
        return
    raise SentimentError("refusing a reddit URL outside the token and listing calls")


def fetch_reddit(
    as_of: datetime,
    *,
    transport: httpx.BaseTransport | None = None,
    limit: int = 100,
) -> list[Post]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SentimentError("as_of must be timezone-aware")
    client_id, secret, agent = require_reddit_env()
    _assert_reddit_url(TOKEN_URL, "POST")
    _assert_reddit_url(LISTING_URL, "GET")
    with httpx.Client(transport=transport, timeout=30.0) as client:
        token_response = client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, secret),
            headers={"User-Agent": agent},
        )
        if token_response.status_code >= 400:
            raise SentimentError(f"reddit token request failed with status {token_response.status_code}")
        token_body = token_response.json()
        access = token_body.get("access_token") if isinstance(token_body, dict) else None
        if not isinstance(access, str) or not access:
            raise SentimentError("reddit token response had no access token")
        listing = client.get(
            LISTING_URL,
            params={"limit": str(limit), "raw_json": "1"},
            headers={"Authorization": f"bearer {access}", "User-Agent": agent},
        )
        if listing.status_code >= 400:
            raise SentimentError(f"reddit listing failed with status {listing.status_code}")
        body = listing.json()
    children = body.get("data", {}).get("children", []) if isinstance(body, dict) else None
    if not isinstance(children, list):
        raise SentimentError("reddit listing had no children")
    posts: list[Post] = []
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get("data"), dict):
            continue
        row = child["data"]
        created = row.get("created_utc")
        if not isinstance(created, (int, float)) or isinstance(created, bool):
            continue
        moment = datetime.fromtimestamp(float(created), tz=timezone.utc)
        if moment > as_of:
            continue
        title = row.get("title") if isinstance(row.get("title"), str) else ""
        body_text = row.get("selftext") if isinstance(row.get("selftext"), str) else ""
        author = row.get("author") if isinstance(row.get("author"), str) else "[deleted]"
        post_id = row.get("id")
        if not isinstance(post_id, str) or not post_id:
            continue
        posts.append(
            Post(
                source="reddit",
                id=post_id,
                created_at=moment,
                author=author,
                text=f"{title}\n{body_text}".strip(),
                metrics={
                    "score": row.get("score"),
                    "num_comments": row.get("num_comments"),
                },
            )
        )
    return posts
