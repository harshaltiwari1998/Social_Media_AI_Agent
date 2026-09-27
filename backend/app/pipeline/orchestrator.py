from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.analysis.analyzer import analyze
from app.config import settings
from app.media.image_gen import generate_image
from app.media.video_gen import generate_video
from app.pipeline import store
from app.social.base import SocialPoster
from app.social.facebook import FacebookPoster
from app.social.instagram import InstagramPoster
from app.social.twitter import TwitterPoster
from app.social.youtube import YouTubePoster
from app.trends.aggregator import aggregate

logger = logging.getLogger(__name__)

def _cleanup_media(paths: list[str]) -> None:
    """Delete generated media once it's been posted live - keeps media_output/ from growing forever."""
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete generated media file: %s", path, exc_info=True)


_POSTERS: dict[str, SocialPoster] = {
    "facebook": FacebookPoster(),
    "instagram": InstagramPoster(),
    "twitter": TwitterPoster(),
    "youtube": YouTubePoster(),
}


def run_pipeline(
    limit_per_source: int = 10,
    top_n: int = 15,
    platforms: list[str] | None = None,
    include_video: bool = True,
) -> dict[str, Any]:
    """Trends -> LLM content ideas -> generated media -> (dry-run log | post), deduped via SQLite."""
    target_platforms = platforms if platforms is not None else settings.target_platforms
    unknown = sorted(set(target_platforms) - set(_POSTERS))
    if unknown:
        raise ValueError(f"unknown platform(s) {unknown}; choose from {sorted(_POSTERS)}")

    aggregated = aggregate(limit_per_source=limit_per_source, top_n=top_n)
    if not aggregated["trends"]:
        return {"success": False, "error": "no trends fetched", "sources_skipped": aggregated["sources_skipped"], "ideas": []}

    analysis = analyze(aggregated["trends"])
    if not analysis["success"]:
        return {"success": False, "error": analysis["error"], "ideas": []}

    trends_by_title = {t["title"]: t for t in aggregated["trends"]}
    ideas_results: list[dict[str, Any]] = []

    for idea in analysis["data"].get("content_ideas", []):
        trend = trends_by_title.get(idea["trend_title"])
        trend_id = trend["id"] if trend else idea["trend_title"]

        image_results = [generate_image(p) for p in idea["image_prompts"]]
        image_paths = [r["data"]["path"] for r in image_results if r["success"]]
        idea_result: dict[str, Any] = {
            "trend_title": idea["trend_title"],
            "headline": idea["headline"],
            "images": image_results,
            "video": None,
            "posts": [],
        }

        if not image_paths:
            logger.warning("Skipping platform posting for %r: image generation failed", idea["trend_title"])
            ideas_results.append(idea_result)
            continue

        # A single still image held for the whole narration looks static/boring as a
        # "video" - generate_video slideshows across all scene images instead.
        video_result = None
        if include_video:
            video_result = generate_video(image_paths, idea["caption"], idea["video_script"])
            idea_result["video"] = video_result

        use_video = bool(video_result and video_result["success"])
        media_path = video_result["data"]["path"] if use_video else image_paths[0]

        for platform_name in target_platforms:
            poster = _POSTERS[platform_name]

            if store.already_posted(trend_id, platform_name):
                idea_result["posts"].append({"platform": platform_name, "skipped": "already posted"})
                continue

            tags = idea.get("tags")
            result = (
                poster.post_video(media_path, idea["caption"], tags=tags)
                if use_video
                else poster.post_image(media_path, idea["caption"], tags=tags)
            )
            store.record_post(trend_id, platform_name, result.post_id, result.dry_run)
            post_entry = {"platform": platform_name, **result.model_dump()}

            if result.success and not result.dry_run and result.post_id:
                post_entry["verified"] = poster.verify_post(result.post_id)

            idea_result["posts"].append(post_entry)

        live_posts = [p for p in idea_result["posts"] if p.get("success") and not p.get("dry_run")]
        # Only delete generated media once every platform that claimed a successful
        # live post has been re-confirmed to actually have it - a false-positive
        # verify would otherwise lose the only copy of unpublished content.
        if live_posts and all(p.get("verified") for p in live_posts):
            paths_to_delete = list(image_paths)
            if use_video:
                paths_to_delete.append(video_result["data"]["path"])
                paths_to_delete.extend(video_result["data"].get("intermediate_paths", []))
            _cleanup_media(paths_to_delete)
            idea_result["media_deleted"] = True

        ideas_results.append(idea_result)

    return {
        "success": True,
        "dry_run": settings.dry_run,
        "sources_skipped": aggregated["sources_skipped"],
        "summary": analysis["data"].get("summary"),
        "ideas": ideas_results,
    }
