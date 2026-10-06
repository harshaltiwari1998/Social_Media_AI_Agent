from __future__ import annotations

import pytest

from app.config import settings
from app.pipeline import orchestrator, store


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_output_dir", str(tmp_path))
    monkeypatch.setattr(settings, "dry_run", True)
    # Tests assert on the "not configured" path regardless of what real credentials
    # happen to be sitting in the developer's .env.
    monkeypatch.setattr(settings, "meta_page_id", None)
    monkeypatch.setattr(settings, "meta_access_token", None)


def test_store_round_trip(tmp_path):
    assert store.already_posted("trend-1", "facebook") is False

    store.record_post("trend-1", "facebook", "post-abc", dry_run=False)
    assert store.already_posted("trend-1", "facebook") is True

    # A dry-run attempt never counts as "already posted".
    store.record_post("trend-2", "facebook", None, dry_run=True)
    assert store.already_posted("trend-2", "facebook") is False


def test_run_pipeline_rejects_unknown_platform():
    with pytest.raises(ValueError, match="unknown platform"):
        orchestrator.run_pipeline(platforms=["myspace"])


def test_run_pipeline_returns_error_when_no_trends(monkeypatch):
    monkeypatch.setattr(orchestrator, "aggregate", lambda **kw: {"trends": [], "sources_skipped": []})

    result = orchestrator.run_pipeline(platforms=["facebook"])

    assert result["success"] is False
    assert "no trends" in result["error"]


def test_run_pipeline_happy_path_dry_run(monkeypatch):
    fake_trend = {"id": "hf:some-model", "source": "huggingface", "title": "Some Model", "score": 1.0}
    monkeypatch.setattr(
        orchestrator, "aggregate", lambda **kw: {"trends": [fake_trend], "sources_skipped": []}
    )
    monkeypatch.setattr(
        orchestrator,
        "analyze",
        lambda trends: {
            "success": True,
            "data": {
                "summary": "Some Model is trending.",
                "content_ideas": [
                    {
                        "trend_title": "Some Model",
                        "headline": "Big news",
                        "caption": "Check this out",
                        "image_prompts": ["a robot", "a robot from another angle"],
                        "video_script": "narration",
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "generate_image",
        lambda prompt: {"success": True, "data": {"path": "img.png", "prompt": prompt}},
    )
    monkeypatch.setattr(
        orchestrator,
        "generate_video",
        lambda images, caption, script, headline=None: {"success": True, "data": {"path": "clip.mp4", "duration_seconds": 10}},
    )

    result = orchestrator.run_pipeline(platforms=["facebook"], include_video=True)

    assert result["success"] is True
    assert result["dry_run"] is True
    idea = result["ideas"][0]
    assert idea["trend_title"] == "Some Model"
    assert idea["posts"][0]["platform"] == "facebook"
    # No credentials configured in the test env, so it fails the config check
    # rather than silently pretending to post.
    assert idea["posts"][0]["success"] is False


def test_run_pipeline_skips_already_posted_trend(monkeypatch):
    fake_trend = {"id": "hf:some-model", "source": "huggingface", "title": "Some Model", "score": 1.0}
    monkeypatch.setattr(
        orchestrator, "aggregate", lambda **kw: {"trends": [fake_trend], "sources_skipped": []}
    )
    monkeypatch.setattr(
        orchestrator,
        "analyze",
        lambda trends: {
            "success": True,
            "data": {
                "summary": "s",
                "content_ideas": [
                    {
                        "trend_title": "Some Model",
                        "headline": "h",
                        "caption": "c",
                        "image_prompts": ["p"],
                        "video_script": "v",
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(
        orchestrator, "generate_image", lambda prompt: {"success": True, "data": {"path": "img.png", "prompt": prompt}}
    )

    store.record_post("hf:some-model", "facebook", "already-there", dry_run=False)

    result = orchestrator.run_pipeline(platforms=["facebook"], include_video=False)

    assert result["ideas"][0]["posts"][0] == {"platform": "facebook", "skipped": "already posted"}


@pytest.mark.parametrize(
    ("idea", "expected"),
    [
        ({"category": "gaming"}, ["youtube", "instagram"]),
        ({"category": "sports", "tags": ["Gaming"]}, ["instagram", "facebook"]),
        ({"tags": ["GTA6", "Gaming"]}, ["youtube", "instagram"]),
        ({"tags": ["Bollywood"]}, ["instagram", "facebook"]),
    ],
)
def test_platforms_for_routes_gaming_separately(monkeypatch, idea, expected):
    monkeypatch.setattr(settings, "target_platforms", ["instagram", "facebook"])
    monkeypatch.setattr(settings, "gaming_platforms", ["youtube", "instagram"])

    assert orchestrator.platforms_for(idea) == expected
