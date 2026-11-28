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


def gta6_logo() -> Image.Image:
    """Our own "GTA VI" logo for the GTA 6 shorts (owner 2026-11-23 picked design A over the plain red "GTA 6" badge):
    white "GTA" + "VI" in a Vice City sunset gradient (yellow -> orange -> pink -> purple) with a soft pink glow.
    Not Rockstar's official logo (trademark). 1080x260, transparent."""
    import numpy as np

    big = ImageFont.truetype(IMPACT, 150)
    im = Image.new("RGBA", (1080, 260), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w1, w2 = d.textlength("GTA ", font=big), d.textlength("VI", font=big)
    x, y = 540 - (w1 + w2) / 2, 40
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).text((x + w1, y), "VI", font=big, fill=255)
    glow = Image.new("RGBA", im.size, (255, 60, 150, 0))
    glow.putalpha(mask.filter(ImageFilter.GaussianBlur(18)))
    im.alpha_composite(glow)
    d = ImageDraw.Draw(im)
    for text, at in (("GTA", x), ("VI", x + w1)):
        d.text((at, y), text, font=big, fill="white", stroke_width=8, stroke_fill="black")
    stops = [(0, (255, 214, 90)), (0.35, (255, 214, 90)), (0.55, (255, 110, 70)), (0.7, (255, 50, 140)),
             (0.85, (150, 60, 255)), (1, (150, 60, 255))]
    ys = np.linspace(0, 1, im.height)
    rows = np.array([np.interp(ys, [p for p, _ in stops], [c[k] for _, c in stops]) for k in range(3)]).T
    grad = Image.fromarray(np.repeat(rows[:, None, :], im.width, axis=1).astype("uint8"), "RGB").convert("RGBA")
    grad.putalpha(mask)
    im.alpha_composite(grad)
    return im


GTA6_OFFICIAL = Path(__file__).resolve().parent.parent / "assets" / "gta6_logo_official.png"


def gta6_logo_official() -> Image.Image:
    """The official GTA VI logo (owner 2026-11-28: "GTA 6 wale shorts me GTA 6 ka original logo lagao"). Wikimedia
    Commons "Grand Theft Auto VI logo (with gradient).svg" (marked public domain - too simple for copyright, still
    Rockstar's trademark), 230 px tall, centred in the same 1080x260 box. Falls back to our own design."""
    if not GTA6_OFFICIAL.exists():
        return gta6_logo()
    logo = Image.open(GTA6_OFFICIAL).convert("RGBA")
    logo = logo.resize((round(logo.width * 230 / logo.height), 230), Image.LANCZOS)
    im = Image.new("RGBA", (1080, 260), (0, 0, 0, 0))
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 255), (540 - logo.width // 2 + 6, 22), logo.getchannel("A"))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
    im.alpha_composite(logo, (540 - logo.width // 2, 15))
    return im


LOGOS = {"GTA 6": gta6_logo_official}  # game name -> logo drawn instead of the red text badge
OLD_GTA6_BOX = (340, 58, 420, 190)  # x, y, w, h around our old "GTA VI" badge (349-748 x 69-236) on rendered shorts
OFFICIAL_SINCE = 1795870226  # 2026-11-28 18:20 IST: every short written after this was drawn with the official logo


def has_official_logo(video: Path) -> bool:
    """Rendered after the switch, or swapped by .cache/gta6_logo_swap.py (listed in <plan>/logo_official_done.txt)."""
    video = Path(video)
    done = video.parent / "logo_official_done.txt"
    if done.exists() and video.name in done.read_text().split():
        return True
    return video.exists() and video.stat().st_mtime >= OFFICIAL_SINCE


def swap_gta6_logo(src: Path, dst: Path) -> None:
    """Replace our old "GTA VI" badge on an already rendered GTA 6 short with the official logo, without the source
    recording (launch / day 2 / day 3 recordings are deleted) and keeping v2 re-edits (hook, captions) as they are:
    ffmpeg delogo erases the badge (it sits on the blurred background band) and the logo is laid over it."""
    import imageio_ffmpeg

    logo = Path(dst).with_suffix(".logo.png")
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    canvas.alpha_composite(gta6_logo_official(), (0, 20))
    canvas.save(logo)
    x, y, w, h = OLD_GTA6_BOX
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"]  # ~10 s a short; NVENC doubled the file size
    try:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src), "-i", str(logo),
                        "-filter_complex", f"[0:v]delogo=x={x}:y={y}:w={w}:h={h}[a];[a][1:v]overlay=0:0[v]",
                        "-map", "[v]", "-map", "0:a?", *enc, "-pix_fmt", "yuv420p", "-c:a", "copy",
                        "-movflags", "+faststart", str(dst)], check=True)
    finally:
        logo.unlink(missing_ok=True)


def short_overlay(title: str, game: str, path: Path) -> None:
    im = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if game in LOGOS:
        im.alpha_composite(LOGOS[game](), (0, 20))
        d = ImageDraw.Draw(im)
    else:
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
    # clips posted on every platform are deleted to free F: (owner 2026-11-26) - never render those again
    gone = set((dest / "deleted_posted.txt").read_text().split()) if (dest / "deleted_posted.txt").exists() else set()
    if only in (None, "shorts"):
        for n, s in enumerate(plan.get("shorts", []), 1):
            out = dest / f"short_{n:02d}.mp4"
            if out.exists() or out.name in gone:
                continue
            print(f"short {n:02d}: {s['title']}", flush=True)
            ov = work / f"short_{n:02d}.png"
            short_overlay(s["title"], game, ov)
            render_short(src, s["start"], s.get("dur", 50), ov, out)
    if only in (None, "longs"):
        for n, lv in enumerate(plan.get("longs", []), 1):
            out = dest / f"long_{n}.mp4"
            if out.exists() or out.name in gone:
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
# Shorts go to the gaming YouTube channel and the gaming Facebook Page + Instagram; long videos skip Instagram.
TARGETS = {"short": ("youtube", "facebook_gaming", "instagram_gaming"), "long": ("youtube", "facebook_gaming")}
_YT_LIMIT = ("uploadLimitExceeded", "Quota exceeded", "rateLimitExceeded")


def upload(plan: dict, limit: int | None = None, long_limit: int | None = None,
           short_limit: int | None = None) -> dict[str, int]:
    """Post unposted clips (longs first); returns how many longs/shorts went up on YouTube. The limits cap one run,
    so a stream's clips spread over several days - a burst of dozens of uploads in one day gets far less reach."""
    from app.pipeline import store
    from app.pipeline.orchestrator import _POSTERS

    dest = OUT / plan["name"]
    tags = plan.get("tags", [])
    hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:6])
    tag = plan.get("title_tag", "")
    done = 0
    posted = {"long": 0, "short": 0}
    caps = {"long": long_limit, "short": short_limit}
    yt_blocked = False
    jobs = [("long", n, lv) for n, lv in enumerate(plan.get("longs", []), 1)]
    jobs += [("short", n, s) for n, s in enumerate(plan.get("shorts", []), 1)]
    for kind, n, item in jobs:
        if limit is not None and done >= limit:
            break
        if caps[kind] is not None and posted[kind] >= caps[kind]:
            continue
        trend_id = f"gameplay:{plan['name']}:{kind}{n:02d}"
        todo = [p for p in TARGETS[kind] if _POSTERS[p].is_configured() and not store.already_posted(trend_id, p)]
        if not todo:
            continue
        video = dest / (f"short_{n:02d}.mp4" if kind == "short" else f"long_{n}.mp4")
        if not video.exists():
            print(f"missing {video} - run build first")
            continue
        from app.media import safety
        if safety.scan_video(video):  # owner 2026-11-16: no nudity, ever - held for the owner, never posted
            for p in todo:
                store.record_post(trend_id, p, store.HOLD, False)
            print(f"{kind} {n}: HELD - flagged frames in {video.name}.nsfw/", flush=True)
            continue
        title = item["title"]
        if tag and tag.lower() not in title.lower():
            title = f"{title} | {tag}"  # the game's name in the title - it is what people search for
        if kind == "short":
            text = f"{title}\n{plan['game']} gameplay from my live stream - full stream on the channel.\n{hashtags}"
        else:
            chapters = (dest / f"long_{n}_chapters.txt").read_text(encoding="utf-8")
            text = (f"{item.get('description', '')}\n\nChapters:\n{chapters}\n\n"
                    + " ".join(f"#{t.replace(' ', '')}" for t in tags[:8]))
        for platform in todo:
            if platform == "youtube" and yt_blocked:
                continue
            if platform == "facebook_gaming" and not store.fb_gaming_allowed(trend_id):
                print(f"{kind} {n} facebook_gaming: skipped (2 own clips a day on the Page)", flush=True)
                continue
            poster = _POSTERS[platform]
            if platform == "youtube" and kind == "long":
                thumb = dest / f"long_{n}_thumb.jpg"
                result = poster.post_long_video(str(video), title, text, tags, str(thumb) if thumb.exists() else None)
            elif kind == "long":
                result = poster.post_video(str(video), f"{title}\n\n{text}", tags=tags)
            else:
                result = poster.post_video(str(video), text, tags=tags)
            store.record_post(trend_id, platform, result.post_id, result.dry_run)
            print(f"{kind} {n} {platform}: {'ok ' + (result.url or result.post_id or '') if result.success else 'FAILED ' + str(result.error)[:200]}", flush=True)
            if platform == "youtube":
                if result.success:
                    done += 1
                    posted[kind] += 1
                elif any(k in (result.error or "") for k in _YT_LIMIT):
                    print("YouTube daily upload limit reached - the rest goes up next run")
                    yt_blocked = True
        if yt_blocked and all(store.already_posted(trend_id, p) for p in todo if p != "youtube"):
            # YouTube is done for today; stop once the other platforms have caught up on this clip
            break
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
