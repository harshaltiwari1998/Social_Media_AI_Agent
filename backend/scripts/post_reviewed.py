"""Owner review of held videos (owner rule 2026-11-16: nothing is posted without approval - the YouTube channel was
terminated over an adult game in a deal video, appealed back, and then got a community-guideline strike).

Videos built with POST_MODE=review (every laptop_batch.py run) wait in <run folder>/review_queue.json.
    post_reviewed.py <run folder> list                 # numbered list
    post_reviewed.py <run folder> show                 # open all held videos in one VLC playlist
    post_reviewed.py <run folder> approve 1,3,5        # post exactly those files (numbers or trend ids)
    post_reviewed.py <run folder> reject 2,4           # never post them (skip:rejected marker on every platform)
After approve / reject the run folder is merged into gaming-state by laptop_batch.py's merge step:
    laptop_batch.py <tag> --merge-only"""
import json
import os
import subprocess
import sys
from pathlib import Path

RUN = Path(sys.argv[1]).resolve()
os.environ["MEDIA_OUTPUT_DIR"] = str(RUN)
os.environ.pop("POST_MODE", None)  # this script is the only place that really posts
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import daily_job as dj  # noqa: E402

QUEUE = RUN / "review_queue.json"
VLC = Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe")


def _queue() -> dict:
    return json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {}


def _pick(arg: str) -> list[str]:
    ids = list(_queue())
    out = []
    for x in arg.split(","):
        x = x.strip()
        if x.isdigit() and 1 <= int(x) <= len(ids):
            out.append(ids[int(x) - 1])
        elif x in ids:
            out.append(x)
        elif x:
            print("not in the queue:", x)
    return out


def _title(e: dict) -> str:
    return (e["spec"]["title"] if e["kind"] == "long" else e["text"].split("\n")[0])[:90]


def main() -> None:
    cmd = sys.argv[2] if len(sys.argv) > 2 else "list"
    queue = _queue()
    if cmd == "list":
        for i, (tid, e) in enumerate(queue.items(), 1):
            warn = f"  !! {len(e['safety'])} FLAGGED FRAMES - see {Path(e['video']).name}.nsfw/" if e.get("safety") else ""
            print(f"{i:2d}. [{e['kind']}] {_title(e)}  ({tid}){warn}")
        print(f"{len(queue)} held")
    elif cmd == "show":
        vids = [e["video"] for e in queue.values() if Path(e["video"]).exists()]
        if vids:
            subprocess.Popen([str(VLC), *vids])
        print(f"opened {len(vids)} videos")
    elif cmd in ("approve", "reject"):
        poster = dj.Poster()
        for tid in _pick(sys.argv[3]):
            e = queue[tid]
            dj.store.clear_hold(tid)
            if cmd == "reject":
                targets = (dj.longv.default_platforms(e["spec"]) if e["kind"] == "long"
                           else poster.targets(e.get("category", "gaming"), e.get("platforms")))
                for name in targets:
                    if not dj.store.already_posted(tid, name):
                        dj.store.record_post(tid, name, "skip:rejected", False)
                print("rejected", _title(e))
            elif e["kind"] == "long":
                dj.post_long(poster, e["spec"], e["entry"], safety_ok=True)  # the owner saw it (and any flags)
                print("posted", _title(e))
            else:
                poster.post(tid, e["text"], e["tags"], e["video"], e.get("category", "gaming"), e.get("platforms"),
                            safety_ok=True)  # the owner watched it (flagged frames included)
                print("posted", _title(e))
            queue.pop(tid)
            QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(dj.HEALTH)


if __name__ == "__main__":
    main()
