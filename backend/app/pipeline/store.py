from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings


def _db_path() -> Path:
    return Path(settings.media_output_dir) / "pipeline_store.sqlite3"


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posted_trends (
            trend_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            post_id TEXT,
            dry_run INTEGER NOT NULL,
            posted_at TEXT NOT NULL,
            PRIMARY KEY (trend_id, platform)
        )
        """
    )
    return conn


def already_posted(trend_id: str, platform: str) -> bool:
    """True only if a real (non-dry-run) post already succeeded for this trend+platform."""
    conn = _get_conn()
    try:
        row = conn.execute(
            # Failed attempts are recorded too (post_id NULL) - they must not block a retry.
            "SELECT 1 FROM posted_trends WHERE trend_id = ? AND platform = ? AND dry_run = 0"
            " AND post_id IS NOT NULL",
            (trend_id, platform),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# Gaming Facebook Page volume (owner 2026-11-14): Jun-Aug 2026 the owner's own clips got 100-340 views each; since the
# pipeline started (21 Oct) ~1,000 videos at 50-90 a day got 0.2 views each, with no restriction on the Page. So only
# these go to facebook_gaming; every other gaming upload gets a "skip:fb-volume" marker there (YouTube + Instagram as before).
FB_GAMING_ALWAYS = ("custom:motion-poke-", "custom:fusion-", "custom:dbzfusion-", "custom:stone-mewtwo-",
                    "custom:compilation-")
FB_GAMING_DAILY = {"gameplay:": 2}  # own-stream clips (RDR2...): at most this many a day (IST)
FB_SKIP = "skip:fb-volume"


def fb_gaming_allowed(trend_id: str) -> bool:
    """True when this upload may go to the gaming Facebook Page; otherwise records the skip marker so it is never
    retried."""
    if trend_id.startswith(FB_GAMING_ALWAYS):
        return True
    for prefix, cap in FB_GAMING_DAILY.items():
        if trend_id.startswith(prefix):
            now = datetime.now(timezone.utc)
            ist = now + timedelta(hours=5, minutes=30)
            start = (ist.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5, minutes=30)).isoformat()
            conn = _get_conn()
            try:
                used = conn.execute(
                    "SELECT COUNT(*) FROM posted_trends WHERE platform = 'facebook_gaming' AND trend_id LIKE ?"
                    " AND posted_at >= ? AND dry_run = 0 AND post_id IS NOT NULL AND post_id NOT LIKE 'skip:%'"
                    " AND post_id != 'laptop'", (prefix + "%", start)).fetchone()[0]
            finally:
                conn.close()
            if used < cap:
                return True
    record_post(trend_id, "facebook_gaming", FB_SKIP, False)
    return False


def record_post(trend_id: str, platform: str, post_id: str | None, dry_run: bool) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO posted_trends (trend_id, platform, post_id, dry_run, posted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trend_id, platform, post_id, int(dry_run), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
