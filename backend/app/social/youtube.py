from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from app.config import settings
from app.social.base import PostResult, SocialPoster

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


logger = logging.getLogger(__name__)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class YouTubePoster(SocialPoster):
    """Uploads shorts to one YouTube channel via the Data API v3 (OAuth installed-app flow).

    Each channel has its own cached token file (one login per channel), e.g. the gaming
    channel and the movies channel. The first login must happen interactively
    (scripts/youtube_login.py opens a browser); later scheduled runs reuse the token.
    """

    def __init__(self, platform: str = "youtube", token_file: str | None = None, category_id: str | None = None) -> None:
        self.platform = platform
        self._token_file = token_file
        self._category_id = category_id

    def _token_path(self) -> Path:
        path = Path(self._token_file or settings.youtube_token_file)
        return path if path.is_absolute() else _BACKEND_ROOT / path

    def is_configured(self) -> bool:
        return bool(settings.youtube_client_secret_file)

    def _get_credentials(self):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = self._token_path()
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    # Refresh tokens of OAuth apps in "Testing" status expire after 7 days;
                    # fall back to the interactive login instead of failing the upload.
                    creds = None
            if not creds or not creds.valid:
                if os.environ.get("YOUTUBE_NONINTERACTIVE"):
                    # Scheduled runs have nobody to click through the browser login - fail fast
                    # instead of blocking forever on run_local_server().
                    raise RuntimeError(
                        f"YouTube login for {self.platform} expired - run scripts/youtube_login.py {self.platform} once to sign in again"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(settings.youtube_client_secret_file, _SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        return creds

    def _post_image(self, image_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        return PostResult(
            platform=self.platform,
            success=False,
            dry_run=False,
            error="YouTube has no standalone image post - use post_video instead",
        )

    def verify_post(self, post_id: str) -> bool:
        try:
            from googleapiclient.discovery import build

            youtube = build("youtube", "v3", credentials=self._get_credentials())
            response = youtube.videos().list(part="id", id=post_id).execute()
            return bool(response.get("items"))
        except Exception:
            return False

    def _post_video(self, video_path: str, caption: str, tags: list[str] | None = None) -> PostResult:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = self._get_credentials()
        youtube = build("youtube", "v3", credentials=creds)
        # The orchestrator puts the headline on the first line; Hindi sentences end
        # with "।" rather than ".", so split on line breaks first.
        first_line = caption.strip().split("\n")[0]
        title = (re.split(r"[।.?]", first_line)[0].strip() or first_line)[:90] or "Trending Short"

        # "#Shorts" in the description helps YouTube file vertical <60s videos under Shorts.
        snippet = {"title": title, "description": f"{caption}\n\n#Shorts", "categoryId": self._category_id or settings.youtube_category_id}
        if tags:
            snippet["tags"] = tags

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": snippet,
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
            },
            media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
        )
        response = request.execute()
        video_id = response["id"]
        return PostResult(
            platform=self.platform,
            success=True,
            dry_run=False,
            post_id=video_id,
            url=f"https://youtube.com/watch?v={video_id}",
        )

    def post_long_video(
        self, video_path: str, title: str, description: str, tags: list[str] | None = None, thumbnail: str | None = None
    ) -> PostResult:
        """Upload a regular (non-Shorts) video with an explicit title and an optional custom thumbnail."""
        if settings.dry_run:
            return PostResult(platform=self.platform, success=True, dry_run=True)
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            youtube = build("youtube", "v3", credentials=self._get_credentials())
            snippet = {
                "title": title[:100],
                "description": description[:4900],
                "categoryId": self._category_id or settings.youtube_category_id,
            }
            if tags:
                snippet["tags"] = tags
            response = youtube.videos().insert(
                part="snippet,status",
                body={"snippet": snippet, "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}},
                media_body=MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True),
            ).execute()
            video_id = response["id"]
            if thumbnail:
                try:
                    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumbnail)).execute()
                except Exception as exc:  # custom thumbnails need a phone-verified channel
                    logger.warning("Custom thumbnail rejected for %s: %s", video_id, exc)
            return PostResult(
                platform=self.platform, success=True, dry_run=False,
                post_id=video_id, url=f"https://youtube.com/watch?v={video_id}",
            )
        except Exception as exc:
            logger.exception("Long video upload to %s failed", self.platform)
            return PostResult(platform=self.platform, success=False, dry_run=False, error=str(exc))
