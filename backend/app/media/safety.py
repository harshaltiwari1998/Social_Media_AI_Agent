"""Nudity check of a finished video before it is posted or shown for approval (owner rule 2026-11-16, after a YouTube
community-guideline strike for a deal short whose Steam screenshot showed sexual content: "jo bhi videos aap banaoge
kisi games clips se to waha check kar lena ki koi nudity to nahi he").

scan_video() samples one frame a second, runs NudeNet (local onnx model) and returns every frame with an exposed
breast / genitalia / buttocks / anus detection. It FAILS CLOSED: if NudeNet or ffmpeg is missing the video counts as
unsafe, so nothing slips through on a machine without the model. Shirtless men (wrestlers, fighters) are sometimes
flagged as "breast" - a flagged video is held for the owner, never deleted or posted automatically."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("safety")
BAD = {"FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "FEMALE_BREAST_EXPOSED", "ANUS_EXPOSED",
       "BUTTOCKS_EXPOSED"}
MIN_SCORE = 0.40
_det = None


def _detector():
    global _det
    if _det is None:
        from nudenet import NudeDetector
        _det = NudeDetector()
    return _det


def scan_video(video: str | Path, step: float = 1.0) -> list[dict]:
    """Cached per file (size + mtime) in <video>.safety.json. [] = clean. Otherwise one dict per flagged frame: {"t", "hits", "frame"}; the frames are kept next to the video in
    <video>.nsfw/ so the owner can look at them."""
    import json
    video = Path(video)
    cache = video.with_name(video.name + ".safety.json")
    stamp = f"{video.stat().st_size}-{int(video.stat().st_mtime)}" if video.exists() else ""
    if stamp and cache.exists():
        old = json.loads(cache.read_text(encoding="utf-8"))
        if old.get("stamp") == stamp:
            return old["flagged"]
    flagged = _scan(video, step)
    if stamp and not any(f["t"] < 0 for f in flagged):
        cache.write_text(json.dumps({"stamp": stamp, "flagged": flagged}), encoding="utf-8")
    return flagged


def _scan(video: Path, step: float) -> list[dict]:
    try:
        import imageio_ffmpeg
        det = _detector()
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - fail closed
        log.warning("safety check unavailable (%s) - treating %s as unsafe", exc, video.name)
        return [{"t": -1, "hits": [("CHECK_UNAVAILABLE", 1.0)], "frame": ""}]
    flagged = []
    keep = video.with_name(video.name + ".nsfw")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([ff, "-v", "error", "-i", str(video), "-vf", f"fps=1/{step},scale=640:-2", "-q:v", "3",
                        str(Path(tmp) / "f%05d.jpg")], check=False)
        frames = sorted(Path(tmp).glob("f*.jpg"))
        if not frames:
            return [{"t": -1, "hits": [("NO_FRAMES", 1.0)], "frame": ""}]
        for f in frames:
            hits = [(d["class"], round(d["score"], 2)) for d in det.detect(str(f))
                    if d["class"] in BAD and d["score"] >= MIN_SCORE]
            if hits:
                keep.mkdir(exist_ok=True)
                t = (int(f.stem[1:]) - 1) * step
                dst = keep / f"{t:07.1f}.jpg"
                shutil.copy(f, dst)
                flagged.append({"t": t, "hits": hits, "frame": str(dst)})
    if flagged:
        log.warning("safety: %s has %d flagged frame(s) - held for the owner", video.name, len(flagged))
    return flagged
