"""Long-form horizontal (16:9) YouTube videos: chapters, narration, subtitles and a thumbnail.

A spec in scripts/specs/long/*.json has "category" ("gaming" -> HarryBeast Gaming in
English, "movies" -> Name Writer + Facebook in Hindi), a title/description/tags, a
"thumbnail" ({"image": REF, "text": "..."}) and "sections": a list of
{"heading": "...", "script": "...", "images": [REF, ...]}. Image REFs are the same as in
game_videos.py (steam:, wiki:, wikifile:, yt:). Each section becomes a chapter: its
narration is synthesised separately, its images cross-fade with a slow Ken Burns move,
and word-synced subtitles run along the bottom.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.media.text_render import get_font, render_block, render_runs, wrap  # noqa: E402
from app.media.video_gen import _ease, _even_word_timings, _synthesize_narration  # noqa: E402
from scripts.game_videos import fetch_image  # noqa: E402

W, H = 1920, 1080
FPS = 24
_XFADE = 0.7
_PAD = 0.8  # silence after each section's narration
_SUB_MAX_W = 1500
_SUB_MAX_WORDS = 7
_YELLOW, _WHITE, _ACCENT = (255, 214, 0), (255, 255, 255), (255, 45, 85)
# Measured narration speed: English ~165 words/min, Hindi ~124 words/min. These minimums
# give roughly 7+ minute videos; shorter scripts still render but are flagged.
MIN_WORDS = {"gaming": 1200, "movies": 900}


def word_count(spec: dict) -> int:
    return sum(len(sec["script"].split()) for sec in spec["sections"])


def is_too_short(spec: dict) -> bool:
    return word_count(spec) < MIN_WORDS.get(spec.get("category", "gaming"), 0)


def _cover(img: Image.Image, scale: float = 1.12) -> Image.Image:
    """Cover-fit the frame with headroom for the zoom; small images sit on a blurred copy."""
    size = (int(W * scale), int(H * scale))
    if img.width >= W * 0.6:
        return ImageOps.fit(img, size, Image.LANCZOS)
    bg = ImageOps.fit(img, size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)
    fg = ImageOps.contain(img, (int(size[0] * 0.55), int(size[1] * 0.78)), Image.LANCZOS)
    bg.paste(fg, ((size[0] - fg.width) // 2, (size[1] - fg.height) // 2))
    return bg


def _shade() -> Image.Image:
    """Bottom gradient so subtitles stay readable over bright images."""
    g = Image.new("L", (1, H), 0)
    for y in range(H):
        g.putpixel((0, y), int(200 * max(0.0, (y - H * 0.55) / (H * 0.45)) ** 1.4))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shade.putalpha(g.resize((W, H)))
    return shade


def _chunks(words, font):
    chunks, cur = [], []
    for w in words:
        text = font.clean(w.text)
        if not text:
            continue
        w = type(w)(text, w.start, w.end)
        if cur and (w.start - cur[-1].end > 0.4 or len(cur) >= _SUB_MAX_WORDS
                    or font.measure(" ".join(x.text for x in [*cur, w])) > _SUB_MAX_W):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


class _Section:
    def __init__(self, heading: str, images: list[Image.Image], words, duration: float, index: int, total: int):
        self.duration = duration
        self.frames = [_cover(i) for i in images]
        self.seg = duration / len(self.frames)
        sub_font = get_font("bold", 56)
        self.sub_font = sub_font
        self.chunks = _chunks(words, sub_font)
        self.spans = [
            (c[0].start, self.chunks[k + 1][0].start if k + 1 < len(self.chunks) else c[-1].end + 0.5)
            for k, c in enumerate(self.chunks)
        ]
        self.sub_cache: dict = {}
        hfont = get_font("bold", 84)
        lines = wrap(hfont, heading, max_width=1500, max_lines=2)
        self.title = render_block(hfont, lines, color=_WHITE, stroke=6, shadow=10)
        tag = render_runs(get_font("bold", 34), [(f"CHAPTER {index + 1}/{total}", _WHITE)])
        self.tag = Image.new("RGBA", (tag.width + 40, tag.height + 8), (0, 0, 0, 0))
        ImageDraw.Draw(self.tag).rounded_rectangle([0, 0, self.tag.width - 1, self.tag.height - 1],
                                                   radius=self.tag.height // 2, fill=_ACCENT)
        self.tag.alpha_composite(tag, (20, 4))
        small = render_runs(get_font("bold", 36), [(heading, _WHITE)], stroke=4, shadow=6)
        self.small = small

    def _img_at(self, i: int, t: float) -> Image.Image:
        src = self.frames[i]
        p = _ease((t - i * self.seg) / (self.seg + _XFADE))
        zoom = 1.0 + 0.08 * (p if i % 2 == 0 else 1 - p)
        cw = src.width / zoom
        ch = src.height / zoom
        dx = (src.width - cw) * (0.5 + (0.35 if i % 2 else -0.35) * (p - 0.5))
        dy = (src.height - ch) / 2
        return src.resize((W, H), Image.BILINEAR, box=(dx, dy, dx + cw, dy + ch))

    def frame(self, t: float, shade: Image.Image) -> Image.Image:
        i = min(int(t // self.seg), len(self.frames) - 1)
        img = self._img_at(i, t)
        nxt = (i + 1) * self.seg
        if i + 1 < len(self.frames) and t > nxt - _XFADE:
            img = Image.blend(img, self._img_at(i + 1, t), _ease((t - (nxt - _XFADE)) / _XFADE))
        img = img.convert("RGBA")
        img.alpha_composite(shade)
        # Chapter title card for the first ~3 s, then a small heading in the corner.
        a = 1.0 - _ease((t - 2.6) / 0.6)
        if a > 0:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, int(110 * a)))
            img.alpha_composite(layer)
            for im, y in ((self.tag, H // 2 - self.title.height // 2 - 90), (self.title, H // 2 - self.title.height // 2)):
                part = im.copy()
                part.putalpha(part.getchannel("A").point(lambda v: int(v * a)))
                img.alpha_composite(part, ((W - im.width) // 2, y))
        else:
            img.alpha_composite(self.small, (60, 50))
        for k, (s, e) in enumerate(self.spans):
            if s <= t < e:
                words = self.chunks[k]
                active = max((j for j, w in enumerate(words) if w.start <= t), default=0)
                key = (k, active)
                if key not in self.sub_cache:
                    runs = [(w.text, _YELLOW if j == active else _WHITE) for j, w in enumerate(words)]
                    self.sub_cache[key] = render_runs(self.sub_font, runs, stroke=6, shadow=10)
                sub = self.sub_cache[key]
                img.alpha_composite(sub, ((W - sub.width) // 2, H - 150 - sub.height // 2))
                break
        return img.convert("RGB")


def make_thumbnail(spec: dict, path: Path) -> Path:
    th = spec["thumbnail"]
    base = ImageOps.fit(fetch_image(th["image"]), (1280, 720), Image.LANCZOS)
    if base.width < 1280:
        base = base.resize((1280, 720))
    base = ImageEnhance.Brightness(base).enhance(0.75).convert("RGBA")
    font = get_font("bold", 112)
    lines = wrap(font, th["text"], max_width=1150, max_lines=2)
    text = render_block(font, lines, color=_YELLOW, stroke=10, shadow=14)
    base.alpha_composite(text, ((1280 - text.width) // 2, 720 - text.height - 50))
    base.convert("RGB").save(path, quality=92)
    return path


def build_video(spec_path: Path) -> dict:
    """Render the long video + thumbnail. Returns {"video": {...}} like the other builders."""
    from moviepy.editor import AudioFileClip, VideoClip

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    gaming = spec.get("category", "gaming") == "gaming"
    if is_too_short(spec):
        import logging

        logging.getLogger(__name__).warning(
            "%s: script is only %d words (minimum %d for %s) - the video will be under ~7 minutes",
            spec["id"], word_count(spec), MIN_WORDS[spec.get("category", "gaming")], spec.get("category", "gaming"),
        )
    settings.tts_voice = spec.get("tts_voice") or (settings.gaming_tts_voice if gaming else "hi-IN-SwaraNeural")
    settings.tts_rate = spec.get("tts_rate") or "+0%"

    out_dir = Path(settings.media_output_dir) / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = uuid.uuid4().hex[:8]
    try:
        sections, audios, starts, t0 = [], [], [], 0.0
        for n, sec in enumerate(spec["sections"]):
            mp3 = out_dir / f"long-{work}-{n}.mp3"
            words = asyncio.run(_synthesize_narration(sec["script"], mp3))
            clip = AudioFileClip(str(mp3))
            if not words:
                words = _even_word_timings(sec["script"], clip.duration)
            dur = clip.duration + _PAD
            images = [fetch_image(r) for r in sec["images"]]
            sections.append(_Section(sec["heading"], images, words, dur, n, len(spec["sections"])))
            audios.append(clip)
            starts.append(t0)
            t0 += dur
        total = t0
        shade = _shade()

        def make_frame(t: float):
            import numpy as np

            k = max(j for j, s in enumerate(starts) if s <= t + 1e-6)
            frame = sections[k].frame(t - starts[k], shade)
            bar = int(W * min(t / total, 1.0))
            ImageDraw.Draw(frame).rectangle([0, H - 8, bar, H], fill=_ACCENT)
            return np.asarray(frame)

        from moviepy.audio.AudioClip import CompositeAudioClip

        tracks = [a.set_start(s) for a, s in zip(audios, starts)]
        audio = CompositeAudioClip(tracks).set_duration(total).set_fps(44100)
        out = out_dir / f"long-{work}.mp4"
        VideoClip(make_frame, duration=total).set_audio(audio).write_videofile(
            str(out), fps=FPS, codec="libx264", audio_codec="aac", audio_bitrate="160k", preset="veryfast",
            ffmpeg_params=["-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart"], threads=4, logger=None,
        )
        thumb = make_thumbnail(spec, out_dir / f"long-{work}-thumb.jpg")
    except Exception as exc:
        return {"video": {"success": False, "error": f"long video failed: {exc}", "data": None}}

    chapters = "\n".join(f"{int(s // 60)}:{int(s % 60):02d} {sec['heading']}" for s, sec in zip(starts, spec["sections"]))
    return {"video": {"success": True, "error": None,
                      "data": {"path": str(out.resolve()), "thumbnail": str(thumb.resolve()),
                               "duration_seconds": round(total, 1), "chapters": chapters}}}


def post_id_for(spec: dict) -> str:
    return f"custom:long-{spec['id']}"


def default_platforms(spec: dict) -> list[str]:
    return spec.get("platforms") or (["youtube", "facebook_gaming"] if spec.get("category", "gaming") == "gaming" else ["youtube_movies", "facebook"])


def description_for(spec: dict, chapters: str) -> str:
    tags = " ".join(f"#{h}" for h in spec.get("hashtags", []))
    head = "Chapters:" if spec.get("category", "gaming") == "gaming" else "Chapters / अध्याय:"
    return f"{spec['description']}\n\n{head}\n{chapters}\n\n{tags}".strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build long-form videos from specs.")
    parser.add_argument("specs", nargs="+", type=Path)
    args = parser.parse_args()
    res = [build_video(p) for p in args.specs]
    print(json.dumps(res, indent=2, ensure_ascii=False))
    sys.exit(0 if all(r["video"]["success"] for r in res) else 1)


if __name__ == "__main__":
    main()
