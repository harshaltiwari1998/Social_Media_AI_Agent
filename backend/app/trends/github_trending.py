from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "github"
_SEARCH_URL = "https://api.github.com/search/repositories"
_AI_TOPICS = ["llm", "large-language-models", "generative-ai", "stable-diffusion", "ai-agents"]


def fetch(limit: int = 10, lookback_days: int = 14) -> list[TrendItem]:
    """Approximate GitHub's trending page using the public Search API.

    GitHub does not expose an official trending endpoint, so this searches for
    AI-related repositories pushed recently and sorted by star count instead.
    """
    # GitHub's search API does not support OR-ing qualifiers together (e.g.
    # "topic:a OR topic:b" returns a 422), so each topic is queried separately
    # and the results are merged and deduplicated by repo id.
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    repos_by_id: dict[int, dict] = {}
    last_error: Exception | None = None
    for topic in _AI_TOPICS:
        query = f"topic:{topic} pushed:>{since}"
        try:
            response = httpx.get(
                _SEARCH_URL,
                params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
                headers=headers,
                timeout=15.0,
            )
            response.raise_for_status()
            for repo in response.json().get("items", []):
                repos_by_id[repo["id"]] = repo
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise TrendFetchError(SOURCE, "rate limited (set GITHUB_TOKEN to raise the limit)") from exc
            last_error = exc
        except Exception as exc:
            last_error = exc

    if not repos_by_id and last_error is not None:
        raise TrendFetchError(SOURCE, f"request failed: {last_error}")

    repos = sorted(repos_by_id.values(), key=lambda r: r.get("stargazers_count", 0), reverse=True)[:limit]
    if not repos:
        return []

    max_stars = max(repo.get("stargazers_count", 0) for repo in repos) or 1
    items: list[TrendItem] = []
    for repo in repos:
        stars = repo.get("stargazers_count", 0)
        items.append(
            TrendItem(
                id=f"github:{repo['full_name']}",
                source=SOURCE,
                title=repo["full_name"],
                url=repo.get("html_url"),
                score=round(stars / max_stars, 4),
                summary=repo.get("description"),
                raw={
                    "stars": stars,
                    "language": repo.get("language"),
                    "topics": repo.get("topics"),
                    "pushed_at": repo.get("pushed_at"),
                },
            )
        )
    return items
