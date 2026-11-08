"""One light, on-topic joke per video (owner request 2026-11-08: "har video me humour, but no guideline crossed").

Rules for every line here and for the writers' "joke" field in specs:
- about the video's own topic (the game, the Pals, the deal, the movie-going experience) - never about a real person,
  group, body, religion, politics or anything adult; no insults, no fear, no money/health advice
- a joke is never a fact claim, so it cannot break the two-source fact check
- no humour in tributes (deaths) or serious news

A spec can carry its own `"joke"` (preferred - the writer routines add one that fits the video); `"joke": ""`
turns humour off for that spec. Otherwise a line is picked from the slot's pool, the same one every time for an id.
"""
from __future__ import annotations

import re
import zlib

POOLS: dict[str, list[str]] = {
    "fusion": [
        "Somewhere, a Pokedex just crashed trying to register this one.",
        "Honestly, the hardest part of this fusion was deciding whose name goes first.",
        "Nurse Joy is going to need a much bigger healing machine.",
        "Team Rocket saw this and quietly went home.",
        "Two trainers, one Poke Ball, and a very long argument about who keeps it.",
        "It still can't decide which parent's attack to use first, so it uses both.",
    ],
    "evolution": [
        "And somewhere, a trainer is pressing B right now. Don't be that trainer.",
        "Same Pokemon, new look, way more attitude.",
        "Its old Poke Ball suddenly feels a little too small. Awkward.",
        "Evolution: the only glow-up that comes with a free stat boost.",
        "It still remembers that one time you ran away from a wild Rattata.",
    ],
    "breeding": [
        "Warning: the new Pal may refuse to work night shifts.",
        "Your Breeding Farm is basically the busiest place in all of Palpagos.",
        "Spoiler: it hatches, it looks cute, and then it goes straight to work at your base.",
        "Bake the cake, wait for the egg, and try not to check the farm every ten seconds.",
        "Just remember, the baby will expect to be paid in cake.",
    ],
    "palworld": [
        "And no, your Pals still don't get weekends off.",
        "Your base Pals called. They would like a word about working hours.",
        "Feed them well, or they will stare at you like you forgot their lunch.",
        "Somewhere in Palpagos, a Pal is pretending to work right now.",
        "Fair warning: once it joins your base, it will never let you forget the mining quota.",
    ],
    "aniimo": [
        "Will it carry your team? Maybe. Will it look cute doing it? Definitely.",
        "Collect them all, they said. Your storage box has other plans.",
        "Fair warning: once you catch one, you will want five more.",
        "Its battle face is serious. Its idle animation is not.",
    ],
    "games": [
        "Fair warning: just one more match turns into three in the morning very fast.",
        "Your backlog called. It said please, not another one.",
        "Pro tip: save often. Your future self will thank you.",
        "And yes, taking a snack break mid boss fight is a valid strategy.",
        "Rage quitting does not count as a speedrun, sadly.",
    ],
    "deals": [
        "Your wallet will thank you. Your backlog, not so much.",
        "Free games: the only time adding to cart costs nothing.",
        "Buying games on sale counts as saving money. We checked. With ourselves.",
        "Your backlog just got longer again. No regrets.",
    ],
    "dml": [
        "Dragon breeding: part science, part luck, and a lot of hoping.",
        "No combo is guaranteed, but your dragons will still act like it was their idea.",
    ],
    "movies_hi": [
        "और हाँ, इंटरवल से पहले पॉपकॉर्न खत्म मत कर देना!",
        "फिल्म के वक्त फोन साइलेंट रखना, वरना पूरा हॉल आपको ही विलेन समझेगा!",
        "बस एक बात, स्पॉइलर देने वाले दोस्त से आज दूर ही रहना!",
        "फिल्म चाहे जैसी हो, समोसा तो हिट ही रहेगा!",
    ],
    "rashifal_hi": [
        "और हाँ, आज चाय ठंडी होने से पहले पी लीजिए, मूड अपने आप अच्छा रहेगा!",
        "बाकी सितारे संभाल लेंगे, आप बस मोबाइल की बैटरी संभाल लीजिए!",
        "दिन थोड़ा धीमा लगे तो एक मुस्कान लगाइए, सितारे भी साथ मुस्कुराएंगे!",
        "आज रिमोट पर बहस न करें, सितारे भी आराम के मूड में हैं!",
    ],
}

# spec slot -> pool (None = no humour: tributes, serious news)
SLOT_POOL = {
    "new": "games", "classic": "games", "gamenews": "games", "gow": "games",
    "aniimo": "aniimo", "palguide": "palworld", "palbase": "palworld", "palchain": "palworld",
    "dml": "dml", "deals": "deals",
    "movie_new": "movies_hi", "movie_review": "movies_hi", "reel": "movies_hi", "comedy": "movies_hi",
    "movie_news": None, "tribute": None,
}


def pick(pool: str, key: str) -> str:
    lines = POOLS[pool]
    return lines[zlib.crc32(key.encode("utf-8")) % len(lines)]


def for_spec(spec: dict, pool: str | None = None) -> str:
    """The joke for a spec: its own "joke", else one from the slot's pool ('' when the slot gets none)."""
    if "joke" in spec:
        return (spec["joke"] or "").strip()
    pool = pool or SLOT_POOL.get(spec.get("slot", ""))
    return pick(pool, spec.get("id") or spec.get("title") or "") if pool else ""


_SENT = re.compile(r"(?<=[.!?।])\s+")


def insert(script: str, joke: str) -> str:
    """Put the joke just before the closing call to action (the last question / "comment" / "follow" sentence),
    or at the end when there is none. Already present -> unchanged."""
    if not joke or joke in script:
        return script
    parts = _SENT.split(script.strip())
    cta = re.compile(r"\?|comment|follow|कमेंट|फॉलो|बताइए|बताएं", re.I)
    at = len(parts)
    while at > 0 and cta.search(parts[at - 1]):
        at -= 1
    if at == 0:  # the whole script is a question - just append
        at = len(parts)
    return " ".join(parts[:at] + [joke] + parts[at:])
