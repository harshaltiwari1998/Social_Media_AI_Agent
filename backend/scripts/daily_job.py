"""Daily automation, run by Windows Task Scheduler (see scripts/daily_job.cmd).

1. Retry: every Palworld video in media_output/palworld_videos.json is posted to the
   gaming platforms it hasn't reached yet (e.g. after YouTube's daily upload limit).
2. New: the next few combos from scripts/specs/palworld_queue.json become new videos.
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
from scripts.palworld_breeding_videos import build_video, post_id_for, post_text_and_tags, spec_from_combo  # noqa: E402

MANIFEST = ROOT / "media_output" / "palworld_videos.json"
QUEUE = ROOT / "scripts" / "specs" / "palworld_queue.json"
OLLAMA_EXE = Path(r"F:\AI AGENT\Ollama\ollama.exe")
OLLAMA_MODELS = ROOT / ".cache" / "ollama-models"

log = logging.getLogger("daily_job")


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def _save_manifest(manifest: dict) -> None:
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


class Poster:
    """Posts Palworld videos; stops using YouTube for the run once its daily upload limit is hit."""

    def __init__(self) -> None:
        self.youtube_blocked = False

    def post(self, spec: dict, video_path: str) -> None:
        trend_id = post_id_for(spec)
        text, tags = post_text_and_tags(spec)
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


def retry_pending(poster: Poster) -> None:
    log.info("== Retry pending Palworld uploads")
    for child, entry in _load_manifest().items():
        if not Path(entry["video"]).exists():
            log.warning("%s: video file missing (%s), skipping", child, entry["video"])
            continue
        spec = json.loads(Path(entry["spec"]).read_text(encoding="utf-8"))
        log.info("%s", child)
        poster.post(spec, entry["video"])


def make_new_palworld(poster: Poster, count: int) -> None:
    log.info("== New Palworld videos (up to %d)", count)
    if count <= 0:
        return
    manifest = _load_manifest()
    todo = [c for c in json.loads(QUEUE.read_text(encoding="utf-8")) if c["child"] not in manifest][:count]
    if not todo:
        log.warning("Palworld queue is empty - add verified unique combos to %s", QUEUE.name)
        return
    for combo in todo:
        a, b = combo["parents"]
        spec = spec_from_combo(a, b, combo["child"], index=len(manifest))
        spec_path = ROOT / "scripts" / "specs" / f"palworld_{combo['child'].lower().replace(' ', '_')}.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("%s = %s + %s", combo["child"], a, b)
        try:
            video = build_video(spec_path)["video"]
        except Exception:
            log.exception("  build failed")
            continue
        if not video["success"]:
            log.warning("  video failed: %s", video["error"])
            continue
        manifest[combo["child"]] = {"spec": str(spec_path.relative_to(ROOT)), "video": video["data"]["path"]}
        _save_manifest(manifest)
        poster.post(spec, video["data"]["path"])


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
    parser.add_argument("--new-palworld", type=int, default=3, help="new Palworld videos per day")
    parser.add_argument("--skip-trends", action="store_true")
    args = parser.parse_args()

    log_dir = ROOT / "media_output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / f"daily-{date.today()}.log", encoding="utf-8"), logging.StreamHandler()],
    )
    log.info("Daily job start (DRY_RUN=%s)", settings.dry_run)

    poster = Poster()
    for step in (lambda: retry_pending(poster), lambda: make_new_palworld(poster, args.new_palworld)):
        try:
            step()
        except Exception:
            log.exception("step crashed")
    if not args.skip_trends:
        run_trends()
    log.info("Daily job done")


if __name__ == "__main__":
    main()
