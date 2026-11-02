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


def poke_scene(name: str, background: str | None, work: Path) -> Image.Image:
    """Free start frame: the official Pokemon artwork (PokeAPI) on a blurred, darkened 9:16 background photo."""
    import io

    import httpx
    from PIL import ImageEnhance, ImageFilter, ImageOps

    art_path = Path(name) if name.endswith(".png") else work / f"poke_{name.lower()}.png"  # .png = local art (a fusion)
    if not art_path.exists():
        data = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{name.lower()}", timeout=30).raise_for_status().json()
        url = data["sprites"]["other"]["official-artwork"]["front_default"]
        art_path.write_bytes(httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content)
    art = Image.open(art_path).convert("RGBA")
    art = art.crop(art.getbbox() or (0, 0, art.width, art.height))
    if background:
        bg = Image.open(_resolve_source(background, work)).convert("RGB")
        canvas = ImageOps.fit(bg, (1080, 1920), Image.LANCZOS).filter(ImageFilter.GaussianBlur(6))
        canvas = ImageEnhance.Brightness(canvas).enhance(0.6)
    else:
        canvas = Image.new("RGB", (1080, 1920), (34, 30, 52))
    scale = min(900 / art.width, 1100 / art.height)
    art = art.resize((int(art.width * scale), int(art.height * scale)), Image.LANCZOS)
    canvas.paste(art, ((1080 - art.width) // 2, 1920 - art.height - 380), art)  # standing in the lower half
    return canvas


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
        if "still" in c:  # hybrid mode: a still with a zoom instead of a paid/quota-limited AI clip
            clips.append(still_clip(_resolve_source(c["still"], work), c.get("seconds", 3), c.get("zoom", [1.0, 1.12]), c.get("flash", False),
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
