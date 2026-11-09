"""Daily free Pokemon evolution shorts - runs on GitHub Actions (daily-pokemon.yml), no laptop needed.

Works through scripts/specs/pokemon_queue.json (evolution lines checked against PokeAPI's evolution chains).
Each line becomes a 4-clip, ~20 s storyboard for fal_video.py: start frames are the official artwork on a
background photo, motion comes from the free Wan 2.2 Hugging Face ZeroGPU Space (HF_TOKEN secret - about 2-3
clips a day; HF PRO ~8x). Finished clips are cached under media_output/pokemon/<id>/ and kept between runs on the
`motion-cache` branch, so a short builds up over a couple of days; when all clips exist it is rendered and
posted to the gaming platforms (YouTube HarryBeast + gaming Facebook Page + Instagram).

Fusion shorts (scripts/specs/fusion_pairs.json) use the same quota, taking turns with the evolution lines: the fused
creature is drawn by the free FLUX.1 Kontext ZeroGPU Space from both official artworks + the pair's "look", then
Wan animates A, B and the fusion reveal. They share the "fusion-<a>+<b>" record with fusion_inbox.py, so a pair
made by hand in Gemini is never made again here.

Narration states no numbers - only the evolution line itself (PokeAPI). Posted lines are recorded in
game_videos.json ("kind": "motion") + the pipeline store like every other video.

    python scripts/pokemon_daily.py            # build what the quota allows, post finished shorts
    python scripts/pokemon_daily.py --plan ID  # print the storyboard for one queue entry
    python scripts/pokemon_daily.py --fusion-image charizard+gyarados   # only draw one fused creature (preview)
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import daily_job as dj  # noqa: E402  (chdirs to backend; Poster, manifests, store)
from scripts import fal_video as fv  # noqa: E402
from scripts import fusion_inbox as fi  # noqa: E402  (fusion post text + record shared with the inbox flow)
from scripts import humor  # noqa: E402

QUEUE = ROOT / "scripts" / "specs" / "pokemon_queue.json"
PAIRS = fi.PAIRS
KONTEXT_SPACE = "black-forest-labs/FLUX.1-Kontext-Dev"  # free Hugging Face ZeroGPU Space (image editing)
# multi-image editing: sees BOTH parents, so the fusion can mix parts of each (owner's reference, 2026-11-08: a real
# Lucario + Cinderace mix - body, mask and aura appendages of one, ears, fur collar, flame legs of the other)
# Qwen-Image-2.1 (2-step distilled) with both parents as reference images: draws a NEW standing character with clear
# limbs in the official art style, ~13 s and ~20 s of GPU (owner approved 2026-11-09: "mast bane he"). Tried before:
# Qwen-2509 "Super-Fusion" LoRA (pasted both parents together in their own poses - "pair kidhar he"), official
# Qwen-2509 (TypeError / 10-min queue), FLUX.2-klein + Qwen-2511-Fast (RuntimeError), FLUX.1 Kontext (one parent only).
QWEN_SPACE = "linoyts/qwen-image-2-1-realtime"
CACHE = ROOT / dj.settings.media_output_dir / "pokemon"
log = logging.getLogger("pokemon_daily")
SPECS = ROOT / "scripts" / "specs"

# Palworld Pal fusions (owner request 2026-11-09; Aniimo was asked for, then dropped the same day) run through the same
# pipeline. key = record/cache/post-id prefix ("fusion" stays Pokemon's, shared with fusion_inbox.py).
FAMILIES = {
    "pokemon": {"word": "Pokemon", "plural": "Pokemon", "game": "Pokemon", "key": "fusion", "pairs": PAIRS,
                "style": "official Pokemon Sugimori art style, clean line art"},
    "pal": {"word": "Pal", "plural": "Pals", "game": "Palworld", "key": "palfusion", "pairs": SPECS / "pal_fusion_pairs.json",
            "style": "official Palworld 3D creature render style, soft shading, cute but cool"},
    # canon Dragon Ball fusions (owner 2026-11-09, Toei claim risk accepted): no free official art, so the two
    # characters AND the fusion are drawn by Qwen-Image-2.1 as fan art; facts (who fuses, how) from Wikipedia's
    # "List of Dragon Ball characters"
    "dbz": {"word": "character", "plural": "characters", "game": "Dragon Ball", "key": "dbzfusion",
            "pairs": SPECS / "dbz_fusion_pairs.json",
            "style": "official Akira Toriyama Dragon Ball Z anime art style, clean line art, cel shading"},
}
# Pal elements -> the Pokemon type whose background photo + energy words fit
TYPE_ALIAS = {"neutral": "normal"}


def load_pairs(family: str) -> list[dict]:
    path = FAMILIES[family]["pairs"]
    pairs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return [{**p, "family": family} for p in pairs]


def art_ref(family: str, name: str) -> str:
    """What fal_video / _art take for a parent: a PokeAPI slug, or a local transparent .png of the Pal's wiki art."""
    if family == "pokemon":
        return name
    path = CACHE / "art" / f"{family}_{name.replace(' ', '_')}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if family == "dbz":  # no free official art: a fan-art drawing of the character, cut out
            _cutout(_qwen_draw(f"{name} from Dragon Ball, exactly the official character design, full body, standing "
                                 f"facing the viewer, {FAMILIES['dbz']['style']}, plain white background.", [],
                               provider_ok=True), path)
            return str(path)
        from scripts.palworld_breeding_videos import fetch_pal_art  # official Pal art (Palworld wiki)

        im = fetch_pal_art(name).convert("RGBA")
        im.crop(im.getbbox() or (0, 0, im.width, im.height)).save(path)
    return str(path)


def pair_id(pair: dict) -> str:
    """Record / cache / post id of a pair: "<a>+<b>"; Dragon Ball uses the fusion's name - Goku + Vegeta is both
    Gogeta (dance) and Vegito (Potara)."""
    return pair["name"] if pair.get("family") == "dbz" else f"{pair['a']}+{pair['b']}"


def fusion_meta(pair: dict) -> dict:
    fam = pair.get("family", "pokemon")
    a, b = pair["a"], pair["b"]
    types = pair.get("types") or pair.get("method") or "/".join(dict.fromkeys([fi.primary_type(a), fi.primary_type(b)]))
    disp = fi.display if fam == "pokemon" else (lambda n: n)
    return {"id": pair_id(pair), "family": fam, **({"fan": True} if pair.get("fan") else {}), "a": disp(a), "b": disp(b), "name": pair["name"], "types": types}


def fusion_post_id(meta: dict) -> str:
    fam = meta.get("family", "pokemon")
    return fi.post_id(meta) if fam == "pokemon" else f"custom:{FAMILIES[fam]['key']}-{meta['id']}"


def fusion_post_text(meta: dict) -> tuple[str, list[str]]:
    fam = meta.get("family", "pokemon")
    if fam == "pokemon":
        return fi.post_text(meta)
    f = FAMILIES[fam]
    a, b, name, types, game = meta["a"], meta["b"], meta["name"], meta["types"], f["game"]
    if fam == "dbz":  # types = how they fuse ("Fusion Dance" / "Potara Fusion")
        if meta.get("fan"):  # not in the anime - never present it as canon
            title = f"{a} + {b} = {name} | Dragon Ball Fan Fusion | What If"
            desc = (f"What if {a} and {b} did the {types}? Meet {name} - a fan-made fusion that is NOT in the anime. "
                    "Fan-made animation, not official Dragon Ball footage. Which Dragon Ball fusion should we make next? "
                    "Comment below!")
        else:
            title = f"{a} + {b} = {name} | Dragon Ball {types} | Fan Animation"
            desc = (f"{a} and {b} become {name} with the {types}. Fan-made animation, not official Dragon Ball footage. "
                    "Which Dragon Ball fusion should we make next? Comment below!")
        tags = [a, b, name, f"{name} fusion", "Dragon Ball", "Dragon Ball Z", "DBZ", types, "fusion", "DBZ fusion",
                "Dragon Ball fusion", "anime fusion", "Dragon Ball shorts", "anime shorts"]
        hashtags = " ".join(f"#{h}" for h in ["DragonBall", "DBZ", name.replace(" ", ""), "Anime", "Shorts"])
        return f"{title}\n\n{desc}\n\n{hashtags}", tags
    title = f"{a} + {b} = {name} | {game} Fusion | {types}"
    desc = (f"What if {a} and {b} fused? Meet {name}, a {types} {game} fusion. Fan-made AI animation from the official "
            f"{game} art. Which two {f['plural']} should fuse next? Comment below!")
    tags = [a, b, name, f"{game} fusion", game, f"{game} {f['plural']}", f"{a} {game}", f"{b} {game}",
            f"{game} fusion animation", "creature fusion", f"{game} shorts", "fusion"]
    hashtags = " ".join(f"#{h}" for h in [game, f"{game}Fusion", name.replace(" ", ""), "Gaming", "Shorts"])
    return f"{title}\n\n{desc}\n\n{hashtags}", tags

BACKGROUND = {"fire": "wiki:Lava", "water": "wiki:Wind wave", "grass": "wiki:Forest", "electric": "wiki:Lightning",
              "ghost": "wiki:Graveyard", "dragon": "wiki:Mountain", "psychic": "wiki:Aurora", "fighting": "wiki:Waterfall",
              "rock": "wiki:Canyon", "ground": "wiki:Desert", "steel": "wiki:Thunderstorm", "ice": "wiki:Glacier",
              "normal": "wiki:Meadow", "poison": "wiki:Swamp", "dark": "wiki:Night sky", "fairy": "wiki:Rainbow",
              "bug": "wiki:Rainforest", "flying": "wiki:Cloud"}
ENERGY = {"fire": "fire and embers", "water": "water and spray", "grass": "leaves and green light", "electric": "lightning and sparks",
          "ghost": "purple shadow mist", "dragon": "blue dragon energy", "psychic": "pink psychic waves", "fighting": "orange aura",
          "rock": "rocks and dust", "ground": "sand and dust", "steel": "silver metal sparks", "ice": "ice crystals",
          "normal": "white energy", "poison": "purple poison mist", "dark": "dark shadows", "fairy": "pink sparkles",
          "bug": "green energy", "flying": "strong wind"}


def _name(slug: str) -> str:
    return slug.replace("-", " ").title()


def check_chain(stages: list[str]) -> None:
    """The line must be a real evolution chain (PokeAPI), so the narration never says something false."""
    species = httpx.get(f"https://pokeapi.co/api/v2/pokemon-species/{stages[0]}", timeout=30).raise_for_status().json()
    node = httpx.get(species["evolution_chain"]["url"], timeout=30).raise_for_status().json()["chain"]
    for k, stage in enumerate(stages):
        if node["species"]["name"] != stage:
            raise ValueError(f"{stages}: stage {k} is {node['species']['name']}, not {stage}")
        nxt = [n for n in node["evolves_to"] if k + 1 < len(stages) and n["species"]["name"] == stages[k + 1]]
        if k + 1 < len(stages) and not nxt:
            raise ValueError(f"{stage} does not evolve into {stages[k + 1]}")
        node = nxt[0] if nxt else node


def megas_of(final: str) -> list[str]:
    """Mega Evolutions of the last stage, from PokeAPI's species varieties (only forms with official artwork)."""
    species = httpx.get(f"https://pokeapi.co/api/v2/pokemon-species/{final}", timeout=30).raise_for_status().json()
    out = []
    for v in species["varieties"]:
        name = v["pokemon"]["name"]
        if "-mega" in name:
            data = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{name}", timeout=30).raise_for_status().json()
            if data["sprites"]["other"]["official-artwork"]["front_default"]:
                out.append(name)
    return out


def _types(slug: str) -> list[str]:
    data = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{slug}", timeout=30).raise_for_status().json()
    return [t["type"]["name"] for t in data["types"]]


def mega_name(slug: str) -> str:
    """charizard-mega-x -> Mega Charizard X, blastoise-mega -> Mega Blastoise"""
    base, _, rest = slug.partition("-mega")
    return " ".join(["Mega", _name(base), *rest.strip("-").upper().split("-")]).strip()


def storyboard(entry: dict) -> dict:
    """Game-style evolution: a Wan clip of each form, joined by locally drawn evolution sequences (glow, swapping
    silhouettes, white screen); the next form fades in from white. Mega Evolutions (entry["megas"]) come last."""
    s, t, megas = entry["stages"], entry["type"], entry.get("megas", [])
    names = [_name(x) for x in s]
    bg, fx = BACKGROUND.get(t, "wiki:Meadow"), ENERGY.get(t, "white energy")
    idle = f"the creature looks around determined, {fx} float around it, anime style, smooth animation, camera slowly pushes in"
    reveal = (f"the creature appears in a burst of white light and looks around proudly, {fx} swirl around it, "
              "anime style, smooth animation")
    finale = f"the creature roars and unleashes a powerful attack of {fx}, epic low angle camera, anime style, dynamic motion"
    wan = lambda who, prompt, fade=0.0: {"image": {"poke": who, "background": bg}, "shots": [[prompt, 5]],  # noqa: E731
                                         **({"white_in": fade} if fade else {})}
    evolve = lambda a, b, mega=False: {"evolve": [a, b], "background": bg, "seconds": 3.2, "mega": mega}  # noqa: E731
    outro = f"Who is your favourite {t.title()}-type Pokemon? Comment below!"
    clips, narration = [wan(s[0], idle)], [f"{names[0]} is getting ready for something big."]
    for k in range(1, len(s)):
        last = k == len(s) - 1
        clips += [evolve(s[k - 1], s[k]), wan(s[k], finale if last and not megas else reveal, 0.6)]
        narration += [f"Oh? {names[k - 1]} is evolving!",
                      f"{names[k - 1]} evolved into {names[k]}!" + ("" if not last else
                                                                    " But it is not over yet." if megas else f" {outro}")]
    for k, m in enumerate(megas):
        mtypes = _types(m)
        mfx = ENERGY.get(mtypes[-1], fx)
        clips += [evolve(s[-1], m, mega=True),
                  wan(m, f"the creature roars with overwhelming power, {mfx} and rainbow energy explode around it, "
                         "epic low angle camera, anime style, dynamic motion", 0.6)]
        narration.append(f"{names[-1]} reacts to its Mega Stone. Mega Evolution!" if k == 0
                         else f"But {names[-1]} has a second Mega Evolution!")
        typed = f"{mega_name(m)}, a {' and '.join(x.title() for x in mtypes)} type!"
        if k < len(megas) - 1:
            narration.append(typed)
        elif len(megas) > 1:
            narration.append(f"{typed} {' or '.join(mega_name(x).split()[-1] for x in megas)}, which one do you like more? "
                             "Comment below!")
        else:
            narration.append(f"{typed} {outro}")
    clips.append({"still": str(CACHE / entry["id"] / f"scene_{len(clips) - 1:02d}.jpg"), "seconds": 4, "zoom": [1.05, 1.15]})
    narration.append(humor.pick("evolution", entry["id"]))
    return {"title": f"{' to '.join(names)} evolution", "backend": "wan", "voice": "en-US-GuyNeural", "style": 2,
            "narration": narration, "clips": clips}


def storyboard_v1(entry: dict) -> dict:
    """The first style (4 Wan clips, no transitions) - only to finish lines whose clips were cached before style 2."""
    s, t = entry["stages"], entry["type"]
    names = [_name(x) for x in s]
    bg, fx = BACKGROUND.get(t, "wiki:Meadow"), ENERGY.get(t, "white energy")
    frame = lambda k: {"poke": s[k], "background": bg}  # noqa: E731
    idle = f"the creature looks around determined, {fx} float around it, anime style, smooth animation, camera slowly pushes in"
    glow = (f"the creature starts glowing bright white, swirling {fx} spins around its body, light beams shoot out, "
            "evolution transformation, anime style")
    finale = f"the creature roars and unleashes a powerful attack of {fx}, epic low angle camera, anime style, dynamic motion"
    if len(s) == 3:
        clips = [(frame(0), idle), (frame(0), glow), (frame(1), glow.replace("bright white", "bright")), (frame(2), finale)]
        narration = [f"{names[0]} is getting ready for something big.", f"Its body starts to glow. {names[0]} is evolving!",
                     f"{names[0]} evolved into {names[1]}, and the power keeps growing!",
                     f"And now {names[1]} becomes {names[2]}! Who is your favourite {t.title()}-type Pokemon? Comment below!"]
    else:
        clips = [(frame(0), idle), (frame(0), glow), (frame(1), finale.replace("unleashes a powerful attack of", "is surrounded by")),
                 (frame(1), finale)]
        narration = [f"{names[0]} is getting ready for something big.", f"Its body starts to glow. {names[0]} is evolving!",
                     f"{names[0]} evolved into {names[1]}!",
                     f"{names[1]} is ready for battle! Who is your favourite {t.title()}-type Pokemon? Comment below!"]
    return {"title": f"{' to '.join(names)} evolution", "backend": "wan", "voice": "en-US-GuyNeural", "narration": narration,
            "clips": [{"image": img, "shots": [[prompt, 5]]} for img, prompt in clips]}


def _art(slug: str):
    from PIL import Image

    if slug.endswith(".png"):  # a Pal parent (art_ref)
        im = Image.open(slug).convert("RGBA")
        return im.crop(im.getbbox() or (0, 0, im.width, im.height))
    url = (httpx.get(f"https://pokeapi.co/api/v2/pokemon/{slug}", timeout=30).raise_for_status()
           .json()["sprites"]["other"]["official-artwork"]["front_default"])
    im = Image.open(io.BytesIO(httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content)).convert("RGBA")
    return im.crop(im.getbbox() or (0, 0, im.width, im.height))


def fusion_image(pair: dict, out: Path) -> Path:
    """The fused creature as a transparent PNG on a chroma-key background, keyed out (soft edge, despill).
    Pairs with a part-by-part "fuse" text: Qwen sees both parents and mixes them. Otherwise (or if Qwen fails):
    FLUX.1 Kontext edits the base parent's official artwork with the pair's "look"."""
    if out.exists():
        return out
    from gradio_client import Client, handle_file
    from PIL import Image

    out.parent.mkdir(parents=True, exist_ok=True)
    pair = {**pair, "look": pair.get("look") or pair.get("fuse", "")}  # Pal pairs only have "fuse"
    green = any(w in (pair["look"] + pair.get("fuse", "")).lower() for w in ("green", "teal", "leaf", "moss", "grass"))
    key, key_name = ((255, 0, 255), "magenta") if green else ((0, 255, 0), "green")
    if pair.get("fuse"):  # detailed part-by-part mix of both parents -> Qwen-Image-2.1 with both images
        # NO Kontext fallback: on 2026-11-09 Qwen failed, Kontext drew a spider-like Darkceus and it was posted
        # straight away. A failed fusion now fails the build; it is tried again next run.
        return _cutout(_qwen_fusion(pair, key_name, out), out)
    # Kontext edits ONE creature well; given both side by side it mostly kept one parent unchanged (Garchizard came
    # out as a plain Charizard). So edit the parent whose body the "look" keeps and paint the other one's parts on.
    base, other = fusion_base(pair)
    # chroma-key background: a white one had to be flood-filled, which also punched holes in white faces (Lucarace)
    sheet = Image.new("RGB", (1024, 1024), key)
    fam = pair.get("family", "pokemon")
    art = _art(art_ref(fam, base))
    scale = min(860 / art.width, 900 / art.height)
    art = art.resize((int(art.width * scale), int(art.height * scale)), Image.LANCZOS)
    sheet.paste(art, ((1024 - art.width) // 2, (1024 - art.height) // 2), art)
    src = out.with_name("base.png")
    sheet.save(src)
    # naming the other Pokemon made Kontext redraw it (Garchomp + Charizard -> a plain Charizard): describe its parts only
    look = re.sub(rf"(?i)\b(like )?{re.escape(_name(other))}(?:'s)?\s*", "", pair["look"])
    look = re.sub(rf"(?i)\b{re.escape(_name(base))}'s\s*", "its ", look)
    prompt = (f"Edit this creature: {look}. Keep its own head, face, body shape and pose exactly, only change the colors "
              f"and add the new parts. One creature only, full body, flat solid pure {key_name} background, "
              f"{FAMILIES[fam]['style']}")
    client = Client(KONTEXT_SPACE, hf_token=fv.hf_token(), verbose=False)
    try:
        res = client.predict(handle_file(str(src)), prompt, 7, False, 3.0, 28, api_name="/infer")
    except Exception as exc:
        if "quota" in str(exc).lower():
            raise fv.QuotaExhausted(str(exc)) from exc
        raise
    return _key_out(Path(res[0]["path"] if isinstance(res[0], dict) else res[0]), key, out)


def _qwen_fusion(pair: dict, key_name: str, out: Path) -> Path:
    """Both parents' official art (white sheets) as references -> Qwen-Image-2.1 draws ONE new standing creature from
    the pair's part-by-part "fuse" text, on a plain white background (_key_flat cuts it out)."""
    from gradio_client import Client, handle_file
    from PIL import Image

    fam = FAMILIES[pair.get("family", "pokemon")]
    srcs = []
    # "base_only": only the parent whose body the fusion keeps goes in as a reference - with both images the model
    # sometimes drew the two creatures side by side (Univolt + Pyrin, 2026-11-09)
    for slug in ([pair[pair["base_only"]]] if pair.get("base_only") else (pair["a"], pair["b"])):
        art = _art(art_ref(pair.get("family", "pokemon"), slug))
        scale = min(900 / art.width, 900 / art.height)
        art = art.resize((int(art.width * scale), int(art.height * scale)), Image.LANCZOS)
        sheet = Image.new("RGB", (1024, 1024), (255, 255, 255))
        sheet.paste(art, ((1024 - art.width) // 2, (1024 - art.height) // 2), art)
        src = out.with_name(f"parent_{slug.replace(' ', '_')}.png")
        sheet.save(src)
        srcs.append(src)
    if pair.get("family") == "dbz":  # a canon fusion: draw THAT character, the parents only guide the style
        return _qwen_draw(f"Draw {pair['fuse']}. One single character, full body from head to feet, standing facing "
                          f"the viewer, {fam['style']}, same art style as the reference images, plain white "
                          "background.", srcs, provider_ok="text")
    # "ONE single creature": Snorlax + Arcanine once came out as Arcanine standing in front of Snorlax (2026-11-09)
    return _qwen_draw(f"Design ONE brand-new {fam['word']} that is a fusion of {_name(pair['a'])} (image 1) and "
                      f"{_name(pair['b'])} (image 2): {pair['fuse']}. ONE single creature with one head and one body, "
                      "standing facing the viewer, full body from head to feet, clearly drawn legs and feet, "
                      f"character-design pose, {fam['style']}, clean line art and soft shading, plain white background.",
                      srcs, provider_ok=True)


def _provider_draw(prompt: str) -> Path:
    """Text-to-image through HF Inference Providers (fal-ai/qwen-image via router.huggingface.co), billed to the HF
    credits - works when the ZeroGPU quota is used up (ZeroGPU over-quota credits did not). No reference images, so
    only for characters the model knows by name (Dragon Ball). ~$0.03 an image."""
    import tempfile

    import time

    for attempt in range(3):  # fal answered one Piccorus call with a 422 that worked on the next try
        r = httpx.post("https://router.huggingface.co/fal-ai/fal-ai/qwen-image",
                       headers={"Authorization": f"Bearer {fv.hf_token()}"},
                       json={"prompt": prompt + " ONLY ONE figure, front view, the whole figure fits inside the picture "
                                                "with empty space above the head and below the feet, nothing cut off.",
                             "image_size": "portrait_16_9", "num_inference_steps": 30}, timeout=300)
        if r.status_code < 400 or attempt == 2:
            break
        log.warning("   Inference Providers %s - retrying: %s", r.status_code, r.text[:150])
        time.sleep(10)
    r.raise_for_status()
    out = Path(tempfile.mkdtemp()) / "provider.png"
    out.write_bytes(httpx.get(r.json()["images"][0]["url"], timeout=120, follow_redirects=True).raise_for_status().content)
    return out


def _provider_edit(prompt: str, refs: list) -> Path:
    """Multi-reference edit through HF Inference Providers (fal-ai/qwen-image-edit-2509), billed to the HF credits:
    the Pokemon / Pal fusion fallback when the ZeroGPU quota is used up (both parents as references, ~18 s, ~$0.04).
    First test (Lucario + Cinderace, 2026-11-09) matched the owner's reference closely."""
    import base64
    import tempfile
    import time

    uris = ["data:image/png;base64," + base64.b64encode(Path(x).read_bytes()).decode() for x in refs]
    for attempt in range(3):
        r = httpx.post("https://router.huggingface.co/fal-ai/fal-ai/qwen-image-edit-2509",
                       headers={"Authorization": f"Bearer {fv.hf_token()}"},
                       json={"prompt": prompt, "image_urls": uris, "image_size": "square_hd", "num_inference_steps": 30},
                       timeout=300)
        if r.status_code < 400 or attempt == 2:
            break
        log.warning("   Inference Providers edit %s - retrying: %s", r.status_code, r.text[:150])
        time.sleep(10)
    r.raise_for_status()
    out = Path(tempfile.mkdtemp()) / "provider_edit.png"
    out.write_bytes(httpx.get(r.json()["images"][0]["url"], timeout=120, follow_redirects=True).raise_for_status().content)
    return out


def _qwen_draw(prompt: str, refs: list, provider_ok: bool = False) -> Path:
    """One Qwen-Image-2.1 image (realtime Space, 1024x1024) from a prompt + optional reference images. With
    provider_ok, a used-up ZeroGPU quota falls back to HF Inference Providers (credits)."""
    if provider_ok:
        try:
            return _qwen_draw(prompt, refs)
        except fv.QuotaExhausted:
            log.info("   ZeroGPU quota used up - drawing through Inference Providers (credits)")
            return _provider_edit(prompt, refs) if refs and provider_ok != "text" else _provider_draw(prompt)
    from gradio_client import Client, handle_file

    client = Client(QWEN_SPACE, hf_token=fv.hf_token(), verbose=False)  # token needs "Make calls to Inference
    try:                                                                 # Providers" for over-quota credits
        import random  # a fixed seed (0) redrew the same failed image on every retry (Snorcanine, 2026-11-09)

        img = client.predict(prompt, [{"image": handle_file(str(x)), "caption": None} for x in refs],
                             random.randint(1, 2_000_000_000), 1024, "1:1", api_name="/generate")
    except Exception as exc:
        if "quota" in str(exc).lower():
            raise fv.QuotaExhausted(str(exc)) from exc
        raise
    return Path(img if isinstance(img, str) else img["path"])


def _cutout(src: Path, out: Path) -> Path:
    """Background removal for the fused art: rembg's "isnet-anime" model (laptop: backend/.cache/u2net, md5
    6f184e75...; GitHub's release download 504'd, the same file is huggingface.co/skytnt/anime-seg isnetis.onnx). The
    border flood fill (_key_flat) cannot cope with a gradient background next to white clothes (Gogeta's pants were
    eaten, 2026-11-09); it is still the fallback when rembg or the model is missing (CI)."""
    import os

    home = os.environ.setdefault("U2NET_HOME", str(ROOT / ".cache" / "u2net"))
    if not (Path(home) / "isnet-anime.onnx").exists():
        return _key_flat(src, out)
    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError:
        return _key_flat(src, out)
    res = remove(Image.open(src).convert("RGB"), session=new_session("isnet-anime"))
    box = res.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    res.crop(box).save(out)
    return out


def _key_flat(src: Path, out: Path, tol: int = 38, min_gap: int = 80, gap_tol: int = 12) -> Path:
    """Cut the creature out of a flat (not pure) background colour: everything near the border's median colour that
    touches the border goes, plus enclosed gaps of that colour (between legs) bigger than min_gap pixels. Small
    saturated details (Arceus's green gems) differ by more than tol and stay. Edge pixels lose the background tint."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    a = np.asarray(Image.open(src).convert("RGB")).astype(np.int32)
    h, w, _ = a.shape
    bg = np.median(np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]]), axis=0)
    dist = np.sqrt(((a - bg) ** 2).sum(axis=2))
    near = (dist < tol).astype(np.uint8) * 255
    m = Image.fromarray(near, "L").copy()  # fromarray shares a read-only buffer: floodfill silently did nothing
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if m.getpixel((x, y)) == 255:
            ImageDraw.floodfill(m, (x, y), 128)
    mm = np.asarray(m).copy()
    # enclosed gaps must match the background almost exactly (gap_tol): on a white background a white face's soft
    # shading is within tol too, and a gap pass with tol punched a hole in Darkceus's face (2026-11-09)
    mm[(mm == 255) & (dist >= gap_tol)] = 0
    # seeds from an eroded mask, so specks and thin lines never start a fill (one fill per pixel took minutes)
    core = np.asarray(Image.fromarray(np.where(mm == 255, 255, 0).astype(np.uint8), "L").filter(ImageFilter.MinFilter(9)))
    for y, x in np.argwhere(core == 255)[::23]:
        if mm[y, x] != 255:
            continue
        tmp = Image.fromarray(mm, "L").copy()
        ImageDraw.floodfill(tmp, (int(x), int(y)), 77)
        region = np.asarray(tmp) == 77
        mm[region] = 128 if region.sum() >= min_gap else 0
    alpha = np.where(mm == 128, 0, 255).astype(np.uint8)
    alpha_img = Image.fromarray(alpha, "L").filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    edge = np.asarray(Image.fromarray(255 - alpha, "L").filter(ImageFilter.MaxFilter(7))) > 0
    rgb = a.astype(np.float32)
    dom = int(np.argmax(bg))
    o1, o2 = [c for c in range(3) if c != dom]
    rgb[..., dom] = np.where(edge, np.minimum(rgb[..., dom], np.maximum(rgb[..., o1], rgb[..., o2]) + 10), rgb[..., dom])
    res = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), np.asarray(alpha_img)]).astype(np.uint8), "RGBA")
    res.crop(res.getchannel("A").getbbox()).save(out)
    return out


def _key_out(src: Path, key: tuple, out: Path) -> Path:
    import numpy as np
    from PIL import Image

    im = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    # every key-coloured pixel goes, enclosed gaps (between tail and body) included; soft edge + despill
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    spill = g - np.maximum(r, b) if key == (0, 255, 0) else np.minimum(r, b) - g
    alpha = np.clip((140 - spill) / 80, 0, 1)
    cut = np.maximum(spill, 0)  # remove the key's tint from edge pixels only
    if key == (0, 255, 0):
        im[..., 1] = g - cut
    else:
        im[..., 0], im[..., 2] = r - cut, b - cut
    rgba = np.dstack([im, alpha * 255]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(out)
    return out


def fusion_base(pair: dict) -> tuple[str, str]:
    """(base, other): the parent whose body the pair's "look" keeps ("Snorlax's huge round body ...") comes first."""
    look = pair["look"].lower()
    pos = {s: look.find(_name(s).lower()) for s in (pair["a"], pair["b"])}
    a, b = pair["a"], pair["b"]
    return (b, a) if 0 <= pos[b] < pos[a] or pos[a] < 0 <= pos[b] else (a, b)


# Wan keeps a character when the prompt asks for body motion; big "white flash" / "explosion" prompts made it melt
# the creature into a different one (Garchizard turned into a blue ghost dragon), and heavy effects hid it.
_KEEP = "the character keeps exactly the same design, shape and colors, anime style, smooth natural motion"


FUSION_STYLE = 8
# owner 2026-11-09: not the same jump for every fusion - two moves that fit the creature (fal_video.action_clip).
# First move from the first type, second from the second type (or the first type's second option); Dragon Ball
# pairs list their own "moves".
MOVES = {"fire": ("breath", "powerup"), "water": ("swirl", "beam"), "electric": ("lightning", "teleport"),
         "grass": ("swirl", "levitate"), "ice": ("swirl", "beam"), "ghost": ("teleport", "levitate"),
         "dark": ("teleport", "blast"), "dragon": ("fly", "breath"), "psychic": ("levitate", "blast"),
         "fairy": ("levitate", "swirl"), "fighting": ("leap", "powerup"), "normal": ("leap", "powerup"),
         "rock": ("leap", "swirl"), "ground": ("leap", "powerup"), "steel": ("powerup", "beam"),
         "flying": ("fly", "swirl"), "bug": ("fly", "swirl"), "poison": ("swirl", "levitate")}


def fusion_moves(pair: dict, ta: str, tb: str) -> tuple[str, str]:
    if pair.get("moves"):
        return tuple(pair["moves"][:2])
    first = MOVES.get(ta, ("leap", "blast"))
    second = MOVES.get(tb, first)
    m1 = first[0]
    m2 = next((m for m in (second[0], second[1], first[1], "blast") if m != m1), "blast")
    return m1, m2
# aura / particle colour of the fused creature's first type (fal_video.idle_clip)
TYPE_COLOR = {"fire": (255, 120, 40), "water": (60, 150, 255), "grass": (90, 210, 90), "electric": (255, 220, 50),
              "ghost": (150, 90, 230), "dragon": (90, 110, 255), "psychic": (255, 100, 200), "fighting": (230, 110, 50),
              "rock": (200, 170, 110), "ground": (220, 180, 100), "steel": (190, 200, 220), "ice": (140, 230, 255),
              "normal": (240, 230, 200), "poison": (180, 80, 220), "dark": (150, 90, 230), "fairy": (255, 150, 220),
              "bug": (160, 210, 60), "flying": (170, 200, 255)}


def fusion_storyboard(pair: dict, meta: dict, fused: Path) -> dict:
    """Style 7: parent A's art, parent B's art (stills with a slow zoom - owner 2026-11-08: no animation on the
    parents), the locally drawn fusion (silhouettes slide together, swap, white screen), the fused creature fades in
    from white and holds still for 3 s, then DOES something (owner 2026-11-09: "kuch kar raha ho"): a leap-and-slam and
    an energy blast at the camera, both drawn locally by fal_video.action_clip so the design can never change, and a
    closing still. No Wan here: on 2026-11-09 it turned Darkceus into a bearded armoured character and broke its eyes."""
    a, b, name = meta["a"], meta["b"], meta["name"]
    fam = pair.get("family", "pokemon")
    ta, tb = (TYPE_ALIAS.get(t.lower(), t.lower()) for t in (meta["types"].split("/") * 2)[:2])
    bg_a, bg_b = BACKGROUND.get(ta, "wiki:Meadow"), BACKGROUND.get(tb, "wiki:Meadow")
    if pair.get("background"):  # Dragon Ball pairs choose their own scene + aura colour
        bg_a = bg_b = pair["background"]
    still = lambda who, bg, secs: {"still": {"poke": who, "background": bg}, "seconds": secs, "zoom": [1.0, 1.12]}  # noqa: E731
    color = list(pair.get("color") or TYPE_COLOR.get(ta, (140, 190, 255)))
    moves = fusion_moves(pair, ta, tb)
    act = lambda mode, secs: {"action": str(fused), "background": bg_a, "seconds": secs, "mode": mode, "color": color}  # noqa: E731
    ra, rb = art_ref(fam, pair["a"]), art_ref(fam, pair["b"])
    clips = [still(ra, bg_a, 4.0),
             still(rb, bg_b, 3.5),
             {"fuse": [ra, rb, str(fused)], "background": bg_a, "seconds": 3.8},
             # owner 2026-11-09: hold the new creature still for 3 s, then let it DO something (local, design-safe)
             {"still": {"poke": str(fused), "background": bg_a}, "seconds": 3.0, "zoom": [1.0, 1.0], "white_in": 0.6},
             act(moves[0], 4.0),
             act(moves[1], 4.5),  # + joke
             {"still": str(fused.parent / "scene_03.jpg"), "seconds": 5.5, "zoom": [1.05, 1.15]}]  # closing question
    joke = humor.pick({"pokemon": "fusion", "pal": "palfusion", "dbz": "dbzfusion"}[fam], meta["id"])
    if fam == "dbz":
        dance = pair.get("method") == "Fusion Dance"
        narration = [f"{a} and {b} are about to fuse!", f"Here comes {b}!",
                     "Fu... sion... HA!" if dance else "The Potara earrings are on!",
                     "Fusion complete!", f"Meet {name}!", joke,
                     (f"{name}, a fan-made {pair['method']} fusion." if pair.get("fan") else f"{name}, born from the {pair['method']}.")
                     + " Which Dragon Ball fusion should we make next? Comment below!"]
    else:
        narration = [f"What happens when {a} fuses with {b}?", f"And here comes {b}!", f"{a} and {b} are fusing!",
                     "Fusion complete!", f"Meet {name}!", joke,
                     f"{name}, a {meta['types']} fusion. Which two {FAMILIES[fam]['plural']} should fuse next? Comment below!"]
    return {"title": f"{a} + {b} fusion", "backend": "wan", "voice": "en-US-GuyNeural", "style": FUSION_STYLE,
            "narration": narration, "clips": clips}


def make_fusion(pair: dict, poster, games: dict) -> None:
    meta = fusion_meta(pair)
    fam = FAMILIES[meta["family"]]
    log.info("== %s fusion %s + %s = %s (%s)", fam["game"], meta["a"], meta["b"], meta["name"], meta["types"])
    out = CACHE / f"{fam['key']}-{meta['id']}"
    board = out / "storyboard.json"
    old_style = json.loads(board.read_text(encoding="utf-8")).get("style") if board.exists() else FUSION_STYLE
    if old_style != FUSION_STYLE:
        # clips of an older style would be mixed in - redraw them. Fused art: kept for old `look` pairs (Kontext style 2/3),
        # always redrawn for `fuse` pairs (it may be a Kontext fallback from before 2026-11-09; Qwen takes ~25 s)
        keep_art = (old_style in (2, 3) and not pair.get("fuse")) or (old_style or 0) >= 6  # 6+: approved Qwen-2.1 art
        for f in [*out.glob("clip_*"), *out.glob("scene_*.jpg"), board, *([] if keep_art else [out / "fused.png"])]:
            f.unlink(missing_ok=True)
    fused = fusion_image(pair, out / "fused.png")
    board.write_text(json.dumps(fusion_storyboard(pair, meta, fused), indent=2) + "\n", encoding="utf-8")
    video = fv.build(board, out)
    games[f"{fam['key']}-{meta['id']}"] = {"kind": "motion", "spec": fam["pairs"].relative_to(ROOT).as_posix(),
                                           "video": str(video), "data": meta}
    dj._save(dj.GAME_MANIFEST, games)
    poster.post(fusion_post_id(meta), *fusion_post_text(meta), str(video))


def make_evolution(entry: dict, poster, games: dict) -> None:
    log.info("== %s", " -> ".join(entry["stages"]))
    out = CACHE / entry["id"]
    check_chain(entry["stages"])
    board = out / "storyboard.json"
    out.mkdir(parents=True, exist_ok=True)
    old = json.loads(board.read_text(encoding="utf-8")) if board.exists() else None
    if old is not None and old.get("style") != 2 and any(out.glob("clip_*.mp4")):
        entry = {**entry, "megas": []}  # clips cached in the first style - finish it that way
        sb = storyboard_v1(entry)
    else:
        if "megas" not in entry:
            entry = {**entry, "megas": megas_of(entry["stages"][-1])}
        sb = storyboard(entry)
    board.write_text(json.dumps(sb, indent=2) + "\n", encoding="utf-8")
    video = fv.build(board, out)
    games[f"poke-{entry['id']}"] = {"kind": "motion", "spec": QUEUE.relative_to(ROOT).as_posix(), "video": str(video), "data": entry}
    dj._save(dj.GAME_MANIFEST, games)
    poster.post(post_id(entry), *post_text(entry), str(video))


def post_id(entry: dict) -> str:
    return f"custom:motion-poke-{entry['id']}"


def post_text(entry: dict) -> tuple[str, list[str]]:
    names = [_name(x) for x in entry["stages"]]
    megas = [mega_name(m) for m in entry.get("megas", [])]
    if len(megas) > 1:  # Mega Charizard X and Y
        mega = " ".join(megas[0].split()[:-1]) + " " + " and ".join(m.split()[-1] for m in megas)
    else:
        mega = megas[0] if megas else ""
    later = names[1:] + ([mega] if mega else [])
    title = f"{names[0]} Evolves Into " + (", ".join(later[:-1]) + " and " + later[-1] if len(later) > 1 else later[0])
    title += " | Pokemon Evolution Animation" if len(title) <= 66 else " | Pokemon Evolution"
    desc = (f"{' to '.join(names + megas)} - a Pokemon evolution animation, made with AI from the official artwork. "
            f"Who is your favourite {entry['type'].title()}-type Pokemon? Comment below!")
    tags = [*names, *megas, f"{names[0]} evolution", "Pokemon evolution", "Pokemon animation", "Pokemon",
            *(["Mega Evolution"] if megas else []), f"{entry['type']} type Pokemon", "evolution animation", "Pokemon shorts"]
    hashtags = " ".join(f"#{h}" for h in ["Pokemon", "PokemonEvolution", names[-1].replace(" ", ""), "Gaming", "Shorts"])
    return f"{title}\n\n{desc}\n\n{hashtags}", tags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", metavar="ID")
    parser.add_argument("--max-shorts", type=int, default=3, help="new evolution shorts per run (owner: 3 a day)")
    parser.add_argument("--max-fusions", type=int, default=0,  # paused 2026-11-08 until the owner approves the Qwen art
                        help="new fusion shorts per run")
    parser.add_argument("--max-pal-fusions", type=int, default=0, help="new Palworld fusion shorts per run")
    parser.add_argument("--max-dbz-fusions", type=int, default=0, help="new Dragon Ball fusion shorts per run")
    parser.add_argument("--fusion-image", metavar="A+B")
    args = parser.parse_args()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    pairs = [p for fam in FAMILIES for p in load_pairs(fam)]
    if args.plan:
        print(json.dumps(storyboard(next(e for e in queue if e["id"] == args.plan)), indent=2))
        return
    if args.fusion_image:
        pair = next(p for p in pairs if pair_id(p) == args.fusion_image)
        print(fusion_image(pair, CACHE / f"{FAMILIES[pair['family']]['key']}-{args.fusion_image}" / "fused.png"))
        return
    log_dir = ROOT / dj.settings.media_output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_dir / f"pokemon-{date.today()}.log", encoding="utf-8"), logging.StreamHandler()])
    for noisy in ("httpx", "httpcore", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    poster = dj.Poster()
    games = dj._load(dj.GAME_MANIFEST)

    for entry in queue:  # finished earlier but some platform still missing (e.g. YouTube upload limit)
        key, video = f"poke-{entry['id']}", CACHE / entry["id"] / "final.mp4"
        if key in games and poster.pending(post_id(entry)):
            if video.exists():
                log.info("retry %s", key)
                poster.post(post_id(entry), *post_text(games[key].get("data", entry)), str(video))  # data has the megas
            else:
                log.warning("%s still pending but its video is gone from the cache", key)

    made_here = {f["pairs"].relative_to(ROOT).as_posix() for f in FAMILIES.values()}
    for key, rec in games.items():  # a fusion made here that still misses a platform
        if rec.get("kind") == "motion" and rec.get("spec") in made_here and poster.pending(fusion_post_id(rec["data"])):
            video = CACHE / key / "final.mp4"
            if video.exists():
                log.info("retry %s", key)
                poster.post(fusion_post_id(rec["data"]), *fusion_post_text(rec["data"]), str(video))
            else:
                log.warning("%s still pending but its video is gone from the cache", key)

    # Evolution lines and fusions take turns, so one kind never uses up the whole GPU quota.
    evos = [("evo", e) for e in queue if f"poke-{e['id']}" not in games][: args.max_shorts]
    fusions = []
    for fam, cap in (("pokemon", args.max_fusions), ("pal", args.max_pal_fusions), ("dbz", args.max_dbz_fusions)):
        k = FAMILIES[fam]["key"]
        fusions += [("fusion", p) for p in load_pairs(fam)
                    if f"{k}-{pair_id(p)}" not in games and f"{k}-{p['b']}+{p['a']}" not in games][:cap]
    jobs = [job for k in range(max(len(evos), len(fusions))) for job in (evos[k:k + 1] + fusions[k:k + 1])]
    for kind, item in jobs:
        try:
            (make_evolution if kind == "evo" else make_fusion)(item, poster, games)
        except fv.QuotaExhausted as exc:
            log.info("   free GPU quota used up - continuing next run (%s)", str(exc)[:120])
            break
        except Exception:
            log.exception("   failed - skipping to the next one")
            dj.HEALTH["build_failed"] += 1
            continue
        dj.HEALTH["built"] += 1

    for entry in queue:  # fully posted -> drop its clips so the motion-cache branch stays small
        if f"poke-{entry['id']}" in games and not poster.pending(post_id(entry)):
            shutil.rmtree(CACHE / entry["id"], ignore_errors=True)
    for key, rec in games.items():
        if rec.get("kind") == "motion" and rec.get("spec") in made_here and not poster.pending(fusion_post_id(rec["data"])):
            shutil.rmtree(CACHE / key, ignore_errors=True)
    log.info("Done (%s)", ", ".join(f"{k} {v}" for k, v in dj.HEALTH.items()))
    if dj.HEALTH["post_failed"] and not dj.HEALTH["posted"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
