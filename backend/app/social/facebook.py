from __future__ import annotations

import time
from pathlib import Path

import httpx

from app.config import settings
from app.social.base import PostResult, SocialPoster

_GRAPH_API = "https://graph.facebook.com/v19.0"
# Video files go to the video host: graph.facebook.com answers a few-hundred-MB upload with a non-JSON error page
# (every long video to the gaming Page failed that way until 2026-11-10).
_GRAPH_VIDEO_API = "https://graph-video.facebook.com/v19.0"
_RESUMABLE_OVER = 80 * 1024 * 1024  # bigger files go up in chunks (_post_video_resumable)


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
        if Path(video_path).stat().st_size > _RESUMABLE_OVER:
            return self._post_video_resumable(video_path, caption)
        with open(video_path, "rb") as f:
            response = httpx.post(
                f"{_GRAPH_VIDEO_API}/{self.page_id}/videos",
                data={"description": caption, "access_token": self.token},
                files={"source": f},
                timeout=1800.0,  # long-form videos can be a few hundred MB
            )
        return self._to_result(response)

    def _post_video_resumable(self, video_path: str, caption: str) -> PostResult:
        """Graph API resumable upload (start / transfer chunks / finish) - one request with a 264 MB long video got
        HTTP 413 on both Graph hosts."""
        url = f"{_GRAPH_VIDEO_API}/{self.page_id}/videos"
        size = Path(video_path).stat().st_size
        r = httpx.post(url, data={"upload_phase": "start", "file_size": str(size), "access_token": self.token},
                       timeout=120.0)
        if not r.is_success:
            return self._to_result(r)
        start = r.json()
        session, video_id = start["upload_session_id"], start.get("video_id")
        lo, hi = int(start["start_offset"]), int(start["end_offset"])
        with open(video_path, "rb") as f:
            while lo < hi:
                f.seek(lo)
                chunk = f.read(hi - lo)
                for attempt in range(4):
                    try:
                        r = httpx.post(url, data={"upload_phase": "transfer", "upload_session_id": session,
                                                  "start_offset": str(lo), "access_token": self.token},
                                       files={"video_file_chunk": ("chunk", chunk)}, timeout=600.0)
                        if r.is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(10 * (attempt + 1))
                if not r.is_success:
                    return self._to_result(r)
                lo, hi = int(r.json()["start_offset"]), int(r.json()["end_offset"])
        r = httpx.post(url, data={"upload_phase": "finish", "upload_session_id": session, "description": caption,
                                  "access_token": self.token}, timeout=300.0)
        if r.is_success and r.json().get("success") and video_id:
            return PostResult(platform=self.platform, success=True, dry_run=False, post_id=video_id,
                              url=f"https://facebook.com/{video_id}")
        return self._to_result(r)

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
        try:
            payload = response.json()
        except ValueError:  # an HTML error page (e.g. 413) instead of a Graph API answer
            payload = {"error": {"message": f"HTTP {response.status_code}: {response.text[:300]}"}}
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
