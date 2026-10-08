"""Gaming and movie shorts built from real images (game screenshots, cover art, posters).

A spec in scripts/specs/games/*.json or scripts/specs/movies/*.json describes the
narration, post text and 4 scenes ("category": "movies" for movie specs, else gaming).
Each scene shows a real image - "steam:<appid>:<n>" (nth official Steam screenshot)
or "wiki:<Wikipedia article>" (the article's infobox image, usually the cover art)
or "wikifile:<File name>" (a specific Wikipedia/Commons file)
or "yt:<video id>:<maxresdefault|maxres1-3>" (frames of an official YouTube trailer) -
either as an image card with a label, or as a "fact" card (big text over the image).
Images are cached under media_output/game_art/.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.media.text_render import get_font, render_block, render_runs, wrap  # noqa: E402
from app.media.video_gen import generate_video  # noqa: E402

# Wikimedia asks for a descriptive User-Agent with contact info.
_HEADERS = {"User-Agent": "AITrendPoster/1.0 (https://harshaltiwari1998.github.io/; namewriter20081998@gmail.com)"}
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_CARD = 1080
_WHITE, _YELLOW = (255, 255, 255), (255, 214, 0)


def _art_dir() -> Path:
    path = Path(settings.media_output_dir) / "game_art"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_json(url: str, params: dict) -> dict:
    """GET JSON, backing off when Wikipedia/Steam rate-limit us (they answer with HTML or a 429)."""
    for attempt in range(5):
        response = httpx.get(url, params=params, headers=_HEADERS, timeout=30)
        try:
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPStatusError, ValueError):
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def _resolve_url(ref: str) -> str:
    kind, _, rest = ref.partition(":")
    if kind == "steam":
        appid, _, index = rest.partition(":")
        data = _get_json("https://store.steampowered.com/api/appdetails", {"appids": appid, "filters": "screenshots"})[appid]
        shots = (data.get("data") or {}).get("screenshots") or []
        if not shots:
            raise RuntimeError(f"no Steam screenshots for app {appid}")
        return shots[int(index or 0) % len(shots)]["path_full"]
    if kind == "yt":
        # Official trailer frames: yt:<video id>:<maxresdefault|maxres1|maxres2|maxres3>
        video_id, _, variant = rest.partition(":")
        return f"https://i.ytimg.com/vi/{video_id}/{variant or 'maxresdefault'}.jpg"
    if kind == "wikifile":
        files = _get_json(
            _WIKI_API, {"action": "query", "titles": f"File:{rest}", "prop": "imageinfo", "iiprop": "url", "format": "json"}
        )["query"]["pages"]
        return next(iter(files.values()))["imageinfo"][0]["url"]
    if kind == "wiki":
        pages = _get_json(
            _WIKI_API, {"action": "query", "titles": rest, "prop": "pageprops", "ppprop": "page_image|page_image_free", "format": "json", "redirects": 1}
        )["query"]["pages"]
        props = next(iter(pages.values())).get("pageprops", {})
        image = props.get("page_image") or props.get("page_image_free")
        if not image:
            raise RuntimeError(f"no infobox image on Wikipedia article {rest!r}")
        files = _get_json(
            _WIKI_API, {"action": "query", "titles": f"File:{image}", "prop": "imageinfo", "iiprop": "url", "format": "json"}
        )["query"]["pages"]
        return next(iter(files.values()))["imageinfo"][0]["url"]
    raise ValueError(f"unknown image ref {ref!r} (use steam:<appid>:<n>, wiki:<article>, wikifile:<file name> or yt:<id>:<frame>)")


def fetch_image(ref: str) -> Image.Image:
    cached = _art_dir() / (hashlib.sha1(ref.encode()).hexdigest()[:16] + ".png")
    if not cached.exists():
        data = httpx.get(_resolve_url(ref), headers=_HEADERS, timeout=60, follow_redirects=True).content
        Image.open(io.BytesIO(data)).convert("RGB").save(cached)
    return Image.open(cached).convert("RGB")


def _backdrop(img: Image.Image, brightness: float = 0.4) -> Image.Image:
    bg = ImageOps.fit(img, (_CARD, _CARD), Image.LANCZOS).filter(ImageFilter.GaussianBlur(30))
    return ImageEnhance.Brightness(bg).enhance(brightness).convert("RGBA")


def _fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    scale = min(max_w / img.width, max_h / img.height)
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)


# The video's Ken Burns zoom/pan crops up to ~14% off one side of each card, so all
# text stays inside a centred safe area.
_SAFE_W = 760


def _label(text: str, size: int, color=_WHITE, max_width: int = _SAFE_W, max_lines: int = 2) -> Image.Image:
    font = get_font("bold", size)
    lines = wrap(font, text, max_width=max_width, max_lines=max_lines)
    return render_block(font, lines, color=color, stroke=6, shadow=8)


def image_card(img: Image.Image, label: str | None) -> Image.Image:
    canvas = _backdrop(img)
    if not label:
        pic = _fit(img, 1000, 900)
        canvas.paste(pic, ((_CARD - pic.width) // 2, (_CARD - pic.height) // 2))
        return canvas.convert("RGB")
    # Label goes under the picture, both inside the area the Ken Burns crop never reaches.
    lbl = _label(label, 64, _YELLOW)
    pic = _fit(img, 1000, _CARD - 300 - lbl.height - 30)
    top = 150 + (_CARD - 300 - (pic.height + 30 + lbl.height)) // 2
    canvas.paste(pic, ((_CARD - pic.width) // 2, top))
    canvas.alpha_composite(lbl, ((_CARD - lbl.width) // 2, top + pic.height + 30))
    return canvas.convert("RGB")


def fact_card(img: Image.Image, fact: str, sub: str | None) -> Image.Image:
    canvas = _backdrop(img, 0.3)
    thumb = _fit(img, 600, 340)
    top = 150
    canvas.paste(thumb, ((_CARD - thumb.width) // 2, top))
    big = _label(fact, 92, _YELLOW, max_lines=3)
    y = top + thumb.height + 36
    canvas.alpha_composite(big, ((_CARD - big.width) // 2, y))
    if sub:
        small = _label(sub, 52, _WHITE, max_lines=3)
        canvas.alpha_composite(small, ((_CARD - small.width) // 2, min(y + big.height + 16, _CARD - small.height - 150)))
    return canvas.convert("RGB")


_BADGES = {"new": "NEW GAME", "classic": "CLASSIC GAME", "movie_new": "NEW MOVIE", "movie_review": "BOX OFFICE + REVIEW", "reel": "MOVIE NEWS", "tribute": "SHRADDHANJALI"}
_DEFAULT_HASHTAGS = {"gaming": ["Gaming", "GamingNews"], "movies": ["Movies", "MovieNews", "Bollywood"]}
_DEFAULT_VOICE = settings.tts_voice  # TTS_VOICE as configured, before any per-video override
_DEFAULT_RATE = settings.tts_rate


def build_video(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out_dir = Path(settings.media_output_dir) / "images" / f"game_{spec['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, scene in enumerate(spec["scenes"], 1):
        img = fetch_image(scene["image"])
        card = fact_card(img, scene["fact"], scene.get("sub")) if "fact" in scene else image_card(img, scene.get("label"))
        path = out_dir / f"{i}.png"
        card.save(path)
        paths.append(str(path.resolve()))
    # Gaming videos are English (voice, text and post copy); movie videos stay Hindi.
    settings.tts_rate = spec.get("tts_rate") or _DEFAULT_RATE
    settings.tts_voice = spec.get("tts_voice") or (
        settings.gaming_tts_voice if category_of(spec) == "gaming" else _DEFAULT_VOICE
    )
    video = generate_video(
        paths, spec["title"], spec["video_script"], headline=spec["headline"], badge=_BADGES.get(spec.get("slot"), "GAMING")
    )
    return {"spec": spec_path.name, "images": paths, "video": video}


def post_id_for(spec: dict) -> str:
    return f"custom:{'movie' if category_of(spec) == 'movies' else 'game'}-{spec['id']}"


def category_of(spec: dict) -> str:
    return spec.get("category", "gaming")


def post_text_and_tags(spec: dict) -> tuple[str, list[str]]:
    """Title on the first line (YouTube's title - no "." or "?", it's cut there), description, hashtags."""
    extra = [h for h in _DEFAULT_HASHTAGS[category_of(spec)] if h not in spec.get("hashtags", [])]
    hashtags = " ".join(f"#{h}" for h in [*spec.get("hashtags", []), *extra, "Shorts"])
    return f"{spec['title']}\n\n{spec['description']}\n\n{hashtags}", list(spec.get("tags", []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gaming shorts from specs.")
    parser.add_argument("specs", nargs="+", type=Path)
    args = parser.parse_args()
    results = [build_video(p) for p in args.specs]
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    sys.exit(0 if all(r["video"]["success"] for r in results) else 1)


if __name__ == "__main__":
    main()
