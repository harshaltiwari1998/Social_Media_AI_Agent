"""Opens every evolution short that waits for the owner's OK (run_evo/pokemon/review_pending.json) in one VLC
playlist - evolution_job.cmd runs it after the nightly build. Nothing is posted here."""
import json
import subprocess
from pathlib import Path

CACHE = Path(__file__).resolve().parents[3] / "run_evo" / "pokemon"
VLC = Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe")
pending = CACHE / "review_pending.json"
ids = json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else []
videos = [str(CACHE / i / "final.mp4") for i in ids if (CACHE / i / "final.mp4").exists()]
print("waiting for the owner's OK:", ", ".join(ids) or "none", flush=True)
if videos and VLC.exists():
    subprocess.Popen([str(VLC), *videos])
