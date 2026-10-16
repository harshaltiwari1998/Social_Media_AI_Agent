"""Generate the images for the "Lucario Stone Series" shorts (prompts in F:/AI AGENT/lucario-stone-series-prompts.md).

The base character is made once with FLUX; every other image is an image-to-image edit of the
base (or of the transformed form) with FLUX.2 klein, so face, pose and body stay consistent.
Uses the Pollinations API (POLLINATIONS_API_KEY in backend/.env). Images are cached, so
re-running only fills in what is missing.

  python scripts/lucario_stone_images.py OUT_DIR [--video 1|2]
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
API = "https://gen.pollinations.ai"
SIZE = (768, 1344)

STYLE = ("hyper-realistic 3D collectible figure, full body, clean light grey seamless studio background, "
         "glossy reflective floor with soft reflection, soft diffused studio lighting, octane render, "
         "8k, highly detailed textures, centered composition, vertical 9:16")
BASE = ("Lucario, blue and black jackal warrior with cream chest fur, sharp spike on its chest and back of paws, "
        "red eyes, four black appendages behind its head, standing in a confident fighting stance, " + STYLE)
KEEP = "Keep exactly the same character, face, proportions and camera framing. "

# (key, form name, stone description, element burst, transformed look, attack)
FORMS = [
    ("fire", "Blaze Lucario", "a glowing orange fire stone with flames inside", "flames and embers",
     "a fire warrior, crimson and black fur, head appendages made of living flames, glowing molten orange chest spike, fists wrapped in fire, embers floating around, burning orange eyes",
     "throws a huge fiery aura sphere that explodes on impact"),
    ("ice", "Frost Lucario", "a glowing pale blue ice crystal stone", "ice shards and frost",
     "an ice warrior, white and icy blue fur, translucent ice crystal chest spike and paw spikes, frozen ice armor on forearms, frost mist at feet, glowing pale blue eyes",
     "fires a freezing aura blast and the floor freezes into ice spikes"),
    ("thunder", "Volt Lucario", "a glowing yellow thunder stone with lightning inside", "lightning bolts",
     "a lightning warrior, black and electric yellow fur, lightning bolt shaped head appendages, crackling yellow chest spike, electric sparks running across its body, glowing yellow eyes",
     "dashes as a lightning streak and strikes with an electric palm, lightning explodes"),
    ("dark", "Shadow Lucario", "a black and purple crystal dark stone", "dark smoke and purple sparks",
     "a shadow warrior, jet black fur with glowing purple veins, dark chains wrapped around arms, curved shadow horns, purple-black smoke around body, glowing blood red eyes",
     "releases a storm of dark purple aura lightning from both hands"),
    ("dragon", "Dragon Lucario", "a glowing blue and gold dragon scale stone", "golden energy and dragon scales",
     "a dragon warrior, armored blue and gold dragon scales, large dragon wings, curved dragon horns, long spiked tail, golden chest spike, glowing golden eyes",
     "roars and fires a giant blue-gold dragon-shaped aura beam"),
    ("water", "Tidal Lucario", "a glowing blue water stone", "a water splash",
     "a water warrior, deep ocean blue wet fur, fin-shaped head appendages, coral-like chest spike, swirling water around arms, water droplets, glowing cyan eyes",
     "pushes both palms and a huge tidal wave of aura water crashes forward"),
    ("leaf", "Forest Lucario", "a glowing green leaf stone", "leaves and vines",
     "a forest warrior, mossy green fur with bark-like armor, leaf-shaped head appendages, vines wrapped around arms, thorn chest spike, small flowers, glowing green eyes",
     "spins and releases a tornado of razor leaves"),
    ("moon", "Lunar Lucario", "a glowing pale silver moon stone", "moonlight and stars",
     "a lunar warrior, silver-white fur with glowing blue crescent moon marks, flowing silver crescent mane, silver armored paws, soft moonlight aura, glowing pale blue eyes",
     "charges a giant glowing silver aura sphere and fires it"),
    ("earth", "Titan Lucario", "a brown earth stone with glowing cracks", "rocks and dust",
     "a rock titan, heavy grey-brown stone armor plates, huge boulder fists, amber crystal chest spike, glowing orange cracks, dust around feet, glowing amber eyes",
     "punches the ground and giant rock spikes erupt from the floor"),
    ("sun", "Solar Lucario", "a radiant golden sun stone glowing brightly", "golden light rays",
     "a solar god warrior, radiant gold and white fur, blazing golden halo behind head, ornate golden armor, wings made of light, glowing white-gold eyes, light rays",
     "fires a massive golden solar beam with a blinding flash and the floor shatters"),
]
VIDEO_FORMS = {1: FORMS[:5], 2: FORMS[5:]}


def _key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("POLLINATIONS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("POLLINATIONS_API_KEY missing from backend/.env")


def _retry(fn, what: str):
    for attempt in range(5):
        try:
            return fn()
        except Exception as exc:  # network hiccups, 5xx, rate limits
            wait = 15 * (attempt + 1)
            print(f"  {what}: {type(exc).__name__} {str(exc)[:120]} - retry in {wait}s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"giving up on {what}")


def text_to_image(prompt: str, out: Path, seed: int, model: str = "flux") -> None:
    if out.exists():
        return
    url = f"{API}/image/{urllib.parse.quote(prompt)}"

    def go():
        r = httpx.get(url, params={"width": SIZE[0], "height": SIZE[1], "seed": seed, "model": model, "nologo": "true"},
                      headers={"Authorization": f"Bearer {_key()}"}, timeout=300)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
        out.write_bytes(r.content)

    _retry(go, out.name)
    print("  made", out.name, flush=True)


def edit_image(prompt: str, source: Path, out: Path, model: str = "klein") -> None:
    if out.exists():
        return

    def go():
        with source.open("rb") as f:
            r = httpx.post(f"{API}/v1/images/edits", headers={"Authorization": f"Bearer {_key()}"},
                           data={"model": model, "prompt": prompt, "size": f"{SIZE[0]}x{SIZE[1]}"},
                           files={"image": (source.name, f, "image/jpeg")}, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
        out.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))

    _retry(go, out.name)
    print("  made", out.name, flush=True)


def generate(out_dir: Path, video: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / "base.jpg"
    text_to_image(BASE, base, seed=42)
    forms = VIDEO_FORMS[video]
    stones = ", ".join(f[2] for f in forms)
    edit_image(KEEP + f"Five evolution stones lie in a row on the floor in front of the character: {stones}. "
               "The character clenches its fists and a blue aura glows around it. " + STYLE,
               base, out_dir / f"hook{video}.jpg")
    for key, _name, stone, burst, look, attack in forms:
        edit_image(KEEP + f"Add {stone} lying on the floor in front of the character. The character looks down at the stone, "
                   "a faint blue aura flickers around its paws. " + STYLE, base, out_dir / f"{key}_a.jpg")
        edit_image(KEEP + f"The character punches down with a glowing blue aura fist and smashes {stone} on the floor. "
                   f"The stone shatters into pieces and releases a huge burst of {burst}, debris flying, dynamic action shot. " + STYLE,
                   base, out_dir / f"{key}_smash.jpg")
        edit_image(KEEP + f"Transform the character into {look}. Keep the same face shape, pose and body shape. " + STYLE,
                   base, out_dir / f"{key}_b.jpg")
        edit_image(KEEP + f"The transformed warrior {attack}. The floor cracks and debris flies, dynamic action shot. " + STYLE,
                   out_dir / f"{key}_b.jpg", out_dir / f"{key}_attack.jpg")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--video", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    generate(Path(args.out_dir), args.video)
