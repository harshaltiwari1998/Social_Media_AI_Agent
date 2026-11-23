"""GTA 6 shorts to YouTube from GitHub Actions, so the laptop can stay closed (owner 2026-11-23: "me office me laptop
open nahi kar sakta hu ... har 2 ghante me youtube me shorts jaye GTA 6 ke").

The shorts live only on the laptop, so:

  push N   laptop: put the next N GTA 6 shorts that have the GTA VI logo, are clean in NudeNet and not on YouTube yet
           on the orphan branch clips/gta6-youtube (shorts/<plan>_shortNN.mp4 + queue.json, in the posting order of
           .cache/gta6_hourly.py). Pushed in ~300 MB steps; shorts already in the queue are skipped.
  post     CI (gta6-youtube.yml, every 2 hours): download queue.json and only the next short from
           raw.githubusercontent.com (the repo is public - no git fetch of the whole branch), upload it to YouTube,
           record it in the posting store. The Google project allows ~10,000 units a day (1,600 an upload) for both
           channels, so ~5-6 shorts a day get through; a quota error just ends the run.

The owner approved every GTA 6 clip of these plans for posting; nudity was checked on the laptop (strict scan of the
recording + NudeNet on the short) before a short is pushed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

BRANCH = "clips/gta6-youtube"
RAW = "https://raw.githubusercontent.com/harshaltiwari1998/Social_Media_AI_Agent/" + BRANCH
STEP = 300 * 1024 * 1024
PLANS = ROOT / "scripts" / "specs" / "gameplay"


def caption(plan: dict, title: str) -> str:
    tags = " ".join(f"#{t.replace(' ', '')}" for t in plan["tags"][:6])
    title = title if "gta 6" in title.lower() else f"{title} | GTA 6"
    return "\n".join([title, plan.get("post_line", "GTA 6 gameplay from my live stream."), tags])


def yt_open(store, tid: str) -> bool:
    """Not on YouTube yet: no row, a failed try or the strike-week hold."""
    c = store._get_conn()
    row = c.execute("select post_id from posted_trends where trend_id=? and platform='youtube'", (tid,)).fetchone()
    c.close()
    return not row or not row[0] or row[0] == store.HOLD


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def push(n: int) -> None:
    run = REPO.parent / "run_gta6"
    out = REPO.parent / "gameplay_out"
    os.environ["MEDIA_OUTPUT_DIR"] = str(run)
    from app.media import safety
    from app.pipeline import store

    src = (ROOT / ".cache" / "gta6_hourly.py").read_text(encoding="utf-8")
    shorts = eval(src.split("SHORTS = ", 1)[1].split("\nLONG_PLANS", 1)[0])  # noqa: S307 - our own file, same order
    held = json.loads((run / "hourly_held.json").read_text()) if (run / "hourly_held.json").exists() else []
    cleared = json.loads((run / "hourly_cleared.json").read_text()) if (run / "hourly_cleared.json").exists() else []

    wt = REPO.parent / "wt_gta6yt"
    if not wt.exists():
        remote = _git("ls-remote", "--heads", "origin", BRANCH, cwd=REPO).strip()
        if remote:
            _git("fetch", "-q", "origin", f"{BRANCH}:{BRANCH}", cwd=REPO)
            _git("worktree", "add", "-q", str(wt), BRANCH, cwd=REPO)
        else:
            _git("worktree", "add", "-q", "--detach", str(wt), cwd=REPO)
            _git("checkout", "-q", "--orphan", BRANCH, cwd=wt)
            _git("rm", "-rq", "--cached", ".", cwd=wt)
            for p in wt.iterdir():
                if p.name != ".git":
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
    queue_file = wt / "queue.json"
    queue = json.loads(queue_file.read_text(encoding="utf-8")) if queue_file.exists() else []
    have = {q["tid"] for q in queue}
    (wt / "shorts").mkdir(exist_ok=True)

    added, size = 0, 0

    def flush(msg: str) -> None:
        queue_file.write_text(json.dumps(queue, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        _git("add", "-A", cwd=wt)
        _git("-c", "user.name=HarryBeast laptop", "-c", "user.email=laptop@localhost", "commit", "-qm", msg, cwd=wt)
        for k in range(5):
            if subprocess.run(["git", "push", "-q", "origin", f"HEAD:{BRANCH}"], cwd=wt).returncode == 0:
                return
            time.sleep(20 * (k + 1))
        raise SystemExit("push failed")

    for name, nums in shorts:
        plan = json.loads((PLANS / f"{name}.json").read_text(encoding="utf-8"))
        logo = out / name / "relogo_done.txt"
        with_logo = set(logo.read_text().split()) if logo.exists() else set()
        for k in nums:
            if added >= n:
                break
            tid = f"gameplay:{name}:short{k:02d}"
            if tid in have or f"{k:02d}" not in with_logo or not yt_open(store, tid):
                continue
            if tid in held and tid not in cleared:
                continue
            video = out / name / f"short_{k:02d}.mp4"
            if tid not in cleared and safety.scan_video(video):
                print(f"{tid}: NudeNet hit - left out", flush=True)
                continue
            rel = f"shorts/{name}_short{k:02d}.mp4"
            shutil.copy(video, wt / rel)
            queue.append({"tid": tid, "file": rel, "caption": caption(plan, plan["shorts"][k - 1]["title"]),
                          "tags": plan["tags"]})
            added += 1
            size += video.stat().st_size
            print(f"{added}/{n} {tid}: {plan['shorts'][k - 1]['title']}", flush=True)
            if size >= STEP:
                flush(f"GTA 6 YouTube queue: +{added}")
                size = 0
    if size or not queue_file.exists():
        flush(f"GTA 6 YouTube queue: +{added}")
    print(f"queued {added}, {len(queue)} in queue.json", flush=True)


def post(n: int) -> None:
    import httpx

    from app.pipeline import store
    from app.pipeline.orchestrator import _POSTERS

    yt = _POSTERS["youtube"]
    queue = httpx.get(f"{RAW}/queue.json", timeout=60, follow_redirects=True).raise_for_status().json()
    todo = [q for q in queue if yt_open(store, q["tid"])]
    print(f"{len(todo)} of {len(queue)} queued shorts not on YouTube yet", flush=True)
    done = 0
    for q in todo:
        if done >= n:
            break
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / Path(q["file"]).name
            with httpx.stream("GET", f"{RAW}/{q['file']}", timeout=300, follow_redirects=True) as r:
                r.raise_for_status()
                with open(video, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            r = yt.post_video(str(video), q["caption"], tags=q["tags"])
        if not r.success and "quota" in str(r.error).lower():
            print("YouTube quota used up for today - next run after the reset (~13:30 IST)", flush=True)
            return
        store.record_post(q["tid"], "youtube", r.post_id, r.dry_run)
        print(f"{q['tid']}: {'ok ' + str(r.post_id) if r.success else 'FAILED ' + str(r.error)[:200]}", flush=True)
        if not r.success:
            return
        done += 1
    if not todo:
        print("::warning::the GTA 6 YouTube queue is empty - push more shorts from the laptop", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["push", "post"])
    ap.add_argument("n", type=int, nargs="?", default=1)
    a = ap.parse_args()
    push(a.n) if a.mode == "push" else post(a.n)
