from __future__ import annotations

from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "google_trends"

# Seed keywords we track rising interest against. Kept small: pytrends only
# accepts up to 5 keywords per payload.
_SEED_KEYWORDS = ["Hollywood movies", "Bollywood movies", "Tollywood movies", "video games", "new songs"]


def fetch(limit: int = 10) -> list[TrendItem]:
    """Fetch rising search queries related to popular AI model keywords."""
    try:
        from pytrends.request import TrendReq
    except ImportError as exc:  # pragma: no cover - import guard
        raise TrendFetchError(SOURCE, "pytrends is not installed") from exc

    try:
        pytrends = TrendReq(hl="en-US", tz=0)
        pytrends.build_payload(_SEED_KEYWORDS, timeframe="now 7-d")
        related = pytrends.related_queries()
    except Exception as exc:
        raise TrendFetchError(SOURCE, f"request failed: {exc}") from exc

    candidates: list[tuple[str, str, int]] = []
    for keyword, tables in related.items():
        rising = tables.get("rising") if tables else None
        if rising is None or rising.empty:
            continue
        for _, row in rising.iterrows():
            candidates.append((keyword, str(row["query"]), int(row["value"])))

    if not candidates:
        return []

    candidates.sort(key=lambda c: c[2], reverse=True)
    candidates = candidates[:limit]
    max_value = max(c[2] for c in candidates) or 1

    items: list[TrendItem] = []
    for keyword, query, value in candidates:
        items.append(
            TrendItem(
                id=f"google_trends:{query.lower()}",
                source=SOURCE,
                title=query,
                url=f"https://trends.google.com/trends/explore?q={query.replace(' ', '%20')}",
                score=round(value / max_value, 4),
                summary=f"Rising search related to '{keyword}' (breakout value {value})",
                raw={"seed_keyword": keyword, "rising_value": value},
            )
        )
    return items
