"""Laptop-task step: finish any gameplay plan (scripts/specs/gameplay/*.json) that still has clips to upload.

For each plan with unposted items whose source recording is still on disk, renders whatever is missing
(gameplay_clips.build skips finished files) and uploads to HarryBeast until YouTube's daily limit.
Run by scripts/daily_job.cmd, or by hand:  python scripts/gameplay_daily.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import gameplay_clips as gc  # noqa: E402
import gameplay_cloud  # noqa: E402
from app.pipeline import store  # noqa: E402

PLANS = ROOT / "scripts" / "specs" / "gameplay"
# Per laptop-task run, across all plans: dozens of Shorts in one day each get far less reach than a steady few.
DAILY_LONGS, DAILY_SHORTS = 1, 8
log = logging.getLogger("gameplay_daily")


def pending(plan: dict) -> int:
    items = [f"long{n:02d}" for n in range(1, len(plan.get("longs", [])) + 1)]
    items += [f"short{n:02d}" for n in range(1, len(plan.get("shorts", [])) + 1)]
    return sum(not store.already_posted(f"gameplay:{plan['name']}:{i}", "youtube") for i in items)


def _lock() -> Path | None:
    """One run at a time (the laptop task and a manual run must not upload the same clip twice)."""
    lock = gc.OUT / "gameplay.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 12 * 3600:
        return None
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def main() -> None:
    gc.OUT.mkdir(parents=True, exist_ok=True)
    lock = _lock()
    if lock is None:
        print("gameplay_daily: another run is in progress - skipping")
        return
    try:
        _run_plans()
    finally:
        lock.unlink(missing_ok=True)


def rendered(plan: dict) -> bool:
    """Every short and long video of the plan (with its thumbnail and chapters) is already in its gameplay_out folder."""
    dest = gc.OUT / plan["name"]
    files = [dest / f"short_{n:02d}.mp4" for n in range(1, len(plan.get("shorts", [])) + 1)]
    for n in range(1, len(plan.get("longs", [])) + 1):
        files += [dest / f"long_{n}{s}" for s in (".mp4", "_thumb.jpg", "_chapters.txt")]
    return all(f.exists() and f.stat().st_size > 0 for f in files)


def _run_plans() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(gc.OUT / f"gameplay-{date.today()}.log", encoding="utf-8"),
                                  logging.StreamHandler()])
    budget = {"long": DAILY_LONGS, "short": DAILY_SHORTS}
    for path in sorted(PLANS.glob("*.json")):
        if not any(budget.values()):
            break
        plan = json.loads(path.read_text(encoding="utf-8"))
        if plan.get("cloud") and (gc.OUT / plan["name"] / ".pushed").exists():
            continue  # on its clips/ branch - CI posts it from there
        left = pending(plan)
        if not left:
            continue
        # owner 2026-11-12: the recordings are deleted once every clip of a plan is rendered (F: space) - such a plan
        # still has to be pushed / posted from its rendered files, only the render step is skipped
        have_source = Path(plan["source"]).exists()
        if not have_source and not rendered(plan):
            log.warning("%s: %d clips left, not all rendered and the recording is gone (%s)", plan["name"], left, plan["source"])
            continue
        log.info("== %s: %d clips left - %s", plan["name"], left,
                 "rendering what is missing" if have_source else "all rendered, recording deleted")
        if plan.get("cloud"):  # GitHub Actions (daily-gameplay.yml) posts it - the laptop only renders and pushes
            try:
                if have_source:
                    gc.build(plan)
                gameplay_cloud.push(plan)
            except Exception:
                log.exception("%s: render/push failed - will retry next run", plan["name"])
            continue
        try:
            if have_source:
                gc.build(plan)
            posted = gc.upload(plan, long_limit=budget["long"], short_limit=budget["short"]) or {}
            for kind in budget:
                budget[kind] -= posted.get(kind, 0)
        except Exception:
            log.exception("%s failed - will retry next run", plan["name"])


if __name__ == "__main__":
    main()
