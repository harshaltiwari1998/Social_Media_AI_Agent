from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import httpx

from app.config import settings
from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "reddit"
_SUBREDDITS = [
    "movies", "bollywood", "gaming", "Music",  # entertainment
    "Cricket", "soccer", "sports",  # sports
    "LocalLLaMA", "StableDiffusion", "artificial",  # AI
]
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
# Reddit 403s its .json endpoints for anonymous clients, but the .rss feeds still
# work without OAuth as long as the User-Agent looks like a browser.
_RSS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) universal-ai-agent/0.1 trend-fetcher"


def fetch(limit: int = 10) -> list[TrendItem]:
    """Fetch top posts from the last day across entertainment + AI subreddits.

    Uses the official API when REDDIT_CLIENT_ID/SECRET are set (gives real upvote
    counts); otherwise falls back to the keyless public RSS feeds (rank-based score).
    """
    if settings.reddit_client_id and settings.reddit_client_secret:
        return _fetch_via_api(limit)
    return _fetch_via_rss(limit)


def _fetch_via_rss(limit: int) -> list[TrendItem]:
    # Anonymous clients get a tiny rate limit, so all subreddits are fetched in a
    # single multireddit request (r/a+b+c) and then split back out per subreddit.
    url = f"https://www.reddit.com/r/{'+'.join(_SUBREDDITS)}/top/.rss"
    try:
        with httpx.Client(headers={"User-Agent": _RSS_USER_AGENT}, timeout=15.0, follow_redirects=True) as client:
            response = client.get(url, params={"t": "day", "limit": 100})
            if response.status_code == 429:
                time.sleep(5)
                response = client.get(url, params={"t": "day", "limit": 100})
            response.raise_for_status()
        root = ET.fromstring(response.content)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise TrendFetchError(SOURCE, "rss rate limited (set REDDIT_CLIENT_ID/SECRET for the API)") from exc
        raise TrendFetchError(SOURCE, f"rss request failed: {exc}") from exc
    except Exception as exc:
        raise TrendFetchError(SOURCE, f"rss request failed: {exc}") from exc

    # Group entries by subreddit, keeping feed order (best first), so the big
    # subs like r/gaming don't drown out r/bollywood or r/LocalLLaMA.
    by_sub: dict[str, list[ET.Element]] = {}
    for entry in root.findall("a:entry", _ATOM_NS):
        category = entry.find("a:category", _ATOM_NS)
        sub = category.get("term") if category is not None else "unknown"
        by_sub.setdefault(sub, []).append(entry)

    per_sub = max(2, limit // len(_SUBREDDITS) + 1)
    items: list[TrendItem] = []
    for sub, entries in by_sub.items():
        for rank, entry in enumerate(entries[:per_sub]):
            post_id = (entry.findtext("a:id", namespaces=_ATOM_NS) or "").strip()
            title = (entry.findtext("a:title", namespaces=_ATOM_NS) or "").strip()
            link = entry.find("a:link", _ATOM_NS)
            if not (post_id and title):
                continue
            items.append(
                TrendItem(
                    id=f"reddit:{post_id.removeprefix('t3_')}",
                    source=SOURCE,
                    title=title,
                    url=link.get("href") if link is not None else None,
                    # No upvote counts in RSS, so the sub's #1 post of the day scores highest.
                    score=round((per_sub - rank) / per_sub, 4),
                    summary=f"r/{sub} · #{rank + 1} top post today",
                    raw={"subreddit": sub, "rank": rank + 1, "via": "rss"},
                )
            )

    return sorted(items, key=lambda i: i.score, reverse=True)[:limit]


def _fetch_via_api(limit: int) -> list[TrendItem]:
    try:
        import praw
    except ImportError as exc:  # pragma: no cover - import guard
        raise TrendFetchError(SOURCE, "praw is not installed") from exc

    try:
        reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
        reddit.read_only = True

        posts = []
        per_sub = max(1, limit // len(_SUBREDDITS))
        for sub in _SUBREDDITS:
            posts.extend(reddit.subreddit(sub).top(time_filter="day", limit=per_sub))
    except Exception as exc:
        raise TrendFetchError(SOURCE, f"request failed: {exc}") from exc

    if not posts:
        return []

    max_score = max(post.score for post in posts) or 1
    items: list[TrendItem] = []
    for post in sorted(posts, key=lambda p: p.score, reverse=True)[:limit]:
        items.append(
            TrendItem(
                id=f"reddit:{post.id}",
                source=SOURCE,
                title=post.title,
                url=f"https://reddit.com{post.permalink}",
                score=round(post.score / max_score, 4),
                summary=f"r/{post.subreddit.display_name} · {post.score} upvotes · {post.num_comments} comments",
                raw={
                    "subreddit": post.subreddit.display_name,
                    "upvotes": post.score,
                    "comments": post.num_comments,
                },
            )
        )
    return items
