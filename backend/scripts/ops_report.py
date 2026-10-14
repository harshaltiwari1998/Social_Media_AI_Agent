"""Operations checks for the posting pipeline, run on GitHub Actions.

  python scripts/ops_report.py tokens   # daily: YouTube logins + Meta token still valid?
  python scripts/ops_report.py weekly   # weekly: posts, failures, queue stock, tokens, video stats

Both print Markdown (also appended to $GITHUB_STEP_SUMMARY). `tokens` exits 1 when a login
is broken or the Meta data access expires within META_WARN_DAYS, so the run goes red and
GitHub emails. `weekly` writes the report to --out for the workflow to post as an issue.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("YOUTUBE_NONINTERACTIVE", "1")

from app.config import settings  # noqa: E402
from app.pipeline.orchestrator import _POSTERS  # noqa: E402

GRAPH = "https://graph.facebook.com/v19.0"
STATE = ROOT / settings.media_output_dir
SPECS = ROOT / "scripts" / "specs"
META_WARN_DAYS = 14
# Slots the daily workflows draw from, and how many each uses per day.
DAILY_SLOTS = {"games/new": 1, "games/classic": 1, "movies/movie_new": 1, "movies/movie_review": 1, "movies/reel": 1}


# ---------------------------------------------------------------- tokens

def _youtube_status(name: str) -> tuple[bool, str]:
    from googleapiclient.discovery import build

    try:
        youtube = build("youtube", "v3", credentials=_POSTERS[name]._get_credentials(), cache_discovery=False)
        items = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
        return bool(items), (items[0]["snippet"]["title"] if items else "no channel on this login")
    except Exception as exc:  # expired/revoked refresh token, missing secret, API error
        return False, f"{type(exc).__name__}: {exc}"[:200]


def _meta_status() -> tuple[bool, str, int | None]:
    """(ok, message, days until data access expires)."""
    if not settings.meta_access_token:
        return False, "META_ACCESS_TOKEN not set", None
    try:
        data = httpx.get(
            f"{GRAPH}/debug_token",
            params={"input_token": settings.meta_access_token, "access_token": settings.meta_access_token},
            timeout=30,
        ).json().get("data", {})
    except httpx.HTTPError as exc:
        return False, f"debug_token request failed: {type(exc).__name__}", None
    if not data.get("is_valid"):
        return False, f"token invalid: {data.get('error', {}).get('message', 'unknown')}", None
    expires = data.get("data_access_expires_at") or 0
    days = (datetime.fromtimestamp(expires, timezone.utc).date() - date.today()).days if expires else None
    when = datetime.fromtimestamp(expires, timezone.utc).date().isoformat() if expires else "never"
    return True, f"valid {data.get('type', '').lower()} token, data access until {when}", days


def token_rows() -> tuple[list[str], bool]:
    rows, ok = [], True
    for name, label in (("youtube", "YouTube gaming (HarryBeast)"), ("youtube_movies", "YouTube movies (Name Writer)")):
        good, msg = _youtube_status(name)
        ok &= good
        fix = "" if good else f" - run `python scripts/youtube_login.py {name}` and update the secret"
        rows.append(f"| {label} | {'✅' if good else '❌'} | {msg}{fix} |")
    good, msg, days = _meta_status()
    warn = days is not None and days <= META_WARN_DAYS
    ok &= good and not warn
    if days is not None:
        msg += f" ({days} days left)"
    if warn:
        msg += " - log in to the Meta app again and update META_ACCESS_TOKEN"
    rows.append(f"| Meta (Facebook + Instagram) | {'❌' if not good else '⚠️' if warn else '✅'} | {msg} |")
    return ["| Login | Status | Details |", "|---|---|---|", *rows], ok


# ---------------------------------------------------------------- weekly

def _rows(since: datetime) -> list[tuple]:
    db = STATE / "pipeline_store.sqlite3"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return [r for r in con.execute("SELECT trend_id, platform, post_id, dry_run, posted_at FROM posted_trends")
                if datetime.fromisoformat(r[4]) >= since and not r[3]]
    finally:
        con.close()


def _queue_stock() -> list[str]:
    manifest = {}
    for name in ("game_videos.json",):
        path = STATE / name
        if path.exists():
            manifest.update(json.loads(path.read_text(encoding="utf-8")))
    today = date.today().isoformat()
    lines = ["| Queue | Unposted | Days left |", "|---|---|---|"]
    for slot, per_day in DAILY_SLOTS.items():
        folder, prefix = slot.split("/")
        left = 0
        for path in (SPECS / folder).glob(f"{prefix}_*.json"):
            spec = json.loads(path.read_text(encoding="utf-8"))
            if spec["id"] not in manifest and spec.get("expires", "9999-12-31") >= today:
                left += 1
        lines.append(f"| {prefix} | {left} | {'⚠️ ' if left < 4 else ''}{left // per_day} |")
    pal = STATE / "palworld_videos.json"
    posted = json.loads(pal.read_text(encoding="utf-8")) if pal.exists() else {}
    queue = json.loads((SPECS / "palworld_queue.json").read_text(encoding="utf-8"))
    left = sum(1 for c in queue if c["child"] not in posted)
    lines.append(f"| palworld breeding | {left} | {'⚠️ ' if left < 4 else ''}{left} |")
    for category in ("gaming", "movies"):
        left = 0
        for path in (SPECS / "long").glob(f"{category}_*.json"):
            spec = json.loads(path.read_text(encoding="utf-8"))
            if spec["id"] not in manifest and spec.get("expires", "9999-12-31") >= today:
                left += 1
        lines.append(f"| long {category} | {left} | {'⚠️ ' if left < 4 else ''}{left} |")
    return lines


def _youtube_stats(name: str, ids: list[str]) -> list[dict]:
    from googleapiclient.discovery import build

    if not ids:
        return []
    youtube = build("youtube", "v3", credentials=_POSTERS[name]._get_credentials(), cache_discovery=False)
    out = []
    for i in range(0, len(ids), 50):
        resp = youtube.videos().list(part="snippet,statistics", id=",".join(ids[i:i + 50])).execute()
        for item in resp.get("items", []):
            st = item.get("statistics", {})
            out.append({"title": item["snippet"]["title"], "url": f"https://youtu.be/{item['id']}",
                        "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                        "comments": int(st.get("commentCount", 0))})
    return out


def _meta_stats(platform: str, ids: list[str]) -> list[dict]:
    """Facebook: video views. Instagram: likes/comments (reach needs instagram_manage_insights)."""
    fields = "views,description,permalink_url" if platform == "facebook" else "like_count,comments_count,caption,permalink"
    out = []
    for post_id in ids:
        try:
            d = httpx.get(f"{GRAPH}/{post_id}", params={"fields": fields, "access_token": settings.meta_access_token}, timeout=30).json()
        except httpx.HTTPError:
            continue
        if "error" in d:
            continue
        text = (d.get("description") or d.get("caption") or "").split("\n")[0][:70]
        url = d.get("permalink_url") or d.get("permalink") or ""
        if url.startswith("/"):
            url = "https://www.facebook.com" + url
        out.append({"title": text, "url": url,
                    "views": int(d.get("views", 0)), "likes": int(d.get("like_count", 0)),
                    "comments": int(d.get("comments_count", 0))})
    return out


def _top_table(items: list[dict], key: str, n: int = 5) -> list[str]:
    if not items:
        return ["_No videos in this period._"]
    total = sum(i[key] for i in items)
    cols = [key] + [c for c in ("likes", "comments") if c != key]
    lines = [f"Total {key}: **{total:,}** across {len(items)} videos", "",
             "| Video | " + " | ".join(c.title() for c in cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for item in sorted(items, key=lambda i: i[key], reverse=True)[:n]:
        title = item["title"].replace("|", "/") or "(untitled)"
        lines.append(f"| [{title}]({item['url']}) | " + " | ".join(f"{item[c]:,}" for c in cols) + " |")
    return lines


def weekly() -> str:
    now = datetime.now(timezone.utc)
    week = _rows(now - timedelta(days=7))
    month = _rows(now - timedelta(days=30))
    ok = Counter(r[1] for r in week if r[2])
    failed = sorted({(r[0], r[1]) for r in week if not r[2]})
    md = [f"# Weekly report - {date.today().isoformat()}", "", "## Posted in the last 7 days", "",
          "| Platform | Posts |", "|---|---|"]
    md += [f"| {p} | {n} |" for p, n in sorted(ok.items())] or ["| (none) | 0 |"]
    md += ["", f"Uploads still failing (not retried successfully): **{len(failed)}**"]
    md += [f"- `{t}` on {p}" for t, p in failed[:15]]
    md += ["", "## Content queues", "", *_queue_stock(), "",
           "⚠️ = under 4 left. The Mon/Thu refill routine tops these up; ask Claude for fresh research if a queue stays low.",
           "", "## Logins", ""]
    rows, _ = token_rows()
    md += rows
    md += ["", "## Stats - videos posted in the last 30 days", ""]
    ids = {p: [r[2] for r in month if r[1] == p and r[2]] for p in ("youtube", "youtube_movies", "facebook", "instagram")}
    for platform, label, key in (("youtube", "YouTube - HarryBeast Gaming", "views"), ("youtube_movies", "YouTube - Name Writer", "views"),
                                 ("facebook", "Facebook", "views"), ("instagram", "Instagram (likes only)", "likes")):
        md += [f"### {label}", ""]
        try:
            stats = _youtube_stats(platform, ids[platform]) if platform.startswith("youtube") else _meta_stats(platform, ids[platform])
            md += _top_table(stats, key)
        except Exception as exc:
            md += [f"_Could not load stats: {type(exc).__name__}_"]
        md.append("")
    return "\n".join(md)


def _summary(text: str) -> None:
    print(text)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=("tokens", "weekly"))
    parser.add_argument("--out", help="also write the weekly report to this file")
    args = parser.parse_args()
    if args.check == "tokens":
        rows, ok = token_rows()
        _summary("\n".join(["## Login check", "", *rows]))
        sys.exit(0 if ok else 1)
    report = weekly()
    _summary(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
