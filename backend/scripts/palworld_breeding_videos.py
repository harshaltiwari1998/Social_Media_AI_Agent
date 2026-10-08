"""Palworld breeding-guide shorts built from the official Pal artwork.

For each spec in scripts/specs/palworld_*.json the Pal images are downloaded from
the Palworld Fandom wiki (cached under media_output/pal_art/), composed into
scene cards (parent A, parent B, "A + B = C", child), then narrated into a short
with the regular video pipeline. No Stable Diffusion involved.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.media.text_render import get_font, render_runs  # noqa: E402
from app.media.video_gen import generate_video  # noqa: E402

_WIKI_API = "https://palworld.fandom.com/api.php"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_CARD = 1080
_WHITE, _YELLOW = (255, 255, 255), (255, 214, 0)


def _art_dir() -> Path:
    path = Path(settings.media_output_dir) / "pal_art"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_pal_art(name: str) -> Image.Image:
    """Download (once) the Pal's main wiki artwork as RGBA."""
    cached = _art_dir() / f"{name.replace(' ', '_')}.png"
    if not cached.exists():
        pages = httpx.get(
            _WIKI_API,
            params={"action": "query", "titles": name, "prop": "pageimages", "piprop": "original", "format": "json"},
            headers=_HEADERS,
            timeout=30,
        ).json()["query"]["pages"]
        original = next(iter(pages.values())).get("original")
        if not original:
            raise RuntimeError(f"no wiki image found for Pal {name!r}")
        data = httpx.get(original["source"], headers=_HEADERS, timeout=60, follow_redirects=True).content
        Image.open(io.BytesIO(data)).convert("RGBA").save(cached)
    return Image.open(cached).convert("RGBA")


def _background(color: tuple[int, int, int]) -> Image.Image:
    """Radial glow in the Pal's theme colour fading to near-black."""
    small = Image.new("RGB", (256, 256))
    px = small.load()
    for y in range(256):
        for x in range(256):
            d = min(1.0, (((x - 128) ** 2 + (y - 118) ** 2) ** 0.5) / 175)
            k = (1 - d) ** 1.6
            px[x, y] = tuple(int(12 + (c - 12) * k) for c in color)
    return small.resize((_CARD, _CARD), Image.BICUBIC)


def _fit(art: Image.Image, box: int) -> Image.Image:
    bbox = art.getbbox()
    art = art.crop(bbox) if bbox else art
    scale = box / max(art.size)
    return art.resize((max(1, int(art.width * scale)), max(1, int(art.height * scale))), Image.LANCZOS)


def _paste_with_glow(canvas: Image.Image, art: Image.Image, pos: tuple[int, int]) -> None:
    # Pad before blurring - art cropped to its bbox touches the edges, and a glow
    # clipped to art.size shows up as a visible rectangle.
    pad = 60
    alpha = Image.new("L", (art.width + 2 * pad, art.height + 2 * pad), 0)
    alpha.paste(art.getchannel("A"), (pad, pad))
    glow = Image.new("RGBA", alpha.size, (255, 255, 255, 0))
    glow.putalpha(alpha.filter(ImageFilter.GaussianBlur(18)).point(lambda v: int(v * 0.45)))
    canvas.alpha_composite(glow, (pos[0] - pad, pos[1] - pad))
    canvas.alpha_composite(art, pos)


def _label(text: str, size: int, color=_WHITE) -> Image.Image:
    return render_runs(get_font("bold", size), [(text, color)], stroke=6, shadow=8)


def pal_card(name: str, color: tuple[int, int, int], tag: str) -> Image.Image:
    canvas = _background(color).convert("RGBA")
    art = _fit(fetch_pal_art(name), 760)
    _paste_with_glow(canvas, art, ((_CARD - art.width) // 2, 90 + (760 - art.height) // 2))
    tag_img = _label(tag, 54, _YELLOW)
    canvas.alpha_composite(tag_img, ((_CARD - tag_img.width) // 2, 10))
    name_img = _label(name, 96)
    canvas.alpha_composite(name_img, ((_CARD - name_img.width) // 2, _CARD - name_img.height - 30))
    return canvas.convert("RGB")


def combo_card(parent_a: str, parent_b: str, child: str, color: tuple[int, int, int]) -> Image.Image:
    canvas = _background(color).convert("RGBA")
    for name, cx in ((parent_a, 270), (parent_b, 810)):
        art = _fit(fetch_pal_art(name), 360)
        _paste_with_glow(canvas, art, (cx - art.width // 2, 70 + (360 - art.height) // 2))
        lbl = _label(name, 50)
        canvas.alpha_composite(lbl, (cx - lbl.width // 2, 440))
    plus = _label("+", 140, _YELLOW)
    canvas.alpha_composite(plus, ((_CARD - plus.width) // 2, 160))
    eq = _label("=", 120, _YELLOW)
    canvas.alpha_composite(eq, ((_CARD - eq.width) // 2, 505))
    art = _fit(fetch_pal_art(child), 330)
    _paste_with_glow(canvas, art, ((_CARD - art.width) // 2, 640 + (330 - art.height) // 2))
    lbl = _label(child, 62, _YELLOW)
    canvas.alpha_composite(lbl, ((_CARD - lbl.width) // 2, _CARD - lbl.height - 10))
    return canvas.convert("RGB")


def build_video(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    a, b, child = spec["parents"][0], spec["parents"][1], spec["child"]
    color = tuple(spec.get("color", [120, 60, 200]))

    out_dir = Path(settings.media_output_dir) / "images" / spec_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = [
        ("1_parent_a", pal_card(a, color, "PARENT 1")),
        ("2_parent_b", pal_card(b, color, "PARENT 2")),
        ("3_combo", combo_card(a, b, child, color)),
        ("4_child", pal_card(child, color, "NEW PAL")),
    ]
    paths = []
    for stem, img in cards:
        path = out_dir / f"{stem}.png"
        img.save(path)
        paths.append(str(path.resolve()))

    settings.tts_voice = spec.get("tts_voice") or settings.gaming_tts_voice  # Palworld is gaming -> English
    video = generate_video(
        paths, spec["caption"], spec["video_script"], headline=spec.get("headline"), badge="BREEDING GUIDE"
    )
    return {"spec": spec_path.name, "images": paths, "video": video}


# Variant suffix -> (element for narration and tags, card colour).
_VARIANTS = {
    "Noct": ("Dark", [140, 60, 220]),
    "Cryst": ("Ice", [80, 180, 255]),
    "Ignis": ("Fire", [255, 90, 40]),
    "Terra": ("Ground", [200, 140, 60]),
    "Lux": ("Electric", [240, 200, 40]),
    "Aqua": ("Water", [40, 150, 240]),
    "Botan": ("Grass", [80, 200, 90]),
}
_SCRIPTS = [
    "Want {child}, the {element} variant of {base}, in Palworld? Put {a} and {b} together on your Breeding Farm and add a cake. "
    "This is a unique combo, so these two parents always give you {child}. "
    "It still works after the Palworld one point zero update. Follow for more breeding combos!",
    "Here is how to breed {child} in Palworld. Place {a} and {b} on the Breeding Farm together. "
    "It is a unique combo, which means these parents will always hatch {child}. "
    "Still working in Palworld one point zero. Comment which Pal you want next!",
    "Getting {child} in Palworld is easy with breeding. Just pair {a} with {b} on the Breeding Farm. "
    "Because this is a unique combo, the egg is always {child}. "
    "Confirmed for Palworld one point zero. Save this video for later!",
    "Need {child}? Remember this combo. {a} plus {b} on the Breeding Farm, with a cake. "
    "It is a unique pairing, so you always get {child}, never anything else. "
    "Works in Palworld one point zero too. What is your favourite Pal? Comment below!",
]


def spec_from_combo(a: str, b: str, child: str, index: int = 0) -> dict:
    """Build an English video spec for a verified unique combo."""
    base, _, suffix = child.rpartition(" ")
    element, color = _VARIANTS.get(suffix, ("new", [120, 60, 200]))
    script = _SCRIPTS[index % len(_SCRIPTS)].format(element=element, base=base or child, child=child, a=a, b=b)
    return {
        "headline": f"Palworld Breeding: How to get {child}",
        "caption": f"{a} + {b} = {child}. A unique breeding combo in Palworld 1.0.",
        "video_script": script,
        "parents": [a, b],
        "child": child,
        "color": color,
    }


def post_id_for(spec: dict) -> str:
    """Dedup key for the pipeline store - one per child Pal."""
    return "custom:palworld-breeding-" + spec["child"].lower().replace(" ", "-")


def post_text_and_tags(spec: dict) -> tuple[str, list[str]]:
    """Title on the first line (YouTube's title - no "." or "?", it's cut there), description, hashtags."""
    (a, b), child = spec["parents"], spec["child"]
    element = _VARIANTS.get(child.rpartition(" ")[2], ("", None))[0]
    title = f"How to Get {child} in Palworld | {a} + {b} | Palworld Breeding Guide"
    text = (
        f"{title}\n\n"
        f"Want {child} in Palworld? Put {a} and {b} together on the Breeding Farm and add a Cake. "
        f"This is a UNIQUE breeding combo - these two parents always hatch {child}.\n\n"
        f"Breeding combo:\n{a} + {b} = {child}\n\n"
        f"Still works after the Palworld 1.0 update. "
        f"Follow for more breeding combos, and comment which Pal you want next!\n\n"
        f"#Palworld #PalworldBreeding #{child.replace(' ', '')} #Gaming #Shorts"
    )
    tags = [
        "Palworld", "Palworld breeding", "Palworld breeding guide", "Palworld 1.0",
        child, f"{child} breeding", f"how to get {child}", a, b,
        *([f"Palworld {element} Pal"] if element else []),
        "Palworld unique combo", "Palworld breeding combos", "Palworld shorts",
    ]
    return text, tags


def main() -> None:
    parser = argparse.ArgumentParser(description="Make Palworld breeding shorts with official Pal art.")
    parser.add_argument("specs", nargs="+", type=Path)
    args = parser.parse_args()

    results = [build_video(p) for p in args.specs]
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    sys.exit(0 if all(r["video"]["success"] for r in results) else 1)


if __name__ == "__main__":
    main()
