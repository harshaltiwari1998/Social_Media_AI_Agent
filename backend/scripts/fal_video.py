"""Real AI-motion shorts with fal.ai: storyboard JSON -> scene images -> Kling 3.0 clips -> narrated 1080x1920 short.

  python scripts/fal_video.py STORYBOARD.json OUT_DIR

Storyboard format:
{
  "title": "...", "voice": "en-US-GuyNeural",
  "narration": ["sentence or paragraph per clip", ...],      # one entry per clip, read over that clip
  "clips": [
    {"image": "path.jpg" | {"prompt": "text-to-image prompt"} | {"edit": "instruction", "from": "path or pal:Name"}
              | {"poke": "charmander", "background": "wiki:Lava"},   # free: official artwork on a background photo
     "shots": [["shot prompt", seconds], ...]}             # 1-6 shots, total <= 15 s (Kling multi-shot)
  ]
}
"backend": "kling" (fal.ai, needs FAL_KEY in backend/.env) or "wan" (free Hugging Face ZeroGPU Space, ~5 s per shot,
480p, a few clips per day). Every generated image/clip is cached in OUT_DIR, so re-runs only pay for what is missing.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import lucario_stone_video as sv  # noqa: E402  (tts, captions, audio helpers)

QUEUE = "https://queue.fal.run"
VIDEO_MODEL = "fal-ai/kling-video/v3/turbo/standard/image-to-video"
IMAGE_MODEL = "fal-ai/flux/schnell"
EDIT_MODEL = "fal-ai/nano-banana/edit"
W, H, FPS, SR = 1080, 1920, 30, 44100


def _key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("FAL_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("FAL_KEY missing from backend/.env")


def _data_uri(path: Path) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((1440, 1440))
    import io

    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def fal_run(model: str, args: dict, timeout: int = 1800) -> dict:
    """Submit to the fal queue and wait for the result."""
    headers = {"Authorization": f"Key {_key()}", "Content-Type": "application/json"}
    r = httpx.post(f"{QUEUE}/{model}", json=args, headers=headers, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"{model} submit HTTP {r.status_code}: {r.text[:300]}")
    job = r.json()
    status_url, response_url = job["status_url"], job["response_url"]
    start = time.time()
    while time.time() - start < timeout:
        s = httpx.get(status_url, headers=headers, timeout=60).json()
        if s.get("status") == "COMPLETED":
            res = httpx.get(response_url, headers=headers, timeout=120)
            if res.status_code >= 400:
                raise RuntimeError(f"{model} result HTTP {res.status_code}: {res.text[:300]}")
            return res.json()
        if s.get("status") not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RuntimeError(f"{model} failed: {json.dumps(s)[:300]}")
        time.sleep(8)
    raise RuntimeError(f"{model} timed out")


def _download(url: str, out: Path) -> Path:
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return out


REF_PREFIXES = ("pal:", "steam:", "wiki:", "wikifile:", "yt:")


def _resolve_source(src: str, work: Path) -> Path:
    """A local path, or an image ref understood by game_videos.fetch_image (pal:Name, steam:appid:n, wiki:Article...)."""
    if src.startswith(REF_PREFIXES):
        import hashlib

        from scripts.game_videos import fetch_image

        p = work / f"ref_{hashlib.sha1(src.encode()).hexdigest()[:12]}.jpg"
        if not p.exists():
            im = fetch_image(src).convert("RGB")
            if src.startswith("pal:"):  # Pal art comes on a 16:9 card; put it on a 9:16 canvas for vertical video
                from scripts.palworld_breeding_videos import fetch_pal_art

                art = fetch_pal_art(src[4:])
                art = art.crop(art.getbbox() or (0, 0, art.width, art.height))
                canvas = Image.new("RGB", (1080, 1920), (34, 30, 52))
                scale = min(980 / art.width, 1300 / art.height)
                art = art.resize((int(art.width * scale), int(art.height * scale)), Image.LANCZOS)
                canvas.paste(art, ((1080 - art.width) // 2, (1920 - art.height) // 2 + 60), art)
                im = canvas
            im.save(p, quality=95)
        return p
    return Path(src)


def _poke_art(name: str, work: Path) -> tuple[Image.Image, tuple[int, int]]:
    """Official artwork (PokeAPI, or a local .png such as a fusion) scaled and placed in the lower half of 1080x1920."""
    import httpx

    art_path = Path(name) if name.endswith(".png") else work / f"poke_{name.lower()}.png"  # .png = local art (a fusion)
    if not art_path.exists():
        data = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{name.lower()}", timeout=30).raise_for_status().json()
        url = data["sprites"]["other"]["official-artwork"]["front_default"]
        art_path.write_bytes(httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content)
    art = Image.open(art_path).convert("RGBA")
    art = art.crop(art.getbbox() or (0, 0, art.width, art.height))
    scale = min(900 / art.width, 1100 / art.height)
    art = art.resize((int(art.width * scale), int(art.height * scale)), Image.LANCZOS)
    return art, ((1080 - art.width) // 2, 1920 - art.height - 380)  # standing in the lower half


def _poke_background(background: str | None, work: Path) -> Image.Image:
    from PIL import ImageEnhance, ImageFilter, ImageOps

    if not background:
        return Image.new("RGB", (1080, 1920), (34, 30, 52))
    bg = Image.open(_resolve_source(background, work)).convert("RGB")
    canvas = ImageOps.fit(bg, (1080, 1920), Image.LANCZOS).filter(ImageFilter.GaussianBlur(6))
    return ImageEnhance.Brightness(canvas).enhance(0.6)


def poke_scene(name: str, background: str | None, work: Path) -> Image.Image:
    """Free start frame: the official Pokemon artwork (PokeAPI) on a blurred, darkened 9:16 background photo."""
    canvas = _poke_background(background, work)
    art, pos = _poke_art(name, work)
    canvas.paste(art, pos, art)
    return canvas


def evolve_clip(spec: dict, work: Path, out: Path) -> Path:
    """The games' evolution sequence, drawn locally (no GPU quota): the old form lights up, white silhouettes of the
    old and new form swap faster and faster inside a glow, then the screen burns to white. The next clip fades in
    from white ("white_in"). {"evolve": [from, to], "background": ref, "seconds": 3.2, "mega": bool} - a Mega
    Evolution gets a rainbow aura."""
    if out.exists():
        return out
    import colorsys

    from moviepy.editor import VideoClip
    from PIL import ImageFilter

    seconds = float(spec.get("seconds", 3.2))
    base = _poke_background(spec.get("background"), work)
    forms = [_poke_art(n, work) for n in spec["evolve"]]
    white = Image.new("RGB", (1080, 1920), (255, 255, 255))

    def silhouette(k: int, color: tuple[int, int, int]) -> tuple[Image.Image, Image.Image, Image.Image]:
        art, pos = forms[k]
        mask = Image.new("L", (1080, 1920), 0)
        mask.paste(art.getchannel("A"), pos)
        glow = mask.filter(ImageFilter.GaussianBlur(40))
        return Image.new("RGB", (1080, 1920), color), mask, glow

    cache: dict = {}

    def layer(k: int, hue: float | None):
        key = (k, None if hue is None else round(hue, 2))
        if key not in cache:
            col = (255, 255, 255) if hue is None else tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 1.0))
            cache[key] = silhouette(k, col)
        return cache[key]

    lit, swap_end = 0.7, seconds - 0.55

    def frame(t: float) -> np.ndarray:
        im = base.copy()
        if t < lit:  # the old form starts to shine
            art, pos = forms[0]
            im.paste(art, pos, art)
            fill, mask, glow = layer(0, None)
            k = t / lit
            im = Image.composite(Image.blend(im, fill, 0.9 * k), im, mask)
            im = Image.composite(fill, im, glow.point(lambda v: int(v * k)))
            return np.asarray(im)
        # swapping silhouettes: the interval shrinks from 0.32 s to 0.04 s
        p = min(1.0, (t - lit) / max(0.1, swap_end - lit))
        interval = 0.32 * (1 - p) + 0.04 * p
        k = int((t - lit) / interval) % 2 if p < 0.85 else 1  # settle on the new form before the white
        hue = ((t * 0.9) % 1.0) if spec.get("mega") else None
        fill, mask, glow = layer(k, hue)
        im = Image.composite(fill, im, glow)
        im = Image.composite(Image.new("RGB", im.size, (255, 255, 255)), im, mask)
        if t > swap_end:  # burn to white
            im = Image.blend(im, white, min(1.0, (t - swap_end) / 0.45))
        return np.asarray(im)

    VideoClip(frame, duration=seconds).write_videofile(str(out), fps=FPS, codec="libx264", audio=False, preset="medium",
                                                      logger=None)
    print("  evolve", out.name, flush=True)
    return out


def idle_clip(spec: dict, work: Path, out: Path) -> Path:
    """The fused creature brought to life locally (no GPU, the design can never change - owner 2026-11-09: Wan turned
    Darkceus into a different character and broke its eyes). The art breathes, floats and sways in front of a pulsing
    aura in its type colour while energy particles rise; "mode": "power" makes the aura swell and the camera shake.
    {"idle": art_png_or_slug, "background": ref, "seconds": 5, "mode": "idle" | "power", "color": [r, g, b]}"""
    if out.exists():
        return out
    from moviepy.editor import VideoClip
    from PIL import ImageDraw, ImageFilter

    secs = float(spec.get("seconds", 5))
    power = spec.get("mode") == "power"
    col = tuple(spec.get("color", (140, 190, 255)))
    base = _poke_background(spec.get("background"), work).convert("RGBA")
    art, (px, py) = _poke_art(spec["idle"], work)
    cx, cy = px + art.width / 2, py + art.height / 2
    pad = 160
    tint = Image.new("RGBA", art.size, col + (255,))
    tint.putalpha(art.getchannel("A"))
    aura = Image.new("RGBA", (art.width + 2 * pad, art.height + 2 * pad), (0, 0, 0, 0))
    aura.alpha_composite(tint, (pad, pad))
    aura = aura.filter(ImageFilter.GaussianBlur(45))
    rng = np.random.default_rng(7)
    parts = rng.random((70, 4))  # x offset, phase, speed, size

    def frame(t):
        grow = min(1.0, t / (secs * 0.7)) if power else 0.0
        canvas = base.copy()
        # aura: pulses gently, swells while powering up
        s = (1.0 + 0.05 * np.sin(2 * np.pi * t / 1.4)) * (1.0 + 0.35 * grow)
        a = aura.resize((int(aura.width * s), int(aura.height * s)), Image.BILINEAR)
        strength = (0.55 + 0.2 * np.sin(2 * np.pi * t / 1.4)) * (1.0 + 0.6 * grow)
        a.putalpha(a.getchannel("A").point(lambda v: min(255, int(v * strength))))
        canvas.alpha_composite(a, (int(cx - a.width / 2), int(cy - a.height / 2)))
        # rising energy particles in the type colour
        d = ImageDraw.Draw(canvas)
        speed_up = 1.0 + 2.0 * grow
        for x0, ph, sp, sz in parts:
            p = (ph + t * (0.12 + 0.25 * sp) * speed_up) % 1.0
            x = cx + (x0 - 0.5) * art.width * 1.3
            y = py + art.height + 60 - p * (art.height + 300)
            r = 5 + sz * 10
            alpha = int(220 * np.sin(np.pi * p))
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (alpha,))
            d.ellipse([x - r / 2.5, y - r / 2.5, x + r / 2.5, y + r / 2.5], fill=(255, 255, 255, alpha))
        # the creature: breathes (stretches up a little), floats and sways
        br = 0.018 * np.sin(2 * np.pi * t / 1.6)
        w2, h2 = int(art.width * (1 - br / 2)), int(art.height * (1 + br))
        body = art.resize((w2, h2), Image.BICUBIC).rotate(1.8 * np.sin(2 * np.pi * t / 3.2), Image.BICUBIC, expand=True)
        bob = -20 * np.sin(2 * np.pi * t / 2.3)
        shake = (8 * grow * np.sin(t * 57), 6 * grow * np.cos(t * 43)) if power else (0, 0)
        canvas.alpha_composite(body, (int(cx - body.width / 2 + shake[0]), int(py + art.height - body.height + bob + shake[1])))
        # slow camera push in
        z = 1.0 + 0.06 * (t / secs)
        cw, ch = int(W / z), int(H / z)
        img = canvas.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)
        if power and t > secs - 0.5:  # a short flash as the power peaks
            k = (t - (secs - 0.5)) / 0.5
            img = Image.blend(img, Image.new("RGBA", (W, H), (255, 255, 255, 255)), 0.55 * np.sin(np.pi * k))
        return np.asarray(img.convert("RGB"))

    VideoClip(frame, duration=secs).write_videofile(str(out), fps=FPS, codec="libx264", audio=False, preset="medium", logger=None)
    print("  idle", out.name, flush=True)
    return out


def _glow_sprite(col: tuple, size: int = 512) -> Image.Image:
    """A soft round glow (white core fading into the colour) - resized per frame instead of blurring a full frame."""
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (size / 2)
    a = np.clip(1 - d, 0, 1) ** 1.6
    core = np.clip(1 - d * 2.2, 0, 1)
    rgb = np.stack([col[i] * (1 - core) + 255 * core for i in range(3)], axis=2)
    return Image.fromarray(np.dstack([rgb, a * 255]).astype(np.uint8), "RGBA")


def action_clip(spec: dict, work: Path, out: Path) -> Path:
    """The fused creature DOES something, drawn locally so its design never changes (owner 2026-11-09: "kuch kar raha
    ho" after a 3 s still). "mode": "leap" - crouch, jump with motion trails, slam down with camera shake, a shockwave
    ring and a dust burst, then a breathing stance. "mode": "blast" - an energy ball charges in front of it while
    particles rush in, then fires at the camera: the glow fills the screen, flash, shake, smoke settles.
    More moves, picked per type / character by pokemon_daily.MOVES: powerup, beam, breath, lightning, swirl, teleport,
    fly, levitate (see more()).
    {"action": art_png_or_slug, "background": ref, "seconds": 3.6, "mode": <move>, "color": [r, g, b]}"""
    if out.exists():
        return out
    from moviepy.editor import VideoClip
    from PIL import ImageDraw, ImageFilter

    secs = float(spec.get("seconds", 3.6))
    mode = spec.get("mode", "leap")
    col = tuple(spec.get("color", (140, 190, 255)))
    base = _poke_background(spec.get("background"), work).convert("RGBA")
    art, (px, py) = _poke_art(spec["action"], work)
    cx, feet = px + art.width / 2, py + art.height
    glow = _glow_sprite(col)
    # tall characters (Gogeta) went out of the top of the frame at the peak of a 430 px jump
    jump = int(min(430, max(120, py - 90)))
    pad = 140
    tint = Image.new("RGBA", art.size, col + (255,))
    tint.putalpha(art.getchannel("A"))
    aura = Image.new("RGBA", (art.width + 2 * pad, art.height + 2 * pad), (0, 0, 0, 0))
    aura.alpha_composite(tint, (pad, pad))
    aura = aura.filter(ImageFilter.GaussianBlur(40))
    rng = np.random.default_rng(11)
    dust = rng.random((40, 3))      # angle, speed, size
    rush = rng.random((60, 3))      # angle, start radius, size

    def ease(u):
        u = min(max(u, 0.0), 1.0)
        return u * u * (3 - 2 * u)

    def put(canvas, img, x, y, alpha=1.0):
        if alpha < 1.0:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
        canvas.alpha_composite(img, (int(x), int(y)))

    def body(sx, sy):
        return art.resize((max(1, int(art.width * sx)), max(1, int(art.height * sy))), Image.BICUBIC)

    def aura_at(canvas, k):
        a = aura.resize((int(aura.width * k), int(aura.height * k)), Image.BILINEAR)
        put(canvas, a, cx - a.width / 2, feet - art.height / 2 - a.height / 2, 0.7)


    MORE = ("powerup", "beam", "breath", "lightning", "swirl", "teleport", "fly", "levitate", "signature")
    shapes = rng.random((48, 4))    # angle, radius, speed, size - swirl / levitate / powerup rocks
    bolts = [rng.random((9, 2)) for _ in range(6)]
    top = feet - art.height
    side = 1 if sum(map(ord, str(spec["action"]))) % 2 else -1  # which way beams / breath go
    beam_sprite = None
    if mode == "signature":  # radial energy: white-hot core, the attack's colour, rays fading out at the edge
        n = 512
        yy, xx = np.mgrid[0:n, 0:n] / (n / 2) - 1.0
        r = np.hypot(xx, yy)
        ang = np.arctan2(yy, xx)
        rays = 1 + 0.35 * np.sin(ang * 17) * np.sin(ang * 7 + 1.3)
        colour = np.exp(-(r / (0.62 * rays)) ** 3)
        core = np.exp(-(r / 0.36) ** 4)
        rgb = np.array(col, dtype=np.float32)[None, None, :] * (1 - core[..., None]) + 255 * core[..., None]
        a = np.clip(np.maximum(colour, core), 0, 1) * 255
        beam_sprite = Image.fromarray(np.dstack([rgb, a]).clip(0, 255).astype(np.uint8), "RGBA")

    def jag(d, x0, y0, x1, y1, pts, width, fill):
        line = [(x0, y0)]
        for i, (jx, jy) in enumerate(pts[1:-1], 1):
            k = i / (len(pts) - 1)
            line.append((x0 + (x1 - x0) * k + (jx - 0.5) * 120, y0 + (y1 - y0) * k + (jy - 0.5) * 40))
        line.append((x1, y1))
        d.line(line, fill=fill, width=width, joint="curve")

    def more(canvas, t):
        """Character moves (owner 2026-11-09: not the same jump for everyone). Returns (shake, flash)."""
        shake, flash = (0.0, 0.0), 0.0
        d = ImageDraw.Draw(canvas)
        if mode == "powerup":  # aura flares, rocks rise from the ground, the ground shakes, a burst at the peak
            e = ease(t / (secs * 0.75))
            aura_at(canvas, 1.0 + 0.5 * e + 0.05 * np.sin(t * 18))
            for ang, rad, sp, sz in shapes[:22]:
                x = cx + (ang - 0.5) * art.width * 2.0
                y = feet + 20 - (t * (90 + 160 * sp)) * e
                r = 8 + 20 * sz
                d.polygon([(x - r, y), (x - r * 0.3, y - r), (x + r, y - r * 0.4), (x + r * 0.6, y + r * 0.6)],
                          fill=(110 + int(40 * sz), 95, 80, 230))
            b = body(1.0 + 0.02 * e, 1.0 + 0.03 * e)
            put(canvas, b, cx - b.width / 2, feet - b.height)
            amp = 4 + 14 * e
            shake = (amp * np.sin(t * 77), amp * np.cos(t * 59))
            if t > secs * 0.75:
                k = (t - secs * 0.75) / (secs * 0.25)
                flash = 0.6 * np.sin(np.pi * min(k, 1.0))
        elif mode == "beam":  # charge between the hands at one side, then a long beam across the screen
            t_fire = secs * 0.4
            hx, hy = cx + side * art.width * 0.35, feet - art.height * 0.5
            aura_at(canvas, 1.05 + 0.1 * min(t / t_fire, 1.0))
            b = body(1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height)
            if t < t_fire:
                rad = 40 + 120 * (t / t_fire)
            else:
                u = min((t - t_fire) / 0.35, 1.0)
                end = hx + side * (W * 1.2) * u
                thick = 210 + 30 * np.sin(t * 40)
                x0, x1 = sorted((hx, end))
                outer = glow.resize((max(2, int(x1 - x0) + int(thick * 2)), int(thick * 2)), Image.BILINEAR)
                put(canvas, outer, x0 - thick, hy - thick)
                d.rounded_rectangle([x0, hy - thick * 0.36, x1, hy + thick * 0.36], radius=int(thick * 0.36),
                                    fill=(255, 255, 255, 235))
                rad = 230
                amp = 18
                shake = (amp * np.sin(t * 83), amp * np.cos(t * 61))
                flash = 0.25 if t - t_fire < 0.15 else 0.0
            g = glow.resize((int(rad * 2), int(rad * 2)), Image.BILINEAR)
            put(canvas, g, hx - rad, hy - rad)
            put(canvas, g, hx - rad, hy - rad)
        elif mode == "breath":  # a cone of fire / frost particles from the head across the screen
            b = body(1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height)
            mx, my = cx + side * art.width * 0.25, top + art.height * 0.28
            on = 0.5 < t < secs - 0.4
            for ang, rad, sp, sz in shapes:
                if not on:
                    break
                p_ = ((t * (1.2 + sp)) + ang) % 1.0
                dist = p_ * W * 0.5  # most of a long stream ran off the screen
                spread = (rad - 0.5) * 0.8 * p_
                x = mx + side * dist
                y = my + dist * (0.75 + spread)
                r = 50 + 210 * p_ * (0.5 + sz)
                g = glow.resize((int(r * 2), int(r * 2)), Image.BILINEAR)
                put(canvas, g, x - r, y - r, min(1.0, 1.3 * (1 - p_)))
            if on:
                shake = (6 * np.sin(t * 70), 6 * np.cos(t * 50))
        elif mode == "lightning":  # bolts strike around it, flash on each strike, its aura crackles
            aura_at(canvas, 1.05 + 0.08 * np.sin(t * 25))
            b = body(1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height)
            strike = int(t / (secs / 6))
            k = (t % (secs / 6)) / (secs / 6)
            if strike < 6 and k < 0.45:
                pts = bolts[strike]
                x = cx + (pts[0][0] - 0.5) * W * 0.9
                for w_, fill in ((26, col + (120,)), (10, (255, 255, 255, 255))):
                    jag(d, x, 0, x + (pts[0][1] - 0.5) * 200, feet + 10, pts, w_, fill)
                flash = 0.35 * (1 - k / 0.45)
                shake = (12 * np.sin(t * 90), 12 * np.cos(t * 70))
        elif mode in ("swirl", "levitate"):  # orbiting leaves / shards (+ rings when levitating), then a burst
            lev = mode == "levitate"
            rise = 60 * ease(t / 1.0) if lev else 0.0
            hov = 12 * np.sin(2 * np.pi * t / 1.3) if lev else 0.0
            burst = ease((t - secs * 0.7) / (secs * 0.3))
            if lev:
                for i in range(3):
                    rr = art.width * (0.55 + 0.18 * i) * (1 + 0.04 * np.sin(t * 3 + i))
                    yy = feet - art.height * 0.5 - rise - hov
                    d.ellipse([cx - rr, yy - rr * 0.28, cx + rr, yy + rr * 0.28], outline=col + (170,), width=6)
            aura_at(canvas, 1.05)
            behind, front = [], []
            for ang, rad, sp, sz in shapes:
                a_ = 2 * np.pi * ang + t * (1.5 + sp * 2)
                rr = art.width * (0.45 + 0.4 * rad) * (1 + 2.5 * burst)
                x = cx + np.cos(a_) * rr
                y = feet - art.height * (0.2 + 0.6 * sp) - rise + np.sin(a_) * rr * 0.25
                (front if np.sin(a_) > 0 else behind).append((x, y, sz, a_))

            def leaf(x, y, sz, a_):
                r = 22 + 26 * sz
                pts = [(x + np.cos(a_ + q) * r * (1.0 if j % 2 == 0 else 0.4), y + np.sin(a_ + q) * r * (1.0 if j % 2 == 0 else 0.4))
                       for j, q in enumerate((0, 1.57, 3.14, 4.71))]
                d.polygon(pts, fill=col + (int(230 * (1 - 0.7 * burst)),), outline=(255, 255, 255, 160))

            for it in behind:
                leaf(*it)
            b = body(1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height - rise - hov)
            for it in front:
                leaf(*it)
            if burst > 0:
                flash = 0.3 * np.sin(np.pi * burst)
        elif mode == "teleport":  # vanishes, flickers in at the left and right with afterimages, lands back
            spots = [0.0, -0.32, 0.32, 0.0]
            seg = secs / len(spots)
            i = min(int(t / seg), len(spots) - 1)
            k = (t - i * seg) / seg
            x = cx + spots[i] * W
            if i > 0 and k < 0.2:  # afterimage + streak from the old spot
                b0 = body(1.0, 1.0)
                put(canvas, b0, cx + spots[i - 1] * W - b0.width / 2, feet - b0.height, 0.35 * (1 - k / 0.2))
                d.line([(cx + spots[i - 1] * W, feet - art.height * 0.5), (x, feet - art.height * 0.5)], fill=col + (180,), width=12)
                flash = 0.3 if k < 0.08 else 0.0
            if k > 0.2 or i == 0:
                al = min(1.0, (k - 0.2) / 0.15) if i > 0 else 1.0
                g = glow.resize((int(art.width * 1.6), int(art.height * 1.4)), Image.BILINEAR)
                put(canvas, g, x - g.width / 2, feet - art.height * 0.5 - g.height / 2, 0.5 * al)
                b = body(1.0, 1.0)
                put(canvas, b, x - b.width / 2, feet - b.height, al)
        elif mode == "fly":  # takes off with wind streaks, hovers high, lands back down
            up = ease(t / (secs * 0.35)) - ease((t - secs * 0.75) / (secs * 0.25))
            lift = up * min(520, max(160, py - 60))
            hov = 16 * np.sin(2 * np.pi * t / 1.1) * up
            if up > 0.05:
                for ang, rad, sp, sz in shapes[:26]:
                    x = cx + (ang - 0.5) * art.width * 1.6
                    y = feet - lift + ((t * (900 + 600 * sp)) % 700)
                    d.line([(x, y), (x, y + 60 + 80 * sz)], fill=(255, 255, 255, int(150 * up)), width=4)
            b = body(1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height - lift - hov)
            if t > secs - 0.35:
                shake = (10 * np.sin(t * 80), 10 * np.cos(t * 60))
        elif mode == "signature":
            # Dragon Ball signature attack (Kamehameha, Final Flash...; owner 2026-11-09: the side beam went off to one side
            # of the screen and looked wrong). A ball charges in the hands in front of the chest while energy rushes in,
            # then the beam fires straight AT the camera: a white core ringed with the attack's colour grows until it
            # fills the screen, speed lines race outwards, the screen shakes, then the flash clears and it stands powered up.
            t_fire, t_end = secs * 0.45, secs * 0.8
            hx, hy = cx, top + art.height * 0.42

            def energy(rad_, spin, alpha=1.0):  # the soft glowing beam end, rays turning (flat discs looked like a target)
                sp_ = beam_sprite.rotate(spin, resample=Image.BILINEAR)
                sp_ = sp_.resize((max(2, int(rad_ * 2)), max(2, int(rad_ * 2))), Image.BILINEAR)
                put(canvas, sp_, hx - rad_, hy - rad_, alpha)
            aura_at(canvas, 1.05 + 0.25 * min(t / t_fire, 1.0) + 0.04 * np.sin(t * 22))
            b = body(1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height)
            if t < t_fire:  # charge
                e = t / t_fire
                for ang, r0, sz in rush:
                    p_ = (t * 1.4 + r0) % 1.0
                    dist = (1 - p_) * (260 + 380 * r0)
                    x, y = hx + np.cos(2 * np.pi * ang) * dist, hy + np.sin(2 * np.pi * ang) * dist
                    rr = 4 + 8 * sz
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (int(230 * p_),))
                rad = 60 + 190 * e ** 1.2 + 12 * np.sin(t * 35)
                g = glow.resize((int(rad * 2.4), int(rad * 2.4)), Image.BILINEAR)
                put(canvas, g, hx - rad * 1.2, hy - rad * 1.2)
                energy(rad, t * 240)
                if int(t * 14) % 3 == 0:  # crackles around the ball
                    for k_ in range(3):
                        a_ = 2 * np.pi * ((t * 3.7 + k_ * 0.33) % 1.0)
                        pts = bolts[k_]
                        jag(d, hx + np.cos(a_) * rad * 0.4, hy + np.sin(a_) * rad * 0.4,
                            hx + np.cos(a_) * rad * 1.3, hy + np.sin(a_) * rad * 1.3, pts[:4] * 0.25 + 0.375, 4,
                            (255, 255, 255, 220))
                amp = 3 + 8 * e
                shake = (amp * np.sin(t * 61), amp * np.cos(t * 47))
            elif t < t_end:  # fire at the camera
                u = (t - t_fire) / (t_end - t_fire)
                rad = 210 + 760 * ease(u / 0.35) + 25 * np.sin(t * 45)
                for ang, r0, sz in rush:  # speed lines racing out from the beam to the screen edges
                    a_ = 2 * np.pi * ang
                    r1 = rad * 0.7 + ((t * 2600 + r0 * 1100) % 1100)
                    r2 = r1 + 120 + 200 * sz
                    d.line([(hx + np.cos(a_) * r1, hy + np.sin(a_) * r1), (hx + np.cos(a_) * r2, hy + np.sin(a_) * r2)],
                           fill=(255, 255, 255, 190), width=int(3 + 6 * sz))
                outer = glow.resize((int(rad * 2.6), int(rad * 2.6)), Image.BILINEAR)
                put(canvas, outer, hx - rad * 1.3, hy - rad * 1.3)
                for k_ in range(3):  # shock rings rushing out of the beam towards the viewer
                    q = ((t - t_fire) * 1.8 + k_ / 3) % 1.0
                    rr = rad * (0.6 + 1.2 * q)
                    d.ellipse([hx - rr, hy - rr, hx + rr, hy + rr], outline=col + (int(200 * (1 - q)),),
                              width=int(10 + 30 * (1 - q)))
                energy(rad * (1.15 + 0.04 * np.sin(t * 50)), t * 300)
                amp = 24
                shake = (amp * np.sin(t * 83), amp * np.cos(t * 67))
                flash = 0.3 if u < 0.06 else 0.0
            else:  # the flash clears, it stands there powered up
                k = (t - t_end) / (secs - t_end)
                flash = 0.85 * (1 - ease(k))
        return shake, flash

    def frame(t):
        canvas = base.copy()
        shake = (0.0, 0.0)
        flash = 0.0
        if mode in MORE:
            shake, flash = more(canvas, t)
        elif mode == "leap":
            t_jump, t_land = 0.5, 1.3
            if t < t_jump:  # crouch
                e = ease(t / t_jump)
                sx, sy, lift = 1 + 0.08 * e, 1 - 0.12 * e, 0.0
            elif t < t_land:  # jump up and back down
                u = (t - t_jump) / (t_land - t_jump)
                sx, sy, lift = 0.96, 1.06, jump * np.sin(np.pi * u)
            else:  # slam, then stand
                k = (t - t_land) / 0.3
                sq = 0.15 * max(0.0, 1 - k)
                sx, sy, lift = 1 + sq * 0.6, 1 - sq, 0.0
                amp = 26 * max(0.0, 1 - (t - t_land) / 0.7)
                shake = (amp * np.sin(t * 71), amp * np.cos(t * 53))
                # shockwave ring + dust burst at the feet
                r = (t - t_land) * 1400
                ring_a = int(200 * max(0.0, 1 - (t - t_land) / 0.6))
                if ring_a > 0:
                    d = ImageDraw.Draw(canvas)
                    d.ellipse([cx - r, feet - r * 0.22, cx + r, feet + r * 0.22], outline=col + (ring_a,), width=14)
                    d.ellipse([cx - r * 0.8, feet - r * 0.17, cx + r * 0.8, feet + r * 0.17], outline=(255, 255, 255, ring_a), width=6)
                d = ImageDraw.Draw(canvas)
                for ang, sp, sz in dust:
                    tt = t - t_land
                    if tt > 1.2:
                        break
                    a_ = np.pi * (0.05 + 0.9 * ang)  # upward half
                    dist = (250 + 600 * sp) * tt
                    x, y = cx + np.cos(a_) * dist * 1.6, feet - np.sin(a_) * dist * 0.5
                    rr = 10 + 26 * sz
                    al = int(150 * max(0.0, 1 - tt / 1.2))
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(190, 170, 140, al))
                aura_at(canvas, 1.0 + 0.04 * np.sin(2 * np.pi * t / 1.3))
            if t_jump <= t < t_land:  # motion trail
                for g in (2, 1):
                    tg = max(t_jump, t - 0.05 * g)
                    ug = (tg - t_jump) / (t_land - t_jump)
                    ghost = body(0.96, 1.06)
                    put(canvas, ghost, cx - ghost.width / 2, feet - ghost.height - jump * np.sin(np.pi * ug), 0.18 * (3 - g))
            br = 0.015 * np.sin(2 * np.pi * t / 1.5) if t > t_land + 0.3 else 0.0
            b = body(sx * (1 - br / 2), sy * (1 + br))
            put(canvas, b, cx - b.width / 2, feet - b.height - lift)
        elif mode == "blast":
            t_fire, t_flash = 1.6, 2.2
            ball_x, ball_y = cx, feet - art.height * 0.55
            if t < t_fire:  # charge
                e = t / t_fire
                aura_at(canvas, 1.0 + 0.3 * e)
                amp = 6 * e
                shake = (amp * np.sin(t * 61), amp * np.cos(t * 47))
            if t >= t_flash:  # calm aura behind it after the blast (drawn over it, it washed the creature out)
                aura_at(canvas, 1.05 + 0.04 * np.sin(2 * np.pi * t / 1.3))
            b = body(0.97 if t_fire <= t < t_fire + 0.3 else 1.0, 1.0)
            put(canvas, b, cx - b.width / 2, feet - b.height)
            d = ImageDraw.Draw(canvas)
            if t < t_fire:
                for ang, r0, sz in rush:  # particles rushing into the ball
                    p = ((t * 1.3 + r0) % 1.0)
                    dist = (1 - p) * (300 + 400 * r0)
                    x, y = ball_x + np.cos(2 * np.pi * ang) * dist, ball_y + np.sin(2 * np.pi * ang) * dist
                    rr = 4 + 8 * sz
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (int(230 * p),))
                rad = 90 + 260 * (t / t_fire) ** 1.3 + 18 * np.sin(t * 30)
            elif t < t_flash:  # fire at the camera
                u = (t - t_fire) / (t_flash - t_fire)
                rad = 210 + 1500 * u ** 1.6
                amp = 30 * u
                shake = (amp * np.sin(t * 83), amp * np.cos(t * 67))
                flash = 0.9 * u
            else:
                rad = 0
                flash = 0.9 * max(0.0, 1 - (t - t_flash) / 0.7)
                for ang, sp, sz in dust:  # sparks drifting down after the blast (grey smoke blobs covered the face)
                    tt = t - t_flash
                    x = ball_x + (ang - 0.5) * 900
                    y = ball_y - 300 + tt * (120 + 200 * sp) + 300 * sz
                    rr = 4 + 6 * sz
                    al = int(230 * max(0.0, 1 - tt / 2.3))
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (al,))
                    d.ellipse([x - rr / 2.5, y - rr / 2.5, x + rr / 2.5, y + rr / 2.5], fill=(255, 255, 255, al))
            if rad > 0:
                g = glow.resize((int(rad * 2), int(rad * 2)), Image.BILINEAR)
                put(canvas, g, ball_x - rad, ball_y - rad)
                if t < t_fire:  # second pass: a brighter core while charging
                    put(canvas, g, ball_x - rad, ball_y - rad)
        img = canvas.crop((int(40 + shake[0]), int(70 + shake[1]), int(W - 40 + shake[0]), int(H - 70 + shake[1]))).resize((W, H), Image.BILINEAR)
        if flash > 0:
            img = Image.blend(img, Image.new("RGBA", (W, H), (255, 255, 255, 255)), min(flash, 1.0))
        return np.asarray(img.convert("RGB"))

    VideoClip(frame, duration=secs).write_videofile(str(out), fps=FPS, codec="libx264", audio=False, preset="medium", logger=None)
    print("  action", mode, out.name, flush=True)
    return out


def provider_i2v(start: Path, prompt: str, out: Path, resolution: str = "720p") -> Path:
    """Wan 2.2 A14B image-to-video (5 s, 81 frames at 16 fps) through HF Inference Providers (fal-ai), billed to the
    HF credits - NOT the ZeroGPU quota the daily evolution shorts need. ~2-3 min a clip. Cached at `out`. fal's
    end_image_url did not give a start->end morph (the whole clip showed the end frame), so transformations are made
    by reversing an "engulf" clip of the new form instead (scripts/stone_forms.py)."""
    import base64
    import time

    import httpx

    if out.exists():
        return out
    headers = {"Authorization": f"Bearer {hf_token()}"}
    base = "https://router.huggingface.co/fal-ai/fal-ai/wan"
    body = {"prompt": prompt, "resolution": resolution, "num_frames": 81, "frames_per_second": 16,
            "image_url": "data:image/png;base64," + base64.b64encode(Path(start).read_bytes()).decode(),
            "negative_prompt": "blurry, deformed, extra limbs, extra eyes, morphing into a different character, text, watermark"}
    for attempt in range(3):
        try:
            r = httpx.post(f"{base}/v2.2-a14b/image-to-video?_subdomain=queue", headers=headers, json=body, timeout=120)
        except httpx.HTTPError:
            if attempt == 2:
                raise
            time.sleep(15)
            continue
        if r.status_code < 400 or attempt == 2:
            break
        time.sleep(15)
    r.raise_for_status()
    rid, t0 = r.json()["request_id"], time.time()
    while True:
        time.sleep(10)
        try:  # a DNS / network blip on the laptop (2026-11-10) must not lose a clip that is already paid for
            s = httpx.get(f"{base}/requests/{rid}/status?_subdomain=queue", headers=headers, timeout=60)
        except httpx.HTTPError:
            continue
        if s.status_code == 200 and s.json().get("status") == "COMPLETED":
            break
        if time.time() - t0 > 1200:
            raise TimeoutError(f"Wan provider clip {rid} not done after 20 min")
    for attempt in range(5):
        try:
            res = httpx.get(f"{base}/requests/{rid}?_subdomain=queue", headers=headers, timeout=60).raise_for_status().json()
            out.write_bytes(httpx.get(res["video"]["url"], timeout=300, follow_redirects=True).raise_for_status().content)
            break
        except httpx.HTTPError:
            if attempt == 4:
                raise
            time.sleep(20)
    print("  wan", out.name, f"{time.time() - t0:.0f}s", flush=True)
    return out


TOOLS = Path(r"F:\AI AGENT\tools")
ESRGAN = TOOLS / "realesrgan" / "realesrgan-ncnn-vulkan.exe"
RIFE = TOOLS / "rife" / "rife-ncnn-vulkan-20221029-windows" / "rife-ncnn-vulkan.exe"


def hd_clip(src: Path, out: Path) -> Path:
    """Owner 2026-11-09: "clarity achhi rakho, HD bilkul". A 720p 16 fps Wan clip -> RIFE v4.6 in-between frames to
    30 fps (the build's minterpolate blend ghosted the motion) -> Real-ESRGAN animevideov3 x2 (sharp anime lines) ->
    1080x1920, CRF 16. Laptop only (ncnn-vulkan tools in F:/AI AGENT/tools, RTX 3050); without them the clip is
    returned as it is."""
    import shutil
    import subprocess
    import tempfile

    import imageio_ffmpeg

    if out.exists():
        return out
    if not (ESRGAN.exists() and RIFE.exists()):
        return src
    import os
    import time

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    # one HD job on the 4 GB RTX 3050 at a time (two builds - Mewtwo stones + Dragon Ball - ran side by side)
    lock = TOOLS / "hd.lock"
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 1800:  # left behind by a killed run
                lock.unlink(missing_ok=True)
            time.sleep(5)
    os.close(fd)
    tmp = Path(tempfile.mkdtemp(dir=out.parent))
    try:
        raw, mid, up = tmp / "raw", tmp / "mid", tmp / "up"
        for d in (raw, mid, up):
            d.mkdir()
        subprocess.run([ff, "-v", "error", "-i", str(src), str(raw / "%06d.png")], check=True)
        from moviepy.editor import VideoFileClip

        clip = VideoFileClip(str(src))
        n_out = int(round(clip.duration * FPS))  # same length at 30 fps (fal sends 32 fps, ZeroGPU Wan 16 fps)
        clip.close()
        subprocess.run([str(RIFE), "-i", str(raw), "-o", str(mid), "-m", str(RIFE.parent / "rife-v4.6"),
                        "-n", str(n_out)], check=True, capture_output=True)
        subprocess.run([str(ESRGAN), "-i", str(mid), "-o", str(up), "-n", "realesr-animevideov3", "-s", "2",
                        "-f", "png"], check=True, capture_output=True, cwd=str(ESRGAN.parent))
        subprocess.run([ff, "-v", "error", "-y", "-framerate", str(FPS), "-i", str(up / "%08d.png"), "-vf",
                        f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}",
                        "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", str(out)],
                       check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        lock.unlink(missing_ok=True)
    print("  hd", out.name, flush=True)
    return out


def punch_clip(spec: dict, work: Path, out: Path) -> Path:
    """Mewtwo punches an evolution stone (owner idea 2026-11-09, scripts/stone_forms.py): the stone floats in glowing,
    Mewtwo winds back and dashes at it, the stone shatters with a flash and a shake, and its energy streams into Mewtwo
    until it glows in the stone's colour (the evolve clip takes over from there). Drawn locally.
    {"punch": art_png_or_slug, "stone": stone_png, "background": ref, "seconds": 4.2, "color": [r, g, b]}"""
    if out.exists():
        return out
    from moviepy.editor import VideoClip
    from PIL import ImageDraw, ImageFilter

    secs = float(spec.get("seconds", 4.2))
    col = tuple(spec.get("color", (255, 140, 60)))
    base = _poke_background(spec.get("background"), work).convert("RGBA")
    art, (px, py) = _poke_art(spec["punch"], work)
    art = art.resize((int(art.width * 0.62), int(art.height * 0.62)), Image.LANCZOS)  # room to dash at the stone
    stone = Image.open(spec["stone"]).convert("RGBA")
    stone = stone.crop(stone.getbbox() or (0, 0, stone.width, stone.height))
    k_ = 230 / max(stone.size)
    stone = stone.resize((int(stone.width * k_), int(stone.height * k_)), Image.LANCZOS)
    glow = _glow_sprite(col)
    feet = py + art.height / 0.62
    home_x = W * 0.33                                  # Mewtwo stands left of centre, the stone hovers on the right
    sx0, sy0 = W * 0.8, feet - art.height * 0.7
    tint = Image.new("RGBA", art.size, col + (255,))
    tint.putalpha(art.getchannel("A"))
    pad = 140
    aura = Image.new("RGBA", (art.width + 2 * pad, art.height + 2 * pad), (0, 0, 0, 0))
    aura.alpha_composite(tint, (pad, pad))
    aura = aura.filter(ImageFilter.GaussianBlur(40))
    rng = np.random.default_rng(5)
    shards = rng.random((18, 4))   # angle, speed, size, spin
    stream = rng.random((70, 3))   # phase, spread, size
    t_in, t_wind, t_hit = 1.1, 1.6, 1.95

    def ease(u):
        u = min(max(u, 0.0), 1.0)
        return u * u * (3 - 2 * u)

    def put(canvas, img, x, y, alpha=1.0):
        if alpha < 1.0:
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
        canvas.alpha_composite(img, (int(x), int(y)))

    def frame(t):
        canvas = base.copy()
        d = ImageDraw.Draw(canvas)
        shake, flash = (0.0, 0.0), 0.0
        # Mewtwo's position: wind back, dash at the stone, drift home
        if t < t_wind:
            mx = home_x - 40 * ease((t - t_in) / (t_wind - t_in))
        elif t < t_hit:
            mx = home_x - 40 + (sx0 - home_x - 150) * ease((t - t_wind) / (t_hit - t_wind)) ** 0.6
        else:
            mx = sx0 - 190 - (sx0 - 190 - home_x) * ease((t - t_hit - 0.5) / 1.0)
        power = ease((t - t_hit - 0.3) / (secs - t_hit - 0.6))  # how much of the stone's energy it has taken in
        if power > 0:
            a = aura.resize((int(aura.width * (1 + 0.15 * power)), int(aura.height * (1 + 0.15 * power))), Image.BILINEAR)
            put(canvas, a, mx - a.width / 2, feet - art.height / 2 - a.height / 2, min(1.0, 0.9 * power + 0.1))
        if t_wind <= t < t_hit + 0.1:  # dash trail
            for g in (3, 2, 1):
                put(canvas, art, mx - 60 * g - art.width / 2, feet - art.height, 0.12 * (4 - g))
        put(canvas, art, mx - art.width / 2, feet - art.height)
        if power > 0:  # it glows in the stone's colour more and more
            put(canvas, tint, mx - art.width / 2, feet - art.height, 0.45 * power * (0.8 + 0.2 * np.sin(t * 20)))
        if t < t_hit:  # the stone floats in from the top right and bobs, glowing
            u = ease(t / t_in)
            x = sx0 + (1 - u) * 260
            y = sy0 - (1 - u) * 700 + 18 * np.sin(t * 3.2)
            rad = 150 + 20 * np.sin(t * 6)
            g = glow.resize((int(rad * 2), int(rad * 2)), Image.BILINEAR)
            put(canvas, g, x - rad, y - rad, 0.8)
            put(canvas, stone, x - stone.width / 2, y - stone.height / 2)
        else:
            tt = t - t_hit
            hx, hy = sx0, sy0
            if tt < 0.5:  # impact burst
                rad = 120 + 900 * ease(tt / 0.5)
                g = glow.resize((int(rad * 2), int(rad * 2)), Image.BILINEAR)
                put(canvas, g, hx - rad, hy - rad, 1 - tt / 0.5)
                r = 60 + 1500 * tt
                d.ellipse([hx - r, hy - r, hx + r, hy + r], outline=(255, 255, 255, int(220 * (1 - tt / 0.5))), width=16)
            for ang, sp, sz, spin in shards:  # stone shards flying out and fading
                if tt > 1.2:
                    break
                a_ = 2 * np.pi * ang
                dist = (300 + 700 * sp) * tt
                x, y = hx + np.cos(a_) * dist, hy + np.sin(a_) * dist + 400 * tt * tt
                r = 14 + 30 * sz
                rot = spin * 10 * tt
                pts = [(x + np.cos(rot + q) * r * (1.0 if j % 2 else 0.55), y + np.sin(rot + q) * r * (1.0 if j % 2 else 0.55))
                       for j, q in enumerate(np.linspace(0, 2 * np.pi, 6, endpoint=False))]
                al = int(255 * max(0.0, 1 - tt / 1.2))
                d.polygon(pts, fill=col + (al,), outline=(255, 255, 255, al))
            if 0.15 < tt < secs - t_hit - 0.2:  # the energy streams from the impact point into Mewtwo's chest
                tx, ty = mx, feet - art.height * 0.55
                for ph, spr, sz in stream:
                    p = (tt * 1.6 + ph) % 1.0
                    x = hx + (tx - hx) * p
                    y = hy + (ty - hy) * p + np.sin(p * np.pi) * (spr - 0.5) * 380
                    rr = 6 + 12 * sz
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (int(230 * (1 - abs(p - 0.5))),))
                    d.ellipse([x - rr / 2.5, y - rr / 2.5, x + rr / 2.5, y + rr / 2.5], fill=(255, 255, 255, 220))
            amp = 34 * max(0.0, 1 - tt / 0.6) + 6 * power
            shake = (amp * np.sin(t * 81), amp * np.cos(t * 63))
            flash = 0.85 * max(0.0, 1 - tt / 0.25) if tt < 0.25 else 0.0
        flash = max(flash, 0.6 * ease((t - (secs - 0.4)) / 0.4))  # brightening into the evolution
        img = canvas.crop((int(40 + shake[0]), int(70 + shake[1]), int(W - 40 + shake[0]), int(H - 70 + shake[1]))).resize((W, H), Image.BILINEAR)
        if flash > 0:
            img = Image.blend(img, Image.new("RGBA", (W, H), (255, 255, 255, 255)), min(flash, 1.0))
        return np.asarray(img.convert("RGB"))

    VideoClip(frame, duration=secs).write_videofile(str(out), fps=FPS, codec="libx264", audio=False, preset="medium", logger=None)
    print("  punch", out.name, flush=True)
    return out


def fuse_clip(spec: dict, work: Path, out: Path) -> Path:
    """A fusion drawn locally (no GPU quota), in the style of evolve_clip: both parents stand side by side and light
    up, their white silhouettes slide together into one, the merged shape and the fused creature swap faster and
    faster, then the screen burns to white. {"fuse": [a, b, fused_png], "background": ref, "seconds": 3.8}"""
    if out.exists():
        return out
    from moviepy.editor import VideoClip
    from PIL import ImageFilter

    seconds = float(spec.get("seconds", 3.8))
    base = _poke_background(spec.get("background"), work)
    white = Image.new("RGB", (1080, 1920), (255, 255, 255))
    parents = []
    for name in spec["fuse"][:2]:
        art, (x, y) = _poke_art(name, work)
        small = art.resize((int(art.width * 0.6), int(art.height * 0.6)), Image.LANCZOS)
        parents.append((small, y + art.height))  # keep the feet on the same line
    fused, fpos = _poke_art(spec["fuse"][2], work)

    def mask_at(art: Image.Image, cx: float, bottom: int) -> Image.Image:
        m = Image.new("L", (1080, 1920), 0)
        m.paste(art.getchannel("A"), (int(cx - art.width / 2), bottom - art.height))
        return m

    fused_mask = Image.new("L", (1080, 1920), 0)
    fused_mask.paste(fused.getchannel("A"), fpos)
    fused_layer = (fused_mask, fused_mask.filter(ImageFilter.GaussianBlur(40)))
    lit, meet, swap_end = 0.8, 1.9, seconds - 0.55
    merged_cache: dict = {}

    def parents_at(gap: float):
        key = round(gap)
        if key not in merged_cache:
            m = Image.new("L", (1080, 1920), 0)
            for (art, bottom), cx in zip(parents, (540 - gap, 540 + gap)):
                m = Image.composite(Image.new("L", m.size, 255), m, mask_at(art, cx, bottom))
            merged_cache[key] = (m, m.filter(ImageFilter.GaussianBlur(40)))
        return merged_cache[key]

    def frame(t: float) -> np.ndarray:
        im = base.copy()
        if t < lit:  # both parents start to shine
            for (art, bottom), cx in zip(parents, (290, 790)):
                im.paste(art, (int(cx - art.width / 2), bottom - art.height), art)
            mask, glow = parents_at(250)
            k = t / lit
            im = Image.composite(Image.blend(im, white, 0.9 * k), im, mask)
            return np.asarray(Image.composite(white, im, glow.point(lambda v: int(v * k))))
        if t < meet:  # the white shapes slide together
            p = (t - lit) / (meet - lit)
            mask, glow = parents_at(250 * (1 - p * p * (3 - 2 * p)))
        else:  # merged shape <-> fused creature, faster and faster; settle on the fusion before the white
            p = min(1.0, (t - meet) / max(0.1, swap_end - meet))
            interval = 0.3 * (1 - p) + 0.04 * p
            k = int((t - meet) / interval) % 2 if p < 0.85 else 1
            mask, glow = fused_layer if k else parents_at(0)
        im = Image.composite(white, im, glow)
        im = Image.composite(white, im, mask)
        if t > swap_end:
            im = Image.blend(im, white, min(1.0, (t - swap_end) / 0.45))
        return np.asarray(im)

    VideoClip(frame, duration=seconds).write_videofile(str(out), fps=FPS, codec="libx264", audio=False, preset="medium",
                                                      logger=None)
    print("  fuse", out.name, flush=True)
    return out


def scene_image(spec, out: Path, work: Path) -> Path:
    if out.exists():
        return out
    if isinstance(spec, str):
        Image.open(_resolve_source(spec, work)).convert("RGB").save(out, quality=95)
        return out
    if "poke" in spec:  # {"poke": "charmander", "background": "wiki:Lava"} - free, no image model
        poke_scene(spec["poke"], spec.get("background"), work).save(out, quality=95)
        return out
    if "prompt" in spec:
        res = fal_run(IMAGE_MODEL, {"prompt": spec["prompt"], "image_size": "portrait_16_9", "num_images": 1})
    else:
        ref = _resolve_source(spec["from"], work)
        res = fal_run(EDIT_MODEL, {"prompt": spec["edit"] + " Vertical 9:16 composition.", "image_urls": [_data_uri(ref)],
                                   "num_images": 1, "aspect_ratio": "9:16"})
    _download(res["images"][0]["url"], out)
    print("  image", out.name, flush=True)
    return out


def make_clip(image: Path, shots: list, out: Path) -> Path:
    if out.exists():
        return out
    args = {"image_url": _data_uri(image)}
    if len(shots) == 1:
        args.update(prompt=shots[0][0], duration=str(int(shots[0][1])))
    else:
        args["multi_prompt"] = [{"prompt": p, "duration": str(int(d))} for p, d in shots]
    res = fal_run(VIDEO_MODEL, args)
    _download(res["video"]["url"], out)
    print("  clip", out.name, flush=True)
    return out


WAN_SPACE = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"  # free Hugging Face ZeroGPU Space (Wan 2.2 image-to-video)


def hf_token() -> str | None:
    """HF_TOKEN: GitHub secret on CI, backend/.env on the laptop."""
    env_file = ROOT / ".env"
    return os.environ.get("HF_TOKEN") or next(
        (l.split("=", 1)[1].strip() for l in (env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else [])
         if l.startswith("HF_TOKEN=") and l.split("=", 1)[1].strip()), None)


def make_clip_wan(image: Path, shots: list, out: Path) -> Path:
    """Free backend: one Wan 2.2 clip per shot (<= ~5 s each); every next shot starts from the previous clip's
    last frame so the motion stays continuous. Clips are joined into `out`."""
    if out.exists():
        return out
    import shutil
    import subprocess

    import imageio_ffmpeg
    from gradio_client import Client, handle_file

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    client = Client(WAN_SPACE, hf_token=hf_token(), verbose=False)  # an HF token (PRO) gives a bigger daily GPU quota
    parts, start = [], image
    for k, (prompt, secs) in enumerate(shots):
        part = out.with_name(f"{out.stem}_part{k}.mp4")
        if not part.exists():
            for attempt in range(3):
                try:
                    res = client.predict(handle_file(str(start)), prompt, 6, "", float(min(secs, 5)), 1, 1, 42, True,
                                         api_name="/generate_video")
                    break
                except Exception as exc:  # ZeroGPU quota / queue errors
                    print(f"  wan shot {k}: {str(exc)[:160]}", flush=True)
                    if "quota" in str(exc).lower():
                        raise QuotaExhausted(str(exc)) from exc
                    if attempt == 2:
                        raise
                    time.sleep(30)
            src = res[0]["video"] if isinstance(res[0], dict) else res[0]
            shutil.copy(src, part)
            shutil.rmtree(Path(src).parent, ignore_errors=True)
            print("  wan", part.name, flush=True)
        parts.append(part)
        last = out.with_name(f"{out.stem}_last{k}.jpg")
        subprocess.run([ff, "-v", "error", "-y", "-sseof", "-0.1", "-i", str(part), "-frames:v", "1", str(last)], check=True)
        start = last
    lst = out.with_name(out.stem + "_list.txt")
    lst.write_text("".join(f"file '{p.as_posix()}'" + chr(10) for p in parts), encoding="utf-8")
    subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    return out


def still_clip(image: Path, seconds: float, zoom, flash: bool, out: Path) -> Path:
    """Render a still image as a short zoom (and optional white flash) clip, using the same look as the AI clips."""
    if out.exists():
        return out
    from moviepy.editor import VideoClip

    shot = sv.Shot(sv._cover(image), seconds, zoom[0], zoom[1], flash=0.9 if flash else 0.0, shake=22 if flash else 0.0)
    VideoClip(lambda t: np.asarray(shot.frame(t)), duration=seconds).write_videofile(
        str(out), fps=FPS, codec="libx264", audio=False, preset="medium", logger=None)
    return out


class QuotaExhausted(RuntimeError):
    pass


class ClipsMissing(RuntimeError):
    """Flow backend: some clips still have to be made by hand in Google Flow (see the pack's FLOW_PROMPTS.md)."""


def flow_prompt(shots: list) -> str:
    """Veo makes one up-to-8 s clip per request, so the storyboard's shots become one flowing prompt."""
    parts = [p for p, _ in shots]
    return (parts[0] if len(parts) == 1 else parts[0] + ", then " + ", then ".join(parts[1:])) +         ". Cinematic, smooth realistic motion, vertical 9:16, no text."


def write_flow_pack(sb: dict, out_dir: Path, work: Path, missing: list[int]) -> Path:
    frames = out_dir / "start_frames"
    frames.mkdir(exist_ok=True)
    (out_dir / "clips").mkdir(exist_ok=True)
    lines = [f"# {sb.get('title', out_dir.name)} - Google Flow clip list", "",
             "For every clip below (Google Flow: https://labs.google/flow):",
             "1. New project -> **Frames to Video**. Upload the **Start frame** image.",
             "2. Paste the **Prompt**. Model: **Veo 3.1 - Fast** (20 credits) or **Lite** (10 credits). Aspect ratio **9:16 (portrait)**.",
             "3. Generate, pick the best result, download it, and save it in this pack's `clips` folder with the exact file name shown.",
             "", "When all clips are in `clips/`, the daily job (or Claude) builds the final video automatically.", ""]
    for i in missing:
        c = sb["clips"][i]
        img = out_dir / f"scene_{i:02d}.jpg"
        shutil_copy(img, frames / f"clip_{i:02d}.jpg")
        lines += [f"## clip_{i:02d}", f"- Start frame: `start_frames/clip_{i:02d}.jpg`", f"- Save as: `clips/clip_{i:02d}.mp4`",
                  "- Prompt:", "```", flow_prompt(c["shots"]), "```", ""]
    pack = out_dir / "FLOW_PROMPTS.md"
    pack.write_text(chr(10).join(lines), encoding="utf-8")
    return pack


def shutil_copy(src: Path, dst: Path) -> None:
    import shutil

    shutil.copy(src, dst)


def _clip_frames(path: Path, work: Path) -> np.memmap:
    import subprocess

    import imageio_ffmpeg

    raw = work / (path.stem + ".rgb")
    if not raw.exists():
        with raw.open("wb") as f:
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path), "-an", "-vf",
                            f"minterpolate=fps={FPS}:mi_mode=blend,scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=f, check=True)
    n = raw.stat().st_size // (W * H * 3)
    return np.memmap(raw, dtype=np.uint8, mode="r", shape=(n, H, W, 3))


def build(storyboard: Path, out_dir: Path) -> Path:
    from PIL import ImageDraw, ImageFont
    from moviepy.audio.AudioClip import AudioArrayClip
    from moviepy.editor import VideoClip

    sb = json.loads(storyboard.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    work.mkdir(exist_ok=True)
    sv.VOICE = sb.get("voice", sv.VOICE)

    clips = []
    backend = sb.get("backend", "kling")
    for i, c in enumerate(sb["clips"]):
        if "video" in c:  # an already generated clip
            clips.append(Path(c["video"]))
            continue
        if "evolve" in c:  # game-style evolution transition, drawn locally
            clips.append(evolve_clip(c, work, out_dir / f"clip_{i:02d}.mp4"))
            continue
        if "fuse" in c:  # fusion transition, drawn locally
            clips.append(fuse_clip(c, work, out_dir / f"clip_{i:02d}.mp4"))
            continue
        if "punch" in c:  # Mewtwo punches an evolution stone, drawn locally
            clips.append(punch_clip(c, work, out_dir / f"clip_{i:02d}.mp4"))
            continue
        if "idle" in c:  # the fused creature animated locally (breathing, aura, particles)
            clips.append(idle_clip(c, work, out_dir / f"clip_{i:02d}.mp4"))
            continue
        if "action" in c:  # the fused creature doing a move, drawn locally (leap / blast)
            clips.append(action_clip(c, work, out_dir / f"clip_{i:02d}.mp4"))
            continue
        if "still" in c:  # hybrid mode: a still with a zoom instead of a paid/quota-limited AI clip
            # "still" is an image ref/path, or an image spec ({"poke": ..., "background": ...}) drawn to scene_NN.jpg
            src = (scene_image(c["still"], out_dir / f"scene_{i:02d}.jpg", work) if isinstance(c["still"], dict)
                   else _resolve_source(c["still"], work))
            clips.append(still_clip(src, c.get("seconds", 3), c.get("zoom", [1.0, 1.12]), c.get("flash", False),
                                    out_dir / f"clip_{i:02d}.mp4"))
            continue
        img = scene_image(c["image"], out_dir / f"scene_{i:02d}.jpg", work)
        if backend == "flow":  # clips made by hand in Google Flow (Veo 3.1), dropped into out_dir/clips/
            clips.append(out_dir / "clips" / f"clip_{i:02d}.mp4")
            continue
        maker = make_clip_wan if backend == "wan" else make_clip
        clips.append(maker(img, c["shots"], out_dir / f"clip_{i:02d}.mp4"))
    missing = [i for i, c in enumerate(clips) if not Path(c).exists()]
    if missing:
        pack = write_flow_pack(sb, out_dir, work, missing)
        raise ClipsMissing(f"{len(missing)} of {len(clips)} clips still to make in Google Flow - see {pack}")

    frames = [_clip_frames(c, work) for c in clips]
    clip_audio = [sv._decode(c) if _has_audio(c) else None for c in clips]
    durs = [len(f) / FPS for f in frames]
    starts = np.cumsum([0] + durs[:-1])
    total = float(sum(durs))

    # narration: one block per clip, placed at the clip start (sped up slightly if it's longer than the clip)
    chunks, mix = [], np.zeros((int(total * SR) + SR, 2), dtype=np.float32)
    for i, text in enumerate(sb["narration"][: len(clips)]):
        mp3 = work / f"vo_{i:02d}.mp3"
        words = asyncio.run(sv._tts(text, mp3))
        a = sv._decode(mp3)
        st = int((starts[i] + 0.15) * SR)
        mix[st:st + len(a)] += a[: len(mix) - st]
        cur = []
        for w in words:
            cur.append(w)
            if len(cur) == 3 or w[0].endswith((".", ",", "?", "!")):
                chunks.append((starts[i] + 0.15 + cur[0][1], " ".join(x[0] for x in cur)))
                cur = []
        if cur:
            chunks.append((starts[i] + 0.15 + cur[0][1], " ".join(x[0] for x in cur)))
    for i, a in enumerate(clip_audio):
        if a is not None:
            st = int(starts[i] * SR)
            mix[st:st + len(a)] += a[: len(mix) - st] * 0.35  # Kling's own sound effects under the narration
    mix = np.clip(mix, -1, 1)[: int(total * SR)]
    chunks = [(s, chunks[k + 1][0] if k + 1 < len(chunks) else total, t) for k, (s, t) in enumerate(chunks)]

    font = ImageFont.truetype(sv.FONT, 80)
    cache = {}

    def caption(text):
        if text not in cache:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            f = font
            bw = d.textbbox((0, 0), text, font=f, stroke_width=7)
            if bw[2] - bw[0] > W - 80:
                f = ImageFont.truetype(sv.FONT, int(80 * (W - 80) / (bw[2] - bw[0])))
                bw = d.textbbox((0, 0), text, font=f, stroke_width=7)
            d.text(((W - (bw[2] - bw[0])) // 2, int(H * 0.80)), text, font=f, fill=sv.YELLOW, stroke_width=7, stroke_fill=(0, 0, 0))
            cache[text] = layer
        return cache[text]

    def make_frame(t):
        i = int(np.searchsorted(starts, t, side="right") - 1)
        fr = frames[i]
        im = Image.fromarray(np.asarray(fr[min(int((t - starts[i]) * FPS), len(fr) - 1)])).convert("RGBA")
        fade = sb["clips"][i].get("white_in", 0)
        if fade and t - starts[i] < fade:  # comes out of an evolution's white screen
            im = Image.blend(im, Image.new("RGBA", im.size, (255, 255, 255, 255)), 1 - (t - starts[i]) / fade)
        for cs, ce, txt in chunks:
            if cs <= t < ce:
                im.alpha_composite(caption(txt))
                break
        return np.asarray(im.convert("RGB"))

    out = out_dir / "final.mp4"
    VideoClip(make_frame, duration=total).set_audio(AudioArrayClip(mix, fps=SR)).write_videofile(
        str(out), fps=FPS, codec="libx264", audio_codec="aac", preset="medium", threads=4, logger=None,
        ffmpeg_params=["-crf", "17"],  # owner 2026-11-09: HD clarity (x264's default CRF 23 softened the detail)
        temp_audiofile=str(work / "tmp_audio.m4a"))
    print(f"wrote {out} ({total:.1f}s)")
    # the raw RGB frame caches are ~1 GB per clip: 42 fusion folders kept 204 GB and filled F: (2026-11-09). Drop the
    # memmaps first - Windows cannot delete a file that is still mapped - then the .rgb files (rebuilt if ever needed).
    import gc

    for i in range(len(frames)):
        frames[i] = None
    gc.collect()
    for raw in work.glob("*.rgb"):
        try:
            raw.unlink()
        except OSError:
            pass
    return out


def _has_audio(path: Path) -> bool:
    import subprocess

    import imageio_ffmpeg

    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    return "Audio:" in r.stderr


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
