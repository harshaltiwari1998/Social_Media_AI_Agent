"""Gameplay clips posted from GitHub Actions, so the laptop only has to render them once.

The recordings stay on the laptop (F:/AI AGENT/VIDEOS GAMING) - the cloud cannot read them - so:

  push <plan.json>   laptop: after gameplay_clips.build, put the rendered clips of a plan with "cloud": true on its own
                     orphan branch clips/<name> (files over 45 MB split into parts - GitHub refuses files over 100 MB),
                     plus posted.json = what the laptop already posted for that plan (so CI never posts it again).
  post               CI (daily-gameplay.yml): for every clips/* branch, rebuild the files, post the next clips within
                     the daily caps (1 long + 8 shorts in total, like gameplay_daily) and list fully posted plans in
                     media_output/gameplay_done.txt so the workflow deletes their branch.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import gameplay_clips as gc  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline import store  # noqa: E402

REPO = ROOT.parent
PLANS = ROOT / "scripts" / "specs" / "gameplay"
PART = 45 * 1024 * 1024
DAILY_LONGS, DAILY_SHORTS = 1, 8


def branch(name: str) -> str:
    return f"clips/{name}"


def _git(*args: str, cwd: Path = REPO, capture: bool = False) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=capture, text=True)
    return r.stdout if capture else ""


def items(plan: dict) -> list[tuple[str, int, tuple[str, ...]]]:
    out = [("long", n, gc.TARGETS["long"]) for n in range(1, len(plan.get("longs", [])) + 1)]
    return out + [("short", n, gc.TARGETS["short"]) for n in range(1, len(plan.get("shorts", [])) + 1)]


def trend_id(plan: dict, kind: str, n: int) -> str:
    return f"gameplay:{plan['name']}:{kind}{n:02d}"


def fully_posted(plan: dict) -> bool:
    return all(store.already_posted(trend_id(plan, k, n), p) for k, n, targets in items(plan) for p in targets)


def push(plan: dict) -> bool:
    """Laptop side. Returns True once the branch exists on GitHub."""
    name = plan["name"]
    src = gc.OUT / name
    marker = src / ".pushed"  # CI deletes the branch once everything is posted - never push it again after that
    if marker.exists() or _git("ls-remote", "--heads", "origin", branch(name), capture=True).strip():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return True
    files = [src / f"long_{n}{s}" for n in range(1, len(plan.get("longs", [])) + 1)
             for s in (".mp4", "_thumb.jpg", "_chapters.txt")]
    files += [src / f"short_{n:02d}.mp4" for n in range(1, len(plan.get("shorts", [])) + 1)]
    missing = [f.name for f in files if not f.exists() and not f.name.endswith("_thumb.jpg")]
    if missing:
        print(f"{name}: not fully rendered yet ({len(missing)} files missing) - not pushing")
        return False
    with tempfile.TemporaryDirectory(dir=gc.OUT) as tmp:
        wt = Path(tmp) / "wt"
        _git("worktree", "add", "--orphan", "-b", branch(name), str(wt))
        try:
            for f in files:
                if not f.exists():
                    continue
                if f.stat().st_size <= PART:
                    shutil.copy2(f, wt / f.name)
                    continue
                with f.open("rb") as fh:  # byte split; post() joins the parts back in order
                    k = 0
                    while chunk := fh.read(PART):
                        (wt / f"{f.name}.part{k:02d}").write_bytes(chunk)
                        k += 1
            rows = [{"trend_id": trend_id(plan, k, n), "platform": p}
                    for k, n, targets in items(plan) for p in targets if store.already_posted(trend_id(plan, k, n), p)]
            (wt / "posted.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
            _git("add", "-A", cwd=wt)
            _git("commit", "-qm", f"Rendered clips for {name}", cwd=wt)
            _git("push", "-q", "origin", f"HEAD:refs/heads/{branch(name)}", cwd=wt)
        finally:
            _git("worktree", "remove", "--force", str(wt))
            _git("branch", "-D", branch(name))
    marker.touch()
    print(f"{name}: pushed to {branch(name)}")
    return True


def _restore(name: str, dest: Path) -> None:
    """CI side: download clips/<name> and join split files."""
    _git("fetch", "-q", "--depth", "1", "origin", branch(name))
    dest.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(["git", "archive", "FETCH_HEAD"], cwd=REPO, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
    for first in sorted(dest.glob("*.part00")):
        target = dest / first.name[: -len(".part00")]
        with target.open("wb") as out:
            for part in sorted(dest.glob(f"{target.name}.part*")):
                out.write(part.read_bytes())
                part.unlink()


def post() -> None:
    heads = _git("ls-remote", "--heads", "origin", "clips/*", capture=True).split()
    names = sorted(h.removeprefix("refs/heads/clips/") for h in heads if h.startswith("refs/heads/clips/"))
    budget, done = {"long": DAILY_LONGS, "short": DAILY_SHORTS}, []
    work = Path(tempfile.mkdtemp(prefix="gameplay-"))
    gc.OUT = work  # gameplay_clips.upload reads OUT / <name> / short_XX.mp4 ...
    for name in names:
        path = PLANS / f"{name}.json"
        if not path.exists():
            print(f"{name}: no plan in {PLANS.name}/ - skipping")
            continue
        plan = json.loads(path.read_text(encoding="utf-8"))
        _restore(name, work / name)
        for row in json.loads((work / name / "posted.json").read_text(encoding="utf-8")):
            if not store.already_posted(row["trend_id"], row["platform"]):
                store.record_post(row["trend_id"], row["platform"], "laptop", False)  # posted by gameplay_daily
        if any(budget.values()):
            posted = gc.upload(plan, long_limit=budget["long"], short_limit=budget["short"]) or {}
            for kind in budget:
                budget[kind] -= posted.get(kind, 0)
        if fully_posted(plan):
            done.append(branch(name))
        shutil.rmtree(work / name, ignore_errors=True)
    out = Path(settings.media_output_dir) / "gameplay_done.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{b}\n" for b in done), encoding="utf-8")
    print(f"{len(names)} plan(s) on the cloud; fully posted: {', '.join(done) or 'none'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("push").add_argument("plan")
    sub.add_parser("post")
    args = ap.parse_args()
    if args.cmd == "push":
        push(json.loads(Path(args.plan).read_text(encoding="utf-8")))
    else:
        post()


if __name__ == "__main__":
    main()
