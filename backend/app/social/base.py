from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class PostResult(BaseModel):
    """Outcome of a single post attempt on one platform."""

    platform: str
    success: bool
    dry_run: bool
    post_id: str | None = None
    url: str | None = None
    error: str | None = None


class SocialPoster(ABC):
    """Base class for a single platform's posting integration.

    Subclasses only implement the real API calls (`_post_image`/`_post_video`)
    and `is_configured`; the DRY_RUN gate and credential check are handled
    once here so every platform starts safe by default.
    """

    platform: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether this platform has the credentials it needs to post for real."""

    @abstractmethod
    def _post_image(self, image_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        ...

    @abstractmethod
    def _post_video(self, video_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        ...

    def post_image(self, image_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        return self._publish(image_path, caption, lambda p, c: self._post_image(p, c, tags))

    def post_video(self, video_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        return self._publish(video_path, caption, lambda p, c: self._post_video(p, c, tags))

    def verify_post(self, post_id: str) -> bool:
        """Re-check with the platform that a post_id from a successful response actually
        exists there. Subclasses override this with a real lookup; default trusts the
        original response (used only if a platform has no cheap way to look a post up)."""
        return True

    def _publish(
        self, media_path: str, caption: str, real_call: Callable[[str, str], PostResult]
    ) -> PostResult:
        if not self.is_configured():
            return PostResult(
                platform=self.platform,
                success=False,
                dry_run=settings.dry_run,
                error=f"{self.platform} is not configured; missing credentials in .env",
            )

        if settings.dry_run:
            logger.info("[DRY_RUN] would post to %s: %s (caption=%r)", self.platform, media_path, caption)
            return PostResult(platform=self.platform, success=True, dry_run=True)

        try:
            return real_call(media_path, caption)
        except Exception as exc:
            logger.exception("Posting to %s failed", self.platform)
            return PostResult(platform=self.platform, success=False, dry_run=False, error=str(exc))
