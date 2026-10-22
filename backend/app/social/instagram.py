from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx

from app.config import settings
from app.social.base import PostResult, SocialPoster
from app.social.public_tunnel import public_url_for

_GRAPH_API = "https://graph.facebook.com/v19.0"


class InstagramPoster(SocialPoster):
    """Posts to an Instagram Business account via the Meta Graph API.

    Unlike Facebook Pages, Instagram's Content Publishing API has no raw
    file-upload endpoint - it only accepts a publicly reachable image_url or
    video_url. If `MEDIA_PUBLIC_BASE_URL` serves `MEDIA_OUTPUT_DIR` publicly it's
    used directly; otherwise the file is exposed through a temporary Cloudflare
    quick tunnel that stays open until Instagram has fetched and published it.
    """

    def __init__(self, platform: str = "instagram", ig_user_id: str | None = None, access_token: str | None = None) -> None:
        # ig_user_id / access_token pick a second account (e.g. the gaming one); default is the main account.
        self.platform = platform
        self._ig_user_id = ig_user_id
        self._token = access_token

    @property
    def ig_user_id(self) -> str | None:
        return self._ig_user_id if self.platform != "instagram" else (self._ig_user_id or settings.meta_ig_user_id)

    @property
    def token(self) -> str | None:
        return self._token if self.platform != "instagram" else (self._token or settings.meta_access_token)

    def is_configured(self) -> bool:
        return bool(self.ig_user_id and self.token)

    @contextmanager
    def _public_url(self, local_path: str) -> Iterator[str]:
        if settings.media_public_base_url:
            rel = Path(local_path).resolve().relative_to(Path(settings.media_output_dir).resolve())
            yield f"{settings.media_public_base_url.rstrip('/')}/{rel.as_posix()}"
        else:
            with public_url_for(local_path) as url:
                yield url

    def _post_image(self, image_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        from PIL import Image

        # Instagram only accepts JPEG for image posts; Stable Diffusion writes PNG.
        jpeg_path = Path(image_path).with_suffix(".ig.jpg")
        Image.open(image_path).convert("RGB").save(jpeg_path, "JPEG", quality=92)
        try:
            with self._public_url(str(jpeg_path)) as image_url:
                creation = httpx.post(
                    f"{_GRAPH_API}/{self.ig_user_id}/media",
                    data={"image_url": image_url, "caption": caption, "access_token": self.token},
                    timeout=60.0,
                ).json()
                return self._publish_container(creation)
        finally:
            jpeg_path.unlink(missing_ok=True)

    def _post_video(self, video_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        # The URL must stay reachable until processing finishes, so publish inside the block.
        with self._public_url(video_path) as video_url:
            creation = httpx.post(
                f"{_GRAPH_API}/{self.ig_user_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": video_url,
                    # Cover frame in ms: the first frame is the black fade-in, which leaves a black tile in the grid.
                    "thumb_offset": str(settings.instagram_thumb_offset_ms),
                    "caption": caption,
                    "access_token": self.token,
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
                params={"fields": "id", "access_token": self.token},
                timeout=30.0,
            )
            return response.is_success and "id" in response.json()
        except Exception:
            return False

    def _wait_until_ready(self, creation_id: str, timeout_s: float = 300.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = httpx.get(
                f"{_GRAPH_API}/{creation_id}",
                params={"fields": "status_code", "access_token": self.token},
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
            f"{_GRAPH_API}/{self.ig_user_id}/media_publish",
            data={"creation_id": creation["id"], "access_token": self.token},
            timeout=60.0,
        ).json()
        if "id" in publish:
            return PostResult(platform=self.platform, success=True, dry_run=False, post_id=publish["id"])
        error = publish.get("error", {}).get("message", str(publish))
        return PostResult(platform=self.platform, success=False, dry_run=False, error=error)
