"""Merge a run's posting state INTO a gaming-state checkout - never overwrite it.

Every workflow (and every laptop batch) saves its state with this, so two writers can't wipe each other's
records: a plain copy + push either loses the other writer's posts or gets rejected and loses its own.

- posted_trends: rows the target lacks are added; a target row that failed (post_id NULL) or was a dry run is
  replaced by a real post from the source.
- game_videos.json / palworld_videos.json: keys the target lacks are added.

    python scripts/merge_state.py <source dir> <gaming-state checkout>
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

MANIFESTS = ("game_videos.json", "palworld_videos.json")
DB = "pipeline_store.sqlite3"


def merge(src: Path, dst: Path) -> None:
    for name in MANIFESTS:
        if not (src / name).exists():
            continue
        theirs = json.loads((src / name).read_text(encoding="utf-8"))
        ours = json.loads((dst / name).read_text(encoding="utf-8")) if (dst / name).exists() else {}
        added = [k for k in theirs if k not in ours]
        ours.update({k: theirs[k] for k in added})
        (dst / name).write_text(json.dumps(ours, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: +{len(added)}")
    if not (src / DB).exists():
        return
    con = sqlite3.connect(dst / DB)
    con.execute("""CREATE TABLE IF NOT EXISTS posted_trends (
        trend_id TEXT NOT NULL, platform TEXT NOT NULL, post_id TEXT, dry_run INTEGER NOT NULL,
        posted_at TEXT NOT NULL, PRIMARY KEY (trend_id, platform))""")
    con.execute("ATTACH DATABASE ? AS src", (str(src / DB),))
    before = con.total_changes
    con.execute("""INSERT INTO posted_trends (trend_id, platform, post_id, dry_run, posted_at)
                   SELECT s.trend_id, s.platform, s.post_id, s.dry_run, s.posted_at FROM src.posted_trends s
                   WHERE NOT EXISTS (SELECT 1 FROM posted_trends t WHERE t.trend_id = s.trend_id AND t.platform = s.platform)""")
    con.execute("""INSERT OR REPLACE INTO posted_trends (trend_id, platform, post_id, dry_run, posted_at)
                   SELECT s.trend_id, s.platform, s.post_id, s.dry_run, s.posted_at FROM src.posted_trends s
                   JOIN posted_trends t ON t.trend_id = s.trend_id AND t.platform = s.platform
                   WHERE (t.post_id IS NULL OR t.dry_run = 1) AND s.post_id IS NOT NULL AND s.dry_run = 0""")
    con.commit()
    print(f"posted_trends: {con.total_changes - before} rows changed")
    con.execute("DETACH DATABASE src")
    con.close()


if __name__ == "__main__":
    merge(Path(sys.argv[1]), Path(sys.argv[2]))
