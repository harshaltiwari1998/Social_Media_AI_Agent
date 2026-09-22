from __future__ import annotations

import logging
from typing import Any, Callable

from app.trends import github_trending, google_trends, huggingface, reddit, twitter_trends
from app.trends.schemas import TrendFetchError, TrendItem

logger = logging.getLogger(__name__)

_FETCHERS: list[Callable[..., list[TrendItem]]] = [
    huggingface.fetch,
    github_trending.fetch,
    google_trends.fetch,
    reddit.fetch,
    twitter_trends.fetch,
]


def aggregate(limit_per_source: int = 10, top_n: int = 15) -> dict[str, Any]:
    """Run every trend fetcher, skipping any source that fails or isn't configured."""
    items: list[TrendItem] = []
    skipped: list[dict[str, str]] = []

    for fetch_fn in _FETCHERS:
        try:
            source_items = fetch_fn(limit=limit_per_source)
            items.extend(source_items)
        except TrendFetchError as exc:
            logger.warning("Skipping trend source: %s", exc)
            skipped.append({"source": exc.source, "reason": exc.reason})
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error fetching trends")
            skipped.append({"source": getattr(fetch_fn, "__module__", "unknown"), "reason": str(exc)})

    ranked = sorted(items, key=lambda i: i.score, reverse=True)[:top_n]

    return {
        "success": True,
        "count": len(ranked),
        "sources_used": sorted({item.source for item in items}),
        "sources_skipped": skipped,
        "trends": [item.model_dump(mode="json") for item in ranked],
    }
