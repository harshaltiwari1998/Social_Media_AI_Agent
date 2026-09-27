from __future__ import annotations

import asyncio
import textwrap
import uuid
from pathlib import Path
from typing import Any

from app.config import settings

_CANVAS_SIZE = (1080, 1920)  # vertical short-form video (Reels/Shorts)
# Nirmala UI / Mangal render Devanagari (Hindi) glyphs; Arial doesn't and would
# draw the caption as empty boxes now that captions are Hindi by default.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/Nirmala.ttc",
    "C:/Windows/Fonts/mangal.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _load_font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _compose_frame(image_path: str, caption: str) -> "Image.Image":  # noqa: F821
    from PIL import Image, ImageDraw, ImageFilter

    source = Image.open(image_path).convert("RGB")
    canvas = Image.new("RGB", _CANVAS_SIZE, color=(10, 10, 10))

    # Blurred, cropped copy of the same image fills the background so square
    # Stable Diffusion output doesn't leave hard letterbox bars on a 9:16 canvas.
    bg = source.resize(_CANVAS_SIZE, Image.LANCZOS).filter(ImageFilter.GaussianBlur(30))
    canvas.paste(bg, (0, 0))

    fg_width = _CANVAS_SIZE[0]
    fg_height = int(source.height * (fg_width / source.width))
    fg = source.resize((fg_width, fg_height), Image.LANCZOS)
    canvas.paste(fg, (0, (_CANVAS_SIZE[1] - fg_height) // 2))

    draw = ImageDraw.Draw(canvas)
    font = _load_font(52)
    wrapped = textwrap.fill(caption, width=28)
    text_bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    x = (_CANVAS_SIZE[0] - text_w) // 2
    y = _CANVAS_SIZE[1] - text_h - 160

    padding = 24
    draw.rounded_rectangle(
        [x - padding, y - padding, x + text_w + padding, y + text_h + padding],
        radius=20,
        fill=(0, 0, 0, 180),
    )
    draw.multiline_text((x, y), wrapped, font=font, fill=(255, 255, 255), align="center")
    return canvas


async def _synthesize_narration(text: str, output_path: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, settings.tts_voice)
    await communicate.save(str(output_path))


def generate_video(
    image_paths: list[str],
    caption: str,
    narration_script: str,
) -> dict[str, Any]:
    """Assemble generated images + AI narration + burned-in captions into a short vertical MP4."""
    if not image_paths:
        return {"success": False, "source": "video_gen", "error": "no images provided", "data": None}

    output_dir = Path(settings.media_output_dir) / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_id = uuid.uuid4().hex[:8]

    audio_path = output_dir / f"narration-{work_id}.mp3"
    try:
        asyncio.run(_synthesize_narration(narration_script, audio_path))
    except Exception as exc:
        return {"success": False, "source": "video_gen", "error": f"narration synthesis failed: {exc}", "data": None}

    try:
        from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips

        audio_clip = AudioFileClip(str(audio_path))
        per_image_duration = max(audio_clip.duration / len(image_paths), 1.5)

        frame_paths: list[Path] = []
        clips = []
        for i, image_path in enumerate(image_paths):
            frame = _compose_frame(image_path, caption)
            frame_path = output_dir / f"frame-{work_id}-{i}.png"
            frame.save(frame_path)
            frame_paths.append(frame_path)
            clips.append(ImageClip(str(frame_path)).set_duration(per_image_duration))

        video = concatenate_videoclips(clips, method="compose").set_audio(audio_clip)
        output_path = output_dir / f"short-{work_id}.mp4"
        video.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None,
        )
    except Exception as exc:
        return {"success": False, "source": "video_gen", "error": f"video assembly failed: {exc}", "data": None}

    return {
        "success": True,
        "source": "video_gen",
        "error": None,
        "data": {
            "path": str(output_path.resolve()),
            "duration_seconds": video.duration,
            "intermediate_paths": [str(audio_path.resolve())] + [str(p.resolve()) for p in frame_paths],
        },
    }
