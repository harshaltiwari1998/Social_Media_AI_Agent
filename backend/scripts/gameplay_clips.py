"""Cut Shorts and highlight videos out of the user's own gameplay recordings in F:/AI AGENT/VIDEOS GAMING.

  scan  <video>        loudest/most active moments -> preview grids (to pick clips and write titles)
  build <plan.json>    renders shorts (1080x1920) and long highlight videos (1920x1080)
  upload <plan.json>   posts them to the HarryBeast channel (once each, tracked in the pipeline store)

Plan JSON: {"name", "source", "game", "tags": [...],
            "shorts": [{"start": sec, "dur": sec, "title": "..."}],
            "longs":  [{"title", "description", "segments": [{"start", "dur", "label"}]}]}
Output: F:/AI AGENT/gameplay_out/<name>/short_XX.mp4, long_X.mp4 (+ thumbnails).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FF = imageio_ffmpeg.get_ffmpeg_exe()
OUT = Path("F:/AI AGENT/gameplay_out")
IMPACT = "C:/Windows/Fonts/impact.ttf"
BOLD = "C:/Windows/Fonts/arialbd.ttf"
CHANNEL = "HarryBeast Gaming"
YELLOW = (255, 214, 0)


def _run(args: list[str]) -> None:
    subprocess.run([FF, "-v", "error", "-y", *args], check=True)


def _encode(args: list[str], out: Path) -> None:
    """Write to a temp name and rename when done, so an interrupted render never looks finished."""
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    _run([*args, str(tmp)])
    tmp.replace(out)


# ---------- scan ----------
def loudness(src: str) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    sec = len(a) // 8000
    return 20 * np.log10(np.sqrt((a[: sec * 8000].reshape(sec, 8000) ** 2).mean(1)) + 1e-6)


def scan(src: str, out_dir: Path, count: int = 36, window: int = 45, gap: int = 150) -> list[int]:
    db = loudness(src)
    mean = np.convolve(db, np.ones(window) / window, "valid")
    spread = np.array([db[i:i + window].std() for i in range(len(mean))])
    score = mean + 0.5 * spread                     # loud + bursty = action
    score[:60] = score[-90:] = -999
    picks: list[int] = []
    for i in np.argsort(-score):
        if score[i] < -900 or len(picks) >= count:
            break
        if all(abs(i - p) > gap for p in picks):
            picks.append(int(i))
    picks.sort()
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = out_dir / "_f.jpg"
    rows = []
    for t in picks:
        row = Image.new("RGB", (1440, 270))
        for j, off in enumerate((5, 20, 38)):
            _run(["-ss", str(t + off), "-i", src, "-frames:v", "1", "-vf", "scale=480:-2", str(frame)])
            row.paste(Image.open(frame), (480 * j, 0))
        ImageDraw.Draw(row).text((10, 10), f"t={t}", fill="yellow")
        rows.append(row)
    for g in range(0, len(rows), 6):
        grid = Image.new("RGB", (1440, 270 * 6))
        for j, row in enumerate(rows[g:g + 6]):
            grid.paste(row, (0, 270 * j))
        grid.save(out_dir / f"grid{g // 6}.jpg", quality=75)
    frame.unlink(missing_ok=True)
    (out_dir / "picks.json").write_text(json.dumps(picks))
    return picks


# ---------- overlays ----------
def _text_block(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, cx: int, top: int,
                width_chars: int, fill=(255, 255, 255), stroke: int = 6) -> int:
    for line in textwrap.wrap(text, width_chars):
        w = draw.textlength(line, font=font)
        draw.text((cx - w / 2, top), line, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
        top += int(font.size * 1.12)
    return top


def short_overlay(title: str, game: str, path: Path) -> None:
    im = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    badge = ImageFont.truetype(IMPACT, 54)
    label = game.upper()
    w = d.textlength(label, font=badge)
    d.rounded_rectangle((540 - w / 2 - 28, 150, 540 + w / 2 + 28, 230), 18, fill=(200, 16, 16))
    d.text((540 - w / 2, 160), label, font=badge, fill="white")
    _text_block(d, title.upper(), ImageFont.truetype(IMPACT, 92), 540, 270, 18, fill=YELLOW, stroke=7)
    foot = ImageFont.truetype(BOLD, 42)
    _text_block(d, f"Full stream on {CHANNEL}", foot, 540, 1530, 40, stroke=4)
    im.save(path)


def lower_third(label: str, path: Path) -> None:
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(IMPACT, 64)
    w = d.textlength(label.upper(), font=font)
    d.rectangle((60, 860, 60 + w + 60, 960), fill=(0, 0, 0, 190))
    d.rectangle((60, 860, 72, 960), fill=(200, 16, 16))
    d.text((100, 872), label.upper(), font=font, fill=YELLOW)
    im.save(path)


def thumbnail(src: str, at: float, text: str, path: Path) -> None:
    frame = path.with_suffix(".src.jpg")
    _run(["-ss", str(at), "-i", src, "-frames:v", "1", "-vf", "scale=1280:720", str(frame)])
    im = Image.open(frame).convert("RGB")
    shade = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle((0, 380, 1280, 720), fill=(0, 0, 0, 150))
    im = Image.alpha_composite(im.convert("RGBA"), shade.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(im)
    _text_block(d, text.upper(), ImageFont.truetype(IMPACT, 104), 640, 420, 20, fill=YELLOW, stroke=8)
    im.convert("RGB").save(path, quality=92)
    frame.unlink(missing_ok=True)


# ---------- render ----------
AUDIO = "loudnorm=I=-14:TP=-1.5:LRA=11"
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart"]


def render_short(src: str, start: float, dur: float, overlay: Path, out: Path) -> None:
    """16:9 gameplay, zoomed so the action fills the middle, over a blurred copy of itself, title on top."""
    end = dur - 0.6
    vf = ("[0:v]fps=30,split[a][b];"
          "[a]scale=-2:1920,crop=1080:1920,boxblur=24:2,eq=brightness=-0.12[bg];"
          "[b]scale=-2:820,crop=1080:820[fg];"
          "[bg][fg]overlay=0:550[v1];[v1][1:v]overlay=0:0,"
          f"fade=t=in:st=0:d=0.3,fade=t=out:st={end}:d=0.6[v]")
    _encode(["-ss", str(start), "-t", str(dur), "-i", src, "-i", str(overlay), "-filter_complex", vf,
          "-map", "[v]", "-map", "0:a", "-af", f"{AUDIO},afade=t=out:st={end}:d=0.6", *ENC], out)


def render_segment(src: str, start: float, dur: float, overlay: Path, out: Path) -> None:
    end = dur - 0.5
    vf = (f"[0:v]fps=30,scale=1920:1080[g];[g][1:v]overlay=0:0:enable='lt(t,5)',"
          f"fade=t=in:st=0:d=0.5,fade=t=out:st={end}:d=0.5[v]")
    _encode(["-ss", str(start), "-t", str(dur), "-i", src, "-i", str(overlay), "-filter_complex", vf,
          "-map", "[v]", "-map", "0:a", "-af", f"{AUDIO},afade=t=in:d=0.5,afade=t=out:st={end}:d=0.5", *ENC], out)


def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build(plan: dict, only: str | None = None) -> Path:
    src, game = plan["source"], plan["game"]
    dest = OUT / plan["name"]
    work = dest / "_work"
    work.mkdir(parents=True, exist_ok=True)
    if only in (None, "shorts"):
        for n, s in enumerate(plan.get("shorts", []), 1):
            out = dest / f"short_{n:02d}.mp4"
            if out.exists():
                continue
            print(f"short {n:02d}: {s['title']}", flush=True)
            ov = work / f"short_{n:02d}.png"
            short_overlay(s["title"], game, ov)
            render_short(src, s["start"], s.get("dur", 50), ov, out)
    if only in (None, "longs"):
        for n, lv in enumerate(plan.get("longs", []), 1):
            out = dest / f"long_{n}.mp4"
            if out.exists():
                continue
            print(f"long {n}: {lv['title']}", flush=True)
            parts, chapters, t = [], [], 0.0
            for k, seg in enumerate(lv["segments"]):
                ov = work / f"lt_{n}_{k:02d}.png"
                lower_third(seg["label"], ov)
                part = work / f"seg_{n}_{k:02d}.mp4"
                if not part.exists():
                    render_segment(src, seg["start"], seg["dur"], ov, part)
                parts.append(part)
                chapters.append(f"{_ts(t)} {seg['label']}")
                t += seg["dur"]
            listing = work / f"concat_{n}.txt"
            listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
            _encode(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart"], out)
            (dest / f"long_{n}_chapters.txt").write_text("\n".join(chapters), encoding="utf-8")
            best = lv.get("thumb_at", lv["segments"][0]["start"] + 10)
            thumbnail(src, best, lv.get("thumb_text", lv["title"]), dest / f"long_{n}_thumb.jpg")
    return dest


# ---------- upload ----------
def upload(plan: dict, limit: int | None = None, long_limit: int | None = None,
           short_limit: int | None = None) -> dict[str, int]:
    """Post unposted clips (longs first); returns how many longs/shorts went up. The limits cap one run, so a
    stream's clips spread over several days - a burst of dozens of uploads in one day gets far less reach."""
    from app.pipeline import store
    from app.pipeline.orchestrator import _POSTERS

    yt = _POSTERS["youtube"]
    dest = OUT / plan["name"]
    tags = plan.get("tags", [])
    done = 0
    posted = {"long": 0, "short": 0}
    caps = {"long": long_limit, "short": short_limit}
    tag = plan.get("title_tag", "")
    jobs = [("long", n, lv) for n, lv in enumerate(plan.get("longs", []), 1)]
    jobs += [("short", n, s) for n, s in enumerate(plan.get("shorts", []), 1)]
    for kind, n, item in jobs:
        if limit is not None and done >= limit:
            break
        if caps[kind] is not None and posted[kind] >= caps[kind]:
            continue
        trend_id = f"gameplay:{plan['name']}:{kind}{n:02d}"
        if store.already_posted(trend_id, "youtube"):
            continue
        video = dest / (f"short_{n:02d}.mp4" if kind == "short" else f"long_{n}.mp4")
        if not video.exists():
            print(f"missing {video} - run build first")
            continue
        if kind == "short":
            text = (f"{item['title']}\n{plan['game']} gameplay from my live stream - full stream on the channel.\n"
                    + " ".join(f"#{t.replace(' ', '')}" for t in tags[:6]))
            result = yt.post_video(str(video), text, tags=tags)
        else:
            chapters = (dest / f"long_{n}_chapters.txt").read_text(encoding="utf-8")
            desc = f"{item.get('description', '')}\n\nChapters:\n{chapters}\n\n" + " ".join(f"#{t.replace(' ', '')}" for t in tags[:8])
            thumb = dest / f"long_{n}_thumb.jpg"
            result = yt.post_long_video(str(video), title, desc, tags, str(thumb) if thumb.exists() else None)
        store.record_post(trend_id, "youtube", result.post_id, result.dry_run)
        print(f"{kind} {n}: {'ok ' + (result.url or '') if result.success else 'FAILED ' + str(result.error)[:200]}", flush=True)
        if not result.success:
            if any(k in (result.error or "") for k in ("uploadLimitExceeded", "Quota exceeded", "rateLimitExceeded")):
                print("YouTube daily upload limit reached - run upload again tomorrow")
                return posted
            continue
        done += 1
        posted[kind] += 1
    return posted


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("video"); s.add_argument("--out", required=True)
    b = sub.add_parser("build"); b.add_argument("plan"); b.add_argument("--only", choices=["shorts", "longs"])
    u = sub.add_parser("upload"); u.add_argument("plan"); u.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.cmd == "scan":
        print(scan(args.video, Path(args.out)))
        return
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if args.cmd == "build":
        print(build(plan, args.only))
    else:
        upload(plan, args.limit)


if __name__ == "__main__":
    main()
