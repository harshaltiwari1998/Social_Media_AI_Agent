"""Sign in to a YouTube channel (opens a browser) and cache its token for scheduled runs.

    python scripts/youtube_login.py youtube          # gaming channel (HarryBeast Gaming)
    python scripts/youtube_login.py youtube_movies   # movies channel (Name Writer)

On the Google sign-in page pick the account / brand channel that should receive the uploads.
Needed once per channel, and again whenever a job logs "YouTube login ... expired".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.discovery import build  # noqa: E402

from app.pipeline.orchestrator import _POSTERS  # noqa: E402

if __name__ == "__main__":
    platform = sys.argv[1] if len(sys.argv) > 1 else "youtube"
    poster = _POSTERS[platform]
    creds = poster._get_credentials()
    channels = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute()
    names = [item["snippet"]["title"] for item in channels.get("items", [])]
    print(f"{platform} login saved for channel: {', '.join(names) or '(no channel found)'}")
