"""Pokemon fusion shorts from the repo's inbox/ folder - runs on GitHub Actions (fusion-inbox.yml), no laptop.

The owner makes each fusion reveal in the Gemini app (prompt: F:/AI AGENT/pokemon_fusion_prompts.md) and uploads
the clip through the GitHub website to inbox/, named after the two Pokemon:

    inbox/articuno+venusaur.mp4              fusion name from specs/fusion_pairs.json, else made from both names
    inbox/articuno+venusaur=artisaur.mp4     the name Gemini showed

Each clip becomes a 1080x1920 short: the clip centred on a blurred copy of itself (Gemini often gives 16:9), the
two names at the top for the first seconds, the fusion name + dual type (PokeAPI) in the lower third at the reveal,
and a frozen last frame with "Which fusion next" - original audio kept. Posted to the gaming platforms, recorded in
game_videos.json ("kind": "motion") + the pipeline store. Fully posted clips are listed in
media_output/fusion_done.txt so the workflow can delete them from inbox/.

    python scripts/fusion_inbox.py                 # render + post every new clip, retry unfinished ones
    python scripts/fusion_inbox.py --render CLIP   # render one clip to media_output/fusion/ only (preview)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import daily_job as dj  # noqa: E402  (chdirs to backend; Poster, manifests, store)
from scripts.lucario_stone_video import FONT  # noqa: E402  (Arial Bold Italic on the laptop, DejaVu on CI)

INBOX = ROOT.parent / "inbox"
PAIRS = ROOT / "scripts" / "specs" / "fusion_pairs.json"
OUT = ROOT / dj.settings.media_output_dir / "fusion"
DONE = ROOT / dj.settings.media_output_dir / "fusion_done.txt"
W, H, OUTRO = 1080, 1920, 2.5
YELLOW, WHITE = (255, 214, 0), (255, 255, 255)
log = logging.getLogger("fusion_inbox")


def parse(path: Path) -> tuple[str, str, str | None]:
    m = re.fullmatch(r"\s*([a-z0-9 _.-]+?)\s*\+\s*([a-z0-9 _.-]+?)\s*(?:=\s*([a-z0-9 _-]+?))?\s*", path.stem.lower())
    if not m:
        raise ValueError(f"{path.name}: name it pokemon1+pokemon2.mp4 (or pokemon1+pokemon2=fusionname.mp4)")
    slug = lambda s: re.sub(r"[\s_.]+", "-", s.strip()).strip("-")  # noqa: E731
    return slug(m[1]), slug(m[2]), (m[3].strip().replace("_", " ").title() if m[3] else None)


def display(slug: str) -> str:
    return "-".join(p.capitalize() for p in slug.split("-"))


def primary_type(slug: str) -> str:
    data = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{slug}", timeout=30)
    if data.status_code == 404:
        raise ValueError(f"PokeAPI does not know {slug!r} - check the spelling in the file name")
    return data.raise_for_status().json()["types"][0]["type"]["name"].title()


def fusion_name(a: str, b: str, given: str | None) -> str:
    if given:
        return given
    pairs = json.loads(PAIRS.read_text(encoding="utf-8")) if PAIRS.exists() else []
    for p in pairs:
        if {p["a"], p["b"]} == {a, b}:
            return p["name"]
    a2, b2 = a.replace("-", ""), b.replace("-", "")
    return (a2[: (len(a2) + 1) // 2] + b2[len(b2) // 2:]).capitalize()


def info(path: Path) -> dict:
    a, b, given = parse(path)
    types = list(dict.fromkeys([primary_type(a), primary_type(b)]))
    return {"id": f"{a}+{b}", "a": display(a), "b": display(b), "name": fusion_name(a, b, given), "types": "/".join(types)}


def _layer(lines: list[tuple[str, int, tuple]], y: int, out: Path) -> Path:
    """Full-frame transparent PNG with centred lines (text, size, colour) starting at height y."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for text, size, color in lines:
        font = ImageFont.truetype(FONT, size)
        while d.textlength(text, font=font) > W - 140 and size > 40:
            size -= 6
            font = ImageFont.truetype(FONT, size)
        x = (W - d.textlength(text, font=font)) / 2
        d.text((x, y), text, font=font, fill=color, stroke_width=max(4, size // 12), stroke_fill=(0, 0, 0))
        y += int(size * 1.25)
    im.save(out)
    return out


def _duration(ff: str, path: Path) -> tuple[float, bool]:
    r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s), "Audio:" in r.stderr


def render(src: Path, meta: dict) -> Path:
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    work = OUT / meta["id"]
    work.mkdir(parents=True, exist_ok=True)
    out = work / "final.mp4"
    dur, has_audio = _duration(ff, src)
    title = _layer([(f"{meta['a']} + {meta['b']}", 96, WHITE), ("= ???", 84, YELLOW)], 230, work / "title.png")
    reveal = _layer([(meta["name"].upper(), 128, YELLOW), (meta["types"], 70, WHITE)], int(H * 0.64), work / "reveal.png")
    outro = _layer([("Which fusion next", 84, WHITE), ("Comment below!", 76, YELLOW)], 230, work / "outro.png")
    show = max(dur - 2.5, dur * 0.7)  # Gemini reveals the fusion in the last couple of seconds
    graph = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5,eq=brightness=-0.12[bg];"
        f"[0:v]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,tpad=stop_mode=clone:stop_duration={OUTRO}[base];"
        f"[base][1:v]overlay=0:0:enable='lt(t,3)'[v1];"
        f"[v1][2:v]overlay=0:0:enable='gte(t,{show:.2f})'[v2];"
        f"[v2][3:v]overlay=0:0:enable='gte(t,{dur:.2f})'[v]"
    )
    cmd = [ff, "-v", "error", "-y", "-i", str(src), "-i", str(title), "-i", str(reveal), "-i", str(outro),
           "-filter_complex", graph + (f";[0:a]apad=pad_dur={OUTRO}[a]" if has_audio else ""), "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"] if has_audio else []
    cmd += ["-t", f"{dur + OUTRO:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out


def post_id(meta: dict) -> str:
    return f"custom:fusion-{meta['id']}"


def post_text(meta: dict) -> tuple[str, list[str]]:
    a, b, name, types = meta["a"], meta["b"], meta["name"], meta["types"]
    title = f"{a} + {b} = {name} | Pokemon Fusion | {types}"
    desc = (f"What if {a} and {b} fused? Meet {name}, a {types} Pokemon fusion. Fan-made AI animation. "
            "Which two Pokemon should fuse next? Comment below!")
    tags = [a, b, name, "Pokemon fusion", "Pokemon", "Pokemon fusion animation", f"{a} fusion", f"{b} fusion",
            "AI Pokemon", "Pokemon shorts", "fusion"]
    hashtags = " ".join(f"#{h}" for h in ["Pokemon", "PokemonFusion", name.replace(" ", ""), "Gaming", "Shorts"])
    return f"{title}\n\n{desc}\n\n{hashtags}", tags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--render", type=Path, metavar="CLIP")
    args = parser.parse_args()
    if args.render:
        print(render(args.render, info(args.render)))
        return
    log_dir = ROOT / dj.settings.media_output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_dir / f"fusion-{date.today()}.log", encoding="utf-8"), logging.StreamHandler()])
    for noisy in ("httpx", "httpcore", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    poster, games, done = dj.Poster(), dj._load(dj.GAME_MANIFEST), []
    clips = sorted(p for p in INBOX.glob("*") if p.suffix.lower() in (".mp4", ".mov", ".webm"))
    log.info("%d clip(s) in inbox/", len(clips))
    for clip in clips:
        try:
            meta = info(clip)
        except Exception as exc:
            log.error("%s: %s", clip.name, exc)
            dj.HEALTH["build_failed"] += 1
            continue
        key = f"fusion-{meta['id']}"
        if key in games and not poster.pending(post_id(meta)):
            done.append(clip.name)  # posted on an earlier run
            continue
        log.info("== %s + %s = %s (%s)", meta["a"], meta["b"], meta["name"], meta["types"])
        try:
            video = render(clip, meta)
        except Exception:
            log.exception("   render failed")
            dj.HEALTH["build_failed"] += 1
            continue
        dj.HEALTH["built"] += 1
        if key not in games:
            games[key] = {"kind": "motion", "spec": f"inbox/{clip.name}", "video": str(video), "data": meta}
            dj._save(dj.GAME_MANIFEST, games)
        poster.post(post_id(meta), *post_text(meta), str(video))
        if not poster.pending(post_id(meta)):
            done.append(clip.name)
    DONE.write_text("".join(f"{n}\n" for n in done), encoding="utf-8")
    log.info("Done (%s); %d clip(s) fully posted", ", ".join(f"{k} {v}" for k, v in dj.HEALTH.items()), len(done))
    if dj.HEALTH["post_failed"] and not dj.HEALTH["posted"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
