from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
