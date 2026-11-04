"""Gaming and movie shorts built from real images (game screenshots, cover art, posters).

A spec in scripts/specs/games/*.json or scripts/specs/movies/*.json describes the
narration, post text and 4 scenes ("category": "movies" for movie specs, else gaming).
Each scene shows a real image - "steam:<appid>:<n>" (nth official Steam screenshot)
or "wiki:<Wikipedia article>" (the article's infobox image, usually the cover art)
or "wikifile:<File name>" (a specific Wikipedia/Commons file)
or "yt:<video id>:<maxresdefault|maxres1-3>" (frames of an official YouTube trailer)
or "url:<https url>" (store art from the Epic/Steam APIs, used by store_deals.py) -
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
    if kind == "url":  # url:https://... - store art the store API handed us (Epic key images, Steam capsules)
        return rest
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
    raise ValueError(f"unknown image ref {ref!r} (use steam:<appid>:<n>, wiki:<article>, wikifile:<file name>, yt:<id>:<frame> or url:<https url>)")


def _pal_image(name: str) -> Image.Image:
    """Official Pal art (transparent PNG) centred on a dark backdrop, as a regular RGB image."""
    from scripts.palworld_breeding_videos import fetch_pal_art

    art = fetch_pal_art(name)
    art = art.crop(art.getbbox() or (0, 0, art.width, art.height))
    # 16:9 canvas with the art scaled UP to fill it, so it stays large inside fact cards.
    canvas = Image.new("RGB", (1600, 900), (28, 22, 48))
    scale = min(1500 / art.width, 840 / art.height)
    art = art.resize((max(1, int(art.width * scale)), max(1, int(art.height * scale))), Image.LANCZOS)
    canvas.paste(art, ((1600 - art.width) // 2, (900 - art.height) // 2), art)
    return canvas


def fetch_image(ref: str) -> Image.Image:
    if ref.startswith("pal:"):  # pal:<Pal name> - official Palworld art from the Fandom wiki
        return _pal_image(ref[4:])
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


def image_card(img: Image.Image, label: str | None, rect: list | None = None) -> Image.Image:
    canvas = _backdrop(img)
    if not label:
        pic = _fit(img, 1000, 900)
        canvas.paste(pic, ((_CARD - pic.width) // 2, (_CARD - pic.height) // 2))
        if rect is not None:
            rect[:] = [(_CARD - pic.width) // 2, (_CARD - pic.height) // 2, pic.width, pic.height]
        return canvas.convert("RGB")
    # Label goes under the picture, both inside the area the Ken Burns crop never reaches.
    lbl = _label(label, 64, _YELLOW)
    pic = _fit(img, 1000, _CARD - 300 - lbl.height - 30)
    top = 150 + (_CARD - 300 - (pic.height + 30 + lbl.height)) // 2
    canvas.paste(pic, ((_CARD - pic.width) // 2, top))
    if rect is not None:
        rect[:] = [(_CARD - pic.width) // 2, top, pic.width, pic.height]
    canvas.alpha_composite(lbl, ((_CARD - lbl.width) // 2, top + pic.height + 30))
    return canvas.convert("RGB")


def fact_card(img: Image.Image, fact: str, sub: str | None, rect: list | None = None) -> Image.Image:
    canvas = _backdrop(img, 0.3)
    # Big picture on top (most of the card), short text underneath - small thumbnails looked empty on phones.
    thumb = _fit(img, 980, 560)
    top = 120
    canvas.paste(thumb, ((_CARD - thumb.width) // 2, top))
    if rect is not None:  # where the picture sits, so gameplay footage can play there instead
        rect[:] = [(_CARD - thumb.width) // 2, top, thumb.width, thumb.height]
    big = _label(fact, 84, _YELLOW, max_lines=2)
    y = top + thumb.height + 36
    canvas.alpha_composite(big, ((_CARD - big.width) // 2, y))
    if sub:
        small = _label(sub, 50, _WHITE, max_lines=2)
        canvas.alpha_composite(small, ((_CARD - small.width) // 2, min(y + big.height + 12, _CARD - small.height - 40)))
    return canvas.convert("RGB")


_BADGES = {"new": "NEW GAME", "classic": "CLASSIC GAME", "movie_new": "NEW MOVIE", "movie_review": "BOX OFFICE + REVIEW",
           "reel": "MOVIE NEWS", "movie_news": "MOVIE NEWS", "comedy": "COMEDY", "tribute": "SHRADDHANJALI",
           "palguide": "PALWORLD GUIDE", "palbase": "BASE GUIDE", "palchain": "BREEDING CHAIN", "gamenews": "GAMING NEWS",
           "aniimo": "ANIIMO GUIDE", "gow": "GOD OF WAR", "gta6": "GTA 6", "deals": "GAME DEALS"}


def badge_for(spec: dict) -> str:
    """A spec's own "badge", else its slot's, else a neutral one for its category (never "GAMING" on a movie)."""
    return spec.get("badge") or _BADGES.get(spec.get("slot")) or ("MOVIE NEWS" if category_of(spec) == "movies" else "GAMING")
_DEFAULT_HASHTAGS = {"gaming": ["Gaming", "GamingNews"], "movies": ["Movies", "MovieNews", "Bollywood"]}
_DEFAULT_VOICE = settings.tts_voice  # TTS_VOICE as configured, before any per-video override
_DEFAULT_RATE = settings.tts_rate


_STEP_COLORS = [(90, 170, 255), (150, 80, 230), (255, 120, 40), (60, 190, 120), (240, 190, 40)]


def _combo_scene(combo: list[str], step: str | None, index: int) -> Image.Image:
    from scripts.palworld_breeding_videos import combo_card

    card = combo_card(combo[0], combo[1], combo[2], _STEP_COLORS[index % len(_STEP_COLORS)]).convert("RGBA")
    if step:
        tag = _label(step, 58, _YELLOW)
        card.alpha_composite(tag, ((_CARD - tag.width) // 2, 95))
    return card.convert("RGB")


_FOOTAGE_SECONDS = 10.0  # per scene - longer than any scene of a short


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _duration(path: Path) -> float:
    import re
    import subprocess

    err = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def trailer_list(appid: str) -> list[dict]:
    """The game's official Steam trailers, gameplay ones first (at most 3). Cinematic-only trailers come last -
    they show no actual game."""
    data = _get_json("https://store.steampowered.com/api/appdetails", {"appids": appid, "filters": "movies"})[appid]
    movies = [m for m in (data.get("data") or {}).get("movies") or [] if m.get("hls_h264")]
    rank = lambda m: (0 if any(k in m["name"].lower() for k in ("gameplay", "in-game", "recorded", "preview")) else  # noqa: E731
                      2 if "cinematic" in m["name"].lower() else 1)
    return sorted(movies, key=rank)[:3]


def trailer(appid: str, movie: dict) -> Path | None:
    """One trailer as a silent 720p mp4 (first 3 minutes), cached in game_art/trailers/."""
    import re
    import subprocess

    folder = _art_dir() / "trailers"
    folder.mkdir(exist_ok=True)
    path = folder / f"{appid}_{movie['id']}.mp4"
    if path.exists():
        return path
    master = movie["hls_h264"]
    playlist = httpx.get(master, headers=_HEADERS, timeout=30, follow_redirects=True).raise_for_status().text
    variants = re.findall(r"#EXT-X-STREAM-INF:[^\n]*RESOLUTION=\d+x(\d+)[^\n]*\n([^\n#][^\n]*)", playlist)
    fitting = [(int(h), uri.strip()) for h, uri in variants if int(h) <= 720] or [(int(h), uri.strip()) for h, uri in variants]
    url = httpx.URL(master).join(max(fitting)[1]) if fitting else master
    tmp = path.with_suffix(".part.mp4")
    r = subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(url), "-an", "-c:v", "copy", "-t", "180", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 500_000:
        tmp.replace(path)
        return path
    tmp.unlink(missing_ok=True)
    print(f"  trailer {movie['name']!r} of app {appid} could not be downloaded: {r.stderr[-200:]}")
    return None


_PAL_SLOTS = ("palguide", "palbase", "palchain")
_PAL_MOTION_PROMPT = ("the creature comes alive, breathes, looks around and moves energetically, then strikes a "
                      "battle pose, 3D game character animation, smooth motion, plain dark background, static camera")
_wan_quota_left = True


def pal_motion(name: str) -> Path | None:
    """The Pal's official art brought to life by the free Wan 2.2 ZeroGPU Space (HF_TOKEN), 5 s played forward and
    back (10 s), cached per Pal in game_art/pal_motion/ - so each Pal costs GPU quota once. None when the quota is
    used up (the scene stays a still)."""
    import subprocess

    global _wan_quota_left
    folder = _art_dir() / "pal_motion"
    folder.mkdir(exist_ok=True)
    slug = name.lower().replace(" ", "_")
    out = folder / f"{slug}.mp4"
    if out.exists() or not _wan_quota_left:
        return out if out.exists() else None
    from scripts import fal_video as fv

    still = folder / f"{slug}.png"
    _pal_image(name).save(still)
    raw = folder / f"{slug}_wan.mp4"
    try:
        fv.make_clip_wan(still, [[_PAL_MOTION_PROMPT, 5]], raw)
    except fv.QuotaExhausted:
        _wan_quota_left = False
        print(f"  {name}: free GPU quota used up - still image for now")
        return None
    except Exception as exc:
        print(f"  {name}: Wan animation failed ({str(exc)[:150]}) - still image")
        return None
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(raw), "-filter_complex",
                    "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0,fps=30[v]", "-map", "[v]", "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)], check=True)
    for f in folder.glob(f"{slug}_wan*"):
        f.unlink(missing_ok=True)
    return out


def footage_scene(card_png: Path, rect: list, trailer: Path, start: float, out: Path) -> Path:
    """The card with gameplay footage playing where its picture was (silent, card-sized mp4)."""
    import subprocess

    x, y, w, h = rect
    w, h = w - w % 2, h - h % 2
    graph = (f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1[v];"
             f"[0:v][v]overlay={x}:{y}:eof_action=repeat,format=yuv420p[out]")
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-loop", "1", "-t", str(_FOOTAGE_SECONDS), "-i", str(card_png),
                    "-ss", f"{start:.2f}", "-t", str(_FOOTAGE_SECONDS), "-i", str(trailer), "-filter_complex", graph,
                    "-map", "[out]", "-t", str(_FOOTAGE_SECONDS), "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "21", "-an", str(out)], check=True)
    return out


def build_video(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out_dir = Path(settings.media_output_dir) / "images" / f"game_{spec['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Owner (Nov 2026): shorts made only of still images looked cheap - scenes showing a Steam screenshot play the
    # game's official trailer footage there instead (silent; the narration stays). "footage": false opts out.
    seed = int(hashlib.sha1(spec["id"].encode()).hexdigest()[:8], 16)
    footage: dict[str, list[dict]] = {}
    main_pal = next((s["image"][4:] for s in spec["scenes"] if s.get("image", "").startswith("pal:")), None)
    paths = []
    for i, scene in enumerate(spec["scenes"], 1):
        rect: list = []
        if "combo" in scene:  # Palworld "A + B = C" card with official Pal art
            card = _combo_scene(scene["combo"], scene.get("step"), i)
        else:
            img = fetch_image(scene["image"])
            card = (fact_card(img, scene["fact"], scene.get("sub"), rect) if "fact" in scene
                    else image_card(img, scene.get("label"), rect))
        path = out_dir / f"{i}.png"
        card.save(path)
        entry: str | dict = str(path.resolve())
        ref = scene.get("image", "")
        pal = ref[4:] if ref.startswith("pal:") else main_pal if ref.startswith("steam:") else None
        if rect and pal and spec.get("footage", True):
            # Palworld (owner, Nov 2026): the trailer showed other Pals - the short's own Pal moves instead.
            motion = pal_motion(pal)
            if motion:
                clip = footage_scene(path, rect, motion, 0, out_dir / f"{i}_footage.mp4")
                entry = {"image": str(path.resolve()), "video": str(clip.resolve())}
        elif rect and ref.startswith("steam:") and spec.get("footage", True) and spec.get("slot") not in _PAL_SLOTS:
            appid = ref.split(":")[1]
            try:
                if appid not in footage:
                    footage[appid] = trailer_list(appid)
                if footage[appid]:  # one trailer per short (seeded by its id), a different stretch per scene
                    movie = trailer(appid, footage[appid][seed % len(footage[appid])])
                    if movie:
                        length = _duration(movie)
                        usable = max(1.0, length * 0.8 - _FOOTAGE_SECONDS)
                        start = length * 0.1 + ((seed // 7 + i * 0.29) % 1.0) * usable  # skip logos at start and end
                        clip = footage_scene(path, rect, movie, start, out_dir / f"{i}_footage.mp4")
                        entry = {"image": str(path.resolve()), "video": str(clip.resolve())}
            except Exception as exc:  # a still is still a video - never fail the short over footage
                print(f"  scene {i}: no trailer footage ({exc}) - using the still")
        paths.append(entry)
    # Gaming videos are English (voice, text and post copy); movie videos stay Hindi.
    settings.tts_rate = spec.get("tts_rate") or _DEFAULT_RATE
    settings.tts_voice = spec.get("tts_voice") or (
        settings.gaming_tts_voice if category_of(spec) == "gaming" else _DEFAULT_VOICE
    )
    video = generate_video(
        paths, spec["title"], spec["video_script"], headline=spec["headline"], badge=badge_for(spec)
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
