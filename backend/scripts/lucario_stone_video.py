"""Build a "Lucario Stone Series" short (1080x1920) from the images of lucario_stone_images.py.

Structure (from lucario-stone-series-prompts.md): hook -> 5 forms x 6 shots (stone, smash,
transform, hero, close-up, attack) -> outro. Motion is simulated with zooms/pans, white flashes,
camera shake and synthesized impact sounds; narration is edge-tts with yellow word-by-word captions.

  python scripts/lucario_stone_video.py IMAGES_DIR OUT.mp4 --video 1|2
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.lucario_stone_images import VIDEO_FORMS  # noqa: E402

W, H, FPS = 1080, 1920, 30
VOICE = "en-US-GuyNeural"
RATE = "+8%"
YELLOW = (255, 226, 26)
FONT = "C:/Windows/Fonts/arialbi.ttf"
WATERMARK = "@HarryBeast Gaming"

VO = {
    "fire": "When Lucario smashes a Fire Stone, it becomes Blaze Lucario. Its fur burns crimson and black, with flames for ear appendages, a molten chest spike, and fists wrapped in fire. It can unleash a blazing aura sphere that explodes like a meteor.",
    "ice": "When Lucario smashes an Ice Stone, it becomes Frost Lucario. Its fur turns white and icy blue, with crystal ice spikes, frozen armor on its arms, and freezing mist around its feet. It can unleash a frozen aura blast that turns everything to ice.",
    "thunder": "When Lucario smashes a Thunder Stone, it becomes Volt Lucario. Its fur turns black and electric yellow, with lightning-shaped ears, crackling spikes, and sparks running through its body. It can move at the speed of lightning and strike before you blink.",
    "dark": "When Lucario smashes a Dark Stone, it becomes Shadow Lucario. Its fur turns jet black with glowing purple veins, with chains around its arms, horns of shadow, and blood-red eyes. It can unleash a dark aura storm that swallows all light.",
    "dragon": "When Lucario smashes a Dragon Stone, it becomes Dragon Lucario. Its body grows armored blue-gold scales, with massive wings, dragon horns, and a long spiked tail. It can unleash a dragon aura roar that shakes the earth.",
    "water": "When Lucario smashes a Water Stone, it becomes Tidal Lucario. Its fur turns deep ocean blue, with fin-shaped ears, a coral chest spike, and water swirling around its arms. It can unleash a tidal aura wave that crushes everything.",
    "leaf": "When Lucario smashes a Leaf Stone, it becomes Forest Lucario. Its fur grows into moss and bark, with leafy ears, vines around its arms, and a thorn chest spike. It can unleash a storm of razor leaves.",
    "moon": "When Lucario smashes a Moon Stone, it becomes Lunar Lucario. Its fur turns silver-white with glowing crescent marks, with a flowing silver mane and eyes like the full moon. It can unleash a moonlight aura sphere.",
    "earth": "When Lucario smashes an Earth Stone, it becomes Titan Lucario. Its body is covered in heavy stone armor, with boulder fists, a crystal chest spike, and glowing cracks. It can punch the ground and raise mountains.",
    "sun": "And when Lucario smashes the Sun Stone, it becomes Solar Lucario. Its fur turns radiant gold and white, with a blazing halo, golden armor, and wings of light. It can unleash a solar aura beam brighter than the sun.",
}
HOOK = {1: "What if Lucario smashed every evolution stone? Each one changes him forever.",
        2: "Lucario is back, and this time he smashes five more evolution stones. Each one changes him forever."}
OUTRO = {1: "Which Lucario is your favorite? Comment the next stone!",
         2: "Which form is the strongest? Comment the next stone!"}


# ---------------------------------------------------------------- narration

async def _tts(text: str, mp3: Path) -> list[tuple[str, float, float]]:
    import edge_tts

    words = []
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    with mp3.open("wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append((chunk["text"], s, s + chunk["duration"] / 1e7))
    return _restore_punctuation(text, words)


def _restore_punctuation(text: str, words: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """WordBoundary events drop punctuation; map them back onto the script's own tokens."""
    import re

    tokens = text.split()
    norm = lambda w: re.sub(r"[^0-9a-z]", "", w.lower())  # noqa: E731
    out, j = [], 0
    for word, start, end in words:
        k = j
        while k < len(tokens) and norm(tokens[k]) != norm(word):
            k += 1
        if k < len(tokens):
            out.append((tokens[k], start, end))
            j = k + 1
        else:
            out.append((word, start, end))
    return out


def _decode(mp3: Path, sr: int = 44100) -> np.ndarray:
    """MP3 -> float32 stereo array via the bundled ffmpeg (moviepy 1.0's to_soundarray breaks on numpy 2)."""
    import subprocess

    import imageio_ffmpeg

    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(mp3), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


# ---------------------------------------------------------------- images

def _cover(path: Path) -> Image.Image:
    im = Image.open(path).convert("RGB")
    scale = max(W / im.width, H / im.height) * 1.02
    return im.resize((math.ceil(im.width * scale), math.ceil(im.height * scale)), Image.LANCZOS)


def _collage(paths: list[Path]) -> Image.Image:
    bg = _cover(paths[-1]).filter(ImageFilter.GaussianBlur(30))
    bg = Image.blend(bg, Image.new("RGB", bg.size, (215, 215, 220)), 0.55)
    tiles = [Image.open(p).convert("RGB") for p in paths]
    tw, th = 340, 595
    pos = [(30, 260), (370, 260), (710, 260), (200, 900), (540, 900)]
    canvas = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    for t, (x, y) in zip(tiles, pos):
        t = t.resize((tw, th), Image.LANCZOS)
        canvas.paste(t, (x, y))
        ImageDraw.Draw(canvas).rectangle((x, y, x + tw, y + th), outline=(255, 255, 255), width=5)
    return canvas


class Shot:
    def __init__(self, img: Image.Image, dur: float, z0=1.0, z1=1.08, cy=0.5, flash=0.0, shake=0.0):
        self.img, self.dur, self.z0, self.z1, self.cy = img, dur, z0, z1, cy
        self.flash, self.shake = flash, shake

    def frame(self, t: float) -> Image.Image:
        p = min(max(t / self.dur, 0.0), 1.0)
        e = p * p * (3 - 2 * p)
        z = self.z0 + (self.z1 - self.z0) * e
        im = self.img
        cw, ch = W / z, H / z  # the cover image is ~W x H, so the crop box is the zoomed viewport
        cx = im.width / 2
        cy = im.height * self.cy
        dx = dy = 0.0
        if self.shake:
            amp = self.shake * math.exp(-4 * t)
            dx = amp * math.sin(t * 73.0)
            dy = amp * math.cos(t * 61.0)
        left = min(max(cx - cw / 2 + dx, 0), im.width - cw)
        top = min(max(cy - ch / 2 + dy, 0), im.height - ch)
        out = im.resize((W, H), Image.BILINEAR, box=(left, top, left + cw, top + ch))
        if self.flash:
            a = self.flash * math.exp(-7 * t)
            if a > 0.02:
                out = Image.blend(out, Image.new("RGB", (W, H), (255, 255, 255)), min(a, 1.0))
        return out


# ---------------------------------------------------------------- audio fx

def _boom(sr=44100, dur=1.1, gain=0.9):
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    freq = 70 * np.exp(-2.2 * t) + 32
    sine = np.sin(2 * np.pi * np.cumsum(freq) / sr)
    noise = np.random.default_rng(1).standard_normal(len(t))
    noise = np.convolve(noise, np.ones(40) / 40, mode="same")
    sig = (0.8 * sine + 0.6 * noise) * np.exp(-4.0 * t)
    return gain * sig / np.max(np.abs(sig))


def _whoosh(sr=44100, dur=0.5, gain=0.35):
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    noise = np.random.default_rng(2).standard_normal(len(t))
    k = np.convolve(noise, np.ones(12) / 12, mode="same")
    env = np.sin(np.pi * t / dur) ** 2
    return gain * k * env / np.max(np.abs(k))


# ---------------------------------------------------------------- build

def build(img_dir: Path, out: Path, video: int) -> None:
    from moviepy.editor import VideoClip
    from moviepy.audio.AudioClip import AudioArrayClip

    work = out.parent / f".work{video}"
    work.mkdir(parents=True, exist_ok=True)
    forms = VIDEO_FORMS[video]
    segments = [("hook", HOOK[video])] + [(f[0], VO[f[0]]) for f in forms] + [("outro", OUTRO[video])]

    # narration per segment
    seg_audio, seg_words, seg_dur = [], [], []
    for key, text in segments:
        mp3 = work / f"{key}.mp3"
        words = asyncio.run(_tts(text, mp3))
        arr = _decode(mp3)
        seg_audio.append(arr)
        seg_words.append(words)
        seg_dur.append(len(arr) / 44100 + 0.35)

    shots: list[tuple[float, Shot]] = []  # (start time, shot)
    fx: list[tuple[float, np.ndarray]] = []
    t0 = 0.0
    hook = _cover(img_dir / f"hook{video}.jpg")
    shots.append((t0, Shot(hook, seg_dur[0], 1.0, 1.12, cy=0.55)))
    fx.append((seg_dur[0] - 0.4, _whoosh()))
    t0 += seg_dur[0]
    for i, (key, *_rest) in enumerate(forms, start=1):
        d = seg_dur[i]
        a, smash, b, attack = (_cover(img_dir / f"{key}_{n}.jpg") for n in ("a", "smash", "b", "attack"))
        weights = [1.0, 0.8, 0.8, 1.15, 1.0, 1.25]
        unit = d / sum(weights)
        plan = [
            Shot(a, weights[0] * unit, 1.0, 1.08, cy=0.6),
            Shot(smash, weights[1] * unit, 1.18, 1.02, cy=0.6, flash=0.9, shake=26),
            Shot(b, weights[2] * unit, 1.25, 1.05, cy=0.5, flash=1.0),
            Shot(b, weights[3] * unit, 1.0, 1.1, cy=0.5),
            Shot(b, weights[4] * unit, 1.75, 1.95, cy=0.27),
            Shot(attack, weights[5] * unit, 1.04, 1.16, cy=0.5, flash=0.7, shake=30),
        ]
        s = t0
        for k, shot in enumerate(plan):
            shots.append((s, shot))
            if k in (1, 5):
                fx.append((max(s - 0.05, 0), _boom(gain=0.9 if k == 1 else 0.75)))
            if k == 2:
                fx.append((s, _whoosh(gain=0.3)))
            s += shot.dur
        t0 += d
    collage = _collage([img_dir / f"{f[0]}_b.jpg" for f in forms])
    shots.append((t0, Shot(collage, seg_dur[-1], 1.0, 1.04, cy=0.5)))
    total = t0 + seg_dur[-1]

    # captions: chunks of up to 3 words, absolute times
    chunks = []
    t = 0.0
    for words, d in zip(seg_words, seg_dur):
        cur = []
        for w in words:
            cur.append(w)
            if len(cur) == 3 or w[0].endswith((".", ",", "?", "!")):
                chunks.append((t + cur[0][1], t + cur[-1][2], " ".join(x[0] for x in cur)))
                cur = []
        if cur:
            chunks.append((t + cur[0][1], t + cur[-1][2], " ".join(x[0] for x in cur)))
        t += d
    # stretch each chunk until the next one starts (no flicker)
    chunks = [(s, (chunks[i + 1][0] if i + 1 < len(chunks) else e + 0.3), txt) for i, (s, e, txt) in enumerate(chunks)]
    font = ImageFont.truetype(FONT, 78)
    wm_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 34)

    def caption_layer(text: str) -> Image.Image:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        box = d.textbbox((0, 0), text, font=font, stroke_width=7)
        tw = box[2] - box[0]
        if tw > W - 80:
            f2 = ImageFont.truetype(FONT, int(78 * (W - 80) / tw))
        else:
            f2 = font
        box = d.textbbox((0, 0), text, font=f2, stroke_width=7)
        x = (W - (box[2] - box[0])) // 2
        d.text((x, int(H * 0.80)), text, font=f2, fill=YELLOW, stroke_width=7, stroke_fill=(0, 0, 0))
        return layer

    wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dw = ImageDraw.Draw(wm)
    bw = dw.textbbox((0, 0), WATERMARK, font=wm_font)
    dw.text(((W - (bw[2] - bw[0])) // 2, int(H * 0.755)), WATERMARK, font=wm_font, fill=(255, 255, 255, 110))
    cache: dict[str, Image.Image] = {}

    def make_frame(t: float):
        idx = max(i for i, (s, _) in enumerate(shots) if s <= t + 1e-6)
        s, shot = shots[idx]
        im = shot.frame(t - s).convert("RGBA")
        im.alpha_composite(wm)
        for cs, ce, txt in chunks:
            if cs <= t < ce:
                if txt not in cache:
                    cache[txt] = caption_layer(txt)
                im.alpha_composite(cache[txt])
                break
        return np.asarray(im.convert("RGB"))

    # audio: narration placed at each segment start + fx
    sr = 44100
    n = int(total * sr) + sr
    mix = np.zeros((n, 2), dtype=np.float32)
    t = 0.0
    for clip, d in zip(seg_audio, seg_dur):
        arr = clip
        st = int(t * sr)
        mix[st:st + len(arr)] += arr[: n - st]
        t += d
    for at, sig in fx:
        st = int(at * sr)
        seg = sig[: n - st].astype(np.float32)
        mix[st:st + len(seg)] += np.stack([seg, seg], axis=1) * 0.6
    mix = np.clip(mix, -1, 1)
    audio = AudioArrayClip(mix[: int(total * sr)], fps=sr)

    clip = VideoClip(make_frame, duration=total).set_audio(audio)
    clip.write_videofile(str(out), fps=FPS, codec="libx264", audio_codec="aac", preset="medium",
                         threads=4, logger=None, temp_audiofile=str(work / "tmp_audio.m4a"))
    print(f"wrote {out} ({total:.1f}s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("img_dir")
    parser.add_argument("out")
    parser.add_argument("--video", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    build(Path(args.img_dir), Path(args.out), args.video)
