from __future__ import annotations

from app.config import settings
from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "twitter"

# X's free API tier does not include the legacy trends/place endpoint, so we
# approximate "trending" by counting recent-search volume for movie/game/music
# keywords instead of relying on a dedicated trends endpoint.
_KEYWORDS = ["Hollywood", "Bollywood", "Tollywood", "new movie", "new game", "new song"]


def fetch(limit: int = 10) -> list[TrendItem]:
    """Estimate movies/games/music buzz on X via recent-search result counts."""
    if not settings.twitter_bearer_token:
        raise TrendFetchError(SOURCE, "TWITTER_BEARER_TOKEN not configured")

    try:
        import tweepy
    except ImportError as exc:  # pragma: no cover - import guard
        raise TrendFetchError(SOURCE, "tweepy is not installed") from exc

    try:
        client = tweepy.Client(bearer_token=settings.twitter_bearer_token)
        counts_by_keyword: dict[str, int] = {}
        for keyword in _KEYWORDS[:limit]:
            query = f'"{keyword}" -is:retweet lang:en'
            result = client.get_recent_tweets_count(query=query, granularity="day")
            total = sum(bucket["tweet_count"] for bucket in (result.data or []))
            counts_by_keyword[keyword] = total
    except Exception as exc:
        raise TrendFetchError(SOURCE, f"request failed: {exc}") from exc

    if not counts_by_keyword:
        return []

    max_count = max(counts_by_keyword.values()) or 1
    items: list[TrendItem] = []
    for keyword, count in sorted(counts_by_keyword.items(), key=lambda kv: kv[1], reverse=True):
        items.append(
            TrendItem(
                id=f"twitter:{keyword.lower().replace(' ', '-')}",
                source=SOURCE,
                title=keyword,
                url=f"https://x.com/search?q={keyword.replace(' ', '%20')}",
                score=round(count / max_count, 4),
                summary=f"{count} recent posts mentioning '{keyword}' (last 24h-7d)",
                raw={"tweet_count": count},
            )
        )
    return items
