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

QUEUE = ROOT / "scripts" / "specs" / "pokemon_queue.json"
PAIRS = fi.PAIRS
KONTEXT_SPACE = "black-forest-labs/FLUX.1-Kontext-Dev"  # free Hugging Face ZeroGPU Space (image editing)
CACHE = ROOT / dj.settings.media_output_dir / "pokemon"
log = logging.getLogger("pokemon_daily")

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

    url = (httpx.get(f"https://pokeapi.co/api/v2/pokemon/{slug}", timeout=30).raise_for_status()
           .json()["sprites"]["other"]["official-artwork"]["front_default"])
    im = Image.open(io.BytesIO(httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content)).convert("RGBA")
    return im.crop(im.getbbox() or (0, 0, im.width, im.height))


def fusion_image(pair: dict, out: Path) -> Path:
    """The fused creature as a transparent PNG: the base parent's official artwork on a chroma-key background ->
    FLUX.1 Kontext with the pair's "look" -> key colour keyed out (soft edge, despill)."""
    if out.exists():
        return out
    import numpy as np
    from gradio_client import Client, handle_file
    from PIL import Image

    out.parent.mkdir(parents=True, exist_ok=True)
    # Kontext edits ONE creature well; given both side by side it mostly kept one parent unchanged (Garchizard came
    # out as a plain Charizard). So edit the parent whose body the "look" keeps and paint the other one's parts on.
    base, other = fusion_base(pair)
    # chroma-key background: a white one had to be flood-filled, which also punched holes in white faces (Lucarace)
    green = any(w in pair["look"].lower() for w in ("green", "teal", "leaf"))
    key, key_name = ((255, 0, 255), "magenta") if green else ((0, 255, 0), "green")
    sheet = Image.new("RGB", (1024, 1024), key)
    art = _art(base)
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
              "official Pokemon art style")
    client = Client(KONTEXT_SPACE, hf_token=fv.hf_token(), verbose=False)
    try:
        res = client.predict(handle_file(str(src)), prompt, 7, False, 3.0, 28, api_name="/infer")
    except Exception as exc:
        if "quota" in str(exc).lower():
            raise fv.QuotaExhausted(str(exc)) from exc
        raise
    im = np.asarray(Image.open(res[0]["path"] if isinstance(res[0], dict) else res[0]).convert("RGB")).astype(np.float32)
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


def fusion_storyboard(pair: dict, meta: dict, fused: Path) -> dict:
    """Parent A moves, parent B moves, a locally drawn fusion (silhouettes slide together, swap, white screen), then
    the fused creature fades in from white, looks around and attacks. Style 2 (owner, 2026-11-06: style 1 looked bad)."""
    a, b, name = meta["a"], meta["b"], meta["name"]
    ta, tb = (t.lower() for t in (meta["types"].split("/") * 2)[:2])
    fx, fx_b = ENERGY.get(ta, "white energy"), ENERGY.get(tb, "white energy")
    bg_a, bg_b = BACKGROUND.get(ta, "wiki:Meadow"), BACKGROUND.get(tb, "wiki:Meadow")
    wan = lambda who, bg, prompt, fade=0.0: {"image": {"poke": who, "background": bg}, "shots": [[prompt, 5]],  # noqa: E731
                                             **({"white_in": fade} if fade else {})}
    clips = [wan(pair["a"], bg_a, f"the creature shifts its weight, turns its head and raises its arms into a battle stance, "
                                  f"breathing, a few {fx} drift in the air, static camera, {_KEEP}"),
             wan(pair["b"], bg_b, f"the creature steps forward, turns its head and clenches its fists, breathing, "
                                  f"a few {fx_b} drift in the air, static camera, {_KEEP}"),
             {"fuse": [pair["a"], pair["b"], str(fused)], "background": bg_a, "seconds": 3.8},
             wan(str(fused), bg_a, f"the creature opens its eyes, looks around proudly and flexes its body, breathing, "
                                   f"small sparkles of light fade around it, static camera, {_KEEP}", 0.6),
             wan(str(fused), bg_a, f"the creature roars and swings forward in an attack, a small burst of {fx} from its body, "
                                   f"the creature stays clearly visible in the center, slow camera push in, {_KEEP}")]
    narration = [f"What happens when {a} fuses with {b}?", f"And here comes {b}!", f"{a} and {b} are fusing!",
                 f"Fusion complete! Meet {name}!",
                 f"{name}, a {meta['types']} fusion. Which two Pokemon should fuse next? Comment below!"]
    return {"title": f"{a} + {b} fusion", "backend": "wan", "voice": "en-US-GuyNeural", "style": 2,
            "narration": narration, "clips": clips}


def make_fusion(pair: dict, poster, games: dict) -> None:
    meta = {"id": f"{pair['a']}+{pair['b']}", "a": fi.display(pair["a"]), "b": fi.display(pair["b"]), "name": pair["name"],
            "types": "/".join(dict.fromkeys([fi.primary_type(pair["a"]), fi.primary_type(pair["b"])]))}
    log.info("== fusion %s + %s = %s (%s)", meta["a"], meta["b"], meta["name"], meta["types"])
    out = CACHE / f"fusion-{meta['id']}"
    board = out / "storyboard.json"
    if board.exists() and json.loads(board.read_text(encoding="utf-8")).get("style") != 2:
        # style-1 leftovers (two-parent Kontext art, clips in the old order) would be mixed in - start over
        for f in [*out.glob("clip_*.mp4"), *out.glob("scene_*.jpg"), out / "fused.png", board]:
            f.unlink(missing_ok=True)
    fused = fusion_image(pair, out / "fused.png")
    board.write_text(json.dumps(fusion_storyboard(pair, meta, fused), indent=2) + "\n", encoding="utf-8")
    video = fv.build(board, out)
    games[f"fusion-{meta['id']}"] = {"kind": "motion", "spec": PAIRS.relative_to(ROOT).as_posix(), "video": str(video), "data": meta}
    dj._save(dj.GAME_MANIFEST, games)
    poster.post(fi.post_id(meta), *fi.post_text(meta), str(video))


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
    parser.add_argument("--max-fusions", type=int, default=2, help="new fusion shorts per run")
    parser.add_argument("--fusion-image", metavar="A+B")
    args = parser.parse_args()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    pairs = json.loads(PAIRS.read_text(encoding="utf-8"))
    if args.plan:
        print(json.dumps(storyboard(next(e for e in queue if e["id"] == args.plan)), indent=2))
        return
    if args.fusion_image:
        pair = next(p for p in pairs if f"{p['a']}+{p['b']}" == args.fusion_image)
        print(fusion_image(pair, CACHE / f"fusion-{args.fusion_image}" / "fused.png"))
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

    spec = PAIRS.relative_to(ROOT).as_posix()
    for key, rec in games.items():  # a fusion made here that still misses a platform
        if key.startswith("fusion-") and rec.get("spec") == spec and poster.pending(fi.post_id(rec["data"])):
            video = CACHE / key / "final.mp4"
            if video.exists():
                log.info("retry %s", key)
                poster.post(fi.post_id(rec["data"]), *fi.post_text(rec["data"]), str(video))
            else:
                log.warning("%s still pending but its video is gone from the cache", key)

    # Evolution lines and fusions take turns, so one kind never uses up the whole GPU quota.
    evos = [("evo", e) for e in queue if f"poke-{e['id']}" not in games][: args.max_shorts]
    fusions = [("fusion", p) for p in pairs
               if f"fusion-{p['a']}+{p['b']}" not in games and f"fusion-{p['b']}+{p['a']}" not in games][: args.max_fusions]
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
        if key.startswith("fusion-") and rec.get("spec") == spec and not poster.pending(fi.post_id(rec["data"])):
            shutil.rmtree(CACHE / key, ignore_errors=True)
    log.info("Done (%s)", ", ".join(f"{k} {v}" for k, v in dj.HEALTH.items()))
    if dj.HEALTH["post_failed"] and not dj.HEALTH["posted"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
