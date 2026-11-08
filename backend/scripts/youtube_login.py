"""Sign in to a YouTube channel (opens a browser) and cache its token for scheduled runs.

    python scripts/youtube_login.py youtube          # gaming channel (HarryBeast Gaming)
    python scripts/youtube_login.py youtube_movies   # movies channel (Name Writer)

On the Google sign-in page pick the account / brand channel that should receive the uploads.
Needed once per channel, and again whenever a job logs "YouTube login ... expired".
--force signs in again even if the token still works (e.g. to grant the playlist permission added 2026-11-08);
the old token is kept as <token>.bak and restored if the new sign-in fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.discovery import build  # noqa: E402

from app.pipeline.orchestrator import _POSTERS  # noqa: E402

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    platform = args[0] if args else "youtube"
    poster = _POSTERS[platform]
    token, backup = poster._token_path(), None
    if "--force" in sys.argv and token.exists():
        backup = token.with_suffix(token.suffix + ".bak")
        token.replace(backup)
    try:
        creds = poster._get_credentials()
    except BaseException:
        if backup and not token.exists():
            backup.replace(token)
        raise
    channels = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute()
    names = [item["snippet"]["title"] for item in channels.get("items", [])]
    print(f"{platform} login saved for channel: {', '.join(names) or '(no channel found)'}")
