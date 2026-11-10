"""Mewtwo + evolution stones (owner idea 2026-11-09): Mewtwo punches an evolution stone, the stone explodes into its
energy, Mewtwo evolves into an elemental form (Fire Mewtwo...) that attacks, then evolves again into a Mega form that
attacks. Fan-made "What if" shorts - the forms are NOT in the games and the title/description/narration say so.

Art: Mewtwo = official artwork (PokeAPI). The stone and both forms are drawn text-only by Qwen-Image (HF Inference
Providers, credits) and cut out with rembg. Motion (owner 2026-11-09 on the first, locally drawn sample: "animation
kaha he, proper punch karna chahiye") = Wan 2.2 image-to-video through HF credits (fal_video.provider_i2v), 5 clips a
short: the punch, an energy pillar swallowing each new form PLAYED BACKWARDS (= the pillar clears and the new form
stands there), and one attack per form; the game-style evolution glow (fal_video.evolve_clip) joins them.
Never the ZeroGPU quota - the daily Wan evolution shorts need all of it.

    python scripts/stone_forms.py fire            # build only -> media_output/pokemon/stone-mewtwo-fire/final.mp4
    python scripts/stone_forms.py fire --post     # post an already built, owner-approved video
Posting needs MEDIA_OUTPUT_DIR = a run folder seeded from gaming-state (merge it back with merge_state.py)."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import fal_video as fv, humor, pokemon_daily as pd  # noqa: E402

log = logging.getLogger("stone_forms")
SPECS = Path(__file__).parent / "specs" / "mewtwo_stones.json"
STYLE = ("official Ken Sugimori Pokemon artwork style, flat 2D anime illustration, NOT a 3D render, clean black line "
         "art, soft cel shading, plain pure white background, no scenery")


# owner 2026-11-10: the forms looked soft / cute - "jis tarike se Mewtwo he abhi khatarnak, waise chahiye, rage dikhna
# chahiye": every form is as menacing as the original Mewtwo
FIERCE = ("A fierce, menacing legendary: narrow angry glaring eyes full of rage, a cold stern frown, a tall intimidating "
          "battle stance with clenched fists and a strong muscular build, aura of overwhelming power - as dangerous "
          "looking as the original Mewtwo, NOT cute, NOT chibi, NOT smiling.")


def load() -> list[dict]:
    return json.loads(SPECS.read_text(encoding="utf-8"))


def _draw(prompt: str, out: Path) -> Path:
    if not out.exists():
        pd._cutout(pd._provider_draw(prompt), out)
    return out


def art(entry: dict, out: Path) -> tuple[Path, Path, Path]:
    # the stone alone - asked for an "item from Pokemon" the model drew gems with little bodies and legs (2026-11-10)
    stone = _draw(f"A single magic gemstone: {entry['stone_look']}. Just the glowing stone object floating on its own, "
                  f"NO character, no body, no legs, no arms, no face, centred, game item artwork, {STYLE}.",
                  out / "stone.png")
    # owner 2026-11-09: the plain first form looked bare - every form wears armour (the prompt the approved Fire
    # Mewtwo was drawn with; never put words like that note into the prompt: the provider's content check refused it)
    form = _draw(f"{entry['form']}, a fan-made {entry['type'].title()}-type form of the Pokemon Mewtwo: Mewtwo's exact "
                 "head with its two short horn-like ears, Mewtwo's tall slim body, three-fingered hands, the tube from "
                 "the back of its head to its spine and its long thick tail, same proportions as the official Mewtwo, "
                 f"but {entry['form_look']}. It wears armour plates on the chest, shoulders and hips and a patterned "
                 "bodysuit with bracers on the forearms. " + FIERCE + " ONE single creature, ONE face with exactly TWO "
                 f"eyes, full body from head to feet facing the viewer, {STYLE}.", out / "form.png")
    mega = _draw(f"{entry['mega']}, the Mega Evolution of {entry['form']} (Mewtwo transformed by a {entry['stone']}): "
                 f"Mewtwo's body shape, {entry['form_look']}, now {entry['mega_look']}. " + FIERCE + " Even bigger, "
                 "more menacing and more powerful than the first form, with sharper armour and spikes. ONE single "
                 f"creature, ONE face with exactly TWO eyes, full body from head to feet facing the viewer, {STYLE}.",
                 out / "mega.png")
    return stone, form, mega


def frames(entry: dict, out: Path, stone: Path, form: Path, mega: Path) -> dict:
    """9:16 start frames for the Wan clips: Mewtwo with the glowing stone beside it, and each form alone."""
    from PIL import Image

    work = out / ".work"
    work.mkdir(parents=True, exist_ok=True)
    bg_ref = pd.BACKGROUND.get(entry["type"], "wiki:Meadow")
    col = tuple(pd.TYPE_COLOR.get(entry["type"], (140, 190, 255)))
    res = {}
    start = out / "frame_punch.png"
    if not start.exists():
        bg = fv._poke_background(bg_ref, work).convert("RGBA")
        art, _ = fv._poke_art("mewtwo", work)
        art = art.resize((int(art.width * 0.72), int(art.height * 0.72)), Image.LANCZOS)
        bg.alpha_composite(art, (40, 1920 - art.height - 420))
        st = Image.open(stone).convert("RGBA")
        st = st.crop(st.getbbox() or (0, 0, st.width, st.height))
        k = 200 / max(st.size)
        st = st.resize((int(st.width * k), int(st.height * k)), Image.LANCZOS)
        sx, sy = 820, 1920 - 420 - int(art.height * 0.62)
        bg.alpha_composite(fv._glow_sprite(col).resize((420, 420)), (sx - 210, sy - 210))
        bg.alpha_composite(st, (sx - st.width // 2, sy - st.height // 2))
        bg.convert("RGB").save(start)
    res["punch"] = start
    for name, png in (("form", form), ("mega", mega)):
        f = out / f"frame_{name}.png"
        if not f.exists():
            bg = fv._poke_background(bg_ref, work).convert("RGBA")
            art, (x, y) = fv._poke_art(str(png), work)
            bg.alpha_composite(art, (x, y))
            bg.convert("RGB").save(f)
        res[name] = f
    return res


def _reversed(src: Path, out: Path) -> Path:
    if not out.exists():  # ffmpeg's reverse filter (moviepy's time_mirror asked for a frame past the end and died)
        import subprocess

        import imageio_ffmpeg

        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-vf", "reverse", "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def wan_clips(entry: dict, out: Path, fr: dict) -> dict:
    fx = pd.ENERGY.get(entry["type"], "glowing energy")
    keep = "keeps exactly the same design and colors, anime style"
    def i2v(start, prompt, out):  # owner 2026-11-10: credits ran low - STONE_RES=480p halves the cost, hd_clip upscales
        return fv.provider_i2v(start, prompt, out, resolution=os.environ.get("STONE_RES", "720p"))

    c = {"punch": i2v(fr["punch"], (
        f"Mewtwo turns toward the glowing {entry['stone']} floating beside it, pulls its fist back and throws a powerful "
        f"punch, its fist smashes the stone, the stone cracks and explodes into a burst of bright {fx}, the energy rushes "
        f"onto Mewtwo and swirls around its body, Mewtwo {keep}, smooth dynamic motion, steady camera"),
        out / "wan_punch.mp4")}
    # owner 2026-11-09: the first reveal (a pillar of fire) was "khas nahi", the Mega one (a sphere of light) "mast" -
    # both are spheres now: the stone's own colour for the first, rainbow for the Mega
    engulf = i2v(fr["form"], (
        f"a blinding sphere of golden-white light and {fx} forms around the creature and swallows it completely until "
        "only a glowing ball of energy fills the frame, anime style, dramatic, steady camera"), out / "wan_form_engulf2.mp4")
    c["form_reveal"] = _reversed(engulf, out / "wan_form_reveal2.mp4")
    c["form_attack"] = i2v(fr["form"], entry.get("attack") or (
        f"the creature raises one hand forward and unleashes a huge stream of {fx} toward the camera, it {keep}, "
        "dynamic powerful motion, steady camera"), out / "wan_form_attack.mp4")
    engulf = i2v(fr["mega"], (
        f"a blinding sphere of rainbow light and {fx} forms around the creature and swallows it completely until only a "
        "glowing ball of energy fills the frame, anime style, dramatic, steady camera"), out / "wan_mega_engulf.mp4")
    c["mega_reveal"] = _reversed(engulf, out / "wan_mega_reveal.mp4")
    c["mega_attack"] = i2v(fr["mega"], entry.get("mega_attack") or (
        f"the armoured creature crouches, gathers a giant ball of {fx} between its hands and hurls it at the camera, a "
        f"massive explosion, it {keep}, epic dynamic motion, steady camera"), out / "wan_mega_attack.mp4")
    return c


def storyboard(entry: dict, fr: dict, wan: dict, form: Path, mega: Path) -> dict:
    t = entry["type"]
    bg = pd.BACKGROUND.get(t, "wiki:Meadow")

    def vid(k, fade=0.0):
        return {"video": str(wan[k]), **({"white_in": fade} if fade else {})}

    clips = [{"still": str(fr["punch"]), "seconds": 3.0, "zoom": [1.0, 1.1]},
             vid("punch"),
             {"evolve": ["mewtwo", str(form)], "background": bg, "seconds": 3.4},
             vid("form_reveal", 0.5),
             vid("form_attack"),
             {"evolve": [str(form), str(mega)], "background": bg, "seconds": 3.4, "mega": True},
             vid("mega_reveal", 0.5),
             vid("mega_attack"),
             {"still": {"poke": str(mega), "background": bg}, "seconds": 6.0, "zoom": [1.0, 1.08]}]
    joke = entry.get("joke") or humor.pick("fusion", f"stone-{entry['key']}")
    narration = [f"Mewtwo just found a {entry['stone']}.",
                 "What happens if Mewtwo punches it?",
                 "Oh? Mewtwo is evolving!",
                 f"It became {entry['form']}!",
                 f"Feel that {t} power!",
                 "Wait, it is evolving again!",
                 f"{entry['mega']}!",
                 joke,
                 "A fan-made what-if, not in the games. Which stone should Mewtwo punch next? Comment below!"]
    return {"title": f"Mewtwo + {entry['stone']}", "backend": "wan", "voice": "en-US-GuyNeural", "style": 2,
            "narration": narration, "clips": clips}


def build(entry: dict) -> Path:
    out = pd.CACHE / f"stone-mewtwo-{entry['key']}"
    out.mkdir(parents=True, exist_ok=True)
    stone, form, mega = art(entry, out)
    fr = frames(entry, out, stone, form, mega)
    wan = wan_clips(entry, out, fr)
    wan = {k: fv.hd_clip(v, v.with_name(v.stem + "_hd.mp4")) for k, v in wan.items()}  # RIFE 30 fps + ESRGAN x2
    board = out / "storyboard.json"
    board.write_text(json.dumps(storyboard(entry, fr, wan, form, mega), indent=2) + "\n", encoding="utf-8")
    return fv.build(board, out)


def post_text(entry: dict) -> tuple[str, list[str]]:
    """(text = title + blank line + description, tags) - the format daily_job.Poster.post takes."""
    title = f"What If Mewtwo Punched a {entry['stone']} | {entry['form']} to {entry['mega']} | Pokemon What If"
    desc = (f"Mewtwo punches a {entry['stone']} and evolves into {entry['form']}, then into {entry['mega']}! "
            "This is a fan-made what-if animation - these forms are NOT in the Pokemon games. "
            "Which stone should Mewtwo punch next? Comment below!")
    tags = ["Mewtwo", entry["form"], entry["mega"], entry["stone"], "Pokemon what if", "Pokemon evolution",
            "Mewtwo evolution", "fan made Pokemon", "Pokemon fan animation", "Pokemon shorts", "Pokemon"]
    return f"{title}\n\n{desc}\n\n#Pokemon #Mewtwo #PokemonEvolution #Shorts", tags


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--post", action="store_true", help="post the built, owner-approved video(s)")
    args = ap.parse_args()
    entries = {e["key"]: e for e in load()}
    for key in args.keys:
        entry = entries[key]
        if not args.post:
            print("VIDEO:", build(entry), flush=True)
            continue
        from scripts import daily_job as dj

        video = pd.CACHE / f"stone-mewtwo-{key}" / "final.mp4"
        assert video.exists(), video
        poster, games = dj.Poster(), dj._load(dj.GAME_MANIFEST)
        games[f"stone-mewtwo-{key}"] = {"kind": "motion", "spec": SPECS.relative_to(pd.ROOT).as_posix(),
                                        "video": str(video), "data": entry}
        dj._save(dj.GAME_MANIFEST, games)
        poster.post(f"custom:stone-mewtwo-{key}", *post_text(entry), str(video))
        print("Done", dj.HEALTH, flush=True)


if __name__ == "__main__":
    main()
