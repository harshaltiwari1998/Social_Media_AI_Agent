from __future__ import annotations

import re
from pathlib import Path

from app.config import settings
from app.social.base import PostResult, SocialPoster

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _token_path() -> Path:
    return Path(settings.media_output_dir).parent / ".cache" / "youtube_token.json"


class YouTubePoster(SocialPoster):
    """Uploads shorts to YouTube via the Data API v3 (OAuth installed-app flow).

    First run must happen interactively (it opens a browser to authorize);
    the resulting refresh token is cached to `.cache/youtube_token.json` so
    later scheduled runs don't need a browser.
    """

    platform = "youtube"

    def is_configured(self) -> bool:
        return bool(settings.youtube_client_secret_file)

    def _get_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = _token_path()
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
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
        snippet = {"title": title, "description": f"{caption}\n\n#Shorts", "categoryId": "24"}
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
