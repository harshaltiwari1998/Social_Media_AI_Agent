from __future__ import annotations

from app.config import settings
from app.social.base import PostResult, SocialPoster
from app.social.facebook import FacebookPoster
from app.social.instagram import InstagramPoster
from app.social.twitter import TwitterPoster
from app.social.youtube import YouTubePoster


class _DummyPoster(SocialPoster):
    platform = "dummy"

    def __init__(self, configured: bool) -> None:
        self._configured = configured
        self.real_calls: list[str] = []

    def is_configured(self) -> bool:
        return self._configured

    def _post_image(self, image_path: str, caption: str) -> PostResult:
        self.real_calls.append(image_path)
        return PostResult(platform=self.platform, success=True, dry_run=False, post_id="real-123")

    def _post_video(self, video_path: str, caption: str) -> PostResult:
        self.real_calls.append(video_path)
        return PostResult(platform=self.platform, success=True, dry_run=False, post_id="real-video-123")


def test_unconfigured_poster_fails_without_calling_real_api():
    poster = _DummyPoster(configured=False)
    result = poster.post_image("img.png", "caption")

    assert result.success is False
    assert "not configured" in result.error
    assert poster.real_calls == []


def test_configured_poster_short_circuits_in_dry_run(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", True)
    poster = _DummyPoster(configured=True)

    result = poster.post_image("img.png", "caption")

    assert result.success is True
    assert result.dry_run is True
    assert result.post_id is None
    assert poster.real_calls == []  # real API never touched while DRY_RUN=true


def test_configured_poster_calls_real_api_when_dry_run_disabled(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)
    poster = _DummyPoster(configured=True)

    result = poster.post_video("clip.mp4", "caption")

    assert result.success is True
    assert result.dry_run is False
    assert result.post_id == "real-video-123"
    assert poster.real_calls == ["clip.mp4"]


def test_real_call_exception_becomes_failed_result(monkeypatch):
    monkeypatch.setattr(settings, "dry_run", False)

    class _Boom(_DummyPoster):
        def _post_image(self, image_path: str, caption: str) -> PostResult:
            raise RuntimeError("network exploded")

    poster = _Boom(configured=True)
    result = poster.post_image("img.png", "caption")

    assert result.success is False
    assert "network exploded" in result.error


def test_platform_posters_are_unconfigured_without_credentials(monkeypatch):
    for field in [
        "meta_page_id",
        "meta_access_token",
        "meta_ig_user_id",
        "twitter_api_key",
        "twitter_api_secret",
        "twitter_access_token",
        "twitter_access_secret",
        "youtube_client_secret_file",
    ]:
        monkeypatch.setattr(settings, field, None)

    assert FacebookPoster().is_configured() is False
    assert InstagramPoster().is_configured() is False
    assert TwitterPoster().is_configured() is False
    assert YouTubePoster().is_configured() is False
