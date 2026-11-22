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

Owner review (2026-11-14: "kabhi kabhi pokemon evolution bohot bakwas hote he, mujhe puch ke hi karna"): with --review a
built line is NOT posted - it is listed in <CACHE>/review_pending.json and shown to the owner first.
    python scripts/pokemon_daily.py --review                       # build up to --max-shorts new lines, post nothing
    python scripts/pokemon_daily.py --max-shorts 0 --post-approved abra,gible   # post the lines the owner approved
    python scripts/pokemon_daily.py --max-shorts 0 --reject torchic            # owner said no - never post it
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


# What each Dragon Ball character looks like, for the drawn parent art. With only the name the model drew Jiren purple
# in a black-and-gold suit, Cell as a Goku look-alike and Frieza pale green (owner caught Jiren, 2026-11-09). Designs
# checked on Wikipedia (Jiren: grey alien, big black eyes, Pride Troopers red-and-black suit, white gloves and boots).
DBZ_LOOK = {
    "Jiren": "a towering, extremely muscular grey-skinned alien with adult heroic proportions (small head, huge "
             "broad chest and shoulders, NOT chibi, NOT a big head), a bald grey head, large completely black oval eyes "
             "set wide apart, a small stern mouth, wearing the Pride Troopers uniform: a red and black bodysuit with "
             "white gloves and white boots",
    "Cell": "Perfect Cell, the green insect-like bio-android villain: green exoskeleton covered in black spots, a tall "
            "green crown-shaped head crest, a pale human-like face with purple cheek markings, black insect wings on "
            "the back, black and green armoured body, NOT a Saiyan, no spiky black hair",
    "Frieza": "Frieza in his final form: a slim white alien body with glossy purple domes on the top of the head, the "
              "shoulders, the chest and the forearms, red eyes, black lips and a long white tail",
}


def art_ref(family: str, name: str) -> str:
    """What fal_video / _art take for a parent: a PokeAPI slug, or a local transparent .png of the Pal's wiki art."""
    if family == "pokemon":
        return name
    path = CACHE / "art" / f"{family}_{name.replace(' ', '_')}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if family == "dbz":  # no free official art: a fan-art drawing of the character, cut out
            look = f": {DBZ_LOOK[name]}" if name in DBZ_LOOK else ""
            _cutout(_qwen_draw(f"{name} from Dragon Ball{look}, exactly the official character design, ONE character, "
                                 f"full body, standing facing the viewer, {FAMILIES['dbz']['style']}, plain white "
                                 "background.", [], provider_ok=True), path)
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
    # "names": [a, b] overrides the shown names when the PokeAPI slug is a form ("giratina-altered" -> "Giratina")
    fam = pair.get("family", "pokemon")
    a, b = pair["a"], pair["b"]
    types = pair.get("types") or pair.get("method") or "/".join(dict.fromkeys([fi.primary_type(a), fi.primary_type(b)]))
    disp = fi.display if fam == "pokemon" else (lambda n: n)
    return {"id": pair_id(pair), "family": fam, **({"fan": True} if pair.get("fan") else {}), **({"sprite": True} if pair.get("if_sprite") else {}), "a": (pair.get("names") or [disp(a)])[0], "b": (pair.get("names") or [0, disp(b)])[1], "name": pair["name"], "types": types}


def fusion_post_id(meta: dict) -> str:
    fam = meta.get("family", "pokemon")
    return fi.post_id(meta) if fam == "pokemon" else f"custom:{FAMILIES[fam]['key']}-{meta['id']}"


def fusion_post_text(meta: dict) -> tuple[str, list[str]]:
    fam = meta.get("family", "pokemon")
    if fam == "pokemon":
        text, tags = fi.post_text(meta)
        if meta.get("sprite"):  # design = a Pokemon Infinite Fusion community sprite (fusioncalc.com) - always credited
            text = text.replace("Fan-made AI animation.", "Fusion design based on a custom sprite by the Pokemon "
                                "Infinite Fusion community, redrawn in 3D. Fan-made AI animation.")
            tags = tags + ["Pokemon Infinite Fusion"]
        return text, tags
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
# Gigantamax colour per type (owner 2026-11-20: Gigantamax must not look like the red Dynamax): RGB tint of the change +
# the colour named in the art / Wan prompts
TYPE_TINT = {"fire": ((255, 120, 30), "orange"), "water": ((40, 140, 255), "blue"), "grass": ((60, 200, 80), "green"),
             "electric": ((255, 220, 40), "yellow"), "ghost": ((150, 80, 220), "purple"), "dragon": ((70, 110, 255), "blue"),
             "psychic": ((255, 90, 180), "pink"), "fighting": ((230, 110, 40), "orange"), "rock": ((200, 160, 90), "golden"),
             "ground": ((215, 170, 80), "golden"), "steel": ((190, 205, 225), "silver"), "ice": ((130, 220, 255), "icy blue"),
             "normal": ((245, 235, 200), "golden white"), "poison": ((170, 70, 200), "purple"), "dark": ((120, 70, 180), "violet"),
             "fairy": ((255, 140, 210), "pink"), "bug": ((150, 210, 40), "lime green"), "flying": ((150, 190, 255), "sky blue")}


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


def gmax_of(final: str) -> str | None:
    """The official Gigantamax form of the last stage (PokeAPI variety "<name>-gmax" with official artwork), if any."""
    species = httpx.get(f"https://pokeapi.co/api/v2/pokemon-species/{final}", timeout=30).raise_for_status().json()
    for v in species["varieties"]:
        name = v["pokemon"]["name"]
        if name.endswith("-gmax"):
            data = httpx.get(f"https://pokeapi.co/api/v2/pokemon/{name}", timeout=30).raise_for_status().json()
            if data["sprites"]["other"]["official-artwork"]["front_default"]:
                return name
    return None


def primal_of(final: str) -> str | None:
    """The official Primal Reversion (Groudon / Kyogre: PokeAPI variety "<name>-primal"), if any."""
    species = httpx.get(f"https://pokeapi.co/api/v2/pokemon-species/{final}", timeout=30).raise_for_status().json()
    for v in species["varieties"]:
        if v["pokemon"]["name"].endswith("-primal"):
            return v["pokemon"]["name"]
    return None


ORB = {"groudon": "Red Orb", "kyogre": "Blue Orb"}  # what triggers Primal Reversion (Omega Ruby / Alpha Sapphire)


# Owner 2026-11-17: "jab bhi evolution aaye pokemon ka to mega, dynamax and gigantamax sab dal dena, agar nahi he tab bhi
# aap bana dena" (the Chimchar short had no Mega - Infernape has none). Missing forms are drawn as FAN-MADE art from the
# official artwork (HF Inference Providers edit, credits, ~$0.04 each - no ZeroGPU) and every video says they are
# fan-made / not in the games.
# Owner 2026-11-17 after the first Infernape sheet: "Mega and fan made gigantamax thoda aur khaarnaak banao" - so both
# carry the FIERCE look the owner approved for the Mewtwo stone forms (stone_forms.FIERCE).
FAN_FIERCE = ("It must look DANGEROUS: narrow angry glaring eyes full of rage, a menacing aggressive battle stance ready "
              "to strike, a bigger muscular build, long sharp claws, jagged spikes, an aura of overwhelming power - NOT "
              "cute, NOT chibi, NOT smiling. But its FACE keeps exactly the shape of the reference - same head, snout, eyes "
              "position and antennae / ears / horns as the reference; do not turn the face into another dragon or "
              "creature (Gigantamax Dragonite got a Charizard face, 2026-11-17).")
# Groudon 2026-11-17: "dark armour" / "dark shading" / "storm clouds behind it" turned Groudon black (Mega) and beige on a
# dark red scene (Gigantamax - the cutout then erased it). So: its own colours, and nothing behind it (the red clouds
# come from the Wan prompt).
FAN_KEEP = ("Keep EXACTLY the same colours as the reference picture - do not recolour it, do not make it darker. "
            "Its body stays {colour} like the reference. Solid pure white background (#FFFFFF) with nothing behind the "
            "creature: no clouds, no ground, no scenery, no shadow, NOT a checkerboard, NOT a transparency pattern.")
# body parts the drawing model kept losing (2026-11-20: Mega Hydreigon got claws instead of its hand-heads)
# 2026-11-22: "one main head on its neck" made it draw a FOURTH head on a long neck above the body - the heads are
# now named by where they are in the official art
PARTS = {"hydreigon": "Hydreigon has EXACTLY THREE heads, the three already in the picture: the big main head with the "
                      "pink mane on the left of its body, and one smaller head at the end of EACH arm (its hands ARE heads "
                      "with mouths, it has no claws or fingers). Do NOT add a fourth head, no head on a long neck above the "
                      "body - exactly three heads in total. "
                      # 2026-11-22: every Mega draw gave it two legs with big claws, standing on dusty ground
                      "It has NO legs and NO feet: it FLOATS in the air and its lower body ends in one long striped "
                      "tail - keep the same floating pose as the picture, nothing under it. "}
FAN_PROMPT = {
    # 2026-11-20: drawn as a new picture, the free ZeroGPU Space put dust + ground behind the Megas (no cutout) and gave
    # Hydreigon claws instead of its hand-heads - an EDIT of the official art keeps the body. Mega = armour and sharper
    # shapes in its own colours; Gigantamax (below) = giant energy structures; Dynamax = the same body, giant and red.
    "mega": ("Edit this picture of the Pokemon {name} into its MEGA EVOLUTION. Keep its head, face, eyes, snout, ears / "
             "horns, the same number of heads, arms, legs and wings (if its hands are heads they stay heads) and its "
             "{colour} colours EXACTLY as in the picture. Make it more powerful only with armour: sharper and longer "
             "spikes, extra armour plates in its own colours, bigger claws, a more muscular build, angry glaring eyes, a "
             "menacing stance, small {fx} accents on the armour. NOT cute. Plain pure white background (#FFFFFF), nothing "
             "behind it - no dust, no smoke, no ground, no aura, no clouds, no text. Official Pokemon artwork style, ONE "
             "creature only, exactly one face per head."),
    # 2026-11-17: asked to "draw its Gigantamax form", the model redrew Dragonite with a Charizard face three times
    # (owner: "dragonite ka chehera bohot bekar he"), so the face is pinned. 2026-11-20 the owner: Dynamax and Gigantamax
    # are different, every video must show them differently - the old add-only red cracks looked just like the red
    # Dynamax. So the body now really changes, with huge structures in the type's own colour (not red).
    "gmax": ("Edit this picture of the Pokemon {name} into its GIGANTAMAX form. Keep its head, face, eyes, snout, "
             "ears / horns and its {colour} colours EXACTLY as in the picture - the face must not change at all. Change "
             "the BODY the way official Gigantamax forms do: a far more massive build and huge new structures made of "
             "{fx} growing out of it - a giant {fx} crown or mane around its neck, enormous {fx} wings or fins rising "
             "from its back, its tail or limbs ending in big {fx} formations, glowing {tcol} energy patterns across its "
             "body and {tcol} energy rings orbiting it. It must look clearly DIFFERENT from the normal {name} but still "
             "be the same Pokemon. Angry glaring eyes, menacing pose, NOT cute. Plain pure white background (#FFFFFF), "
             "nothing behind it, no clouds, no ground, no text. Official Pokemon artwork style, ONE creature only, "
             "exactly one face with two eyes."),
}

# Owner 2026-11-21 ("kya sabhi ke gigantamax same rahenge", "Salamence ka gigantamax kaha he"): the one "gmax" prompt above
# buried every Pokemon in the same mane of type-coloured flames - Haxorus and Salamence both came out as a blue flame
# blob, Salamence's red wings were gone. Official G-Max forms change ONE signature part of the Pokemon in its own way
# (Charizard's flame wings, Lapras' ice crown, Gengar's giant mouth), so each Pokemon gets its own design here:
# (what changes, RGB of the evolve-clip tint, colour named in the Wan prompt, what moves in the Wan clip).
GMAX_DESIGN = {
    "salamence": ("its two red crescent wings grow into two COLOSSAL red crescent wings, each wider than its whole body, "
                  "with white jet-stream trails streaming from the wing tips, and a ring of swirling white storm clouds "
                  "orbits its waist like a cyclone", (230, 60, 70), "crimson", "its colossal red wings beat slowly"),
    "haxorus": ("the two red-edged tusks on the sides of its jaw THEMSELVES grow into two HUGE glowing golden axe-blade "
                "tusks, each longer than its body, still attached to its jaw; its hands stay empty claws and it holds NO "
                "weapon; a row of tall golden armour plates rises along its back and tail", (255, 190, 40), "golden",
                "its giant axe tusks gleam and it swings its head"),
    "flygon": ("its diamond wings become FOUR giant green wings with red rims, a spinning sand tornado at each wing tip, "
               "its red eye covers glow and a sandstorm ring circles its feet", (215, 170, 80), "sandy gold",
               "its four giant wings beat and the sand tornadoes spin"),
    "hydreigon": ("its six black wings become six HUGE black wings edged with violet flames, and the pink mane "
                  "of its main head becomes a glowing magenta crown of spikes", (170, 60, 220), "violet",
                  "its six huge wings spread and its three heads look around"),
    "feraligatr": ("its red back spikes grow into a giant crashing tidal wave that rises along its back like an enormous "
                   "fin, and its jaws glow blue", (40, 140, 255), "ocean blue", "the giant wave on its back churns"),
    "typhlosion": ("its flame collar erupts into a towering volcano-shaped ring of fire around its neck, taller than its "
                   "body, with glowing lava cracks on its legs", (255, 110, 20), "orange", "the volcano of flames on its "
                   "neck roars upward"),
    "meganium": ("the petals around its neck grow into one GIGANTIC pink flower like a parasol above it, raining glowing "
                 "golden pollen, with long flowering vines hanging from it", (255, 150, 200), "pink",
                 "the giant flower sways and golden pollen falls"),
    "empoleon": ("its trident crest grows into a giant golden trident crown and its flipper blades become huge icy steel "
                 "blades, with an ice cape flowing from its shoulders", (150, 210, 255), "icy blue",
                 "its icy cape flows and its blades shine"),
    "torterra": ("the tree on its back grows into a GIANT ancient tree with a wide canopy and a small waterfall, and its "
                 "rocky shell rises into mountain peaks", (90, 190, 80), "forest green",
                 "the giant tree's leaves sway and the waterfall flows"),
    "decidueye": ("its hooded cloak grows into a huge flowing cloak of leaves like a forest, and its arrow quill becomes "
                  "a giant glowing green longbow made of vines", (60, 200, 90), "emerald", "its leaf cloak flows and it "
                  "draws the giant bow"),
    "incineroar": ("its flame belt becomes a giant blazing champion belt and huge flame gauntlets cover its forearms",
                   (255, 80, 30), "red-orange", "it flexes and the flame gauntlets blaze"),
    "primarina": ("its long hair becomes a HUGE flowing gown of water and bubbles and a crown of water crystals sits on its "
                  "head", (90, 170, 255), "aqua", "the gown of water flows and bubbles rise"),
    "meowscarada": ("its cape of leaves becomes a GIANT magician's cape of flower petals and a ring of glowing flower bombs "
                    "floats around it", (230, 70, 150), "magenta", "its petal cape flutters and the flower bombs orbit"),
    "skeledirge": ("the little fire bird on its head becomes a GIANT blazing phoenix of flames spreading its wings above "
                   "it", (255, 140, 40), "orange", "the flaming phoenix on its head spreads its wings"),
    "quaquaval": ("its head feathers grow into a giant carnival headdress of water feathers and its leg feathers become "
                  "long flowing water ribbons", (60, 180, 255), "turquoise", "the water ribbons swirl as it poses"),
    "arcanine": ("its mane grows into a HUGE blazing sun-like mane of fire and its tail becomes a long comet of flames",
                 (255, 150, 40), "golden orange", "its sun mane blazes"),
    "ninetales": ("its nine tails grow into nine ENORMOUS tails of pale blue foxfire fanned out behind it like a peacock",
                  (140, 190, 255), "pale blue", "its nine giant tails wave"),
    "crobat": ("its four wings become four GIANT purple wings in an X shape with poison mist dripping from the tips",
               (170, 70, 200), "purple", "its four giant wings flap"),
    "scizor": ("its pincers grow into two GIANT red steel hammer claws with glowing eye patterns, and its wings become "
               "big steel blades", (220, 50, 50), "steel red", "it raises its giant hammer claws"),
    "serperior": ("its leaf collar grows into a huge royal crown of leaves and its body coils into a giant spiral with a "
                  "long train of leaves", (90, 200, 90), "royal green", "its long leaf train flows"),
    "emboar": ("its fiery beard grows into a giant blazing beard and mane of fire, and flame gauntlets cover its arms",
               (255, 90, 30), "orange", "its blazing beard roars"),
    "samurott": ("its shell blades become two HUGE glowing water katanas and its helmet horn grows into a long sweeping "
                 "crest", (70, 150, 230), "steel blue", "it raises the giant water katanas"),
    "delphox": ("its stick becomes a GIANT flaming magic staff and its robe-like fur grows into a huge flowing robe of "
                "fire", (255, 110, 60), "flame red", "it spins the giant staff"),
    "chesnaught": ("its shell grows into a GIANT spiked fortress shield on its arm and its spikes glow green",
                   (110, 180, 60), "green", "it raises the giant shield"),
    "goodra": ("its antennae grow very long and glowing and its body slime pours down like a waterfall, with a small rain "
               "cloud above its head", (170, 120, 220), "lavender", "the slime waterfall flows and rain falls"),
    "kommo-o": ("its scales grow into giant golden armour scales like bells and its head crest becomes a huge golden "
                "scale crown", (255, 200, 60), "gold", "its golden scales shake and shine"),
}
# Owner 2026-11-22 "where is its mega form": 9 Mega Hydreigon draws with FAN_PROMPT["mega"] ("more muscular build,
# bigger claws") all came out as a standing dragon with clawed arms, legs and an extra head. The Gigantamax that worked
# was an add-only edit, so a Pokemon listed here gets its Mega the same way: the picture stays, only these parts change.
MEGA_DESIGN = {
    "hydreigon": "dark steel-blue armour plates with magenta edges over its chest and shoulders, its pink manes grow into "
                 "longer sharper magenta spikes, each of its six black wings gets a sharp silver edge, its eyes glow red, "
                 "and a small glowing red gem sits in the centre of its chest. The top of its body between the wings "
                 "stays a plain furry body with NO head and NO neck on it",
}
FAN_MEGA_ONE = ("Edit this picture of the Pokemon {name} into its MEGA EVOLUTION. Change ONLY this: {design}. Everything "
                "else stays EXACTLY like the picture: the same pose, the same number of heads, arms, wings and tail, the "
                "same head shapes and faces, the same {colour} body colours. Do NOT add arms, legs, feet, claws or "
                "heads. Angry glaring eyes, menacing, NOT cute. Plain pure white background (#FFFFFF), nothing behind or "
                "under it - no dust cloud, no smoke, no ground, no shadow, no aura, no text; the white space under its tail stays empty. Official Pokemon artwork style, ONE creature "
                "only, exactly one face per head.")
FAN_GMAX_ONE = ("Edit this picture of the Pokemon {name} into its GIGANTAMAX form. Change ONLY this, like an official "
                "Gigantamax form: {design}. Everything else stays EXACTLY like the picture: the same head, face, eyes, "
                "snout, horns, legs and pose, the same {colour} body colours. The whole body stays clearly visible - do "
                "NOT cover it with fire, flames, smoke, aura or energy, NO mane of energy around the body. Angry glaring "
                "eyes, menacing, NOT cute. Plain pure white background (#FFFFFF), nothing behind it, no ground, no text. "
                "Official Pokemon artwork style, ONE creature only, exactly one face per head.")
# Owner 2026-11-21 "Salamence ka to animation ekdum bakwas he": Wan turned Mega Salamence into a standing humanoid with
# ghost rings (twice, also with a hover pose) and gave Salamence a second translucent face - these get the local idle
# animation (fal_video.idle_clip: breathing, aura, particles) instead of a Wan clip.
# 2026-11-22 Hydreigon (owner: "end me aate aate haath dikhte he"): Wan turned its hand-heads into human hands and its
# tail into ghost legs. A slug here also covers its fan-made Mega / Gigantamax art (fan_<kind>_..._<slug>.png).
LOCAL_ANIM = {"salamence", "salamence-mega", "hydreigon"}


def _local_anim(who: str) -> str | None:
    """The Pokemon slug when this form is animated locally (LOCAL_ANIM, or a fan-made form of one), else None."""
    if who in LOCAL_ANIM:
        return who
    if who.endswith(".png"):
        return next((x for x in LOCAL_ANIM if Path(who).stem.endswith("_" + x)), None)
    return None
# Wan poses (2026-11-21, Mega Salamence: "stands tall" made Wan stand it up like a person, with ghost rings around it)
POSE = {"salamence": "the dragon stays low on its four legs", "salamence-mega": "the dragon hovers in the air with its "
        "crescent wings spread wide, it does NOT stand up on two legs, its body keeps the same shape",
        "flygon": "it stays on its legs with its wings spread", "haxorus": "it stays on its two legs"}


def _main_colour(art) -> str:
    """Rough name of the art's main colour (Groudon -> red): Gigantamax Groudon kept coming out beige ("sand") until
    the prompt named its colour."""
    import colorsys

    import numpy as np

    a = np.asarray(art.convert("RGBA").resize((96, 96))).reshape(-1, 4).astype(float) / 255
    a = a[a[:, 3] > 0.5][:, :3]
    hsv = np.array([colorsys.rgb_to_hsv(*px) for px in a])
    sat = hsv[(hsv[:, 1] > 0.18) & (hsv[:, 2] > 0.2)]
    if len(sat) < len(hsv) * 0.15:
        dark = (hsv[:, 2] < 0.35).mean()
        return "black" if dark > 0.5 else "white and grey" if hsv[:, 2].mean() > 0.7 else "grey"
    edges = ((15, "red"), (40, "orange"), (72, "yellow"), (160, "green"), (195, "teal"), (250, "blue"),
             (295, "purple"), (345, "pink"), (361, "red"))
    names = [next(n for top, n in edges if d < top) for d in sat[:, 0] * 360]
    counts = sorted(((names.count(n), n) for n in set(names)), reverse=True)
    top = [n for c, n in counts[:2] if c >= 0.25 * len(names)]  # Blastoise: blue AND orange (the shell is bigger)
    main = counts[0][1]
    px = np.array([rgb for rgb, n, (h, sv, v) in zip(a[(hsv[:, 1] > 0.18) & (hsv[:, 2] > 0.2)], names, sat) if n == main])
    hexcol = "#%02X%02X%02X" % tuple(int(x * 255) for x in np.median(px, axis=0))  # Dragonite kept turning dark brown
    return " and ".join(top or [main]) + f" ({hexcol})"


def fan_form(slug: str, kind: str) -> str:
    """Fan-made Mega / Gigantamax art for a Pokemon that has none (kind "mega" / "gmax"): the official artwork edited
    by Qwen-Image-Edit through HF Inference Providers, background cut out. Cached as CACHE/art/fan_<kind>_<slug>.png."""
    from PIL import Image

    design = GMAX_DESIGN.get(slug) if kind == "gmax" else None
    mdesign = MEGA_DESIGN.get(slug) if kind == "mega" else None
    # v2: changed body, type colour; v3: the Pokemon's own design (GMAX_DESIGN, 2026-11-21 / MEGA_DESIGN, 2026-11-22)
    ver = ("_v3" if design else "_v2") if kind == "gmax" else ("_v3" if mdesign else "")
    out = CACHE / "art" / f"fan_{kind}{ver}_{slug}.png"
    if out.exists():
        return str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    art = _art(slug)
    side = int(max(art.size) * 1.15)
    ref = Image.new("RGB", (side, side), (255, 255, 255))
    ref.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)
    ref_path = out.with_name(f"ref_{slug}.png")
    ref.save(ref_path)
    types = _types(slug)
    extra = PARTS.get(slug, "")
    template = (FAN_GMAX_ONE.replace("{design}", design[0]) if design
                else FAN_MEGA_ONE.replace("{design}", mdesign) if mdesign else FAN_PROMPT[kind])
    prompt = extra + template.format(name=_name(slug), types=" and ".join(types), colour=_main_colour(art),
                                     fx=ENERGY.get(types[0], "glowing energy"), tcol=TYPE_TINT.get(types[0], ((0,), "golden"))[1])
    # HF credits first, the free ZeroGPU Space when they are gone (credits ran out 2026-11-17: Axew, Bagon and Deino were
    # built on 2026-11-20 with no fan form at all)
    raw = _qwen_draw(prompt, [ref_path], provider_ok=True)
    shutil.copy(raw, out.with_name(f"fan_{kind}_{slug}_raw.png"))
    # the prompt asks for plain white: a border flood fill is cleaner here than rembg, which left grey patches around
    # Mega Groudon's flames (2026-11-17)
    _key_flat(raw, out)
    # the free ZeroGPU Space sometimes draws dust / ground behind it (Mega Haxorus 2026-11-20): when the flat cut leaves
    # the picture's border mostly solid, rembg's anime model cuts it instead
    import numpy as np
    alpha = np.asarray(Image.open(out).convert("RGBA").getchannel("A"))
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    if (border > 128).mean() > 0.12:
        _cutout(Path(raw), out)
    if mdesign:  # the edit model keeps drawing a dust cloud under Mega Hydreigon even when told not to
        _drop_dust(out)
    log.info("   fan-made %s art for %s: %s", kind, slug, out)
    return str(out)


def _drop_dust(png: Path) -> None:
    """Erase a pale, grey-beige dust cloud from the lower part of a cut-out drawing and keep only the biggest piece
    (2026-11-22: all 8 Mega Hydreigon edits had dust under the tail). Only for MEGA_DESIGN forms - it would eat the
    body of a white or grey Pokemon."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    px = np.asarray(Image.open(png).convert("RGBA")).copy()
    rgb = px[..., :3].astype(float) / 255
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    low = np.arange(px.shape[0])[:, None] > px.shape[0] * 0.55
    px[(px[..., 3] > 0) & (mx > 0.5) & (sat < 0.28) & low, 3] = 0
    lab, n = ndimage.label(px[..., 3] > 20)
    if n > 1:
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        px[(lab != int(np.argmax(sizes)) + 1) & (lab > 0), 3] = 0
    Image.fromarray(px).save(png)


def with_forms(entry: dict, draw: bool = True) -> dict:
    """The entry plus its Mega / Gigantamax forms: official ones from PokeAPI, else fan-made art (draw=False for
    --plan). "forms": false in a queue entry turns this off."""
    if entry.get("forms") is False:
        return {**entry, "megas": entry.get("megas", megas_of(entry["stages"][-1]))}
    final = entry["stages"][-1]
    megas = entry["megas"] if "megas" in entry else megas_of(final)
    gmax = entry["gmax"] if "gmax" in entry else gmax_of(final)
    fan = dict(entry.get("fan", {}))
    for kind, official in (("mega", megas), ("gmax", gmax)):
        # "skip_forms": ["mega"] - a fan form the model cannot draw (Mega Hydreigon: 9 draws, all with extra heads /
        # claws / legs, 2026-11-22) is left out instead of failing the line every run
        if kind in entry.get("skip_forms", []):
            continue
        if draw and not official and not fan.get(kind):
            try:
                fan[kind] = fan_form(final, kind)
            except fv.QuotaExhausted:
                raise
            except Exception as exc:  # noqa: BLE001 - owner 2026-11-20: never a short without its forms - try again next run
                raise RuntimeError(f"fan-made {kind} art for {final} could not be drawn: {str(exc)[:200]}") from exc
    primal = entry["primal"] if "primal" in entry else primal_of(final)
    return {**entry, "megas": megas, "gmax": gmax, "primal": primal, "fan": fan, "forms": True}


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
    forms, fan = entry.get("forms"), entry.get("fan", {})
    fan_mega = forms and not megas and fan.get("mega")
    if fan_mega:
        megas = [fan["mega"]]  # a local .png - _poke_art / evolve / Wan all take it like a slug
    gmax = (entry.get("gmax") or fan.get("gmax")) if forms else None
    names = [_name(x) for x in s]
    bg, fx = BACKGROUND.get(t, "wiki:Meadow"), ENERGY.get(t, "white energy")
    face = "its mouth stays closed, its face, eyes and design keep exactly the same shape"
    idle = (f"the creature looks around determined with small natural movements, {fx} float around it, {face}, "
            "anime style, smooth animation, camera slowly pushes in")
    # no "burst of white light": the drawn evolve clip already flashes white, and a second Wan flash smeared
    # Dragonite's face into a sketch before it opened a huge mouth (owner 2026-11-17: "chehera bekar he")
    reveal = (f"the creature stands in place and looks around proudly with small natural movements, a few {fx} drift "
              f"around it, {face}, anime style, smooth gentle animation, camera slowly pushes in")
    finale = f"the creature roars and unleashes a powerful attack of {fx}, epic low angle camera, anime style, dynamic motion"
    def wan(who, prompt, fade=0.0):
        slug = _local_anim(who)
        if slug:  # Wan breaks this design - animated locally, the art can never change shape
            col = list(TYPE_TINT.get(_types(slug)[-1], ((140, 190, 255), ""))[0])
            stem = Path(who).stem
            return {"idle": who, "background": bg, "seconds": 5, "color": col,
                    "mode": "power" if ("-mega" in who or stem.startswith(("fan_mega", "fan_gmax"))) else "idle",
                    **({"white_in": fade} if fade else {})}
        return {"image": {"poke": who, "background": bg}, "shots": [[prompt, 5]], **({"white_in": fade} if fade else {})}
    evolve = lambda a, b, mega=False: {"evolve": [a, b], "background": bg, "seconds": 3.2, "mega": mega}  # noqa: E731
    outro = (f"Who is your favourite {t.title()}-type Pokemon? Comment below!" if not forms
             else f"{'Primal, ' if entry.get('primal') else ''}{'Mega, ' if megas else ''}Dynamax or Gigantamax, "
                  "which form is your favourite? "
                  "Comment below!")
    clips, narration = [wan(s[0], idle)], [f"{names[0]} is getting ready for something big."]
    for k in range(1, len(s)):
        last = k == len(s) - 1
        shown = finale if last and not (megas or forms) else reveal
        clips += [evolve(s[k - 1], s[k]), wan(s[k], f"{POSE[s[k]]}, {shown}" if s[k] in POSE else shown, 0.6)]
        narration += [f"Oh? {names[k - 1]} is evolving!",
                      f"{names[k - 1]} evolved into {names[k]}!" + ("" if not last else
                                                                    " But it is not over yet." if megas or forms
                                                                    else f" {outro}")]
    if forms and entry.get("primal"):  # Groudon / Kyogre: the official Primal Reversion comes first
        pr = entry["primal"]
        ptypes = _types(pr)
        clips += [{**evolve(s[-1], pr), "tint": [255, 120, 40] if "fire" in ptypes else [60, 140, 255]},
                  wan(pr, f"the colossal creature roars with ancient power, {ENERGY.get(ptypes[-1], fx)} erupt around it, "
                          "glowing patterns pulse on its body, epic low angle camera, anime style, dynamic motion", 0.6)]
        narration += [f"{names[-1]} reacts to the {ORB.get(s[-1], "orb")}. Primal Reversion!",
                      f"Primal {names[-1]}, a {' and '.join(x.title() for x in ptypes)} type! But there is more."]
    for k, m in enumerate(megas):
        mtypes = _types(s[-1] if fan_mega else m)
        mfx = ENERGY.get(mtypes[-1], fx)
        clips += [evolve(s[-1], m, mega=True),
                  # "{mfx} and rainbow energy explode around it" hid Mega Dragonite behind a white blast for the whole
                  # clip (2026-11-17): the Mega must stay clearly visible
                  wan(m, f"{POSE.get(m, 'the creature stands tall')} and looks around confidently with its mouth "
                         f"closed, its face keeps "
                         f"exactly the same shape, a glowing rainbow aura shines around its outline, "
                         f"a few {mfx} drift around it, the creature stays fully visible and in focus the whole time, "
                         f"epic low angle camera slowly pushes in, anime style, {_KEEP}", 0.6)]
        if fan_mega:
            narration.append(f"{names[-1]} has no Mega Evolution in the games, so here is a fan-made one. Mega Evolution!")
        else:
            narration.append(f"{names[-1]} reacts to its Mega Stone. Mega Evolution!" if k == 0
                             else f"But {names[-1]} has a second Mega Evolution!")
        mname = f"Fan-made Mega {names[-1]}" if fan_mega else mega_name(m)
        typed = f"{mname}, a {' and '.join(x.title() for x in mtypes)} type!"
        if k < len(megas) - 1 or forms:
            narration.append(typed)
        elif len(megas) > 1:
            narration.append(f"{typed} {' or '.join(mega_name(x).split()[-1] for x in megas)}, which one do you like more? "
                             "Comment below!")
        else:
            narration.append(f"{typed} {outro}")
    if forms:  # Dynamax (every Pokemon can), then Gigantamax - official, or fan-made when it has none
        clips.append({"dynamax": s[-1], "background": bg, "seconds": 4.6})
        narration.append(f"Now {names[-1]} Dynamaxes and grows to a giant size!")
        if gmax:
            fan_g = not entry.get("gmax")
            gtype = _types(s[-1])[0]
            (grgb, gcol), gfx = TYPE_TINT.get(gtype, ((255, 200, 80), "golden")), ENERGY.get(gtype, fx)
            design = GMAX_DESIGN.get(s[-1]) if fan_g else None
            if design:  # its own colour + motion; nothing swirls over the body (Salamence vanished in blue flames)
                grgb, gcol = design[1], design[2]
                gprompt = (f"the colossal creature glares down menacingly with its mouth closed, {design[3]}, "
                           f"{POSE.get(s[-1], 'it keeps its pose')}, its face keeps exactly the same shape, a few small "
                           f"{gcol} sparks drift in the air, nothing covers its body, its colours stay exactly the same, it "
                           f"stays fully visible, epic low angle camera, anime style, {_KEEP}")
            else:
                gprompt = ("the colossal creature glares down menacingly with its mouth closed and breathes heavily, its "
                           f"face keeps exactly the same shape, huge {gfx} swirl around its giant body, {gcol} energy "
                           f"patterns pulse across it, {gcol} light fills the sky, its colours stay exactly the same, "
                           f"it stays fully visible, epic low angle camera, anime style, {_KEEP}")
            # not red like the Dynamax (owner 2026-11-20: they are different and must look different)
            gclip = wan(gmax, gprompt, 0.6)
            if "idle" in gclip:  # animated locally: the aura takes the Gigantamax colour too
                gclip["color"] = list(grgb)
            clips += [{**evolve(s[-1], gmax), "tint": list(grgb)}, gclip]
            narration.append(f"{names[-1]} cannot Gigantamax in the games, so here is a fan-made Gigantamax form!" if fan_g
                             else f"And {names[-1]} has even more power. Gigantamax!")
            narration.append(f"{'Fan-made ' if fan_g else ''}Gigantamax {names[-1]}! {outro}")
        else:
            narration[-1] += f" {outro}"
    last_scene = max(i for i, c in enumerate(clips) if "image" in c or "idle" in c)  # the Dynamax clip has no scene image
    still = (str(CACHE / entry["id"] / f"scene_{last_scene:02d}.jpg") if "image" in clips[last_scene]
             else {"poke": clips[last_scene]["idle"], "background": bg})  # a locally animated last form: drawn fresh
    clips.append({"still": still, "seconds": 4, "zoom": [1.05, 1.15]})
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
    if pair.get("family", "pokemon") == "pokemon" and pair.get("text_only"):
        # Only for pairs marked "text_only". Owner 2026-11-11 called a text-only batch of 10 "sab bakwas" and picked
        # Darkceus + Umbrasol as the look to follow - both drawn from the parents' official art (the route below),
        # so they keep the official Pokemon art style and recognisable parts of both parents.
        # owner 2026-11-09: "ek ke upar ek mat karo, bilkul fused lagna chahiye" - with the parents' pictures as input
        # the edit models pasted one Pokemon's parts onto the other (Sceptile + Salamence came out as two creatures).
        # Text only (the model knows every Pokemon by name) gives a real blend; the owner approved samples the same day.
        a, b = _name(pair["a"]), _name(pair["b"])
        try:
            return _provider_draw(
                f"A brand-new Pokemon that is a seamless fusion of {a} and {b}, designed as ONE original creature like an "
                f"official Pokemon from a new game - not {a} wearing {b}'s parts and never two creatures: body shape and "
                f"proportions halfway between {a} and {b}, the head blends both heads, both colour schemes woven together "
                f"over the whole body, every feature grows naturally from one body. {pair['fuse']}. ONE face with exactly "
                "TWO eyes, full body from head to feet standing facing the viewer, clearly drawn legs, official Ken "
                "Sugimori Pokemon artwork style, flat 2D anime illustration, NOT a 3D render, clean black line art, soft "
                "cel shading, plain pure white background, no scenery.")
        except Exception as exc:  # noqa: BLE001 - provider down / out of credits: the old reference-image route
            log.warning("   text-only fusion art failed (%s) - using the reference images", str(exc)[:150])
    if pair.get("family") == "dbz":  # a canon fusion: draw THAT character, the parents only guide the style
        return _qwen_draw(f"Draw {pair['fuse']}. One single character, full body from head to feet, standing facing "
                          f"the viewer, {fam['style']}, same art style as the reference images, plain white "
                          "background.", srcs, provider_ok="text")
    # "ONE single creature": Snorlax + Arcanine once came out as Arcanine standing in front of Snorlax (2026-11-09)
    # "ONE face, exactly two eyes": owner 2026-11-09 - fused art showed both parents' eyes on one creature
    return _qwen_draw(f"Design ONE brand-new {fam['word']} that is a fusion of {_name(pair['a'])} (image 1) and "
                      f"{_name(pair['b'])} (image 2): {pair['fuse']}. ONE single creature with one head and one body, "
                      "ONE face with exactly TWO eyes - never a second pair of eyes, no extra eyes, no eye-shaped "
                      "markings and no second face anywhere on the head or body, "
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
    provider_ok, HF Inference Providers (credits, ~$0.03 an image) draw it FIRST and the ZeroGPU Space is only the
    fallback: fusion art drawn on the Space used up the shared 40-min ZeroGPU day, so the Wan evolution shorts got
    "quota used up" from 2026-11-05 to 11-09 and none was posted (owner noticed 2026-11-09)."""
    if provider_ok:
        try:
            return _provider_edit(prompt, refs) if refs and provider_ok != "text" else _provider_draw(prompt)
        except Exception as exc:  # noqa: BLE001 - provider down / out of credits: try the Space
            log.warning("   Inference Providers failed (%s) - trying the ZeroGPU Space", str(exc)[:150])
            return _qwen_draw(prompt, refs)
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


FUSION_STYLE = 9  # 9: Dragon Ball signature attacks at the camera
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


# how each signature attack is thrown (Wan prompt), owner 2026-11-09: the locally drawn Kamehameha (a ball + rings, the
# character never moved) was "bohot bekar" - "proper animation, jaise power shoot karte"
ATTACK_MOTION = {
    "KAMEHAMEHA": "cups both hands together at his side, a bright blue energy ball charges between his palms, then he "
                  "thrusts both hands forward and fires a huge blue Kamehameha energy beam",
    "FINAL KAMEHAMEHA": "cups both hands together at his side, a blue and yellow energy ball charges between his palms, then "
                        "he thrusts both hands forward and fires a huge blue and yellow energy beam",
    "FINAL FLASH": "spreads both arms wide charging yellow energy, brings his palms together in front of him and fires a "
                   "gigantic yellow energy beam",
    "GALICK GUN": "pulls both hands to one side with palms out, purple energy crackles around them, then he thrusts them "
                  "forward and fires a huge purple energy beam",
    "DEATH BEAM": "points one finger straight at the camera, a bright pink light charges at the fingertip, then a "
                  "thick glowing pink-purple laser beam fires from the fingertip straight toward the camera and "
                  "flashes across the screen",  # owner 2026-11-09: a thin line through the chest looked weak
    "ERASER CANNON": "raises one hand, a big green energy ball grows in his palm, then he throws it and it explodes in a "
                     "huge green blast",
    "SPECIAL BEAM CANNON": "puts two fingers to his forehead, yellow energy sparks charge there, then he points the fingers "
                           "forward and fires a thin yellow beam with a spiral of energy around it",
    "MASENKO": "raises both hands above his head charging yellow energy, then brings them down in front of him and fires a "
               "big yellow energy beam",
    "SPHERE OF DESTRUCTION": "raises one hand above his head, a glowing orange sphere of energy grows there, then he hurls "
                             "it forward and it explodes",
}


def dbz_attack_video(pair: dict, fused: Path, bg: str) -> dict | None:
    """The Dragon Ball signature attack as a real animation: Wan 2.2 image-to-video through HF credits
    (fal_video.provider_i2v) from the fused character on the scene, then RIFE + Real-ESRGAN HD on the laptop. Cached in
    the fusion's folder. None (-> the locally drawn attack) when the provider fails."""
    attack = pair["attack"]
    motion = ATTACK_MOTION.get(attack["name"])
    if not motion:
        return None
    out = fused.parent
    raw = out / "wan_attack.mp4"
    try:
        start = out / "frame_attack.png"
        if not start.exists():
            work = out / ".work"
            work.mkdir(parents=True, exist_ok=True)
            frame = fv._poke_background(bg, work).convert("RGBA")
            art, (x, y) = fv._poke_art(str(fused), work)
            frame.alpha_composite(art, (x, y))
            frame.convert("RGB").save(start)
        fv.provider_i2v(start, (
            f"{pair['name']}, a Dragon Ball character, {motion}, the beam blasts across the screen, his aura flares, "
            "he keeps exactly the same face, hair, outfit and colors, Dragon Ball Z anime style, dynamic powerful motion, "
            "steady camera"), raw)
    except Exception as exc:  # noqa: BLE001 - provider down / out of credits
        log.warning("   Wan attack clip failed (%s) - using the drawn attack", str(exc)[:150])
        return None
    return {"video": str(fv.hd_clip(raw, out / "wan_attack_hd.mp4"))}


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
    # Dragon Ball: the pair's signature "attack" (owner 2026-11-09: "Kamehameha jab Goku jaise fused ho") is the last
    # move, in the attack's own colour, and the narration shouts its name over it; the joke moves to the closing line
    attack = pair.get("attack") if fam == "dbz" else None
    if attack:
        moves = tuple([m for m in moves if m not in ("signature", "blast")] + [m for m in moves if m in ("signature", "blast")])
    act = lambda mode, secs, c=None: {"action": str(fused), "background": bg_a, "seconds": secs, "mode": mode,  # noqa: E731
                                      "color": list(c or color)}
    ra, rb = art_ref(fam, pair["a"]), art_ref(fam, pair["b"])
    clips = [still(ra, bg_a, 4.0),
             still(rb, bg_b, 3.5),
             {"fuse": [ra, rb, str(fused)], "background": bg_a, "seconds": 3.8},
             # owner 2026-11-09: hold the new creature still for 3 s, then let it DO something (local, design-safe)
             {"still": {"poke": str(fused), "background": bg_a}, "seconds": 3.0, "zoom": [1.0, 1.0], "white_in": 0.6},
             act(moves[0], 4.0),
             (dbz_attack_video(pair, fused, bg_a) or act(moves[1], 5.0, attack["color"])) if attack
             else act(moves[1], 4.5),  # attack shout, else the joke
             {"still": str(fused.parent / "scene_03.jpg"), "seconds": 8.0 if attack else 5.5,
              "zoom": [1.05, 1.15]}]  # closing question
    joke = humor.pick({"pokemon": "fusion", "pal": "palfusion", "dbz": "dbzfusion"}[fam], meta["id"])
    if fam == "dbz":
        dance = pair.get("method") == "Fusion Dance"
        narration = [f"{a} and {b} are about to fuse!", f"Here comes {b}!",
                     "Fu... sion... HA!" if dance else "The Potara earrings are on!",
                     "Fusion complete!", f"Meet {name}!", attack["say"] if attack else joke,
                     ((joke + " ") if attack and joke else "")
                     + (f"{name}, a fan-made {pair['method']} fusion." if pair.get("fan") else f"{name}, born from the {pair['method']}.")
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


REVIEW = CACHE / "review_pending.json"
REJECTED = CACHE / "review_rejected.json"


def _ids(path: Path) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _set_ids(path: Path, ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(ids)), indent=2) + "\n", encoding="utf-8")


def make_evolution(entry: dict, poster, games: dict, review: bool = False) -> None:
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
        entry = with_forms(entry)  # official Megas / Gigantamax, or fan-made ones (owner 2026-11-17)
        sb = storyboard(entry)
    board.write_text(json.dumps(sb, indent=2) + "\n", encoding="utf-8")
    seen = out / "final.mp4"  # an approved line posts exactly the file the owner watched
    video = seen if not review and entry["id"] in _ids(REVIEW) and seen.exists() else fv.build(board, out)
    if review:  # the owner sees it first - posted later with --post-approved
        _set_ids(REVIEW, _ids(REVIEW) + [entry["id"]])
        log.info("   built %s - waiting for the owner's OK (%s)", entry["id"], video)
        return
    _set_ids(REVIEW, [x for x in _ids(REVIEW) if x != entry["id"]])
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
    if entry.get("forms"):  # Mega + Dynamax + Gigantamax (owner 2026-11-17); fan-made ones are said to be fan-made
        fan = entry.get("fan", {})
        fan_mega, fan_gmax = not megas and bool(fan.get("mega")), not entry.get("gmax") and bool(fan.get("gmax"))
        later = names[1:]
        primal = [f"Primal {names[-1]}"] if entry.get("primal") else []
        if later:
            title = f"{names[0]} Evolves Into " + (", ".join(later[:-1]) + " and " + later[-1] if len(later) > 1
                                                   else later[0])
        else:  # a Pokemon that does not evolve (Groudon): its forms are the video
            title = f"{names[0]} Transforms" + (" | Primal" if primal else "")
        m_, m2 = ("Mega, ", "Mega ") if megas or fan_mega else ("", "")  # Hydreigon: no Mega (skip_forms)
        for suffix in ((f", {m_}Dynamax and Gigantamax",) if not later and primal else ()) + (
                f" | {m_}Dynamax and Gigantamax", f" | {m2}Dynamax Gigantamax"):
            if len(title + suffix) <= 95:
                title += suffix
                break
        if (fan_mega or fan_gmax) and len(title) <= 87:
            title += " What If"
        forms_txt = [*primal, *(megas or ([f"Mega {names[-1]}"] if fan_mega else [])), f"Dynamax {names[-1]}",
                     *([f"Gigantamax {names[-1]}"] if entry.get("gmax") or fan_gmax else [])]
        made = [f"Mega {names[-1]}"] * fan_mega + [f"Gigantamax {names[-1]}"] * fan_gmax
        desc = (f"{' to '.join(names)}, then {', '.join(forms_txt)} - a Pokemon "
                f"{'evolution' if later else 'transformation'} animation, made with AI from "
                "the official artwork. "
                + (f"Fan-made, not in the games: {' and '.join(made)}. " if made else "")
                + f"{m_}Dynamax or Gigantamax, which form is your favourite? Comment below!")
        tags = [*names, *forms_txt, f"{names[0]} evolution", "Pokemon evolution", *(["Mega Evolution"] if m_ else []),
                "Dynamax",
                "Gigantamax", *(["fan made Pokemon", "Pokemon what if"] if made else []), f"{entry['type']} type Pokemon",
                "Pokemon animation", "Pokemon", "Pokemon shorts"]
        hashtags = " ".join(f"#{h}" for h in ["Pokemon", "PokemonEvolution", names[-1].replace(" ", ""), "Gigantamax",
                                                "Shorts"])
        return f"{title}\n\n{desc}\n\n{hashtags}", tags
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
    parser.add_argument("--review", action="store_true", help="build new evolution lines but post nothing")
    parser.add_argument("--post-approved", metavar="IDS", help="comma list of reviewed lines to post now")
    parser.add_argument("--reject", metavar="IDS", help="comma list of reviewed lines the owner rejected")
    args = parser.parse_args()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    pairs = [p for fam in FAMILIES for p in load_pairs(fam)]
    if args.plan:
        print(json.dumps(storyboard(with_forms(next(e for e in queue if e["id"] == args.plan), draw=False)), indent=2))
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
    if args.reject:
        bad = [x.strip() for x in args.reject.split(",") if x.strip()]
        _set_ids(REJECTED, _ids(REJECTED) + bad)
        _set_ids(REVIEW, [x for x in _ids(REVIEW) if x not in bad])
        log.info("rejected %s", ", ".join(bad))
    for lid in [x.strip() for x in (args.post_approved or "").split(",") if x.strip()]:
        entry = next((e for e in queue if e["id"] == lid), None)
        if entry is None or f"poke-{lid}" in games:
            log.warning("%s: not in the queue or already posted", lid)
            continue
        try:
            make_evolution(entry, poster, games)  # clips are cached - this only renders (if needed) and posts
            dj.HEALTH["built"] += 1
        except Exception:
            log.exception("   %s failed", lid)
            dj.HEALTH["build_failed"] += 1
    held = set(_ids(REVIEW)) | set(_ids(REJECTED))  # waiting for the owner / turned down: no new build
    evos = [("evo", e) for e in queue if f"poke-{e['id']}" not in games and e["id"] not in held][: args.max_shorts]
    fusions = []
    for fam, cap in (("pokemon", args.max_fusions), ("pal", args.max_pal_fusions), ("dbz", args.max_dbz_fusions)):
        k = FAMILIES[fam]["key"]
        fusions += [("fusion", p) for p in load_pairs(fam)
                    if f"{k}-{pair_id(p)}" not in games and f"{k}-{p['b']}+{p['a']}" not in games][:cap]
    jobs = [job for k in range(max(len(evos), len(fusions))) for job in (evos[k:k + 1] + fusions[k:k + 1])]
    for kind, item in jobs:
        try:
            if kind == "evo":
                make_evolution(item, poster, games, review=args.review)
            else:
                make_fusion(item, poster, games)
        except fv.QuotaExhausted as exc:
            log.info("   free GPU quota used up - continuing next run (%s)", str(exc)[:120])
            break
        except Exception:
            log.exception("   failed - skipping to the next one")
            dj.HEALTH["build_failed"] += 1
            continue
        dj.HEALTH["built"] += 1

    # a "hold:review" marker counts as done for pending(), so a line waiting in review_queue.json (YouTube during the
    # strike) or in review_pending.json must keep its final.mp4 - 2026-11-22 this deleted Deino and the 11 held
    # YouTube evolution shorts
    held = set(json.loads((CACHE.parent / "review_queue.json").read_text(encoding="utf-8")))         if (CACHE.parent / "review_queue.json").exists() else set()
    for entry in queue:  # fully posted -> drop its clips so the motion-cache branch stays small
        if entry["id"] in _ids(REVIEW) or post_id(entry) in held:
            continue
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
