from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "google_trends_daily"
_RSS_URL = "https://trends.google.com/trending/rss"
_HT_NS = {"ht": "https://trends.google.com/trending/rss"}


def _parse_traffic(text: str | None) -> int:
    """Turn Google's approximate traffic strings like '20,000+' into an int."""
    digits = re.sub(r"[^0-9]", "", text or "")
    return int(digits) if digits else 0


def fetch(limit: int = 10, geo: str = "IN") -> list[TrendItem]:
    """Fetch today's top trending searches from Google's official daily-trends RSS feed.

    Unlike pytrends (an unofficial scraper that Google rate-limits with 429s), this
    feed is public and stable. It covers every category, so the analyzer is left to
    pick the entertainment/sports/AI-relevant ones.
    """
    try:
        response = httpx.get(_RSS_URL, params={"geo": geo}, timeout=15.0, follow_redirects=True)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        raise TrendFetchError(SOURCE, f"request failed: {exc}") from exc

    entries: list[tuple[str, int, list[str], str | None]] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = _parse_traffic(item.findtext("ht:approx_traffic", namespaces=_HT_NS))
        headlines = [
            (n.findtext("ht:news_item_title", namespaces=_HT_NS) or "").strip()
            for n in item.findall("ht:news_item", namespaces=_HT_NS)
        ]
        first_url = next(
            (n.findtext("ht:news_item_url", namespaces=_HT_NS) for n in item.findall("ht:news_item", namespaces=_HT_NS)),
            None,
        )
        entries.append((title, traffic, [h for h in headlines if h], first_url))

    if not entries:
        return []

    entries.sort(key=lambda e: e[1], reverse=True)
    entries = entries[:limit]
    max_traffic = max(e[1] for e in entries) or 1

    items: list[TrendItem] = []
    for title, traffic, headlines, news_url in entries:
        summary = f"~{traffic}+ searches in {geo} today"
        if headlines:
            summary += f" · {headlines[0]}"
        items.append(
            TrendItem(
                id=f"{SOURCE}:{title.lower()}",
                source=SOURCE,
                title=title,
                url=news_url or f"https://trends.google.com/trends/explore?q={title.replace(' ', '%20')}&geo={geo}",
                score=round(traffic / max_traffic, 4),
                summary=summary,
                raw={"approx_traffic": traffic, "geo": geo, "news_headlines": headlines[:3]},
            )
        )
    return items
