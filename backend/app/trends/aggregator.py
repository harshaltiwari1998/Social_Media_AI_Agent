from __future__ import annotations

import logging
from typing import Any, Callable

from app.trends import github_trending, google_trends, google_trends_rss, huggingface, reddit, twitter_trends
from app.trends.schemas import TrendFetchError, TrendItem

logger = logging.getLogger(__name__)

Fetcher = Callable[..., list[TrendItem]]

# Each entry is (primary, fallback). The fallback only runs if the primary fails,
# e.g. the pytrends scraper is kept as a backup for the official Google RSS feed
# because Google rate-limits it (429) most of the time.
_FETCHERS: list[tuple[Fetcher, Fetcher | None]] = [
    # Entertainment: movies / games / music
    (google_trends_rss.fetch, google_trends.fetch),
    (reddit.fetch, None),  # also covers AI subreddits
    (twitter_trends.fetch, None),
    # AI models / tools
    (huggingface.fetch, None),
    (github_trending.fetch, None),
]


def _run(fetch_fn: Fetcher, limit: int, skipped: list[dict[str, str]]) -> list[TrendItem] | None:
    try:
        return fetch_fn(limit=limit)
    except TrendFetchError as exc:
        logger.warning("Skipping trend source: %s", exc)
        skipped.append({"source": exc.source, "reason": exc.reason})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error fetching trends")
        skipped.append({"source": getattr(fetch_fn, "__module__", "unknown"), "reason": str(exc)})
    return None


def aggregate(limit_per_source: int = 10, top_n: int = 15) -> dict[str, Any]:
    """Run every trend fetcher, skipping any source that fails or isn't configured."""
    per_source: list[list[TrendItem]] = []
    skipped: list[dict[str, str]] = []

    for primary, fallback in _FETCHERS:
        source_items = _run(primary, limit_per_source, skipped)
        if source_items is None and fallback is not None:
            source_items = _run(fallback, limit_per_source, skipped)
        if source_items:
            per_source.append(sorted(source_items, key=lambda i: i.score, reverse=True))

    # Interleave by rank within each source (every source's #1, then every #2, ...)
    # so one noisy source can't crowd the others out of the top_n.
    ranked: list[TrendItem] = []
    seen: set[str] = set()
    for rank in range(max((len(s) for s in per_source), default=0)):
        tier = [s[rank] for s in per_source if rank < len(s)]
        for item in sorted(tier, key=lambda i: i.score, reverse=True):
            if item.id not in seen:
                seen.add(item.id)
                ranked.append(item)
    ranked = ranked[:top_n]

    return {
        "success": True,
        "count": len(ranked),
        "sources_used": sorted({item.source for items in per_source for item in items}),
        "sources_skipped": skipped,
        "trends": [item.model_dump(mode="json") for item in ranked],
    }
