"""Full-screen "cinema" look for movie shorts (owner 2026-11-11: the AI Trend Poster videos needed better graphics).

The old card layout showed a small picture inside a dim square with a lot of empty space. Here every scene fills the
phone: blurred backdrop of the picture, the picture itself big with a gold frame that slides in and slowly zooms, a
white flash on every cut, the fact in big type sliding up over a dark band, karaoke subtitles, film grain + vignette
and a big hook line over the first second. Same specs as game_videos.py (scenes with image + label / fact / sub).

    python scripts/cinema_video.py scripts/specs/movies/reel_75_zanjeer-1973-facts.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings  # noqa: E402
from app.media import video_gen as vg  # noqa: E402
from app.media.text_render import get_font, render_block, render_runs, wrap  # noqa: E402
from scripts import game_videos as gv, humor  # noqa: E402

W, H, FPS = 1080, 1920, 30
GOLD, WHITE, ACCENT = (255, 205, 70), (255, 255, 255), (255, 45, 85)
HERO_BOX = (1000, 1000)  # the picture's largest size
HERO_CY = 800  # its vertical centre
TEXT_Y = 1385  # top of the fact text
SUB_Y = 1765
HOOK_SECONDS = 1.7


def _rounded(img: Image.Image, radius: int, border: int) -> Image.Image:
    """Picture with rounded corners and a gold frame, RGBA."""
    w, h = img.size
    out = Image.new("RGBA", (w + 2 * border, h + 2 * border), (0, 0, 0, 0))
    ImageDraw.Draw(out).rounded_rectangle([0, 0, out.width - 1, out.height - 1], radius=radius + border, fill=GOLD + (255,))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out.paste(img.convert("RGB"), (border, border), mask)
    return out


class _Scene:
    def __init__(self, img: Image.Image, big: str | None, small: str | None, index: int) -> None:
        bg = ImageOps.fit(img, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(26))
        bg = ImageEnhance.Brightness(ImageEnhance.Color(bg).enhance(1.25)).enhance(0.42)
        band = Image.new("L", (W, H), 0)  # dark band behind the text, fading upward
        d = ImageDraw.Draw(band)
        for y in range(1180, H):
            d.line([(0, y), (W, y)], fill=int(min(1.0, (y - 1180) / 260) * 215))
        bg.paste((0, 0, 0), (0, 0), band)
        self.bg = bg
        scale = min(HERO_BOX[0] / img.width, HERO_BOX[1] / img.height)
        pic = img.resize((int(img.width * scale * 1.1), int(img.height * scale * 1.1)), Image.LANCZOS)
        if scale > 1.6:  # a small Wikipedia picture blown up: sharpen it a little
            pic = pic.filter(ImageFilter.UnsharpMask(radius=3, percent=90))
        self.hero = _rounded(pic, 34, 8)  # 10% larger than shown, the zoom ends there
        shadow = Image.new("RGBA", (self.hero.width + 120, self.hero.height + 120), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([60, 80, shadow.width - 60, shadow.height - 40], radius=50, fill=(0, 0, 0, 190))
        self.shadow = shadow.filter(ImageFilter.GaussianBlur(28))
        self.side = -1 if index % 2 else 1  # slides in from the right, then the left, ...
        self.text = None
        blocks = []
        if big:
            f = get_font("bold", 100)
            blocks.append(render_block(f, wrap(f, big, max_width=980, max_lines=2), color=GOLD, stroke=7, shadow=12))
        if small:
            f = get_font("bold", 58)
            blocks.append(render_block(f, wrap(f, small, max_width=960, max_lines=2), color=WHITE, stroke=5, shadow=8))
        if blocks:
            text = Image.new("RGBA", (W, sum(b.height for b in blocks) + 14 * (len(blocks) - 1)), (0, 0, 0, 0))
            y = 0
            for b in blocks:
                text.alpha_composite(b, ((W - b.width) // 2, y))
                y += b.height + 14
            self.text = text

    def frame(self, t: float, seconds: float) -> Image.Image:
        """t = seconds since this scene started."""
        frame = self.bg.copy()
        zoom = (1 / 1.1) * (1 + 0.1 * vg._ease(t / max(seconds, 0.1)))  # 1.0 -> 1.1 of the shown size
        slide = (1 - vg._ease(t / 0.38)) * self.side * 900
        hero = self.hero.resize((int(self.hero.width * zoom), int(self.hero.height * zoom)), Image.BILINEAR)
        x, y = (W - hero.width) // 2 + int(slide), HERO_CY - hero.height // 2
        sh = self.shadow.resize((hero.width + 110, hero.height + 110), Image.BILINEAR)
        frame.paste(sh, (x - 55, y - 40), sh)
        frame.paste(hero, (x, y), hero)
        if self.text is not None:
            p = vg._ease((t - 0.25) / 0.35)
            if p > 0:
                text = self.text
                if p < 1:
                    text = text.copy()
                    text.putalpha(text.getchannel("A").point(lambda v: int(v * p)))
                frame.paste(text, (0, TEXT_Y + int((1 - p) * 70)), text)
        return frame


class _Renderer:
    def __init__(self, scenes: list[_Scene], badge: str, title: str, hook: str, chunks, duration: float) -> None:
        self.scenes, self.chunks, self.duration = scenes, chunks, duration
        self.segment = duration / len(scenes)
        pill_text = render_runs(get_font("bold", 40), [(badge, WHITE)])
        pill = Image.new("RGBA", (pill_text.width + 48, pill_text.height + 10), (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle([0, 0, pill.width - 1, pill.height - 1], radius=pill.height // 2, fill=ACCENT)
        pill.alpha_composite(pill_text, (24, 5))
        f = get_font("bold", 50)
        name = render_block(f, wrap(f, title, max_width=980, max_lines=1), color=WHITE, stroke=5, shadow=8)
        self.top = Image.new("RGBA", (W, pill.height + 16 + name.height), (0, 0, 0, 0))
        self.top.alpha_composite(pill, ((W - pill.width) // 2, 0))
        self.top.alpha_composite(name, ((W - name.width) // 2, pill.height + 16))
        f = get_font("bold", 120)
        self.hook = render_block(f, wrap(f, hook, max_width=1000, max_lines=4), color=GOLD, stroke=9, shadow=16)
        self.sub_font = get_font("bold", 76)
        self._subs: dict = {}
        self.spans = [(c[0].start, chunks[i + 1][0].start if i + 1 < len(chunks) else c[-1].end + 0.6)
                      for i, c in enumerate(chunks)]
        rnd = np.random.default_rng(7)
        self.grain = [rnd.integers(-9, 10, (H // 2, W // 2, 1), dtype=np.int16).repeat(2, 0).repeat(2, 1) for _ in range(4)]
        yy, xx = np.mgrid[0:H, 0:W]
        dist = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        self.vignette = np.clip(1.12 - 0.34 * dist, 0.55, 1.0)[..., None].astype(np.float32)

    def _subtitle(self, frame: Image.Image, t: float) -> None:
        for ci, (start, end) in enumerate(self.spans):
            if start <= t < end:
                words = self.chunks[ci]
                active = max((i for i, w in enumerate(words) if w.start <= t), default=0)
                key = (ci, active)
                if key not in self._subs:
                    self._subs[key] = render_runs(self.sub_font, [(w.text, GOLD if i == active else WHITE)
                                                                  for i, w in enumerate(words)], stroke=7, shadow=12)
                img = self._subs[key]
                pop = 0.82 + 0.18 * vg._ease((t - start) / 0.14)
                if pop < 0.999:
                    img = img.resize((max(1, int(img.width * pop)), max(1, int(img.height * pop))), Image.BILINEAR)
                frame.paste(img, ((W - img.width) // 2, SUB_Y - img.height // 2), img)
                return

    def __call__(self, t: float) -> np.ndarray:
        index = min(int(t // self.segment), len(self.scenes) - 1)
        local = t - index * self.segment
        frame = self.scenes[index].frame(local, self.segment)
        if t >= HOOK_SECONDS:
            p = vg._ease((t - HOOK_SECONDS) / 0.4)
            frame.paste(self.top, (0, 60 - int((1 - p) * 60)), self.top)
            self._subtitle(frame, t)
        else:  # the hook line over a darkened first scene
            frame = ImageEnhance.Brightness(frame).enhance(0.45)
            pop = 0.7 + 0.3 * vg._ease(t / 0.25)
            hook = self.hook.resize((int(self.hook.width * pop), int(self.hook.height * pop)), Image.BILINEAR)
            frame.paste(hook, ((W - hook.width) // 2, 760 - hook.height // 2), hook)
        draw = ImageDraw.Draw(frame)
        draw.rectangle([0, H - 12, W, H], fill=(40, 40, 40))
        draw.rectangle([0, H - 12, int(W * min(t / self.duration, 1.0)), H], fill=ACCENT)
        a = np.asarray(frame, dtype=np.float32) * self.vignette + self.grain[int(t * FPS) % 4]
        flash = max(0.0, 1 - local / 0.16) if index else max(0.0, 1 - abs(t - HOOK_SECONDS) / 0.12)
        if flash > 0:  # white flash on every cut and when the hook leaves
            a += (255 - a) * 0.75 * flash
        if t < 0.3:
            a *= t / 0.3
        elif t > self.duration - 0.5:
            a *= max(0.0, (self.duration - t) / 0.5)
        return np.clip(a, 0, 255).astype(np.uint8)


def build_video(spec_path: Path) -> dict:
    """Same result shape as game_videos.build_video: {"video": {"success", "data": {"path"}, "error"}}."""
    from moviepy.editor import AudioFileClip, VideoClip

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    settings.tts_voice = spec.get("tts_voice") or "hi-IN-SwaraNeural"
    settings.tts_rate = spec.get("tts_rate") or "+0%"
    out_dir = Path(settings.media_output_dir) / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = uuid.uuid4().hex[:8]
    script = humor.insert(spec["video_script"], humor.for_spec(spec))  # one on-topic joke before the CTA
    audio_path = out_dir / f"narration-{work}.mp3"
    for attempt in range(4):
        try:
            words = asyncio.run(vg._synthesize_narration(script, audio_path))
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                return {"video": {"success": False, "error": f"narration synthesis failed: {exc}", "data": None}}
            time.sleep(5 * (attempt + 1))
    try:
        narration = AudioFileClip(str(audio_path)).duration
        duration = narration + vg._TAIL
        if not words:
            words = vg._even_word_timings(script, narration)
        scenes = [_Scene(gv.fetch_image(s["image"]), s.get("fact") or s.get("label"), s.get("sub"), i)
                  for i, s in enumerate(spec["scenes"])]
        title = spec["scenes"][0].get("label") or spec["headline"].split(":")[0]
        renderer = _Renderer(scenes, gv.badge_for(spec), title, spec["headline"],
                             vg._build_subtitle_chunks(words, get_font("bold", 76)), duration)
        out = out_dir / f"short-{work}.mp4"
        VideoClip(renderer, duration=duration).set_audio(vg._build_audio(audio_path, duration)).write_videofile(
            str(out), fps=FPS, codec="libx264", audio_codec="aac", audio_bitrate="192k", preset="medium",
            ffmpeg_params=["-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart"], threads=4, logger=None)
    except Exception as exc:  # noqa: BLE001
        return {"video": {"success": False, "error": f"video assembly failed: {exc}", "data": None}}
    return {"video": {"success": True, "error": None, "data": {"path": str(out.resolve()), "duration_seconds": round(duration, 2)}}}


if __name__ == "__main__":
    print(build_video(Path(sys.argv[1]))["video"])
