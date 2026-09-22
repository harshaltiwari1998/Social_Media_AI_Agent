from __future__ import annotations

from app.trends.schemas import TrendFetchError, TrendItem

SOURCE = "huggingface"


def fetch(limit: int = 10) -> list[TrendItem]:
    """Fetch currently trending models from the Hugging Face Hub."""
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover - import guard
        raise TrendFetchError(SOURCE, "huggingface_hub is not installed") from exc

    try:
        api = HfApi()
        models = list(api.list_models(sort="trendingScore", direction=-1, limit=limit))
    except Exception as exc:
        raise TrendFetchError(SOURCE, f"request failed: {exc}") from exc

    if not models:
        return []

    max_likes = max((m.likes or 0) for m in models) or 1
    items: list[TrendItem] = []
    for rank, model in enumerate(models):
        likes = model.likes or 0
        # Blend rank position with likes so #1 trending always scores highest.
        position_score = (limit - rank) / limit
        like_score = likes / max_likes
        score = round((0.7 * position_score) + (0.3 * like_score), 4)
        items.append(
            TrendItem(
                id=f"huggingface:{model.id}",
                source=SOURCE,
                title=model.id,
                url=f"https://huggingface.co/{model.id}",
                score=score,
                summary=f"{likes} likes, pipeline: {model.pipeline_tag or 'n/a'}",
                raw={
                    "likes": likes,
                    "downloads": getattr(model, "downloads", None),
                    "pipeline_tag": model.pipeline_tag,
                    "tags": model.tags,
                },
            )
        )
    return items
