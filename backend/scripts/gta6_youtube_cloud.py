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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

BRANCH = "clips/gta6-youtube"
RAW = "https://raw.githubusercontent.com/harshaltiwari1998/Social_Media_AI_Agent/" + BRANCH
STEP = 300 * 1024 * 1024
PLANS = ROOT / "scripts" / "specs" / "gameplay"
STORY_FIRST = [("gta6_launch", range(1, 42)), ("gta6_day2", range(1, 21))]
PER_4H = 10  # owner 2026-11-25: "youtube gaming ke liye 4 ghante me 10 shorts" - laptop closed, so GitHub does it
GAP_H = 4  # hours between two batches (counted from the last short of the batch)


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
    shorts = STORY_FIRST + shorts  # YouTube in story order: the launch stream and day 2 first (like YT_ORDER)
    rank = {f"gameplay:{name}:short{k:02d}": i for i, (name, k) in
            enumerate((name, k) for name, nums in shorts for k in nums)}
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
    queue.sort(key=lambda q: (0 if q.get("v2") else 1, rank.get(q["tid"], len(rank))))  # v2 re-edits first
    if size or added or not queue_file.exists():
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
    # owner 2026-11-26: "4 ghante ka gap" - a batch of up to 10 shorts, then 4 full hours with no GTA 6 short. The old
    # rolling "max 10 in any 4 h" (long videos counted too) let 1-2 stragglers go up every ~2 hours in between.
    c = store._get_conn()
    last = c.execute("select max(posted_at) from posted_trends where platform='youtube' and trend_id like 'gameplay:gta6_%:short%' "
                     "and post_id is not null and post_id not like 'hold:%' and post_id not like 'skip:%'").fetchone()[0]
    c.close()
    if last:
        wait = datetime.fromisoformat(last).replace(tzinfo=timezone.utc) + timedelta(hours=GAP_H) - datetime.now(timezone.utc)
        if wait.total_seconds() > 0:
            print(f"last GTA 6 short went to YouTube at {last[:16]} UTC - next batch in {wait.total_seconds() / 60:.0f} min", flush=True)
            return
    n = min(n, PER_4H)
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
        if not r.success and "uploadLimitExceeded" in str(r.error):
            # the channel's own limit (~14-15 uploads in a rolling 24 h; the owner's own uploads count too)
            print("YouTube channel upload limit reached - the next run tries again", flush=True)
            return
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


# ---------- long videos (owner 2026-11-26: "2 long a day") ----------
# The 42 GTA 6 long videos are ~39 GB, too much for one branch, so the laptop keeps only the next few on their own
# orphan branch: push-long rebuilds it (force push, old files dropped) with the next N longs not on YouTube yet, but
# only when fewer than --if-low are left on it. post uploads at most LONGS_PER_DAY in any 24 hours.
LONG_BRANCH = "clips/gta6-youtube-long"
RAW_LONG = RAW.rsplit("/", 1)[0] + "/" + LONG_BRANCH.split("/", 1)[1]
LONG_ORDER = ["gta6_launch", "gta6_day2", "gta6_day3b", "gta6_day4", "gta6_day5", "gta6_day6", "gta6_day7"]
LONGS_PER_DAY = 2
PART = 45 * 1024 * 1024


def _long_text(plan: dict, n: int, lv: dict, dest: Path) -> tuple[str, str]:
    title, tag = lv["title"], plan.get("title_tag", "")
    if tag and tag.lower() not in title.lower():
        title = f"{title} | {tag}"
    ch = dest / f"long_{n}_chapters.txt"
    chapters = ch.read_text(encoding="utf-8") if ch.exists() else ""
    text = (f"{lv.get('description', '')}\n\nChapters:\n{chapters}\n\n"
            + " ".join(f"#{t.replace(' ', '')}" for t in plan.get("tags", [])[:8]))
    return title, text


def _yt_done(dbs: list[Path]) -> set[str]:
    import sqlite3
    done = set()
    for db in dbs:
        if db.exists() and db.stat().st_size:
            c = sqlite3.connect(db)
            done |= {r[0] for r in c.execute(
                "select trend_id from posted_trends where platform='youtube' and (trend_id like 'gameplay:gta6_%:long%' "
                "or trend_id like 'gameplay:gta6_ch1:%') and post_id is not null and post_id != '' and post_id not like 'hold:%'")}
            c.close()
    return done


def push_long(n: int, if_low: int) -> None:
    import httpx
    run, out = REPO.parent / "run_gta6", REPO.parent / "gameplay_out"
    os.environ["MEDIA_OUTPUT_DIR"] = str(run)
    from app.media import safety
    _git("fetch", "-q", "origin", "gaming-state", cwd=REPO)
    state = Path(tempfile.mkdtemp()) / "state.sqlite3"
    state.write_bytes(subprocess.run(["git", "show", "origin/gaming-state:pipeline_store.sqlite3"], cwd=REPO,
                                     check=True, capture_output=True).stdout)
    done = _yt_done([state, run / "pipeline_store.sqlite3"])
    r = httpx.get(f"{RAW_LONG}/queue.json", timeout=60, follow_redirects=True)
    left = [q for q in (r.json() if r.status_code == 200 else []) if q["tid"] not in done]
    if len(left) >= if_low:
        print(f"{len(left)} long videos still waiting on {LONG_BRANCH} - nothing to push", flush=True)
        return
    held = json.loads((run / "hourly_held.json").read_text()) if (run / "hourly_held.json").exists() else []
    cleared = json.loads((run / "hourly_cleared.json").read_text()) if (run / "hourly_cleared.json").exists() else []
    # owner 2026-11-26: every Chapter 1 mission as its own video, one in each 4-hour batch - they go first; the plan
    # long videos with the same footage (REPLACES) are left out so nothing is on the channel twice
    mf = out / "gta6_chapter1_full" / "missions" / "missions.json"
    ms = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {"replaces": [], "missions": []}
    cands = [(m["tid"], Path(m["file"]), Path(m["thumb"]), m["title"], m["text"], m["tags"], f"ch1_mission{m['n']:02d}")
             for m in ms["missions"]]
    for name in LONG_ORDER:
        plan = json.loads((PLANS / f"{name}.json").read_text(encoding="utf-8"))
        for k, lv in enumerate(plan.get("longs", []), 1):
            tid = f"gameplay:{name}:long{k:02d}"
            if tid in ms["replaces"]:
                continue
            title, text = _long_text(plan, k, lv, out / name)
            cands.append((tid, out / name / f"long_{k}.mp4", out / name / f"long_{k}_thumb.jpg", title, text,
                          plan.get("tags", []), f"{name}_long{k}"))
    picks = []
    for c in cands:
        tid, video = c[0], c[1]
        if len(picks) >= n or tid in done or not video.exists():
            continue
        if tid in held and tid not in cleared:
            print(f"{tid}: held in hourly_held.json - left out", flush=True)
            continue
        if tid not in cleared and safety.scan_video(video):
            print(f"{tid}: NudeNet hit - left out", flush=True)
            continue
        picks.append(c)
    if not picks:
        print("no GTA 6 long video left for YouTube", flush=True)
        return
    wt = REPO.parent / "wt_gta6yt_long"
    if wt.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO)
        shutil.rmtree(wt, ignore_errors=True)
    _git("worktree", "prune", cwd=REPO)
    _git("worktree", "add", "-q", "--detach", str(wt), cwd=REPO)
    subprocess.run(["git", "branch", "-D", "gta6yt-long-tmp"], cwd=REPO, capture_output=True)
    _git("checkout", "-q", "--orphan", "gta6yt-long-tmp", cwd=wt)
    _git("rm", "-rq", "--cached", ".", cwd=wt)
    for p in wt.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (wt / "longs").mkdir()
    first = [True]

    def flush(msg: str) -> None:
        _git("add", "-A", cwd=wt)
        _git("-c", "user.name=HarryBeast laptop", "-c", "user.email=laptop@localhost", "commit", "-qm", msg, cwd=wt)
        for k in range(5):
            cmd = ["git", "push", "-q", *(["--force"] if first[0] else []), "origin", f"HEAD:{LONG_BRANCH}"]
            if subprocess.run(cmd, cwd=wt).returncode == 0:
                first[0] = False
                return
            time.sleep(20 * (k + 1))
        raise SystemExit("push failed")

    queue, size = [], 0
    for tid, video, thumb, title, text, tags, base in picks:
        parts = []
        with open(video, "rb") as fh:
            i = 0
            while chunk := fh.read(PART):
                rel = f"longs/{base}.mp4.part{i:02d}"
                (wt / rel).write_bytes(chunk)
                parts.append(rel)
                i += 1
                size += len(chunk)
                if size >= STEP:
                    flush(f"GTA 6 long videos: {base}")
                    size = 0
        entry = {"tid": tid, "parts": parts, "title": title, "text": text, "tags": tags}
        if thumb.exists():
            shutil.copy(thumb, wt / f"longs/{base}_thumb.jpg")
            entry["thumb"] = f"longs/{base}_thumb.jpg"
        queue.append(entry)
        print(f"queued {entry['tid']}: {title}", flush=True)
    (wt / "queue.json").write_text(json.dumps(queue, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    flush(f"GTA 6 long videos: queue of {len(queue)}")  # queue.json last - CI never sees a queue without its files
    print(f"{len(queue)} long videos on {LONG_BRANCH}", flush=True)


def post_long() -> None:
    import httpx

    from app.pipeline import store
    from app.pipeline.orchestrator import _POSTERS

    r = httpx.get(f"{RAW_LONG}/queue.json", timeout=60, follow_redirects=True)
    if r.status_code == 404:
        print("no long-video queue on GitHub", flush=True)
        return
    todo = [q for q in r.raise_for_status().json() if yt_open(store, q["tid"])]
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    c = store._get_conn()
    recent = c.execute("select count(*) from posted_trends where platform='youtube' and trend_id like 'gameplay:gta6_%:long%' "
                       "and post_id is not null and post_id not like 'hold:%' and post_id not like 'skip:%' "
                       "and posted_at > ?", (since,)).fetchone()[0]
    last_m = c.execute("select max(posted_at) from posted_trends where platform='youtube' and trend_id like 'gameplay:gta6_ch1:%' "
                       "and post_id is not null and post_id not like 'hold:%' and post_id not like 'skip:%'").fetchone()[0]
    c.close()
    print(f"long videos: {len(todo)} waiting, {recent} uploaded in the last 24 h", flush=True)
    if not todo:
        return
    q = todo[0]
    if q["tid"].startswith("gameplay:gta6_ch1:"):
        # Chapter 1 missions (owner 2026-11-26): one in every 4-hour batch, next to the 10 shorts
        if last_m and datetime.fromisoformat(last_m).replace(tzinfo=timezone.utc) + timedelta(hours=GAP_H) > datetime.now(timezone.utc):
            print(f"last Chapter 1 mission went up at {last_m[:16]} UTC - next one 4 h after it", flush=True)
            return
    elif recent >= LONGS_PER_DAY:
        return
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "long.mp4"
        with open(video, "wb") as f:
            for part in q["parts"]:
                with httpx.stream("GET", f"{RAW_LONG}/{part}", timeout=300, follow_redirects=True) as resp:
                    resp.raise_for_status()
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        thumb = None
        if q.get("thumb"):
            thumb = Path(tmp) / "thumb.jpg"
            thumb.write_bytes(httpx.get(f"{RAW_LONG}/{q['thumb']}", timeout=60, follow_redirects=True).content)
        res = _POSTERS["youtube"].post_long_video(str(video), q["title"], q["text"], q["tags"],
                                                  str(thumb) if thumb else None)
    if not res.success and ("uploadLimitExceeded" in str(res.error) or "quota" in str(res.error).lower()):
        print("YouTube upload limit / quota - the long video goes next run", flush=True)
        return
    store.record_post(q["tid"], "youtube", res.post_id, res.dry_run)
    print(f"{q['tid']}: {'ok ' + str(res.post_id) if res.success else 'FAILED ' + str(res.error)[:200]}", flush=True)


def v2_sync() -> None:
    """Owner 2026-11-26 (max views): the best 100 GTA 6 shorts are re-edited (gta6_shorts_v2.py). On the queue branch
    a best-100 short that is not re-edited yet is taken OUT (so CI never posts the old cut), a re-edited one gets its new
    file and goes to the FRONT (v2 entries in best-list order, then the rest in story order)."""
    run, out = REPO.parent / "run_gta6", REPO.parent / "gameplay_out"
    os.environ["MEDIA_OUTPUT_DIR"] = str(run)
    from app.pipeline import store
    best = json.loads((PLANS / "gta6_best100.json").read_text(encoding="utf-8"))
    edits_f = out / "_v2" / "edits.json"
    edits = json.loads(edits_f.read_text(encoding="utf-8")) if edits_f.exists() else {}
    held = json.loads((run / "hourly_held.json").read_text()) if (run / "hourly_held.json").exists() else []
    cleared = json.loads((run / "hourly_cleared.json").read_text()) if (run / "hourly_cleared.json").exists() else []
    _git("fetch", "-q", "origin", "gaming-state", cwd=REPO)
    state = Path(tempfile.mkdtemp()) / "state.sqlite3"
    state.write_bytes(subprocess.run(["git", "show", "origin/gaming-state:pipeline_store.sqlite3"], cwd=REPO,
                                     check=True, capture_output=True).stdout)
    import sqlite3
    c = sqlite3.connect(state)
    on_yt = {r[0] for r in c.execute("select trend_id from posted_trends where platform='youtube' and post_id is not null "
                                     "and post_id != '' and post_id not like 'hold:%' and post_id not like 'skip:%'")}
    c.close()
    wt = REPO.parent / "wt_gta6yt"
    _git("pull", "-q", "--ff-only", "origin", BRANCH, cwd=wt)
    queue_file = wt / "queue.json"
    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    by_tid = {q["tid"]: q for q in queue}
    order = {f"gameplay:{b['plan']}:short{b['n']:02d}": i for i, b in enumerate(best)}
    plans: dict[str, dict] = {}
    size, changed = 0, 0

    def flush(msg: str) -> None:
        queue_file.write_text(json.dumps(queue, indent=1, ensure_ascii=False) + chr(10), encoding="utf-8")
        _git("add", "-A", cwd=wt)
        if not _git("status", "--porcelain", cwd=wt).strip():
            return
        _git("-c", "user.name=HarryBeast laptop", "-c", "user.email=laptop@localhost", "commit", "-qm", msg, cwd=wt)
        for k in range(5):
            if subprocess.run(["git", "push", "-q", "origin", f"HEAD:{BRANCH}"], cwd=wt).returncode == 0:
                return
            time.sleep(20 * (k + 1))
        raise SystemExit("push failed")

    for b in best:
        name, k = b["plan"], b["n"]
        tid = f"gameplay:{name}:short{k:02d}"
        if tid in on_yt or (tid in held and tid not in cleared):
            continue
        done = f"{name}:{k}" in edits
        q = by_tid.get(tid)
        if not done:
            if q is not None:  # old cut must not go up - out until the new one is ready
                queue.remove(q)
                del by_tid[tid]
                changed += 1
            continue
        if q is not None and q.get("v2"):
            continue
        video = out / name / f"short_{k:02d}.mp4"
        if not video.exists():
            continue
        plan = plans.setdefault(name, json.loads((PLANS / f"{name}.json").read_text(encoding="utf-8")))
        rel = f"shorts/{name}_short{k:02d}.mp4"
        (wt / "shorts").mkdir(exist_ok=True)
        shutil.copy(video, wt / rel)
        size += video.stat().st_size
        entry = {"tid": tid, "file": rel, "caption": caption(plan, plan["shorts"][k - 1]["title"]), "tags": plan["tags"],
                 "v2": True}
        if q is not None:
            queue.remove(q)
        queue.append(entry)
        by_tid[tid] = entry
        changed += 1
        if size >= STEP:
            queue.sort(key=lambda x: (0 if x.get("v2") else 1, order.get(x["tid"], 999)))
            flush("GTA 6 YouTube queue: v2 re-edits")
            size = 0
    v2 = [x for x in queue if x.get("v2")]
    v2.sort(key=lambda x: order.get(x["tid"], 999))
    queue[:] = v2 + [x for x in queue if not x.get("v2")]
    flush("GTA 6 YouTube queue: v2 re-edits first")
    print(f"v2 sync: {changed} changes, {len(v2)} re-edited shorts at the front, {len(queue)} in queue.json", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["push", "post", "push-long", "post-long", "v2-sync"])
    ap.add_argument("n", type=int, nargs="?", default=1)
    ap.add_argument("--if-low", type=int, default=2, help="push-long: only when fewer than this many wait on GitHub")
    a = ap.parse_args()
    {"push": lambda: push(a.n), "post": lambda: post(a.n), "push-long": lambda: push_long(a.n, a.if_low),
     "post-long": post_long, "v2-sync": v2_sync}[a.mode]()
