from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.social.base import PostResult, SocialPoster

_GRAPH_API = "https://graph.facebook.com/v19.0"


class InstagramPoster(SocialPoster):
    """Posts to an Instagram Business account via the Meta Graph API.

    Unlike Facebook Pages, Instagram's Content Publishing API has no raw
    file-upload endpoint - it only accepts a publicly reachable image_url or
    video_url. `MEDIA_PUBLIC_BASE_URL` must be set to whatever serves
    `MEDIA_OUTPUT_DIR` publicly (e.g. an ngrok tunnel) before this can post
    for real; otherwise it fails with a clear error explaining why.
    """

    platform = "instagram"

    def is_configured(self) -> bool:
        return bool(settings.meta_ig_user_id and settings.meta_access_token)

    def _public_url(self, local_path: str) -> str:
        if not settings.media_public_base_url:
            raise RuntimeError(
                "Instagram requires media at a public URL (no direct file-upload endpoint "
                "exists). Set MEDIA_PUBLIC_BASE_URL in .env to a URL that serves "
                "MEDIA_OUTPUT_DIR (e.g. via ngrok or a static file server) first."
            )
        rel = Path(local_path).resolve().relative_to(Path(settings.media_output_dir).resolve())
        return f"{settings.media_public_base_url.rstrip('/')}/{rel.as_posix()}"

    def _post_image(self, image_path: str, caption: str) -> PostResult:
        image_url = self._public_url(image_path)
        creation = httpx.post(
            f"{_GRAPH_API}/{settings.meta_ig_user_id}/media",
            data={"image_url": image_url, "caption": caption, "access_token": settings.meta_access_token},
            timeout=60.0,
        ).json()
        return self._publish_container(creation)

    def _post_video(self, video_path: str, caption: str) -> PostResult:
        video_url = self._public_url(video_path)
        creation = httpx.post(
            f"{_GRAPH_API}/{settings.meta_ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": settings.meta_access_token,
            },
            timeout=60.0,
        ).json()
        if "id" not in creation:
            error = creation.get("error", {}).get("message", str(creation))
            return PostResult(platform=self.platform, success=False, dry_run=False, error=error)
        if not self._wait_until_ready(creation["id"]):
            return PostResult(
                platform=self.platform,
                success=False,
                dry_run=False,
                error="Instagram video processing did not finish in time",
            )
        return self._publish_container(creation)

    def verify_post(self, post_id: str) -> bool:
        try:
            response = httpx.get(
                f"{_GRAPH_API}/{post_id}",
                params={"fields": "id", "access_token": settings.meta_access_token},
                timeout=30.0,
            )
            return response.is_success and "id" in response.json()
        except Exception:
            return False

    def _wait_until_ready(self, creation_id: str, timeout_s: float = 120.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = httpx.get(
                f"{_GRAPH_API}/{creation_id}",
                params={"fields": "status_code", "access_token": settings.meta_access_token},
                timeout=30.0,
            ).json()
            if status.get("status_code") == "FINISHED":
                return True
            time.sleep(5)
        return False

    def _publish_container(self, creation: dict[str, Any]) -> PostResult:
        if "id" not in creation:
            error = creation.get("error", {}).get("message", str(creation))
            return PostResult(platform=self.platform, success=False, dry_run=False, error=error)
        publish = httpx.post(
            f"{_GRAPH_API}/{settings.meta_ig_user_id}/media_publish",
            data={"creation_id": creation["id"], "access_token": settings.meta_access_token},
            timeout=60.0,
        ).json()
        if "id" in publish:
            return PostResult(platform=self.platform, success=True, dry_run=False, post_id=publish["id"])
        error = publish.get("error", {}).get("message", str(publish))
        return PostResult(platform=self.platform, success=False, dry_run=False, error=error)
