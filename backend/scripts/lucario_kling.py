"""Lucario Stone Series with real AI motion: a Kling/Hailuo clip pack + an assembler.

  pack:      python scripts/lucario_kling.py pack IMAGES_DIR PACK_DIR --video 1|2
             Writes PACK_DIR/start_frames/*.jpg (+ end frames) and PACK_DIR/KLING_PROMPTS.md: one
             entry per 5-second clip with the image(s) to upload and the motion prompt to paste.
             Generated clips go into PACK_DIR/clips/ named exactly as listed (e.g. 03_fire_transform.mp4).
  assemble:  python scripts/lucario_kling.py assemble IMAGES_DIR PACK_DIR OUT.mp4 --video 1|2 [--music FILE]
             Cuts the clips to the narration, adds white flashes on hits, yellow word captions, the
             voice-over, impact sounds and optional music. A clip that is still missing falls back to its
             still image with a zoom, so a partly finished pack can already be previewed.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import lucario_stone_video as sv  # noqa: E402
from scripts.lucario_stone_images import VIDEO_FORMS  # noqa: E402

W, H, FPS, SR = sv.W, sv.H, sv.FPS, 44100
CINE = "cinematic, smooth motion, high detail, consistent character, no text"

# per-form motion details (from the prompts file's Fill lines)
FILL = {
    "fire": ("flames and embers", "flames flicker on its fists", "orange"),
    "ice": ("ice shards and frost", "cold mist rises around it", "icy blue"),
    "thunder": ("lightning bolts", "sparks crackle over its body", "yellow"),
    "dark": ("dark smoke and purple sparks", "smoke drifts off its body", "blood red"),
    "dragon": ("golden energy and dragon scales", "its wings spread slowly", "gold"),
    "water": ("a water splash", "water swirls around its arms", "cyan"),
    "leaf": ("leaves and vines", "leaves float around it", "green"),
    "moon": ("moonlight and stars", "its silver mane flows gently", "pale blue"),
    "earth": ("rocks and dust", "small pebbles fall from its armor", "amber"),
    "sun": ("golden light rays", "its halo rotates and light shimmers", "white-gold"),
}


def clip_plan(video: int) -> list[dict]:
    """Ordered clips: id, start/end frame image names, prompt, role and relative share of the segment."""
    plan = [{"id": "00_hook", "start": f"hook{video}.jpg", "end": None, "seg": "hook", "share": 1.0, "hit": False,
             "prompt": "the creature clenches its fists, blue aura energy swirls around its body, the glowing stones on the "
                       "floor pulse with light, slow camera push in, " + CINE}]
    n = 1
    for key, name, stone, _burst, look, attack in VIDEO_FORMS[video]:
        element, idle, eye = FILL[key]
        plan += [
            {"id": f"{n:02d}_{key}_smash", "start": f"{key}_a.jpg", "end": None, "seg": key, "share": 1.1, "hit": True,
             "prompt": f"the creature stares down at {stone} on the floor, then punches down with a glowing blue aura fist and "
                       f"smashes it, the stone shatters into pieces and releases a burst of {element}, slow motion debris, "
                       f"camera shake, " + CINE},
            {"id": f"{n + 1:02d}_{key}_transform", "start": f"{key}_smash.jpg", "end": f"{key}_b.jpg", "seg": key, "share": 0.9, "hit": True,
             "prompt": f"{element} climbs up from its fist and covers its whole body, it transforms into {name}, {look}, "
                       f"particles flying, bright flash, " + CINE},
            {"id": f"{n + 2:02d}_{key}_hero", "start": f"{key}_b.jpg", "end": None, "seg": key, "share": 1.2, "hit": False,
             "prompt": f"the transformed warrior stands in a fighting stance, {idle}, slow camera push in towards its face "
                       f"and chest spike, eyes glow {eye}, " + CINE},
            {"id": f"{n + 3:02d}_{key}_attack", "start": f"{key}_b.jpg", "end": f"{key}_attack.jpg", "seg": key, "share": 1.2, "hit": True,
             "prompt": f"the warrior {attack}, the floor cracks, debris flies, camera shake, " + CINE},
        ]
        n += 4
    return plan


def pack(img_dir: Path, pack_dir: Path, video: int) -> None:
    frames = pack_dir / "start_frames"
    frames.mkdir(parents=True, exist_ok=True)
    (pack_dir / "clips").mkdir(exist_ok=True)
    lines = [f"# Lucario Stone Series - Video {video}: Kling / Hailuo clip list", "",
             "For EVERY clip below:",
             "1. Open Kling AI (klingai.com) -> Image to Video (or Hailuo -> Image to Video).",
             "2. Upload the **Start frame** image. If an **End frame** is listed, also upload it as the end frame "
             "(Kling: 'Start & End frames'). If your plan doesn't allow end frames, just use the start frame.",
             "3. Paste the **Prompt**. Settings: 5 seconds, 9:16 (vertical), highest quality you have.",
             f"4. Download the video and save it as **clips/<file name>** in this folder (`{pack_dir}`).",
             "", "You can make them over several days - tell Claude when some are done and it builds a preview "
             "(missing clips are filled with the still image).", ""]
    for c in clip_plan(video):
        shutil.copy(img_dir / c["start"], frames / f"{c['id']}_start.jpg")
        if c["end"]:
            shutil.copy(img_dir / c["end"], frames / f"{c['id']}_end.jpg")
        lines += [f"## {c['id']}", f"- Start frame: `start_frames/{c['id']}_start.jpg`"]
        if c["end"]:
            lines.append(f"- End frame: `start_frames/{c['id']}_end.jpg`")
        lines += [f"- Save as: `clips/{c['id']}.mp4`", "- Prompt:", "```", c["prompt"], "```", ""]
    (pack_dir / "KLING_PROMPTS.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"pack ready: {pack_dir} ({len(clip_plan(video))} clips)")


def _read_clip(path: Path, work: Path) -> np.memmap:
    """Decode a clip to 1080x1920 RGB frames at FPS (cover-crop) into a raw file on disk and memory-map it,
    so 20+ clips don't have to sit in RAM at once."""
    import imageio_ffmpeg

    raw = work / (path.stem + ".rgb")
    if not raw.exists() or raw.stat().st_mtime < path.stat().st_mtime:
        with raw.open("wb") as f:
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path), "-an",
                            "-vf", f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=f, check=True)
    n = raw.stat().st_size // (W * H * 3)
    return np.memmap(raw, dtype=np.uint8, mode="r", shape=(n, H, W, 3))


def assemble(img_dir: Path, pack_dir: Path, out: Path, video: int, music: Path | None) -> None:
    from PIL import ImageDraw, ImageFont
    from moviepy.audio.AudioClip import AudioArrayClip
    from moviepy.editor import VideoClip

    work = pack_dir / ".work"
    work.mkdir(exist_ok=True)
    forms = VIDEO_FORMS[video]
    segments = [("hook", sv.HOOK[video])] + [(f[0], sv.VO[f[0]]) for f in forms] + [("outro", sv.OUTRO[video])]
    seg_audio, seg_words, seg_dur = {}, {}, {}
    for key, text in segments:
        mp3 = work / f"{key}.mp3"
        seg_words[key] = asyncio.run(sv._tts(text, mp3))
        seg_audio[key] = sv._decode(mp3)
        seg_dur[key] = len(seg_audio[key]) / SR + 0.3

    # timeline: each segment split between its clips by share
    plan = clip_plan(video)
    timeline = []  # (start, dur, source) source = list of frames or ("still", Image)
    seg_start, t = {}, 0.0
    for key, _ in segments:
        seg_start[key] = t
        clips = [c for c in plan if c["seg"] == key]
        if key == "outro":
            collage = sv._collage([img_dir / f"{f[0]}_b.jpg" for f in forms])
            timeline.append((t, seg_dur[key], sv.Shot(collage, seg_dur[key], 1.0, 1.05), False))
        else:
            total_share = sum(c["share"] for c in clips)
            for c in clips:
                d = seg_dur[key] * c["share"] / total_share
                f = pack_dir / "clips" / f"{c['id']}.mp4"
                if f.exists():
                    src = _read_clip(f, work)
                else:
                    still = sv._cover(img_dir / (c["end"] or c["start"]))
                    src = sv.Shot(still, d, 1.0, 1.1, flash=0.8 if c["hit"] else 0.0, shake=24 if c["hit"] else 0.0)
                timeline.append((t, d, src, c["hit"]))
                t += d
            continue
        t += seg_dur[key]
    total = t

    # captions
    chunks = []
    for key, _ in segments:
        base_t, cur = seg_start[key], []
        for w in seg_words[key]:
            cur.append(w)
            if len(cur) == 3 or w[0].endswith((".", ",", "?", "!")):
                chunks.append((base_t + cur[0][1], " ".join(x[0] for x in cur)))
                cur = []
        if cur:
            chunks.append((base_t + cur[0][1], " ".join(x[0] for x in cur)))
    chunks = [(s, chunks[i + 1][0] if i + 1 < len(chunks) else total, txt) for i, (s, txt) in enumerate(chunks)]
    font = ImageFont.truetype(sv.FONT, 80)
    cache = {}

    def caption(text):
        if text not in cache:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            f = font
            bw = d.textbbox((0, 0), text, font=f, stroke_width=7)
            if bw[2] - bw[0] > W - 80:
                f = ImageFont.truetype(sv.FONT, int(80 * (W - 80) / (bw[2] - bw[0])))
                bw = d.textbbox((0, 0), text, font=f, stroke_width=7)
            d.text(((W - (bw[2] - bw[0])) // 2, int(H * 0.80)), text, font=f, fill=sv.YELLOW, stroke_width=7, stroke_fill=(0, 0, 0))
            cache[text] = layer
        return cache[text]

    def make_frame(t):
        idx = max(i for i, item in enumerate(timeline) if item[0] <= t + 1e-6)
        s, d, src, hit = timeline[idx]
        lt = t - s
        if isinstance(src, np.ndarray):
            # play the clip from its start, slightly sped up so ~5 s of motion fits the slot
            speed = max(1.0, min(2.0, (len(src) / FPS) / d)) if d > 0 else 1.0
            im = Image.fromarray(np.asarray(src[min(int(lt * speed * FPS), len(src) - 1)]))
            if hit and lt < 0.25:
                im = Image.blend(im, Image.new("RGB", (W, H), (255, 255, 255)), 0.85 * (1 - lt / 0.25))
        else:
            im = src.frame(lt)
        im = im.convert("RGBA")
        for cs, ce, txt in chunks:
            if cs <= t < ce:
                im.alpha_composite(caption(txt))
                break
        return np.asarray(im.convert("RGB"))

    # audio
    n = int(total * SR) + SR
    mix = np.zeros((n, 2), dtype=np.float32)
    for key, _ in segments:
        st = int(seg_start[key] * SR)
        a = seg_audio[key]
        mix[st:st + len(a)] += a[: n - st]
    for s, _d, _src, hit in timeline:
        if hit:
            b = sv._boom(gain=0.8)
            st = int(s * SR)
            mix[st:st + len(b)] += np.stack([b, b], axis=1)[: n - st] * 0.55
    if music:
        m = sv._decode(music)
        reps = int(np.ceil(n / len(m)))
        m = np.tile(m, (reps, 1))[:n] * 0.22
        fade = int(1.5 * SR)
        m[-fade:] *= np.linspace(1, 0, fade)[:, None]
        mix += m
    mix = np.clip(mix, -1, 1)[: int(total * SR)]
    clip = VideoClip(make_frame, duration=total).set_audio(AudioArrayClip(mix, fps=SR))
    clip.write_videofile(str(out), fps=FPS, codec="libx264", audio_codec="aac", preset="medium", threads=4,
                         logger=None, temp_audiofile=str(work / "tmp_audio.m4a"))
    have = sum((pack_dir / "clips" / f"{c['id']}.mp4").exists() for c in plan)
    print(f"wrote {out} ({total:.1f}s) using {have}/{len(plan)} AI clips")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("img_dir")
    p.add_argument("pack_dir")
    p.add_argument("--video", type=int, choices=(1, 2), required=True)
    a = sub.add_parser("assemble")
    a.add_argument("img_dir")
    a.add_argument("pack_dir")
    a.add_argument("out")
    a.add_argument("--video", type=int, choices=(1, 2), required=True)
    a.add_argument("--music")
    args = ap.parse_args()
    if args.cmd == "pack":
        pack(Path(args.img_dir), Path(args.pack_dir), args.video)
    else:
        assemble(Path(args.img_dir), Path(args.pack_dir), Path(args.out), args.video, Path(args.music) if args.music else None)
