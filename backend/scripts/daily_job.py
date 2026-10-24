"""Daily automation, run by Windows Task Scheduler (see scripts/daily_job.cmd).

1. Retry: every gaming video in media_output/{palworld,game}_videos.json is posted to the
   gaming platforms it hasn't reached yet (e.g. after YouTube's daily upload limit).
2. New videos, per day by default: one new/upcoming game, one classic game
   (scripts/specs/games/), one Palworld breeding combo (scripts/specs/palworld_queue.json)
   -> gaming channel; one new/upcoming movie and one review/box-office video
   (scripts/specs/movies/) -> movies channel.
3. Trends: the regular trend -> LLM -> media -> post pipeline (gaming/non-gaming routing).

Already-posted (trend, platform) pairs are skipped via the pipeline store, so re-running
the job is always safe. Logs go to media_output/logs/daily-YYYY-MM-DD.log.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("YOUTUBE_NONINTERACTIVE", "1")

from app.config import settings  # noqa: E402
from app.pipeline import store  # noqa: E402
from app.pipeline.orchestrator import _POSTERS, platforms_for, run_pipeline  # noqa: E402
from scripts import game_videos as game  # noqa: E402
from scripts import long_video as longv  # noqa: E402
from scripts import palworld_breeding_videos as pal  # noqa: E402

MANIFEST = ROOT / settings.media_output_dir / "palworld_videos.json"
GAME_MANIFEST = ROOT / settings.media_output_dir / "game_videos.json"
QUEUE = ROOT / "scripts" / "specs" / "palworld_queue.json"
GAME_SPECS = ROOT / "scripts" / "specs" / "games"
MOVIE_SPECS = ROOT / "scripts" / "specs" / "movies"
LONG_SPECS = ROOT / "scripts" / "specs" / "long"
OLLAMA_EXE = Path(r"F:\AI AGENT\Ollama\ollama.exe")
OLLAMA_MODELS = ROOT / ".cache" / "ollama-models"

log = logging.getLogger("daily_job")
# --from-end: take the LAST queued specs instead of the first, so a one-off manual batch
# never picks the same specs as a scheduled run that may be running at the same time.
FROM_END = False
# What this run actually produced. A run where every build (or every upload) failed exits
# non-zero so GitHub marks it red and emails - edge-tts 403s once made 0 videos for 3 days
# while every run still showed "success".
HEALTH = {"built": 0, "build_failed": 0, "posted": 0, "post_failed": 0}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Poster:
    """Posts gaming/movie videos; stops using a YouTube channel for the run once its daily upload limit is hit."""

    def __init__(self) -> None:
        self.blocked: set[str] = set()

    @staticmethod
    def targets(category: str, platforms: list[str] | None) -> list[str]:
        """A spec's own "platforms" list (e.g. Facebook + Instagram reels only) beats category routing."""
        return list(platforms) if platforms else platforms_for({"category": category})

    def pending(self, trend_id: str, category: str = "gaming", platforms: list[str] | None = None) -> bool:
        return not all(store.already_posted(trend_id, name) for name in self.targets(category, platforms))

    def post(
        self, trend_id: str, text: str, tags: list[str], video_path: str,
        category: str = "gaming", platforms: list[str] | None = None,
    ) -> None:
        for name in self.targets(category, platforms):
            if store.already_posted(trend_id, name):
                continue
            if name in self.blocked:
                log.info("  %s: skipped (upload limit reached today, retry tomorrow)", name)
                continue
            result = _POSTERS[name].post_video(video_path, text, tags=tags)
            store.record_post(trend_id, name, result.post_id, result.dry_run)
            _record_post_result(name, result)
            if not result.success and name.startswith("youtube") and "uploadLimitExceeded" in (result.error or ""):
                self.blocked.add(name)


def _record_post_result(name: str, result) -> None:
    if result.success:
        HEALTH["posted"] += 1
        log.info("  %s: posted %s%s", name, result.post_id, " (dry run)" if result.dry_run else "")
        return
    log.warning("  %s: FAILED %s", name, result.error)
    if not (name.startswith("youtube") and "uploadLimitExceeded" in (result.error or "")):
        HEALTH["post_failed"] += 1  # a daily upload limit isn't a broken pipeline


def _record_build(ok: bool) -> None:
    HEALTH["built" if ok else "build_failed"] += 1


def _build(builder, spec_path: Path) -> str | None:
    try:
        video = builder(spec_path)["video"]
    except Exception:
        log.exception("  build failed")
        _record_build(False)
        return None
    _record_build(video["success"])
    if not video["success"]:
        log.warning("  video failed: %s", video["error"])
        return None
    return video["data"]["path"]


def retry_pending(poster: Poster) -> None:
    log.info("== Retry pending uploads")
    # Palworld: spec content is kept in the manifest because specs made on GitHub Actions
    # aren't committed - only the state branch survives between runs.
    manifest = _load(MANIFEST)
    for child, entry in manifest.items():
        spec_path = ROOT / entry["spec"]
        spec = entry.get("data") or json.loads(spec_path.read_text(encoding="utf-8"))
        if not poster.pending(pal.post_id_for(spec)):
            continue
        log.info("%s", child)
        if not Path(entry["video"]).exists():
            # Videos aren't kept between GitHub Actions runs - rebuild from the spec.
            spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if not (video := _build(pal.build_video, spec_path)):
                continue
            entry["video"] = video
            _save(MANIFEST, manifest)
        poster.post(pal.post_id_for(spec), *pal.post_text_and_tags(spec), entry["video"])

    games = _load(GAME_MANIFEST)  # shorts built by game_videos.py, long videos by long_video.py
    for game_id, entry in games.items():
        if entry.get("kind") == "long":
            spec = json.loads((ROOT / entry["spec"]).read_text(encoding="utf-8"))
            if not poster.pending(longv.post_id_for(spec), platforms=longv.default_platforms(spec)):
                continue
            log.info("%s (long)", game_id)
            if not Path(entry["video"]).exists():
                result = longv.build_video(ROOT / entry["spec"])["video"]
                _record_build(result["success"])
                if not result["success"]:
                    log.warning("  rebuild failed: %s", result["error"])
                    continue
                entry.update(video=result["data"]["path"], thumbnail=result["data"]["thumbnail"], chapters=result["data"]["chapters"])
                _save(GAME_MANIFEST, games)
            post_long(poster, spec, entry)
            continue
        spec_path = ROOT / entry["spec"]
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        category = game.category_of(spec)
        if not poster.pending(game.post_id_for(spec), category, spec.get("platforms")):
            continue
        log.info("%s", game_id)
        if not Path(entry["video"]).exists():
            if not (video := _build(game.build_video, spec_path)):
                continue
            entry["video"] = video
            _save(GAME_MANIFEST, games)
        poster.post(game.post_id_for(spec), *game.post_text_and_tags(spec), entry["video"], category, spec.get("platforms"))


def make_new_palworld(poster: Poster, count: int) -> None:
    log.info("== New Palworld videos (up to %d)", count)
    if count <= 0:
        return
    manifest = _load(MANIFEST)
    todo = [c for c in json.loads(QUEUE.read_text(encoding="utf-8")) if c["child"] not in manifest]
    todo = (todo[::-1] if FROM_END else todo)[:count]
    if not todo:
        log.warning("Palworld queue is empty - add verified unique combos to %s", QUEUE.name)
        return
    for combo in todo:
        a, b = combo["parents"]
        spec = pal.spec_from_combo(
            a, b, combo["child"], index=len(manifest), unique=combo.get("unique", True), combos=combo.get("combos")
        )
        spec_path = ROOT / "scripts" / "specs" / f"palworld_{combo['child'].lower().replace(' ', '_')}.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("%s = %s", combo["child"], " | ".join(" + ".join(c) for c in combo.get("combos") or [[a, b]]))
        if not (video := _build(pal.build_video, spec_path)):
            continue
        manifest[combo["child"]] = {"spec": spec_path.relative_to(ROOT).as_posix(), "video": video, "data": spec}
        _save(MANIFEST, manifest)
        poster.post(pal.post_id_for(spec), *pal.post_text_and_tags(spec), video)


def make_new_games(poster: Poster, slot: str, count: int, spec_dir: Path = GAME_SPECS) -> None:
    """Next `count` unposted specs of a slot ("new" / "classic" games, "movie_new" / "movie_review"),
    in order, skipping expired news."""
    log.info("== New %s videos (up to %d)", slot, count)
    if count <= 0:
        return
    games = _load(GAME_MANIFEST)
    today = date.today().isoformat()
    specs = []
    for path in sorted(spec_dir.glob(f"{slot}_*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec["id"] in games or spec.get("expires", "9999-12-31") < today:
            continue
        specs.append((spec["order"], path, spec))
    if not specs:
        log.warning("No %s specs left - add more to %s", slot, spec_dir.relative_to(ROOT))
        return
    for _, path, spec in sorted(specs, key=lambda t: t[0], reverse=FROM_END)[:count]:
        log.info("%s", spec["id"])
        if not (video := _build(game.build_video, path)):
            continue
        games[spec["id"]] = {"spec": path.relative_to(ROOT).as_posix(), "video": video}
        _save(GAME_MANIFEST, games)
        poster.post(
            game.post_id_for(spec), *game.post_text_and_tags(spec), video, game.category_of(spec), spec.get("platforms")
        )


def post_long(poster: Poster, spec: dict, entry: dict) -> None:
    """Upload a long video: YouTube channels get title/description/chapters/thumbnail, Facebook the file."""
    trend_id = longv.post_id_for(spec)
    description = longv.description_for(spec, entry.get("chapters", ""))
    for name in longv.default_platforms(spec):
        if store.already_posted(trend_id, name):
            continue
        if name in poster.blocked:
            log.info("  %s: skipped (upload limit reached today, retry tomorrow)", name)
            continue
        target = _POSTERS[name]
        if name.startswith("youtube"):
            result = target.post_long_video(entry["video"], spec["title"], description, spec.get("tags"), entry.get("thumbnail"))
        else:
            result = target.post_video(entry["video"], f"{spec['title']}\n\n{description}", tags=spec.get("tags"))
        store.record_post(trend_id, name, result.post_id, result.dry_run)
        _record_post_result(name, result)
        if not result.success and name.startswith("youtube") and "uploadLimitExceeded" in (result.error or ""):
            poster.blocked.add(name)


def make_long_videos(poster: Poster, per_category: int, categories: tuple[str, ...] = ("gaming", "movies"),
                     series: str | None = None) -> None:
    """Next unposted long spec(s) per category (gaming -> HarryBeast, movies -> Name Writer + Facebook).

    Specs with a "series" (e.g. "palworld", "aniimo") belong to their own daily workflow: they are only picked
    when that series is asked for, and a series run picks nothing else."""
    log.info("== Long videos (up to %d per category)", per_category)
    if per_category <= 0:
        return
    games = _load(GAME_MANIFEST)
    today = date.today().isoformat()
    for category in categories:
        todo = []
        for path in sorted(LONG_SPECS.glob("*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            if spec.get("category", "gaming") != category or spec["id"] in games or spec.get("expires", "9999-12-31") < today:
                continue
            if spec.get("series") != series:
                continue
            todo.append((spec.get("order", 0), path, spec))
        if not todo:
            log.warning("No %s long-video specs left - add more to %s", category, LONG_SPECS.relative_to(ROOT))
            continue
        # Full-length scripts first; too-short ones only when nothing else is left.
        todo.sort(key=lambda t: (longv.is_too_short(t[2]), -t[0] if FROM_END else t[0]))
        for _, path, spec in todo[:per_category]:
            log.info("%s (long, %s)", spec["id"], category)
            result = longv.build_video(path)["video"]
            _record_build(result["success"])
            if not result["success"]:
                log.warning("  build failed: %s", result["error"])
                continue
            entry = {"spec": path.relative_to(ROOT).as_posix(), "kind": "long", "video": result["data"]["path"],
                     "thumbnail": result["data"]["thumbnail"], "chapters": result["data"]["chapters"]}
            games[spec["id"]] = entry
            _save(GAME_MANIFEST, games)
            log.info("  rendered %.0f s", result["data"]["duration_seconds"])
            post_long(poster, spec, entry)


def _ensure_ollama() -> subprocess.Popen | None:
    """Start a local Ollama server (models on F:) if none is running. Returns it so we can stop it."""
    try:
        httpx.get(settings.ollama_host, timeout=3)
        return None
    except httpx.HTTPError:
        pass
    env = {**os.environ, "OLLAMA_MODELS": str(OLLAMA_MODELS)}
    proc = subprocess.Popen([str(OLLAMA_EXE), "serve"], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(30):
        time.sleep(1)
        try:
            httpx.get(settings.ollama_host, timeout=3)
            return proc
        except httpx.HTTPError:
            continue
    log.warning("Ollama did not start within 30s")
    return proc


def run_trends() -> None:
    log.info("== Trend pipeline")
    ollama = _ensure_ollama()
    try:
        result = run_pipeline()
    except Exception:
        log.exception("trend pipeline crashed")
        return
    finally:
        if ollama:
            ollama.terminate()
    if not result.get("success"):
        log.warning("trend pipeline failed: %s", result.get("error"))
        return
    for idea in result["ideas"]:
        log.info("%s [%s]", idea["headline"], idea.get("category"))
        for post in idea["posts"]:
            status = post.get("skipped") or (post.get("post_id") if post.get("success") else f"FAILED {post.get('error')}")
            log.info("  %s: %s", post["platform"], status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily posting job.")
    parser.add_argument("--new-games", type=int, default=1, help="new/upcoming-game videos per day")
    parser.add_argument("--classic-games", type=int, default=1, help="classic-game videos per day")
    parser.add_argument("--new-palworld", type=int, default=1, help="new Palworld videos per day")
    parser.add_argument("--new-movies", type=int, default=1, help="new/upcoming-movie videos per day")
    parser.add_argument("--movie-reviews", type=int, default=1, help="movie review / box-office videos per day")
    parser.add_argument("--long-videos", type=int, default=0, help="long-form videos per category (gaming, movies)")
    parser.add_argument(
        "--long-category", action="append", choices=("gaming", "movies"),
        help="only make long videos for this category (repeatable; default: both)",
    )
    parser.add_argument(
        "--extra-slot", action="append", default=[], metavar="SLOT:COUNT",
        help="also post a one-off spec slot from specs/games or specs/movies, e.g. gta6:10 or reel:10",
    )
    parser.add_argument("--long-series", help="only long specs with this \"series\" (e.g. palworld, aniimo)")
    parser.add_argument("--skip-trends", action="store_true")
    parser.add_argument("--skip-gaming", action="store_true", help="gaming + movie videos run on GitHub Actions instead")
    parser.add_argument("--from-end", action="store_true", help="pick specs from the end of each queue (manual batches)")
    args = parser.parse_args()
    global FROM_END
    FROM_END = args.from_end

    log_dir = ROOT / settings.media_output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / f"daily-{date.today()}.log", encoding="utf-8"), logging.StreamHandler()],
    )
    # httpx logs every request URL at INFO, and Graph API calls carry access_token in the
    # query string - that leaked the Meta token into logs and CI artifacts.
    for noisy in ("httpx", "httpcore", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info("Daily job start (DRY_RUN=%s)", settings.dry_run)

    poster = Poster()
    steps = [] if args.skip_gaming else [
        lambda: retry_pending(poster),
        lambda: make_new_games(poster, "new", args.new_games),
        lambda: make_new_games(poster, "classic", args.classic_games),
        lambda: make_new_palworld(poster, args.new_palworld),
        lambda: make_new_games(poster, "movie_new", args.new_movies, MOVIE_SPECS),
        lambda: make_new_games(poster, "movie_review", args.movie_reviews, MOVIE_SPECS),
        lambda: make_long_videos(poster, args.long_videos, tuple(args.long_category or ("gaming", "movies")), args.long_series),
        *[
            (lambda slot, count: lambda: make_new_games(
                poster, slot, int(count), MOVIE_SPECS if any(MOVIE_SPECS.glob(f"{slot}_*.json")) else GAME_SPECS
            ))(*extra.split(":"))
            for extra in args.extra_slot
        ],
    ]
    for step in steps:
        try:
            step()
        except Exception:
            log.exception("step crashed")
    if not args.skip_trends:
        run_trends()
    log.info("Daily job done (%s)", ", ".join(f"{k} {v}" for k, v in HEALTH.items()))
    if HEALTH["build_failed"] and not HEALTH["built"]:
        log.error("Every video build failed this run - check the warnings above (TTS, images, ffmpeg)")
        sys.exit(1)
    if HEALTH["post_failed"] and not HEALTH["posted"]:
        log.error("Every upload failed this run - check platform tokens/credentials")
        sys.exit(1)


if __name__ == "__main__":
    main()
