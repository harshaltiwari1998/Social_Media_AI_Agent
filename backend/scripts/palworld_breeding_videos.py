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
from scripts import humor  # noqa: E402

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


# The video's Ken Burns zoom/pan crops up to ~14% off one side of each card, so text
# stays at least ~140px from every edge.
_EDGE = 140


# Where each Pal sits on a card - (centre x, centre y, w, h) - for the animated Pals (game_videos.spotlight_card).
PAL_CARD_SLOT = (540, 515, 820, 660)
COMBO_SLOTS = ((330, 285, 400, 330), (750, 285, 400, 330), (540, 730, 400, 300))  # parent A, parent B, child


def _canvas(color, text_only: bool) -> Image.Image:
    return Image.new("RGBA", (_CARD, _CARD), (0, 0, 0, 0)) if text_only else _background(color).convert("RGBA")


def pal_card(name: str, color: tuple[int, int, int], tag: str, skip: bool = False, text_only: bool = False) -> Image.Image:
    """skip = leave the Pal out (it is animated in later); text_only = just the text on a transparent card."""
    canvas = _canvas(color, text_only)
    tag_img = _label(tag, 54, _YELLOW)
    canvas.alpha_composite(tag_img, ((_CARD - tag_img.width) // 2, _EDGE))
    if not (skip or text_only):
        art = _fit(fetch_pal_art(name), 600)
        _paste_with_glow(canvas, art, ((_CARD - art.width) // 2, 215 + (600 - art.height) // 2))
    name_img = _label(name, 88)
    canvas.alpha_composite(name_img, ((_CARD - name_img.width) // 2, _CARD - name_img.height - _EDGE))
    return canvas if text_only else canvas.convert("RGB")


def combo_card(parent_a: str, parent_b: str, child: str, color: tuple[int, int, int], tag: str = "",
               skip: tuple[str, ...] = (), text_only: bool = False) -> Image.Image:
    canvas = _canvas(color, text_only)
    if tag:
        tag_img = _label(tag, 54, _YELLOW)
        canvas.alpha_composite(tag_img, ((_CARD - tag_img.width) // 2, _EDGE - 20))
    for k, (name, cx) in enumerate(((parent_a, 330), (parent_b, 750))):
        if not (text_only or ("a", "b")[k] in skip):
            art = _fit(fetch_pal_art(name), 270)
            _paste_with_glow(canvas, art, (cx - art.width // 2, 150 + (270 - art.height) // 2))
        lbl = _label(name, 44)
        canvas.alpha_composite(lbl, (cx - lbl.width // 2, 435))
    plus = _label("+", 120, _YELLOW)
    canvas.alpha_composite(plus, ((_CARD - plus.width) // 2, 215))
    eq = _label("=", 100, _YELLOW)
    canvas.alpha_composite(eq, ((_CARD - eq.width) // 2, 500))
    if not (text_only or "child" in skip):
        art = _fit(fetch_pal_art(child), 230)
        _paste_with_glow(canvas, art, ((_CARD - art.width) // 2, 615 + (230 - art.height) // 2))
    lbl = _label(child, 58, _YELLOW)
    canvas.alpha_composite(lbl, ((_CARD - lbl.width) // 2, _CARD - lbl.height - _EDGE))
    return canvas if text_only else canvas.convert("RGB")


def build_video(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    a, b, child = spec["parents"][0], spec["parents"][1], spec["child"]
    color = tuple(spec.get("color", [120, 60, 200]))

    out_dir = Path(settings.media_output_dir) / "images" / spec_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    combos = spec.get("combos") or []
    if len(combos) > 1:  # "3 ways to breed X": intro, one card per combo, the new Pal
        cards = [("0_intro", pal_card(child, color, "HOW TO BREED"))]
        cards += [(f"{n}_combo", combo_card(pa, pb, child, color, f"COMBO {n}")) for n, (pa, pb) in enumerate(combos, 1)]
        cards.append((f"{len(combos) + 1}_child", pal_card(child, color, "NEW PAL")))
    else:
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
    paths = _animate(paths, cards, a, b, child, combos, color, out_dir)

    settings.tts_voice = spec.get("tts_voice") or settings.gaming_tts_voice  # Palworld is gaming -> English
    joke = humor.for_spec(spec, "breeding") if "joke" in spec else humor.pick("breeding", spec.get("key") or child)
    script = humor.insert(spec["video_script"], joke)
    if script != spec["video_script"]:
        paths = paths[:-1] + [paths[-1], paths[-1]]
    video = generate_video(paths, spec["caption"], script, headline=spec.get("headline"), badge="BREEDING GUIDE")
    return {"spec": spec_path.name, "images": paths, "video": video}


def _animate(paths: list[str], cards: list, a: str, b: str, child: str, combos: list, color, out_dir: Path) -> list:
    """Owner (Nov 2026): the Pals should move. Each card gets its Pals animated by Wan (game_videos.pal_motion,
    cached per Pal and move) in soft oval spotlights, the text on top; a Pal with no clip (quota used up) stays a
    still. Any failure keeps the still card."""
    from scripts import game_videos as gv

    out, move = [], 0
    for path, (stem, _) in zip(paths, cards):
        try:
            if stem.endswith("_combo"):
                n = int(stem.split("_")[0])
                pa, pb = (combos[n - 1] if combos else (a, b))
                pals, slots = [("a", pa), ("b", pb), ("child", child)], COMBO_SLOTS
            else:
                name = {"0_intro": child, "1_parent_a": a, "2_parent_b": b}.get(stem, child)
                pals, slots = [("pal", name)], (PAL_CARD_SLOT,)
            layers, skip = [], []
            for (key, pal), slot in zip(pals, slots):
                clip = gv.pal_motion(pal, move)
                move += 1
                if clip:
                    layers.append((clip, slot, (move * 1.7) % 5))
                    skip.append(key)
            if not layers:
                out.append(path)
                continue
            if stem.endswith("_combo"):
                base = combo_card(pa, pb, child, color, f"COMBO {n}" if combos else "", skip=tuple(skip))
                top = combo_card(pa, pb, child, color, f"COMBO {n}" if combos else "", text_only=True)
            else:
                label = {"0_intro": "HOW TO BREED", "1_parent_a": "PARENT 1", "2_parent_b": "PARENT 2"}.get(stem, "NEW PAL")
                base = pal_card(name, color, label, skip=True)
                top = pal_card(name, color, label, text_only=True)
            bp, tp = out_dir / f"{stem}_base.png", out_dir / f"{stem}_top.png"
            base.save(bp)
            top.save(tp)
            clip = gv.spotlight_card(bp, tp, layers, out_dir / f"{stem}_motion.mp4")
            out.append({"image": path, "video": str(clip.resolve())})
        except Exception as exc:
            print(f"  {stem}: animation skipped ({str(exc)[:150]}) - still card")
            out.append(path)
    return out


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


# Regular (breeding-power) combos: the child is a different species from both parents.
_NORMAL_SCRIPTS = [
    "Want {child} in Palworld without catching one? Breed {a} with {b}. Put them together on your Breeding Farm, "
    "add a cake, and the egg hatches into {child}. This pair is confirmed in the Palworld one point zero breeding data. "
    "Follow for more breeding combos!",
    "Here is an easy way to get {child} in Palworld. Pair {a} and {b} on the Breeding Farm with a cake. "
    "Their egg gives you {child}, a completely different Pal from both parents. "
    "Checked against Palworld one point zero breeding data. Comment which Pal you want next!",
    "{a} plus {b} equals {child}! That is a real Palworld one point zero breeding combo. "
    "Drop both Pals on your Breeding Farm, add a cake and wait for the egg to hatch. "
    "Save this video so you do not forget it. Which Pal should I cover next? Comment below!",
    "Breeding tip for Palworld: {child} comes from {a} and {b}. Place them together on the Breeding Farm, "
    "feed it a cake, and you get a {child} egg. It works in Palworld one point zero. "
    "What is your favourite Pal? Comment below!",
]
_NORMAL_COLOR = [
    [255, 120, 40], [90, 170, 255], [150, 80, 230], [60, 190, 120], [240, 190, 40],
]


_ORDINALS = ("one", "two", "three", "four", "five")


def spec_from_combo(a: str, b: str, child: str, index: int = 0, unique: bool = True,
                    combos: list[list[str]] | None = None, key: str | None = None) -> dict:
    """Build an English video spec for a verified combo (unique variant combo or regular breeding).

    With several verified ``combos`` for the same child the video shows every one of them."""
    if combos and len(combos) > 1 and not unique:
        color = _NORMAL_COLOR[index % len(_NORMAL_COLOR)]
        # One sentence per card (intro, each combo, outro) so the narration stays on the card it describes.
        lines = [
            "Combo {n}: breed {pa} with {pb} on your Breeding Farm, then add a cake.",
            "Combo {n}: pair {pa} and {pb} together, and you get the same egg.",
            "Combo {n}: {pa} plus {pb} works too, so use whichever Pals you have.",
            "Combo {n}: {pa} with {pb} is another easy option.",
        ]
        steps = " ".join(lines[n % len(lines)].format(n=_ORDINALS[n], pa=pa, pb=pb) for n, (pa, pb) in enumerate(combos))
        script = (f"Here are {_ORDINALS[len(combos) - 1]} ways to breed {child} in Palworld one point zero. {steps} "
                  f"Every egg hatches into {child}. Which Pal should I cover next? Comment below!")
        return {
            "headline": f"{len(combos)} Ways to Breed {child}",
            "caption": f"{len(combos)} ways to breed {child} in Palworld 1.0: "
                       + ", ".join(f"{pa} + {pb}" for pa, pb in combos) + ".",
            "video_script": script,
            "parents": list(combos[0]),
            "combos": [list(c) for c in combos],
            "child": child,
            "color": color,
            "unique": False,
            **({"key": key} if key else {}),
        }
    if unique:
        base, _, suffix = child.rpartition(" ")
        element, color = _VARIANTS.get(suffix, ("new", [120, 60, 200]))
        script = _SCRIPTS[index % len(_SCRIPTS)].format(element=element, base=base or child, child=child, a=a, b=b)
        caption = f"{a} + {b} = {child}. A unique breeding combo in Palworld 1.0."
    else:
        color = _NORMAL_COLOR[index % len(_NORMAL_COLOR)]
        script = _NORMAL_SCRIPTS[index % len(_NORMAL_SCRIPTS)].format(child=child, a=a, b=b)
        caption = f"{a} + {b} = {child}. A Palworld 1.0 breeding combo."
    return {
        "headline": f"Palworld Breeding: How to get {child}",
        "caption": caption,
        "video_script": script,
        "parents": [a, b],
        "child": child,
        "color": color,
        "unique": unique,
    }


def post_id_for(spec: dict) -> str:
    """Dedup key for the pipeline store - one per child Pal (per queue "key" for a second video of the same child)."""
    return "custom:palworld-breeding-" + spec.get("key", spec["child"]).lower().replace(" ", "-")


def post_text_and_tags(spec: dict) -> tuple[str, list[str]]:
    """Title on the first line (YouTube's title - no "." or "?", it's cut there), description, hashtags."""
    (a, b), child = spec["parents"], spec["child"]
    element = _VARIANTS.get(child.rpartition(" ")[2], ("", None))[0]
    combos = spec.get("combos") or []
    if len(combos) > 1:
        title = f"How to Breed {child} in Palworld | {len(combos)} Easy Combos | Palworld Breeding Guide"
        lines = "\n".join(f"Combo {n}: {pa} + {pb} = {child}" for n, (pa, pb) in enumerate(combos, 1))
        text = (
            f"{title}\n\n{len(combos)} ways to breed {child} in Palworld - use whichever parents you already have. "
            f"Put the two Pals on the Breeding Farm and add a Cake.\n\n{lines}\n\n"
            f"Every combo checked with Palworld 1.0 breeding calculator data and the breeding formula. "
            f"Comment which Pal you want next!\n\n"
            f"#Palworld #PalworldBreeding #{child.replace(' ', '')} #Gaming #Shorts"
        )
        parents = sorted({p for c in combos for p in c})
        tags = ["Palworld", "Palworld breeding", "Palworld breeding guide", "Palworld 1.0", child,
                f"{child} breeding", f"how to breed {child}", *parents[:6], "Palworld breeding combos", "Palworld shorts"]
        return text, tags
    title = f"How to Get {child} in Palworld | {a} + {b} | Palworld Breeding Guide"
    text = (
        f"{title}\n\n"
        f"Want {child} in Palworld? Put {a} and {b} together on the Breeding Farm and add a Cake. "
        + (
            f"This is a UNIQUE breeding combo - these two parents always hatch {child}.\n\n"
            if spec.get("unique", True)
            else f"Their egg hatches into {child} - a different Pal from both parents.\n\n"
        )
        + f"Breeding combo:\n{a} + {b} = {child}\n\n"
        f"Verified for Palworld 1.0. "
        f"Follow for more breeding combos, and comment which Pal you want next!\n\n"
        f"#Palworld #PalworldBreeding #{child.replace(' ', '')} #Gaming #Shorts"
    )
    tags = [
        "Palworld", "Palworld breeding", "Palworld breeding guide", "Palworld 1.0",
        child, f"{child} breeding", f"how to get {child}", a, b,
        *([f"Palworld {element} Pal"] if element else []),
        "Palworld unique combo" if spec.get("unique", True) else "Palworld breeding calculator",
        "Palworld breeding combos", "Palworld shorts",
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
