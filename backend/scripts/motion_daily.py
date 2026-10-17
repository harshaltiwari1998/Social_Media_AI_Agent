"""Free daily AI-motion videos: works through F:/AI AGENT/motion_queue/*.json storyboards (fal_video.py format,
"backend": "wan") using the free Hugging Face ZeroGPU quota. Stops when the quota is used up and continues next
run; finished videos land in F:/AI AGENT/motion_videos/<storyboard>/final.mp4. Nothing is uploaded.

Run by scripts/daily_job.cmd after the daily job (laptop task), or by hand:  python scripts/motion_daily.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import fal_video as fv  # noqa: E402

QUEUE = Path("F:/AI AGENT/motion_queue")
OUT = Path("F:/AI AGENT/motion_videos")
log = logging.getLogger("motion_daily")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(OUT / f"motion-{date.today()}.log", encoding="utf-8"), logging.StreamHandler()])
    for board in sorted(QUEUE.glob("*.json")):
        dest = OUT / board.stem
        if (dest / "final.mp4").exists():
            continue
        title = json.loads(board.read_text(encoding="utf-8")).get("title", board.stem)
        log.info("== %s", title)
        try:
            fv.build(board, dest)
            log.info("   done: %s", dest / "final.mp4")
        except fv.QuotaExhausted as exc:
            log.info("   free GPU quota used up for today - continuing next run (%s)", str(exc)[:120])
            return
        except Exception:
            log.exception("   failed - skipping to the next storyboard")


if __name__ == "__main__":
    main()
