"""Keep every upload in its category playlist on both YouTube channels (owner request 2026-11-08).

    python scripts/youtube_playlists.py                  # both channels, up to --max-adds new playlist items each
    python scripts/youtube_playlists.py --dry-run        # only print what would be created / added
    python scripts/youtube_playlists.py --channel youtube --max-adds 400

How a video's category is found: the posting store (trend id -> YouTube video id, plus game_videos.json for the spec's
slot). Videos with no record are the owner's own uploads and streams - never touched (owner rule).
The early RDR2 clips were posted from the laptop and are only in its local store: pass --extra-store for those.
Missing playlists are created (public). Each new item goes to the top, so the newest video is first.
Every workflow runs this after its uploads, so a new video lands in its playlist in the same run.

Needs the full `youtube` OAuth scope - tokens made before 2026-11-08 only had upload + readonly; then this logs a
warning and does nothing (run scripts/youtube_login.py <channel> --force once, then update the GitHub secret).
Quota: the Google project has ~10,000 units a day (resets 13:30 IST) shared with every upload. Listing is ~1 unit per
50 videos, each added item 50 units, so a run adds only --max-adds 8 (newest first); 2026-11-08 a 160-item backfill
used up the whole day and blocked the uploads. A quotaExceeded stops the run quietly.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline.orchestrator import _POSTERS  # noqa: E402

log = logging.getLogger("playlists")
ROOT = Path(__file__).resolve().parent.parent
FULL_SCOPE = "https://www.googleapis.com/auth/youtube"

# key -> (title, description) per channel
PLAYLISTS = {
    "youtube": {
        "fusion": ("Pokemon Fusions", "Two Pokemon fuse into one brand new Pokemon - fan-made fusion animations."),
        "pal_fusion": ("Palworld Fusions", "Two Pals fuse into one brand new Pal - fan-made fusion animations."),
        "evolution": ("Pokemon Evolutions", "Pokemon evolution lines animated from start to final form, Mega Evolutions included."),
        "rdr2_shorts": ("Red Dead Redemption 2 - Best Moments (Shorts)", "Short clips from my own Red Dead Redemption 2 story playthrough."),
        "rdr2_long": ("Red Dead Redemption 2 - Story Highlights", "Longer highlight videos from my own Red Dead Redemption 2 playthrough."),
        "pal_breeding": ("Palworld Breeding Combos", "Verified Palworld 1.0 breeding combinations for every Pal."),
        "palworld": ("Palworld Guides and Tips", "Palworld 1.0 guides: how to get Pals, base tips, work suitability and more."),
        "aniimo": ("Aniimo", "Everything about Aniimo - creatures, guides and news."),
        "deals": ("Free Games and Game Deals", "Free games on Epic and Steam plus the best game deals, checked live every day."),
        "dml": ("Dragon Mania Legends Breeding", "Dragon Mania Legends breeding guides - which elements the parents need."),
        "classic": ("Classic Games", "Facts and stories about classic video games."),
        "new": ("New Games and Gaming News", "New and upcoming games and the latest gaming news."),
        "gaming_long": ("Gaming History and Explained", "Long videos: franchise histories, explainers and top lists."),
    },
    "youtube_movies": {
        "movie_new": ("नई फिल्में | New Movies", "नई और आने वाली फिल्मों की जानकारी।"),
        "movie_review": ("मूवी रिव्यू | Movie Reviews", "फिल्मों के रिव्यू और बॉक्स ऑफिस।"),
        "comedy": ("कॉमेडी फिल्में | Comedy Movies", "कॉमेडी फिल्मों के मज़ेदार फैक्ट्स।"),
        "movie_news": ("फिल्मी खबरें | Movie News", "फिल्म जगत की ताज़ा खबरें।"),
        "tribute": ("श्रद्धांजलि | Tributes", "सिनेमा के दिग्गजों को श्रद्धांजलि।"),
        "safar": ("सितारों का सफ़र | Star Journeys", "सितारों और निर्देशकों के सफ़र की लंबी वीडियो।"),
        "movies_long": ("सिनेमा एक्सप्लेन्ड | Cinema Explained", "फिल्मों, यूनिवर्स और बॉक्स ऑफिस पर लंबी वीडियो।"),
    },
}

_GAME_SLOT = {"new": "new", "gamenews": "new", "gow": "new", "classic": "classic", "aniimo": "aniimo",
              "palguide": "palworld", "palbase": "palworld", "palchain": "palworld", "dml": "dml"}
_MOVIE_SLOT = {"movie_new": "movie_new", "movie_review": "movie_review", "comedy": "comedy",
               "movie_news": "movie_news", "tribute": "tribute"}


def _slot(games: dict, gid: str) -> str:
    """The spec slot of a game/movie id from game_videos.json ("classic_01_x.json" -> "classic")."""
    spec = Path((games.get(gid) or {}).get("spec", "")).name
    m = re.match(r"(movie_new|movie_review|movie_news|[a-z]+)_\d+_", spec)
    return m.group(1) if m else ""


def classify(channel: str, trend_id: str | None, title: str, games: dict) -> str | None:
    t = trend_id or ""
    if channel == "youtube_movies":
        if t.startswith("custom:long-"):
            return "safar" if t.endswith("-safar") else "movies_long"
        if t.startswith("custom:movie-"):
            return _MOVIE_SLOT.get(_slot(games, t[len("custom:movie-"):]), "movie_new")
        return None
    if t.startswith("custom:palfusion-"):
        return "pal_fusion"
    if t.startswith("custom:fusion-") or t.startswith("custom:motion-fusion"):
        return "fusion"
    if t.startswith("custom:motion-poke-"):
        return "evolution"
    if t.startswith("gameplay:rdr2"):
        return "rdr2_long" if ":long" in t else "rdr2_shorts"
    if t.startswith("custom:palworld-breeding-"):
        return "pal_breeding"
    if t.startswith("custom:long-"):
        rest = t[len("custom:long-"):]
        for prefix, key in (("palworld", "palworld"), ("aniimo", "aniimo"), ("deals", "deals"), ("dml", "dml")):
            if rest.startswith(prefix):
                return key
        return "gaming_long"
    if t.startswith("custom:game-"):
        gid = t[len("custom:game-"):]
        if gid.startswith("deal-"):
            return "deals"
        if gid.startswith("palworld-"):
            return "palworld"
        return _GAME_SLOT.get(_slot(games, gid), "new")
    if t.startswith("custom:gta6"):
        return "new"
    # no record = not uploaded by this pipeline (the owner's own videos and streams): owner 2026-11-08 - leave them
    # alone. The early RDR2 clips are only in the laptop's store (--extra-store), not in gaming-state.
    return None


def _paged(call, **kw):
    token = None
    while True:
        res = call(**kw, maxResults=50, **({"pageToken": token} if token else {})).execute()
        yield from res.get("items", [])
        token = res.get("nextPageToken")
        if not token:
            return


def _call(request, tries: int = 6):
    """execute() with a pause between writes: YouTube answers a quick run of inserts with 429 RATE_LIMIT_EXCEEDED
    (11 playlists in 10 s did it) - that is a speed limit, not the daily quota, so wait and try again."""
    import time

    from googleapiclient.errors import HttpError

    for attempt in range(tries):
        try:
            res = request.execute()
            time.sleep(1.0)
            return res
        except HttpError as exc:
            if exc.resp.status == 429 or "RATE_LIMIT_EXCEEDED" in str(exc) or "rateLimitExceeded" in str(exc):
                if attempt < tries - 1:
                    time.sleep(10 * (attempt + 1))
                    continue
            raise


def sync(channel: str, max_adds: int, dry_run: bool, state: Path | None = None, extra_stores: list[Path] = ()) -> int:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    poster = _POSTERS[channel]
    if not poster._token_path().exists():
        log.info("%s: no token - skipped", channel)
        return 0
    creds = poster._get_credentials()
    if FULL_SCOPE not in (creds.scopes or []) and not dry_run:  # a dry run only reads
        log.warning("%s: token has no playlist permission - run scripts/youtube_login.py %s --force once "
                    "(and update the GitHub secret); playlists skipped", channel, channel)
        return 0
    yt = build("youtube", "v3", credentials=creds)
    media = state or ROOT / settings.media_output_dir
    games_file = media / "game_videos.json"
    games = json.loads(games_file.read_text(encoding="utf-8")) if games_file.exists() else {}
    by_video = {}
    for path in [media / "pipeline_store.sqlite3", *extra_stores]:  # the laptop's own store has the early RDR2 clips
        db = sqlite3.connect(path)
        by_video.update({pid: tid for tid, pid in db.execute(
            "SELECT trend_id, post_id FROM posted_trends WHERE platform = ? AND dry_run = 0 AND post_id IS NOT NULL "
            "AND post_id NOT LIKE 'skip:%'", (channel,))})
        db.close()

    uploads_id = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    uploads = [(it["contentDetails"]["videoId"], it["snippet"]["title"], it["contentDetails"].get("videoPublishedAt", ""))
               for it in _paged(yt.playlistItems().list, part="snippet,contentDetails", playlistId=uploads_id)]
    uploads.sort(key=lambda u: u[2])  # oldest first; each one goes to position 0 -> newest ends on top

    wanted = {vid: classify(channel, by_video.get(vid), title, games) for vid, title, _ in uploads}

    titles = {key: spec[0] for key, spec in PLAYLISTS[channel].items()}
    existing = {p["snippet"]["title"]: p["id"] for p in _paged(yt.playlists().list, part="snippet", mine=True)}
    ids, members = {}, set()
    for key, (title, desc) in PLAYLISTS[channel].items():
        if key not in wanted.values():
            continue  # no video for it yet - create it once there is one
        if title in existing:
            ids[key] = existing[title]
            members |= {it["contentDetails"]["videoId"] for it in
                        _paged(yt.playlistItems().list, part="contentDetails", playlistId=existing[title])}
        elif dry_run:
            log.info("%s: would create playlist %r", channel, title)
            ids[key] = None
        else:
            try:
                ids[key] = _call(yt.playlists().insert(part="snippet,status", body={
                    "snippet": {"title": title, "description": desc, "defaultLanguage": "hi" if channel == "youtube_movies" else "en"},
                    "status": {"privacyStatus": "public"}}), tries=2)["id"]
                log.info("%s: created playlist %r", channel, title)
            except Exception as exc:  # YouTube caps new playlists per channel per day (the 12th on 2026-11-08 got 429)
                log.warning("%s: could not create %r yet (%s) - next run", channel, title, str(exc)[:120])

    todo = [(vid, key) for vid, _t, _p in uploads if (key := wanted.get(vid)) in ids and vid not in members]
    log.info("%s: %d uploads, %d already in playlists, %d to add (max %d this run)",
             channel, len(uploads), len(members), len(todo), max_adds)
    added = 0
    for vid, key in todo[-max_adds:] if max_adds else []:  # the newest ones first when capped
        if dry_run:
            log.info("  would add %s -> %s", vid, titles[key])
            added += 1
            continue
        try:
            _call(yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": ids[key], "position": 0, "resourceId": {"kind": "youtube#video", "videoId": vid}}}))
        except HttpError as exc:
            if "quotaExceeded" in str(exc):
                log.warning("%s: API quota used up after %d adds - the rest go in next run", channel, added)
                break
            log.warning("  %s -> %s failed: %s", vid, titles[key], str(exc)[:200])
            continue
        added += 1
    log.info("%s: added %d videos to playlists", channel, added)
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=["youtube", "youtube_movies"], action="append")
    parser.add_argument("--max-adds", type=int, default=8,  # the project quota is ~10k units/day and uploads need most
                        help="playlist items to add per channel this run (50 units each, newest first)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--extra-store", type=Path, action="append", default=[], help="another pipeline_store.sqlite3 to read")
    parser.add_argument("--state", type=Path, help="folder with pipeline_store.sqlite3 + game_videos.json (default media_output)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("googleapiclient.discovery_cache", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for channel in args.channel or ["youtube", "youtube_movies"]:
        try:
            sync(channel, args.max_adds, args.dry_run, args.state, args.extra_store)
        except Exception:  # playlists are a nice-to-have: never fail the posting workflow over them
            log.exception("%s: playlist sync failed", channel)


if __name__ == "__main__":
    main()
