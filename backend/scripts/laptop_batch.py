"""Laptop batch of regular spec shorts: laptop_batch.py <tag> <daily_job.py options...>
A fresh run folder F:/AI AGENT/run_<tag> seeded from the latest gaming-state, daily_job.py with --from-end (so it does
not take the specs a scheduled run is about to pick), then merge_state.py into gaming-state (5 tries).
    laptop_batch.py ani10 --extra-slot aniimo:10
    laptop_batch.py pal10 --extra-slot palguide:5 --new-palworld 5
    laptop_batch.py evo --evolution --max-shorts 3     # pokemon_daily.py instead (evolution_job.cmd, 20:15 task):
the run_evo folder is kept between runs (half-made lines continue), clips on motion-cache are taken over.
A run whose merge failed leaves .unmerged in its folder - the next run merges that first."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = str(REPO / "backend" / ".venv" / "Scripts" / "python.exe")
tag, extra = sys.argv[1], sys.argv[2:]
RUN, WT = REPO.parent / f"run_{tag}", REPO.parent / f"wt_{tag}"


def git(*a, cwd=REPO, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check, capture_output=True, text=True)


def merge() -> bool:
    """Run folder -> latest gaming-state. A workflow may push at the same moment: fetch, merge, push again."""
    for attempt in range(5):
        try:
            git("worktree", "remove", "--force", str(WT), check=False)
            git("worktree", "prune")
            git("fetch", "-q", "origin", "gaming-state")
            git("worktree", "add", "-q", "-f", "--detach", str(WT), "origin/gaming-state")
            subprocess.run([PY, "backend/scripts/merge_state.py", str(RUN), str(WT)], cwd=REPO, check=True,
                           capture_output=True)
            git("add", "-A", cwd=WT)
            if git("diff", "--cached", "--quiet", cwd=WT, check=False).returncode:
                git("commit", "-q", "-m", f"Laptop batch {tag}", cwd=WT)
                git("push", "-q", "origin", "HEAD:gaming-state", cwd=WT)
            git("worktree", "remove", "--force", str(WT), check=False)
            UNMERGED.unlink(missing_ok=True)
            print("MERGED into gaming-state", flush=True)
            return True
        except subprocess.CalledProcessError as exc:
            print(f"merge try {attempt + 1} failed: {str(exc.stderr)[:200]}", flush=True)
            time.sleep(30 * (attempt + 1))
    print(f"MERGE FAILED - run folder kept: {RUN}", flush=True)
    return False


UNMERGED = RUN / ".unmerged"  # posts of an earlier run that never reached gaming-state: merge them before anything
if UNMERGED.exists() and not merge():  # else is overwritten (they would be posted a second time)
    sys.exit(2)
EVO = extra[:1] == ["--evolution"]
if not EVO:  # the evolution folder is kept: a line the quota stopped half-way continues from its clips
    shutil.rmtree(RUN, ignore_errors=True)
RUN.mkdir(parents=True, exist_ok=True)
git("fetch", "-q", "origin", "gaming-state")
for f in ("pipeline_store.sqlite3", "game_videos.json", "palworld_videos.json"):
    (RUN / f).write_bytes(subprocess.run(["git", "show", f"origin/gaming-state:{f}"], cwd=REPO, check=True,
                                         capture_output=True).stdout)
art = REPO / "backend" / "media_output" / "game_art"  # cached pictures / trailers - saves the downloads
if art.exists() and not EVO:
    shutil.copytree(art, RUN / "game_art")
args = {"--new-games": "0", "--classic-games": "0", "--new-palworld": "0", "--new-movies": "0", "--movie-reviews": "0"}
rest, i = [], 0
while i < len(extra):
    if extra[i] in args:
        args[extra[i]] = extra[i + 1]
        i += 2
    else:
        rest.append(extra[i])
        i += 1
cmd = [PY, "scripts/daily_job.py", "--skip-trends", "--from-end", *[x for kv in args.items() for x in kv], *rest]
if EVO:  # laptop_batch.py evo --evolution --max-shorts 3: what daily-pokemon.yml does
    git("fetch", "-q", "origin", "motion-cache")  # clips an earlier run already made
    tar = subprocess.run(["git", "archive", "origin/motion-cache", "pokemon"], cwd=REPO, check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-k", "-C", str(RUN)], input=tar.stdout, check=False)  # -k: keep newer local clips
    cmd = [PY, "scripts/pokemon_daily.py", *extra[1:]]
env = {**os.environ, "MEDIA_OUTPUT_DIR": str(RUN), "YOUTUBE_NONINTERACTIVE": "1", "PYTHONIOENCODING": "utf-8",
       "PYTHONUNBUFFERED": "1"}
print(time.strftime("%Y-%m-%d %H:%M"), "RUN", " ".join(cmd[1:]), flush=True)
UNMERGED.touch()
code = subprocess.run(cmd, cwd=REPO / "backend", env=env).returncode
print("daily_job exit", code, flush=True)
if not merge():
    sys.exit(2)
sys.exit(code)
