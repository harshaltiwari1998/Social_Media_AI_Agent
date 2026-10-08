from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_CANVAS_SIZE = (1080, 1920)  # vertical short-form video (Reels/Shorts)
_FPS = 30
_CARD_SIZE = 1000  # scene image "card" in the middle of the frame
_CARD_POS = (40, 520)
_CARD_RADIUS = 36
_CROSSFADE = 0.45  # seconds of dissolve between scenes
_TAIL = 0.8  # breathing room after the narration ends
_FADE_IN, _FADE_OUT = 0.25, 0.6
_ACCENT = (255, 45, 85)
_HIGHLIGHT = (255, 214, 0)
_SUB_MAX_WIDTH = 960
_SUB_MAX_WORDS = 4
_SUB_Y = 1640  # vertical centre of the subtitle line


@dataclass
class _Word:
    text: str
    start: float
    end: float


@dataclass
class _Scene:
    bg: "Image.Image"  # noqa: F821 - blurred full-frame background with card shadow baked in
    src: "Image.Image"  # noqa: F821 - square source image for the Ken Burns card
    zoom_in: bool
    pan: tuple[float, float]


def _ease(p: float) -> float:
    """Smoothstep, so zooms/pans start and stop gently instead of moving linearly."""
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


async def _synthesize_narration(text: str, output_path: Path) -> list[_Word]:
    """Write the TTS audio to output_path and return per-word timings for subtitle sync."""
    import edge_tts

    communicate = edge_tts.Communicate(text, settings.tts_voice, rate=settings.tts_rate, boundary="WordBoundary")
    words: list[_Word] = []
    with output_path.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7  # 100ns ticks -> seconds
                words.append(_Word(chunk["text"], start, start + chunk["duration"] / 1e7))
    return words


def _even_word_timings(script: str, duration: float) -> list[_Word]:
    """Fallback when TTS gives no word boundaries: spread words evenly over the audio."""
    tokens = script.split()
    if not tokens:
        return []
    step = duration / len(tokens)
    return [_Word(tok, i * step, (i + 1) * step) for i, tok in enumerate(tokens)]


def _build_subtitle_chunks(words: list[_Word], font) -> list[list[_Word]]:
    """Group words into short, single-line subtitle chunks (TikTok/Reels style)."""
    chunks: list[list[_Word]] = []
    current: list[_Word] = []
    for word in words:
        word = _Word(font.clean(word.text), word.start, word.end)
        if not word.text:
            continue
        pause_before = current and word.start - current[-1].end > 0.35
        too_wide = current and font.measure(" ".join(w.text for w in [*current, word])) > _SUB_MAX_WIDTH
        if current and (pause_before or too_wide or len(current) >= _SUB_MAX_WORDS):
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def _rounded_mask(size: int, radius: int) -> "Image.Image":  # noqa: F821
    from PIL import Image, ImageDraw

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _prepare_scene(image_path: str, index: int) -> _Scene:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    src = Image.open(image_path).convert("RGB")
    side = min(src.size)
    src = ImageOps.fit(src, (side, side), Image.LANCZOS)

    # Background: blurred, darkened cover-crop of the same image, so the 9:16 frame
    # has no letterbox bars and the text on top stays readable.
    bg = ImageOps.fit(src, _CANVAS_SIZE, Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)

    # Soft drop shadow under the card.
    shadow = Image.new("L", _CANVAS_SIZE, 0)
    shadow.paste(_rounded_mask(_CARD_SIZE, _CARD_RADIUS), (_CARD_POS[0], _CARD_POS[1] + 18))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28)).point(lambda v: int(v * 0.7))
    bg.paste(Image.new("RGB", _CANVAS_SIZE, (0, 0, 0)), (0, 0), shadow)

    # Alternate zoom direction and pan so consecutive scenes don't move identically.
    pans = [(1, 0), (-1, 0.5), (0, -1), (0.6, 1)]
    return _Scene(bg=bg, src=src, zoom_in=index % 2 == 0, pan=pans[index % len(pans)])


def _render_scene(scene: _Scene, progress: float, card_mask) -> "Image.Image":  # noqa: F821
    from PIL import Image

    p = _ease(progress)
    zoom = 1.0 + 0.14 * p if scene.zoom_in else 1.14 - 0.14 * p
    side = scene.src.width
    crop = side / zoom
    margin = (side - crop) / 2
    cx = side / 2 + scene.pan[0] * margin * (2 * p - 1)
    cy = side / 2 + scene.pan[1] * margin * (2 * p - 1)
    box = (cx - crop / 2, cy - crop / 2, cx + crop / 2, cy + crop / 2)

    card = scene.src.transform((_CARD_SIZE, _CARD_SIZE), Image.EXTENT, box, Image.BILINEAR)
    frame = scene.bg.copy()
    frame.paste(card, _CARD_POS, card_mask)
    return frame


def _build_header(headline: str, badge: str = "TRENDING") -> "Image.Image":  # noqa: F821
    """Badge pill + up-to-3-line headline, rendered once and animated per frame."""
    from PIL import Image, ImageDraw

    from app.media.text_render import get_font, render_block, render_runs, wrap

    pill_text = render_runs(get_font("bold", 36), [(badge, (255, 255, 255))])
    pill = Image.new("RGBA", (pill_text.width + 40, pill_text.height + 6), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle([0, 0, pill.width - 1, pill.height - 1], radius=pill.height // 2, fill=_ACCENT)
    pill.alpha_composite(pill_text, (20, 3))

    font = get_font("bold", 66)
    lines = wrap(font, headline, max_width=980, max_lines=3)
    title = render_block(font, lines, stroke=5, shadow=10) if lines else None

    height = pill.height + 24 + (title.height if title else 0)
    header = Image.new("RGBA", (_CANVAS_SIZE[0], height), (0, 0, 0, 0))
    header.alpha_composite(pill, ((_CANVAS_SIZE[0] - pill.width) // 2, 0))
    if title:
        header.alpha_composite(title, ((_CANVAS_SIZE[0] - title.width) // 2, pill.height + 24))
    return header


class _FrameRenderer:
    def __init__(self, scenes: list[_Scene], header, chunks: list[list[_Word]], duration: float) -> None:
        from app.media.text_render import get_font

        self.scenes = scenes
        self.header = header
        self.chunks = chunks
        self.duration = duration
        self.segment = duration / len(scenes)
        self.card_mask = _rounded_mask(_CARD_SIZE, _CARD_RADIUS)
        self.sub_font = get_font("bold", 78)
        self._sub_cache: dict[tuple[int, int], Any] = {}
        # Each chunk stays up until the next one starts, so there's no flicker between them.
        self.chunk_spans = [
            (chunk[0].start, chunks[i + 1][0].start if i + 1 < len(chunks) else chunk[-1].end + 0.6)
            for i, chunk in enumerate(chunks)
        ]

    def _scene_at(self, t: float, index: int):
        start = index * self.segment
        progress = (t - start) / (self.segment + _CROSSFADE)
        return _render_scene(self.scenes[index], progress, self.card_mask)

    def _subtitle(self, chunk_index: int, active: int):
        from app.media.text_render import render_runs

        key = (chunk_index, active)
        if key not in self._sub_cache:
            runs = [
                (w.text, _HIGHLIGHT if i == active else (255, 255, 255))
                for i, w in enumerate(self.chunks[chunk_index])
            ]
            self._sub_cache[key] = render_runs(self.sub_font, runs, stroke=7, shadow=12)
        return self._sub_cache[key]

    def _draw_subtitle(self, frame, t: float) -> None:
        from PIL import Image

        for ci, (start, end) in enumerate(self.chunk_spans):
            if start <= t < end:
                words = self.chunks[ci]
                active = max((i for i, w in enumerate(words) if w.start <= t), default=0)
                img = self._subtitle(ci, active)
                # Quick "pop" as each new chunk appears.
                pop = 0.82 + 0.18 * _ease((t - start) / 0.14)
                if pop < 0.999:
                    img = img.resize((max(1, int(img.width * pop)), max(1, int(img.height * pop))), Image.BILINEAR)
                frame.paste(img, ((_CANVAS_SIZE[0] - img.width) // 2, _SUB_Y - img.height // 2), img)
                return

    def _draw_header(self, frame, t: float) -> None:
        p = _ease(t / 0.5)
        if p <= 0:
            return
        header = self.header
        if p < 1:
            alpha = header.getchannel("A").point(lambda v: int(v * p))
            header = header.copy()
            header.putalpha(alpha)
        y = 110 - int((1 - p) * 40)  # slides down into place
        frame.paste(header, (0, y), header)

    def __call__(self, t: float):
        import numpy as np
        from PIL import Image, ImageDraw

        index = min(int(t // self.segment), len(self.scenes) - 1)
        frame = self._scene_at(t, index)

        # Crossfade into the next scene during the last _CROSSFADE seconds of this one.
        next_start = (index + 1) * self.segment
        if index + 1 < len(self.scenes) and t > next_start - _CROSSFADE:
            blend = _ease((t - (next_start - _CROSSFADE)) / _CROSSFADE)
            frame = Image.blend(frame, self._scene_at(t, index + 1), blend)

        self._draw_header(frame, t)
        self._draw_subtitle(frame, t)

        draw = ImageDraw.Draw(frame)
        bar_w = int(_CANVAS_SIZE[0] * min(t / self.duration, 1.0))
        draw.rectangle([0, _CANVAS_SIZE[1] - 12, _CANVAS_SIZE[0], _CANVAS_SIZE[1]], fill=(40, 40, 40))
        draw.rectangle([0, _CANVAS_SIZE[1] - 12, bar_w, _CANVAS_SIZE[1]], fill=_ACCENT)

        array = np.asarray(frame, dtype=np.float32 if t < _FADE_IN or t > self.duration - _FADE_OUT else np.uint8)
        if t < _FADE_IN:
            array = (array * (t / _FADE_IN)).astype(np.uint8)
        elif t > self.duration - _FADE_OUT:
            array = (array * max(0.0, (self.duration - t) / _FADE_OUT)).astype(np.uint8)
        return array


def _build_audio(narration_path: Path, duration: float):
    from moviepy.editor import AudioFileClip, CompositeAudioClip
    from moviepy.audio.fx.all import audio_fadeout, audio_loop

    narration = AudioFileClip(str(narration_path))
    bgm_path = settings.bgm_path
    if not (bgm_path and Path(bgm_path).exists()):
        return narration
    try:
        bgm = audio_loop(AudioFileClip(bgm_path), duration=duration).volumex(0.12)
        bgm = audio_fadeout(bgm, 1.5)
        return CompositeAudioClip([bgm, narration]).set_duration(duration).set_fps(44100)
    except Exception:
        logger.warning("Background music %s could not be mixed in; using narration only", bgm_path, exc_info=True)
        return narration


def generate_video(
    image_paths: list[str],
    caption: str,
    narration_script: str,
    headline: str | None = None,
    badge: str = "TRENDING",
) -> dict[str, Any]:
    """Assemble scene images + AI narration into an edited vertical short.

    Ken Burns motion on each scene, crossfades between scenes, an animated
    headline, word-synced karaoke subtitles, a progress bar and fades in/out.
    """
    if not image_paths:
        return {"success": False, "source": "video_gen", "error": "no images provided", "data": None}

    output_dir = Path(settings.media_output_dir) / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_id = uuid.uuid4().hex[:8]

    audio_path = output_dir / f"narration-{work_id}.mp3"
    try:
        words = asyncio.run(_synthesize_narration(narration_script, audio_path))
    except Exception as exc:
        return {"success": False, "source": "video_gen", "error": f"narration synthesis failed: {exc}", "data": None}

    try:
        from moviepy.editor import AudioFileClip, VideoClip

        from app.media.text_render import get_font

        narration_duration = AudioFileClip(str(audio_path)).duration
        duration = max(narration_duration + _TAIL, 1.5 * len(image_paths))
        if not words:
            words = _even_word_timings(narration_script, narration_duration)

        scenes = [_prepare_scene(path, i) for i, path in enumerate(image_paths)]
        renderer = _FrameRenderer(
            scenes,
            _build_header(headline or caption, badge),
            _build_subtitle_chunks(words, get_font("bold", 78)),
            duration,
        )

        video = VideoClip(renderer, duration=duration).set_audio(_build_audio(audio_path, duration))
        output_path = output_dir / f"short-{work_id}.mp4"
        video.write_videofile(
            str(output_path),
            fps=_FPS,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate="192k",
            preset="medium",
            ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            threads=4,
            logger=None,
        )
    except Exception as exc:
        logger.exception("video assembly failed")
        return {"success": False, "source": "video_gen", "error": f"video assembly failed: {exc}", "data": None}

    return {
        "success": True,
        "source": "video_gen",
        "error": None,
        "data": {
            "path": str(output_path.resolve()),
            "duration_seconds": math.floor(duration * 100) / 100,
            "intermediate_paths": [str(audio_path.resolve())],
        },
    }
