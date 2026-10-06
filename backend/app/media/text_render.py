"""Complex-script (Devanagari) text rendering for video overlays.

Pillow on Windows ships without libraqm, so ImageDraw.text() draws Hindi with
broken conjuncts and misplaced matras (e.g. "कि" comes out as "क ि"). Here
HarfBuzz (uharfbuzz) does the shaping and FreeType rasterizes the shaped glyph
ids, which renders Hindi and mixed Hindi/English text correctly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

# (path, face index inside the .ttc). Nirmala UI covers Devanagari + Latin. On Linux
# (GitHub Actions) there's no Nirmala, so the CI workflow downloads Hind (OFL, also
# Devanagari + Latin) into .cache/fonts.
_BUNDLED_FONTS = Path(__file__).resolve().parents[2] / ".cache" / "fonts"
_FONT_FACES = {
    "bold": [
        ("C:/Windows/Fonts/Nirmala.ttc", 1), (str(_BUNDLED_FONTS / "Hind-Bold.ttf"), 0),
        ("C:/Windows/Fonts/mangal.ttf", 0), ("C:/Windows/Fonts/arialbd.ttf", 0),
    ],
    "regular": [
        ("C:/Windows/Fonts/Nirmala.ttc", 0), (str(_BUNDLED_FONTS / "Hind-Regular.ttf"), 0),
        ("C:/Windows/Fonts/mangal.ttf", 0), ("C:/Windows/Fonts/arial.ttf", 0),
    ],
}
# Characters with no glyph are dropped, but these must survive even though
# fonts don't map them to visible glyphs - they steer Devanagari shaping.
_KEEP_ALWAYS = {" ", "\u200c", "\u200d"}


class Font:
    def __init__(self, weight: str, size: int) -> None:
        import freetype
        import uharfbuzz as hb

        path, index = next(((p, i) for p, i in _FONT_FACES[weight] if Path(p).exists()), (None, 0))
        if path is None:
            raise RuntimeError("no usable system font found for text rendering")

        self.size = size
        self._ft = freetype.Face(path, index)
        self._ft.set_char_size(size * 64)
        self._hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(path), index))
        self._hb_font.scale = (size * 64, size * 64)
        self.ascender = self._ft.size.ascender / 64
        self.descender = self._ft.size.descender / 64  # negative
        self.line_height = int(self.ascender - self.descender)

    def clean(self, text: str) -> str:
        """Drop characters the font can't draw (emoji etc.) instead of rendering tofu boxes."""
        kept = [c for c in text if c in _KEEP_ALWAYS or self._ft.get_char_index(ord(c))]
        return " ".join("".join(kept).split())

    def _shape(self, text: str) -> list[tuple[int, float, float, float]]:
        import uharfbuzz as hb

        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self._hb_font, buf, {"kern": True, "liga": True})
        return [
            (info.codepoint, pos.x_advance / 64, pos.x_offset / 64, pos.y_offset / 64)
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
        ]

    def measure(self, text: str) -> int:
        return int(round(sum(adv for _, adv, _, _ in self._shape(text))))

    def mask(self, text: str) -> Image.Image:
        """Render one line of text into an 'L' alpha mask, baseline-aligned."""
        import freetype

        glyphs = self._shape(text)
        width = int(round(sum(adv for _, adv, _, _ in glyphs))) + 4
        canvas = Image.new("L", (max(width, 1), self.line_height + 4), 0)
        baseline = int(self.ascender) + 2
        pen_x = 2.0

        for gid, advance, x_off, y_off in glyphs:
            self._ft.load_glyph(gid, freetype.FT_LOAD_RENDER)
            slot = self._ft.glyph
            bitmap = slot.bitmap
            if bitmap.width and bitmap.rows:
                glyph_img = Image.frombuffer(
                    "L", (bitmap.width, bitmap.rows), bytes(bitmap.buffer), "raw", "L", bitmap.pitch, 1
                )
                x = int(round(pen_x + x_off)) + slot.bitmap_left
                y = baseline - int(round(y_off)) - slot.bitmap_top
                region = (x, y, x + bitmap.width, y + bitmap.rows)
                # Glyphs overlap in Devanagari (matras, conjuncts), so merge with max, not paste.
                canvas.paste(ImageChops.lighter(canvas.crop(region), glyph_img), region)
            pen_x += advance
        return canvas


@lru_cache(maxsize=16)
def get_font(weight: str, size: int) -> Font:
    return Font(weight, size)


def wrap(font: Font, text: str, max_width: int, max_lines: int | None = None) -> list[str]:
    """Greedy word wrap by rendered width. Overflowing lines get an ellipsis."""
    lines: list[str] = []
    current = ""
    for word in font.clean(text).split(" "):
        candidate = f"{current} {word}".strip()
        if current and font.measure(candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,।.") + "…"
    return lines


def render_runs(
    font: Font,
    runs: list[tuple[str, tuple[int, int, int]]],
    stroke: int = 0,
    stroke_color: tuple[int, int, int] = (0, 0, 0),
    shadow: int = 0,
) -> Image.Image:
    """Render a single line made of differently-coloured runs (e.g. a karaoke highlight) to RGBA."""
    space = font.measure(" ") or font.size // 3
    masks = [(font.mask(text), color) for text, color in runs if text]
    width = sum(m.width for m, _ in masks) + space * (len(masks) - 1)
    pad = stroke + shadow + 2
    size = (width + pad * 2, font.line_height + 4 + pad * 2)

    fill_mask = Image.new("L", size, 0)
    color_layer = Image.new("RGB", size, (255, 255, 255))
    x = pad
    for mask, color in masks:
        fill_mask.paste(mask, (x, pad), mask)
        color_layer.paste(Image.new("RGB", mask.size, color), (x, pad), mask)
        x += mask.width + space

    out = Image.new("RGBA", size, (0, 0, 0, 0))
    if shadow:
        shadow_mask = fill_mask.filter(ImageFilter.GaussianBlur(shadow))
        shadow_mask = ImageChops.offset(shadow_mask, shadow // 2, shadow)
        out.paste(Image.new("RGBA", size, (0, 0, 0, 255)), (0, 0), shadow_mask.point(lambda v: v * 0.8))
    if stroke:
        stroke_mask = fill_mask.filter(ImageFilter.MaxFilter(stroke * 2 + 1))
        out.paste(Image.new("RGBA", size, (*stroke_color, 255)), (0, 0), stroke_mask)
    out.paste(color_layer.convert("RGBA"), (0, 0), fill_mask)
    return out


def render_block(
    font: Font,
    lines: list[str],
    color: tuple[int, int, int] = (255, 255, 255),
    line_spacing: float = 1.05,
    **style,
) -> Image.Image:
    """Render several centre-aligned lines into one RGBA image."""
    rendered = [render_runs(font, [(line, color)], **style) for line in lines]
    step = int(font.line_height * line_spacing)
    width = max(r.width for r in rendered)
    height = step * (len(rendered) - 1) + rendered[-1].height
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for i, r in enumerate(rendered):
        out.alpha_composite(r, ((width - r.width) // 2, i * step))
    return out
