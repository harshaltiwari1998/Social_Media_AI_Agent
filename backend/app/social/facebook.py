from __future__ import annotations

import httpx

from app.config import settings
from app.social.base import PostResult, SocialPoster

_GRAPH_API = "https://graph.facebook.com/v19.0"


class FacebookPoster(SocialPoster):
    """Posts to a Facebook Page via the Graph API (direct file upload, no public URL needed)."""

    def __init__(self, platform: str = "facebook", page_id: str | None = None, access_token: str | None = None) -> None:
        # page_id / access_token pick a second Page (e.g. the gaming page); default is the main Page from settings.
        self.platform = platform
        self._page_id = page_id
        self._token = access_token

    @property
    def page_id(self) -> str | None:
        return self._page_id if self.platform != "facebook" else (self._page_id or settings.meta_page_id)

    @property
    def token(self) -> str | None:
        return self._token if self.platform != "facebook" else (self._token or settings.meta_access_token)

    def is_configured(self) -> bool:
        return bool(self.page_id and self.token)

    def _post_image(self, image_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        with open(image_path, "rb") as f:
            response = httpx.post(
                f"{_GRAPH_API}/{self.page_id}/photos",
                data={"caption": caption, "access_token": self.token},
                files={"source": f},
                timeout=60.0,
            )
        return self._to_result(response)

    def _post_video(self, video_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        with open(video_path, "rb") as f:
            response = httpx.post(
                f"{_GRAPH_API}/{self.page_id}/videos",
                data={"description": caption, "access_token": self.token},
                files={"source": f},
                timeout=1800.0,  # long-form videos can be a few hundred MB
            )
        return self._to_result(response)

    def verify_post(self, post_id: str) -> bool:
        try:
            response = httpx.get(
                f"{_GRAPH_API}/{post_id}",
                params={"fields": "id", "access_token": self.token},
                timeout=30.0,
            )
            return response.is_success and "id" in response.json()
        except Exception:
            return False

    def _to_result(self, response: httpx.Response) -> PostResult:
        payload = response.json()
        post_id = payload.get("id") or payload.get("post_id")
        if response.is_success and post_id:
            return PostResult(
                platform=self.platform,
                success=True,
                dry_run=False,
                post_id=post_id,
                url=f"https://facebook.com/{post_id}",
            )
        error = payload.get("error", {}).get("message", response.text)
        return PostResult(platform=self.platform, success=False, dry_run=False, error=error)
