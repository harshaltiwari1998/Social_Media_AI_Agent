from __future__ import annotations

from app.config import settings
from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "reddit"
_SUBREDDITS = ["MachineLearning", "LocalLLaMA"]


def fetch(limit: int = 10) -> list[TrendItem]:
    """Fetch top AI-related posts from the last day via Reddit's read-only API."""
    if not (settings.reddit_client_id and settings.reddit_client_secret):
        raise TrendFetchError(SOURCE, "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not configured")

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
