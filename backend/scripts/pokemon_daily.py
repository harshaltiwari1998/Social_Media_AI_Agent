"""Daily free Pokemon evolution shorts - runs on GitHub Actions (daily-pokemon.yml), no laptop needed.

Works through scripts/specs/pokemon_queue.json (evolution lines checked against PokeAPI's evolution chains).
Each line becomes a 4-clip, ~20 s storyboard for fal_video.py: start frames are the official artwork on a
background photo, motion comes from the free Wan 2.2 Hugging Face ZeroGPU Space (HF_TOKEN secret - about 2-3
clips a day; HF PRO ~8x). Finished clips are cached under media_output/pokemon/<id>/ and kept between runs on the
`motion-cache` branch, so a short builds up over a couple of days; when all clips exist it is rendered and
posted to the gaming platforms (YouTube HarryBeast + gaming Facebook Page + Instagram).

Narration states no numbers - only the evolution line itself (PokeAPI). Posted lines are recorded in
game_videos.json ("kind": "motion") + the pipeline store like every other video.

    python scripts/pokemon_daily.py            # build what the quota allows, post finished shorts
    python scripts/pokemon_daily.py --plan ID  # print the storyboard for one queue entry
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import daily_job as dj  # noqa: E402  (chdirs to backend; Poster, manifests, store)
from scripts import fal_video as fv  # noqa: E402

QUEUE = ROOT / "scripts" / "specs" / "pokemon_queue.json"
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


def storyboard(entry: dict) -> dict:
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


def post_id(entry: dict) -> str:
    return f"custom:motion-poke-{entry['id']}"


def post_text(entry: dict) -> tuple[str, list[str]]:
    names = [_name(x) for x in entry["stages"]]
    title = (f"{names[0]} Evolves Into {names[1]}" + (f" and {names[2]}" if len(names) == 3 else "")
             + " | Pokemon Evolution Animation")
    desc = (f"{' to '.join(names)} - a Pokemon evolution animation, made with AI from the official artwork. "
            f"Who is your favourite {entry['type'].title()}-type Pokemon? Comment below!")
    tags = [*names, f"{names[0]} evolution", "Pokemon evolution", "Pokemon animation", "Pokemon", f"{entry['type']} type Pokemon",
            "evolution animation", "Pokemon shorts"]
    hashtags = " ".join(f"#{h}" for h in ["Pokemon", "PokemonEvolution", names[-1].replace(" ", ""), "Gaming", "Shorts"])
    return f"{title}\n\n{desc}\n\n{hashtags}", tags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", metavar="ID")
    parser.add_argument("--max-shorts", type=int, default=3, help="new shorts per run (owner: 3 a day)")
    args = parser.parse_args()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    if args.plan:
        print(json.dumps(storyboard(next(e for e in queue if e["id"] == args.plan)), indent=2))
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
                poster.post(post_id(entry), *post_text(entry), str(video))
            else:
                log.warning("%s still pending but its video is gone from the cache", key)

    for entry in queue:
        key = f"poke-{entry['id']}"
        if key in games:
            continue
        if dj.HEALTH["built"] >= args.max_shorts:
            log.info("   %d new shorts this run - the rest waits for the next run", args.max_shorts)
            break
        log.info("== %s", " -> ".join(entry["stages"]))
        out = CACHE / entry["id"]
        try:
            check_chain(entry["stages"])
            board = out / "storyboard.json"
            out.mkdir(parents=True, exist_ok=True)
            board.write_text(json.dumps(storyboard(entry), indent=2) + "\n", encoding="utf-8")
            video = fv.build(board, out)
        except fv.QuotaExhausted as exc:
            log.info("   free GPU quota used up - continuing next run (%s)", str(exc)[:120])
            break
        except Exception:
            log.exception("   failed - skipping to the next line")
            dj.HEALTH["build_failed"] += 1
            continue
        dj.HEALTH["built"] += 1
        games[key] = {"kind": "motion", "spec": QUEUE.relative_to(ROOT).as_posix(), "video": str(video), "data": entry}
        dj._save(dj.GAME_MANIFEST, games)
        poster.post(post_id(entry), *post_text(entry), str(video))

    for entry in queue:  # fully posted -> drop its clips so the motion-cache branch stays small
        if f"poke-{entry['id']}" in games and not poster.pending(post_id(entry)):
            shutil.rmtree(CACHE / entry["id"], ignore_errors=True)
    log.info("Done (%s)", ", ".join(f"{k} {v}" for k, v in dj.HEALTH.items()))
    if dj.HEALTH["post_failed"] and not dj.HEALTH["posted"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
