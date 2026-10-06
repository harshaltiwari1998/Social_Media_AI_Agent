"""Daily automation, run by Windows Task Scheduler (see scripts/daily_job.cmd).

1. Retry: every gaming video in media_output/{palworld,game}_videos.json is posted to the
   gaming platforms it hasn't reached yet (e.g. after YouTube's daily upload limit).
2. New gaming videos: one new/upcoming game, one classic game (scripts/specs/games/) and
   one Palworld breeding combo (scripts/specs/palworld_queue.json) per day by default.
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
from scripts import palworld_breeding_videos as pal  # noqa: E402

MANIFEST = ROOT / settings.media_output_dir / "palworld_videos.json"
GAME_MANIFEST = ROOT / settings.media_output_dir / "game_videos.json"
QUEUE = ROOT / "scripts" / "specs" / "palworld_queue.json"
GAME_SPECS = ROOT / "scripts" / "specs" / "games"
OLLAMA_EXE = Path(r"F:\AI AGENT\Ollama\ollama.exe")
OLLAMA_MODELS = ROOT / ".cache" / "ollama-models"

log = logging.getLogger("daily_job")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Poster:
    """Posts gaming videos; stops using YouTube for the run once its daily upload limit is hit."""

    def __init__(self) -> None:
        self.youtube_blocked = False

    @staticmethod
    def pending(trend_id: str) -> bool:
        return not all(store.already_posted(trend_id, name) for name in platforms_for({"category": "gaming"}))

    def post(self, trend_id: str, text: str, tags: list[str], video_path: str) -> None:
        for name in platforms_for({"category": "gaming"}):
            if store.already_posted(trend_id, name):
                continue
            if name == "youtube" and self.youtube_blocked:
                log.info("  %s: skipped (YouTube upload limit reached today, retry tomorrow)", name)
                continue
            result = _POSTERS[name].post_video(video_path, text, tags=tags)
            store.record_post(trend_id, name, result.post_id, result.dry_run)
            if result.success:
                log.info("  %s: posted %s%s", name, result.post_id, " (dry run)" if result.dry_run else "")
            else:
                log.warning("  %s: FAILED %s", name, result.error)
                if name == "youtube" and "uploadLimitExceeded" in (result.error or ""):
                    self.youtube_blocked = True


def _build(builder, spec_path: Path) -> str | None:
    try:
        video = builder(spec_path)["video"]
    except Exception:
        log.exception("  build failed")
        return None
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

    games = _load(GAME_MANIFEST)
    for game_id, entry in games.items():
        spec_path = ROOT / entry["spec"]
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if not poster.pending(game.post_id_for(spec)):
            continue
        log.info("%s", game_id)
        if not Path(entry["video"]).exists():
            if not (video := _build(game.build_video, spec_path)):
                continue
            entry["video"] = video
            _save(GAME_MANIFEST, games)
        poster.post(game.post_id_for(spec), *game.post_text_and_tags(spec), entry["video"])


def make_new_palworld(poster: Poster, count: int) -> None:
    log.info("== New Palworld videos (up to %d)", count)
    if count <= 0:
        return
    manifest = _load(MANIFEST)
    todo = [c for c in json.loads(QUEUE.read_text(encoding="utf-8")) if c["child"] not in manifest][:count]
    if not todo:
        log.warning("Palworld queue is empty - add verified unique combos to %s", QUEUE.name)
        return
    for combo in todo:
        a, b = combo["parents"]
        spec = pal.spec_from_combo(a, b, combo["child"], index=len(manifest))
        spec_path = ROOT / "scripts" / "specs" / f"palworld_{combo['child'].lower().replace(' ', '_')}.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("%s = %s + %s", combo["child"], a, b)
        if not (video := _build(pal.build_video, spec_path)):
            continue
        manifest[combo["child"]] = {"spec": spec_path.relative_to(ROOT).as_posix(), "video": video, "data": spec}
        _save(MANIFEST, manifest)
        poster.post(pal.post_id_for(spec), *pal.post_text_and_tags(spec), video)


def make_new_games(poster: Poster, slot: str, count: int) -> None:
    """Next `count` unposted specs of a slot ("new" / "classic"), in order, skipping expired news."""
    log.info("== New %s-game videos (up to %d)", slot, count)
    if count <= 0:
        return
    games = _load(GAME_MANIFEST)
    today = date.today().isoformat()
    specs = []
    for path in sorted(GAME_SPECS.glob(f"{slot}_*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec["id"] in games or spec.get("expires", "9999-12-31") < today:
            continue
        specs.append((spec["order"], path, spec))
    if not specs:
        log.warning("No %s-game specs left - add more to %s", slot, GAME_SPECS.relative_to(ROOT))
        return
    for _, path, spec in sorted(specs, key=lambda t: t[0])[:count]:
        log.info("%s", spec["id"])
        if not (video := _build(game.build_video, path)):
            continue
        games[spec["id"]] = {"spec": path.relative_to(ROOT).as_posix(), "video": video}
        _save(GAME_MANIFEST, games)
        poster.post(game.post_id_for(spec), *game.post_text_and_tags(spec), video)


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
    parser.add_argument("--skip-trends", action="store_true")
    parser.add_argument("--skip-gaming", action="store_true", help="gaming videos run on GitHub Actions instead")
    args = parser.parse_args()

    log_dir = ROOT / settings.media_output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / f"daily-{date.today()}.log", encoding="utf-8"), logging.StreamHandler()],
    )
    log.info("Daily job start (DRY_RUN=%s)", settings.dry_run)

    poster = Poster()
    steps = [] if args.skip_gaming else [
        lambda: retry_pending(poster),
        lambda: make_new_games(poster, "new", args.new_games),
        lambda: make_new_games(poster, "classic", args.classic_games),
        lambda: make_new_palworld(poster, args.new_palworld),
    ]
    for step in steps:
        try:
            step()
        except Exception:
            log.exception("step crashed")
    if not args.skip_trends:
        run_trends()
    log.info("Daily job done")


if __name__ == "__main__":
    main()
