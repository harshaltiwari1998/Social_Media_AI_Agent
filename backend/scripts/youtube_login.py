"""Sign in to YouTube again (opens a browser) and cache the token for scheduled runs.

Needed whenever the daily job logs "YouTube login expired" - Google expires refresh
tokens of OAuth apps in "Testing" status after 7 days. Sign in with the account
that's a test user on the ai-trend-poster Google Cloud project.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.social.youtube import YouTubePoster  # noqa: E402

if __name__ == "__main__":
    YouTubePoster()._get_credentials()
    print("YouTube login saved.")
