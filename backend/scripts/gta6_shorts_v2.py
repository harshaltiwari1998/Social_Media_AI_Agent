"""GTA 6 shorts, edit v2 (owner 2026-11-26: "mujhe usme jyada se jyada views hona mangta" - chose: re-edit the best 100).
Data before: shorts with a story / Lucia moment averaged 330 views vs 42 for drives, and every short ran ~50 s.

Per short (best list in specs/gameplay/gta6_best100.json: plan, short number, hook):
  - the best 20-35 s of the planned clip: a window scored by game audio loudness + spoken dialogue (the game's own
    burnt-in English subtitles, read with RapidOCR at 2 fps); clips of 35 s or less stay whole
  - a big HOOK line over the video for the first 2.4 s
  - the game's subtitles as big captions under the video (swear words masked)
  - same look otherwise: blurred fill, GTA VI logo, title on top, footer
The old file is kept in <plan>/_v1/, the new one replaces short_NN.mp4 (its .light / NudeNet caches are removed so
they are made again). Usage: gta6_shorts_v2.py [--only plan:n] [--limit N] [--force]"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gameplay_clips as gc  # noqa: E402

SPECS = Path(__file__).resolve().parent / "specs" / "gameplay"
BEST = SPECS / "gta6_best100.json"
OUT = gc.OUT
FF = gc.FFMPEG if hasattr(gc, "FFMPEG") else None
MIN_S, MAX_S, TARGET_S = 20.0, 35.0, 30.0
HOOK_S = 2.4
SUB_TOP = 1385
SWEAR = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|asshole\w*|motherfuck\w*|cunt\w*|dick\w*|pussy\w*|bastard\w*)\b", re.I)


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def mask(text: str) -> str:
    return SWEAR.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


# ---------------------------------------------------------------- analysis
def audio_rms(src: str, start: float, dur: float) -> np.ndarray:
    """Loudness per 0.5 s of the clip (mono 8 kHz)."""
    raw = subprocess.run([ffmpeg(), "-v", "error", "-ss", str(start), "-t", str(dur), "-i", src, "-vn", "-ac", "1",
                          "-ar", "8000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n = int(len(a) // 4000)
    return np.array([np.sqrt(np.mean(a[i * 4000:(i + 1) * 4000] ** 2)) for i in range(n)]) if n else np.zeros(1)


_ocr = None


def subtitles(src: str, start: float, dur: float) -> list[tuple[float, float, str]]:
    """The game's burnt-in subtitle lines (bottom centre of the 16:9 frame) as (from, to, text), clip-relative."""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    x0, y0, w, h, face_x = 260, 880, 1400, 135, 1385  # same crop as gta6_launch/chapter1/ocr_subs.py (1920x1080)
    reads = []
    with tempfile.TemporaryDirectory(dir=r"F:\AI AGENT\tmp") as tmp:
        subprocess.run([ffmpeg(), "-v", "error", "-ss", str(start), "-t", str(dur), "-i", src, "-vf",
                        f"fps=2,scale=1920:1080,crop={w}:{h}:{x0}:{y0}", str(Path(tmp) / "c%04d.png")], check=True)
        for f in sorted(Path(tmp).glob("c*.png")):
            t = (int(f.stem[1:]) - 1) / 2
            boxes, _ = _ocr(str(f))
            rows = {}
            for box, txt, score in boxes or []:
                if float(score) < 0.75:
                    continue
                xs = [p[0] + x0 for p in box]
                ys = [p[1] + y0 for p in box]
                rows.setdefault(round(sum(ys) / 4 / 30), []).append((min(xs), max(xs), txt))
            kept = []
            for r in sorted(rows):
                parts = sorted(rows[r])
                for _ in range(2):
                    lo, hi = parts[0][0], max(p[1] for p in parts)
                    if abs((lo + hi) / 2 - 960) < 90:
                        kept.append(" ".join(p[2] for p in parts))
                        break
                    parts = [p for p in parts if p[0] < face_x]
                    if not parts:
                        break
            reads.append((t, " ".join(kept).strip()))
    cues: list[list] = []
    for t, txt in reads:
        if len(txt) < 3 or not re.search(r"[A-Za-z]{2}", txt):
            continue
        if cues and t - cues[-1][1] <= 0.6 and difflib.SequenceMatcher(None, cues[-1][2].lower(), txt.lower()).ratio() > 0.7:
            cues[-1][1] = t + 0.5
            if len(txt) > len(cues[-1][2]):
                cues[-1][2] = txt
        else:
            cues.append([t, t + 0.5, txt])
    return [(a, b, mask(c)) for a, b, c in cues if b - a >= 0.9]


def pick_window(dur: float, rms: np.ndarray, cues) -> tuple[float, float]:
    if dur <= MAX_S:
        return 0.0, dur
    length = TARGET_S
    loud = rms / (rms.max() or 1)
    talk = np.zeros_like(loud)
    for a, b, _ in cues:
        talk[int(a * 2):int(b * 2) + 1] = 1
    score = loud + 0.8 * talk[:len(loud)]
    best, best_s = 0.0, -1.0
    for s in np.arange(0, dur - length + 0.01, 0.5):
        i, j = int(s * 2), int((s + length) * 2)
        sc = float(score[i:j].mean()) + 0.15 * float(loud[i:i + 6].mean())  # a lively first 3 s helps the hook
        # never start or end in the middle of a subtitle line
        if any(a < s < b - 0.3 for a, b, _ in cues) or any(a + 0.3 < s + length < b for a, b, _ in cues):
            sc -= 0.25
        if sc > best_s:
            best, best_s = float(s), sc
    return best, best + length


# ---------------------------------------------------------------- overlays
def overlay_png(title: str, game: str, path: Path) -> None:
    """Like gameplay_clips.short_overlay, footer moved down to make room for the captions."""
    im = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    im.alpha_composite(gc.LOGOS[game](), (0, 20))
    d = ImageDraw.Draw(im)
    gc._text_block(d, title.upper(), ImageFont.truetype(gc.IMPACT, 92), 540, 270, 18, fill=gc.YELLOW, stroke=7)
    gc._text_block(d, f"Full stream on {gc.CHANNEL}", ImageFont.truetype(gc.BOLD, 40), 540, 1800, 40, stroke=4)
    im.save(path)


def hook_png(text: str, path: Path) -> None:
    im = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    band = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, 760, 1080, 1170), fill=(0, 0, 0, 120))
    im.alpha_composite(band)
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(gc.IMPACT, 128)
    lines = __import__("textwrap").wrap(text.upper(), 14)
    top = 965 - len(lines) * 140 // 2
    gc._text_block(d, text.upper(), font, 540, top, 14, fill=(255, 255, 255), stroke=10)
    im.save(path)


def sub_png(text: str, path: Path) -> None:
    im = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(gc.BOLD, 54)
    lines = __import__("textwrap").wrap(text, 30)[:3]
    gc._text_block(d, "\n".join(lines).replace("\n", " "), font, 540, SUB_TOP, 30, fill=(255, 255, 255), stroke=6)
    im.save(path)


# ---------------------------------------------------------------- render
def render(src: str, a: float, b: float, title: str, game: str, hook: str, cues, out: Path, work: Path) -> None:
    dur = b - a
    ov, hk = work / "ov.png", work / "hook.png"
    overlay_png(title, game, ov)
    hook_png(hook, hk)
    subs = [(max(0.0, x - a), min(dur, y - a), t) for x, y, t in cues if y > a + 0.3 and x < b - 0.3]
    inputs = ["-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", src, "-i", str(ov), "-i", str(hk)]
    end = dur - 0.6
    chain = ("[0:v]fps=30,split[a][b];[a]scale=-2:1920,crop=1080:1920,boxblur=24:2,eq=brightness=-0.12[bg];"
             "[b]scale=-2:820,crop=1080:820[fg];[bg][fg]overlay=0:550[v0];[v0][1:v]overlay=0:0[v1];"
             f"[v1][2:v]overlay=0:0:enable='lt(t,{HOOK_S})'[v2]")
    last = "v2"
    for k, (x, y, t) in enumerate(subs):
        p = work / f"sub{k:02d}.png"
        sub_png(t, p)
        inputs += ["-i", str(p)]
        chain += f";[{last}][{k + 3}:v]overlay=0:0:enable='between(t,{x:.2f},{y:.2f})'[s{k}]"
        last = f"s{k}"
    chain += f";[{last}]fade=t=in:st=0:d=0.25,fade=t=out:st={end:.2f}:d=0.6[v]"
    gc._encode([*inputs, "-filter_complex", chain, "-map", "[v]", "-map", "0:a",
                "-af", f"{gc.AUDIO},afade=t=out:st={end:.2f}:d=0.6", *gc.ENC], out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="redo shorts already in v2_done.json")
    a = ap.parse_args()
    best = json.loads(BEST.read_text(encoding="utf-8"))
    log_f = OUT / "_v2" / "edits.json"
    log_f.parent.mkdir(exist_ok=True)
    log = json.loads(log_f.read_text(encoding="utf-8")) if log_f.exists() else {}
    plans: dict[str, dict] = {}
    done = 0
    for item in best:
        name, n, hook = item["plan"], item["n"], item["hook"]
        key = f"{name}:{n}"
        if a.only and a.only != key:
            continue
        if key in log and not a.force:
            continue
        if a.limit and done >= a.limit:
            break
        plan = plans.setdefault(name, json.loads((SPECS / f"{name}.json").read_text(encoding="utf-8")))
        s = plan["shorts"][n - 1]
        src, start, dur = plan["source"], float(s["start"]), float(s.get("dur", 50))
        dest = OUT / name
        video = dest / f"short_{n:02d}.mp4"
        if not video.exists():
            print(f"{key}: no file (posted + deleted?) - skipped", flush=True)
            continue
        with tempfile.TemporaryDirectory(dir=r"F:\AI AGENT\tmp") as tmp:
            work = Path(tmp)
            cues = subtitles(src, start, dur)
            rms = audio_rms(src, start, dur)
            w0, w1 = pick_window(dur, rms, cues)
            new = work / "new.mp4"
            render(src, start + w0, start + w1, s["title"], plan["game"], hook,
                   [(x + start, y + start, t) for x, y, t in cues], new, work)
            bak = dest / "_v1" / video.name
            bak.parent.mkdir(exist_ok=True)
            if not bak.exists():
                shutil.copy2(video, bak)
            shutil.move(str(new), video)
        for extra in (video.with_suffix(".light.mp4"), video.with_suffix(".ig.mp4"), video.with_name(video.name + ".safety.json")):
            extra.unlink(missing_ok=True)
        shutil.rmtree(video.with_name(video.name + ".nsfw"), ignore_errors=True)
        log[key] = {"hook": hook, "window": [round(start + w0, 1), round(start + w1, 1)], "subs": len(cues)}
        log_f.write_text(json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8")
        done += 1
        print(f"{key} {s['title']}: {w1 - w0:.0f} s from +{w0:.0f}, {len(cues)} subtitle lines, hook '{hook}'", flush=True)


if __name__ == "__main__":
    main()
