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
        if "idle" in c:  # the fused creature animated locally (breathing, aura, particles)
            clips.append(idle_clip(c, work, out_dir / f"clip_{i:02d}.mp4"))
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
        temp_audiofile=str(work / "tmp_audio.m4a"))
    print(f"wrote {out} ({total:.1f}s)")
    return out


def _has_audio(path: Path) -> bool:
    import subprocess

    import imageio_ffmpeg

    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    return "Audio:" in r.stderr


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
