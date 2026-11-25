"""Approve videos from the phone, laptop closed (owner 2026-11-25: "laptop band ho tab?" - "haan").

The laptop makes the videos (owner's voice needs its GPU); nothing is posted before the owner's OK. Then:

  push [sources]   laptop: every video waiting for review - <run>/review_queue.json entries, evolution shorts in
                   run_evo/pokemon/review_pending.json, rashifal made_all.json files not posted yet - goes to the orphan
                   branch `review-queue` (videos/<n>.mp4 + an Instagram light copy, split into 45 MB parts, plus
                   queue.json); a phone preview (<= 15 MB) goes to F:/AI AGENT/review_page/previews/<n>.mp4 for the private review page.
                   Each video gets a number <n> that never changes. sources default: all run folders.
  post             CI (review-post.yml): reads review/approved.json on main ({"approve": [n, ...], "reject": [n, ...]},
                   edited from the phone by a Claude cloud session or on the GitHub website), downloads only the
                   approved files from raw.githubusercontent.com and posts them exactly like
                   post_reviewed.py approve (owner watched them: no NudeNet hold); rejected ones get skip:rejected.
  sync             laptop: drops entries that CI posted or rejected from the local review queues, so nothing is
                   approved (and posted) twice from the laptop.

The repo is public: the branch is world-readable, so only finished, nudity-checked videos are pushed (the same videos
that go out after the OK).
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

BRANCH = "review-queue"
RAW = "https://raw.githubusercontent.com/harshaltiwari1998/Social_Media_AI_Agent/" + BRANCH
PART = 45 * 1024 * 1024
STEP = 300 * 1024 * 1024
APPROVED = REPO / "review" / "approved.json"
WT = REPO.parent / "wt_review"
RUNS = REPO.parent  # F:/AI AGENT/run_*
PREVIEWS = REPO.parent / "review_page" / "previews"  # published with the private claude.ai review page only


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _duration(video: str) -> float:
    from moviepy.editor import VideoFileClip
    with VideoFileClip(video, audio=False) as c:
        return float(c.duration)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _encode(src: str, out: Path, height: int, kbps: int) -> None:
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", src, "-vf", f"scale=-2:{height}", "-r", "30", "-c:v", "libx264",
                    "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)


def _split(src: Path, dest_dir: Path, name: str) -> list[str]:
    """Copy src into dest_dir as name (or name.partNN pieces when over 45 MB); returns the relative paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.stat().st_size <= PART:
        shutil.copy(src, dest_dir / name)
        return [f"{dest_dir.name}/{name}"]
    parts = []
    with open(src, "rb") as f:
        k = 0
        while chunk := f.read(PART):
            p = f"{name}.part{k:02d}"
            (dest_dir / p).write_bytes(chunk)
            parts.append(f"{dest_dir.name}/{p}")
            k += 1
    return parts


# ------------------------------------------------------------------ collect what waits for review (laptop)

def _pending(sources: list[Path]) -> list[dict]:
    items = []
    for run in sources:
        q = run / "review_queue.json"
        if q.exists():
            for tid, e in json.loads(q.read_text(encoding="utf-8")).items():
                if Path(e["video"]).exists():
                    items.append({**e, "trend_id": tid, "source": run.name})
        rp = run / "pokemon" / "review_pending.json"
        if rp.exists():  # evolution shorts: text from pokemon_daily, posted like any gaming short
            from scripts import pokemon_daily as pd
            queue = {e["id"]: e for e in json.loads(pd.QUEUE.read_text(encoding="utf-8"))}
            for lid in json.loads(rp.read_text(encoding="utf-8")):
                video = run / "pokemon" / lid / "final.mp4"
                if lid in queue and video.exists():
                    text, tags = pd.post_text(queue[lid])
                    items.append({"trend_id": pd.post_id(queue[lid]), "kind": "short", "text": text, "tags": tags,
                                  "video": str(video), "category": "gaming", "platforms": None,
                                  "evolution": queue[lid], "source": run.name})
        for made in sorted((run / "rashifal").glob("*/made_all.json")) if (run / "rashifal").exists() else []:
            m = json.loads(made.read_text(encoding="utf-8"))
            day = made.parent.name
            if m.get("video") and Path(m["video"]).exists():
                items.append({"trend_id": f"custom:rashifal-{day}-all", "kind": "short", "text": m["text"], "tags": [],
                              "video": m["video"], "category": "movies", "platforms": ["instagram", "facebook"],
                              "rashifal": day, "source": run.name})
    return items


def push(sources: list[Path]) -> None:
    if not WT.exists():
        if _git("ls-remote", "--heads", "origin", BRANCH, cwd=REPO).strip():
            _git("fetch", "-q", "origin", f"{BRANCH}:{BRANCH}", cwd=REPO)
            _git("worktree", "add", "-q", str(WT), BRANCH, cwd=REPO)
        else:
            _git("worktree", "add", "-q", "--detach", str(WT), cwd=REPO)
            _git("checkout", "-q", "--orphan", BRANCH, cwd=WT)
            _git("rm", "-rq", "--cached", ".", cwd=WT)
            for p in WT.iterdir():
                if p.name != ".git":
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
    else:
        subprocess.run(["git", "pull", "-q", "--ff-only", "origin", BRANCH], cwd=WT)
    qf = WT / "queue.json"
    queue = json.loads(qf.read_text(encoding="utf-8")) if qf.exists() else []
    have = {q["trend_id"] for q in queue}
    size = 0

    def flush(msg: str) -> None:
        qf.write_text(json.dumps(queue, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        _git("add", "-A", cwd=WT)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=WT).returncode == 0:
            return
        _git("-c", "user.name=HarryBeast laptop", "-c", "user.email=laptop@localhost", "commit", "-qm", msg, cwd=WT)
        for k in range(5):
            if subprocess.run(["git", "push", "-q", "origin", f"HEAD:{BRANCH}"], cwd=WT).returncode == 0:
                return
            time.sleep(20 * (k + 1))
        raise SystemExit("push failed")

    added = 0
    for e in _pending(sources):
        if e["trend_id"] in have:
            continue
        n = max([q["n"] for q in queue], default=0) + 1
        video = e.pop("video")
        with tempfile.TemporaryDirectory() as tmp:
            light, prev = Path(tmp) / "light.mp4", PREVIEWS / f"{n}.mp4"
            prev.parent.mkdir(parents=True, exist_ok=True)
            # owner's phone preview on the private review page (not in git): a page file may be 15 MB at most
            secs = _duration(video)
            kbps = max(150, min(1200, int(13.5 * 8 * 1024 / max(secs, 1)) - 100))
            _encode(video, prev, 720 if kbps >= 500 else 480, kbps)
            files = _split(Path(video), WT / "videos", f"{n}.mp4")
            light_files = []
            if e["kind"] == "short":
                _encode(video, light, 1920, 3000)  # Instagram pulls this one (big originals time out there)
                light_files = _split(light, WT / "videos", f"{n}.light.mp4")
        thumb = (e.get("entry") or {}).get("thumbnail")
        if thumb and Path(thumb).exists():
            shutil.copy(thumb, WT / "videos" / f"{n}.thumb{Path(thumb).suffix}")
            e["entry"] = {**e["entry"], "thumbnail": f"videos/{n}.thumb{Path(thumb).suffix}"}
        if e.get("entry"):
            e["entry"] = {**e["entry"], "video": None}
        queue.append({"n": n, **e, "files": files, "light": light_files, "preview": f"previews/{n}.mp4",
                      "added": time.strftime("%Y-%m-%d %H:%M")})
        have.add(e["trend_id"])
        added += 1
        size += sum((WT / f).stat().st_size for f in files + light_files)
        title = (e.get("spec") or {}).get("title") or e["text"].split("\n")[0]
        print(f"#{n} {e['trend_id']}: {title[:80]}", flush=True)
        if size >= STEP:
            flush(f"review queue: +{added}")
            size = 0
    flush(f"review queue: +{added}")
    print(f"{added} new, {len(queue)} in the review queue", flush=True)


# ------------------------------------------------------------------ post approved videos (CI)

def _fetch(rel_parts: list[str], out: Path) -> None:
    import httpx
    with open(out, "wb") as f:
        for rel in rel_parts:
            with httpx.stream("GET", f"{RAW}/{rel}", timeout=600, follow_redirects=True) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes():
                    f.write(chunk)


def post() -> None:
    import httpx

    from scripts import daily_job as dj

    os.environ.pop("POST_MODE", None)
    r = httpx.get(f"{RAW}/queue.json", timeout=60, follow_redirects=True)
    if r.status_code == 404:
        print("the review queue is empty - nothing to post", flush=True)
        return
    queue = r.raise_for_status().json()
    ok = json.loads(APPROVED.read_text(encoding="utf-8")) if APPROVED.exists() else {}
    by_n = {q["n"]: q for q in queue}
    by_tid = {q["trend_id"]: q for q in queue}

    def pick(xs):
        return [by_n.get(int(x)) if str(x).isdigit() else by_tid.get(x) for x in xs]

    poster = dj.Poster()
    games_file = Path(os.environ.get("MEDIA_OUTPUT_DIR", "media_output")) / "game_videos.json"
    for q in [q for q in pick(ok.get("reject", [])) if q]:
        targets = (dj.longv.default_platforms(q["spec"]) if q["kind"] == "long"
                   else poster.targets(q.get("category", "gaming"), q.get("platforms")))
        dj.store.clear_hold(q["trend_id"])
        for name in targets:
            if not dj.store.already_posted(q["trend_id"], name):
                dj.store.record_post(q["trend_id"], name, "skip:rejected", False)
        print(f"#{q['n']} rejected", flush=True)
    for q in [q for q in pick(ok.get("approve", [])) if q]:
        tid, cat = q["trend_id"], q.get("category", "gaming")
        targets = (dj.longv.default_platforms(q["spec"]) if q["kind"] == "long" else poster.targets(cat, q.get("platforms")))
        dj.store.clear_hold(tid)  # hold:review markers (strike-time YouTube holds too) - the owner said yes now
        open_ = [t for t in targets if not dj.store.already_posted(tid, t)]
        if not open_:
            continue
        print(f"#{q['n']} {tid} -> {', '.join(open_)}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / f"{q['n']}.mp4"
            _fetch(q["files"], video)
            if q["kind"] == "long":
                entry = dict(q["entry"], video=str(video))
                if entry.get("thumbnail"):
                    th = Path(tmp) / Path(entry["thumbnail"]).name
                    _fetch([entry["thumbnail"]], th)
                    entry["thumbnail"] = str(th)
                dj.post_long(poster, q["spec"], entry, safety_ok=True)
            else:
                ig = [t for t in targets if t.startswith("instagram")]
                rest = [t for t in targets if t not in ig]
                if rest:
                    poster.post(tid, q["text"], q["tags"], str(video), cat, rest, safety_ok=True)
                if ig:
                    light = Path(tmp) / f"{q['n']}.light.mp4"
                    _fetch(q["light"] or q["files"], light)
                    poster.post(tid, q["text"], q["tags"], str(light), cat, ig, safety_ok=True)
        if q.get("evolution"):  # same record pokemon_daily writes, so the laptop never builds or posts it again
            games = json.loads(games_file.read_text(encoding="utf-8")) if games_file.exists() else {}
            games.setdefault(f"poke-{q['evolution']['id']}", {"kind": "motion", "spec": "backend/scripts/specs/pokemon_queue.json",
                                                               "video": "", "data": q["evolution"]})
            games_file.write_text(json.dumps(games, ensure_ascii=False, indent=1), encoding="utf-8")
    print(dj.HEALTH, flush=True)


# ------------------------------------------------------------------ laptop: forget what CI already handled

def sync() -> None:
    import sqlite3
    db = Path(tempfile.mkdtemp()) / "state.sqlite3"
    _git("fetch", "-q", "origin", "gaming-state", cwd=REPO)
    db.write_bytes(subprocess.run(["git", "show", "origin/gaming-state:pipeline_store.sqlite3"], cwd=REPO, check=True,
                                  capture_output=True).stdout)
    c = sqlite3.connect(db)
    # handled = at least one real post / skip and no platform still on hold (an Instagram post with a held YouTube
    # upload is still waiting for the owner)
    done = {r[0] for r in c.execute("select distinct trend_id from posted_trends where post_id is not null "
                                    "and post_id not like 'hold:%'")}
    done -= {r[0] for r in c.execute("select distinct trend_id from posted_trends where post_id like 'hold:%'")}
    c.close()
    for run in RUNS.glob("run_*"):
        q = run / "review_queue.json"
        if q.exists():
            held = json.loads(q.read_text(encoding="utf-8"))
            keep = {k: v for k, v in held.items() if k not in done}
            if len(keep) != len(held):
                q.write_text(json.dumps(keep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(run.name, "dropped", len(held) - len(keep), flush=True)
        rp = run / "pokemon" / "review_pending.json"
        if rp.exists():
            ids = json.loads(rp.read_text(encoding="utf-8"))
            keep = [i for i in ids if f"custom:motion-poke-{i}" not in done]
            if keep != ids:
                rp.write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
                print(run.name, "evolution dropped", sorted(set(ids) - set(keep)), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["push", "post", "sync"])
    ap.add_argument("sources", nargs="*", help="run folders for push, e.g. \"F:/AI AGENT/run_ov1\"")
    a = ap.parse_args()
    if a.mode == "push":
        if not a.sources:
            raise SystemExit("name the run folders to push (each video in them goes to the public branch)")
        push([Path(s) for s in a.sources])
    elif a.mode == "post":
        post()
    else:
        sync()
