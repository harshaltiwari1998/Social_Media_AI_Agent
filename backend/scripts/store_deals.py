"""Daily "free games + game deals" videos, built from the stores' own live data (no hand-written specs).

Every run checks:
- Epic Games Store (country IN): games free right now (and until when) + next week's free games
  (store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions).
- Steam India (INR): free-to-keep promos (100% off), the Daily Deal, Weekend / Midweek deals, live sale
  events + franchise sales (front-page spotlights), the front-page specials and the top-selling discounted
  games (store search). Every Steam price is re-read from appdetails/price_overview, and game facts (about
  text, genres, developer, release date) from appdetails, reviews from appreviews.

Out of that come:
- short_specs(): one short per game - Epic free now, Steam free, Epic next week, then the best Steam deals.
  The id holds the store, game and offer, so a game is posted once per offer even though the job runs daily.
- long_specs(): two long roundups - "free games + top deals" and "best deals under Rs 500" (falls back to the
  biggest discounts). Skipped when there are too few games.

The store data IS the source (no two-source fact-check needed), but prices change, so every description says
when it was checked. Specs are written to scripts/specs/deals/ (gitignored); daily_job keeps a copy in
game_videos.json because GitHub Actions doesn't commit them.

    python scripts/store_deals.py          # print today's short + long specs (no video, no upload)
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.game_videos import _get_json  # noqa: E402

EPIC_API = "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
STEAM_FEATURED = "https://store.steampowered.com/api/featuredcategories"
STEAM_SEARCH = "https://store.steampowered.com/search/results/"
STEAM_DETAILS = "https://store.steampowered.com/api/appdetails"
SPEC_DIR = Path(__file__).resolve().parent / "specs" / "deals"
IST = timezone(timedelta(hours=5, minutes=30))
_EPIC_IMAGE_TYPES = ("OfferImageWide", "DieselStoreFrontWide", "featuredMedia", "Thumbnail", "OfferImageTall")
# Front-page spotlights that are offers on one game, not a sale event.
_SPOTLIGHT_GAME_TAGS = {"WEEKEND DEAL": "Weekend Deal", "MIDWEEK DEAL": "Midweek Deal", "DAILY DEAL": "Daily Deal"}
MIN_DEAL_PERCENT = 30  # a 10% "deal" isn't worth a video
MAX_CANDIDATES = 40
BUDGET_PAISE = 50000  # "under Rs 500"
LONG_DEALS = 10
MIN_LONG_GAMES = 5


def _clean(text: str) -> str:
    """No '<' / '>' anywhere (YouTube 400s on them)."""
    return text.replace("<", "").replace(">", "").strip()


def _title(text: str) -> str:
    """YouTube titles: no '.' or '?' (cut there), no '<' / '>', max 100 characters."""
    text = re.sub(r"\s+", " ", _clean(text).replace(".", "").replace("?", ""))
    return text[:100].rstrip(" |")


def _short_day(moment: datetime) -> str:
    """'Oct 30' in IST - Epic and Steam give UTC times, the audience is in India."""
    moment = moment.astimezone(IST)
    return f"{moment:%b} {moment.day}"


def _long_day(moment: datetime) -> str:
    """'October 30' - for the narration (TTS reads the full month more naturally)."""
    moment = moment.astimezone(IST)
    return f"{moment:%B} {moment.day}"


def _rs(paise: int) -> str:
    return f"Rs {paise // 100:,}"


def _rupees_spoken(paise: int) -> str:
    return f"{paise // 100:,} rupees"


def _about(text: str, max_words: int = 45) -> str:
    """Store blurb as plain text: HTML stripped, cut at a sentence end near max_words."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    words = text.split()
    if len(words) <= max_words:
        return text
    cut = " ".join(words[:max_words])
    end = max(cut.rfind(". "), cut.rfind("! "))
    return cut[: end + 1] if end > 40 else cut.rstrip(",;:") + "."


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


# --------------------------------------------------------------------------- Epic

def _iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _offers(element: dict, key: str) -> list[dict]:
    return [o for group in (element.get("promotions") or {}).get(key) or [] for o in group.get("promotionalOffers") or []]


def _epic_images(element: dict) -> list[str]:
    images = {i.get("type"): i.get("url") for i in element.get("keyImages") or [] if i.get("url")}
    ordered = [images[t] for t in _EPIC_IMAGE_TYPES if t in images]
    return list(dict.fromkeys(ordered or images.values()))


def _epic_slug(element: dict) -> str | None:
    for mapping in (element.get("catalogNs") or {}).get("mappings") or []:
        if mapping.get("pageSlug"):
            return mapping["pageSlug"]
    for mapping in element.get("offerMappings") or []:
        if mapping.get("pageSlug"):
            return mapping["pageSlug"]
    slug = element.get("productSlug") or ""
    return slug.split("/")[0] or None


def fetch_epic(now: datetime) -> tuple[list[dict], list[dict]]:
    """(free now, free next week). Mystery-game placeholders (no real title/art) are skipped."""
    data = _get_json(EPIC_API, {"locale": "en-US", "country": "IN", "allowCountries": "IN"})
    free_now, free_next = [], []
    for element in data["data"]["Catalog"]["searchStore"]["elements"]:
        name = _clean(element.get("title") or "")
        images = _epic_images(element)
        if not name or not images or "mystery" in name.lower():
            continue
        price = (element.get("price") or {}).get("totalPrice") or {}
        decimals = ((price.get("currencyInfo") or {}).get("decimals") or 2)
        was = int((price.get("originalPrice") or 0) * 100 / 10 ** decimals)  # -> paise
        slug = _epic_slug(element)
        # A free-game promo is a 100%-off offer (Epic stores it as discountPercentage 0 = pay 0%).
        for key, bucket, kind in (("promotionalOffers", free_now, "free"), ("upcomingPromotionalOffers", free_next, "next")):
            for offer in _offers(element, key):
                if (offer.get("discountSetting") or {}).get("discountPercentage") != 0:
                    continue
                start, end = _iso(offer["startDate"]), _iso(offer["endDate"])
                if kind == "free" and not start <= now < end:
                    continue
                bucket.append({
                    "store": "epic", "kind": kind, "name": name, "images": images, "start": start, "end": end,
                    "was": was, "about": _about(element.get("description") or ""),
                    "developer": _clean((element.get("seller") or {}).get("name") or ""),
                    "url": f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games",
                    "key": f"{_slug(name)}-{start.astimezone(IST):%Y%m%d}",
                })
                break
    return free_now, free_next


# --------------------------------------------------------------------------- Steam

def _steam_details(appid: int) -> dict:
    data = _get_json(STEAM_DETAILS, {"appids": appid, "cc": "in", "l": "english"}).get(str(appid)) or {}
    return (data.get("data") or {}) if data.get("success") else {}


def _steam_reviews(appid: int) -> tuple[str, int]:
    try:
        summary = _get_json(
            f"https://store.steampowered.com/appreviews/{appid}",
            {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0},
        ).get("query_summary") or {}
    except (httpx.HTTPError, ValueError):
        return "", 0
    return summary.get("review_score_desc") or "", int(summary.get("total_reviews") or 0)


def _steam_candidates() -> tuple[dict[int, dict], list[str], int]:
    """Discounted apps seen on the front page + top-selling specials: {appid: {"tag", "end"}},
    live sale event names, number of franchise sales."""
    data = _get_json(STEAM_FEATURED, {"cc": "in", "l": "english"})
    found: dict[int, dict] = {}

    def add(appid: int, tag: str | None = None, end: int | None = None) -> None:
        entry = found.setdefault(appid, {"tag": None, "end": None})
        entry["tag"] = entry["tag"] or tag
        entry["end"] = entry["end"] or (datetime.fromtimestamp(end, timezone.utc) if end else None)

    events, franchise_sales = [], 0
    for key, section in data.items():
        if not isinstance(section, dict):
            continue
        if section.get("id") == "cat_dailydeal":
            for item in section.get("items") or []:
                add(item["id"], "Daily Deal", item.get("discount_expiration"))
        elif section.get("id") == "cat_spotlight":
            for spot in section.get("items") or []:
                name, url = _clean(spot.get("name") or "").upper(), spot.get("url") or ""
                app = re.search(r"/app/(\d+)", url)
                if name in _SPOTLIGHT_GAME_TAGS and app:
                    add(int(app.group(1)), _SPOTLIGHT_GAME_TAGS[name])
                elif "FRANCHISE" in name or "PUBLISHER" in name:
                    franchise_sales += 1
                elif name and "/sale/" in url:
                    events.append(name.title())
        elif key in ("specials", "top_sellers", "new_releases"):
            for item in section.get("items") or []:
                if item.get("discounted"):
                    add(item["id"], None, item.get("discount_expiration"))
    try:  # top-selling discounted games - many more than the front page shows
        search = _get_json(STEAM_SEARCH, {"specials": 1, "filter": "topsellers", "json": 1, "count": 50, "cc": "in", "l": "english"})
        for item in search.get("items") or []:
            if app := re.search(r"/apps/(\d+)/", item.get("logo") or ""):
                add(int(app.group(1)))
    except (httpx.HTTPError, ValueError):
        pass
    return dict(list(found.items())[:MAX_CANDIDATES]), list(dict.fromkeys(events)), franchise_sales


def fetch_steam() -> tuple[list[dict], list[dict], list[str], int]:
    """(free to keep, deals best-first, sale event names, number of franchise sales)."""
    candidates, events, franchise_sales = _steam_candidates()
    free, deals = [], []
    for appid, seen in candidates.items():
        details = _steam_details(appid)
        price = details.get("price_overview") or {}
        if not details or price.get("currency") != "INR" or not price.get("discount_percent"):
            continue  # not discounted any more / not sold in India
        percent = int(price["discount_percent"])
        if percent < MIN_DEAL_PERCENT and seen["tag"] is None:
            continue
        reviews, total = _steam_reviews(appid)
        game = {
            "store": "steam", "appid": appid, "name": _clean(details.get("name") or ""),
            "kind": "free" if percent >= 100 or price.get("final") == 0 else "deal",
            "percent": percent, "price": int(price.get("final") or 0), "was": int(price.get("initial") or 0),
            "end": seen["end"], "tag": seen["tag"], "dlc": details.get("type") == "dlc",
            "about": _about(details.get("short_description") or ""),
            "genres": [g["description"] for g in details.get("genres") or []][:3],
            "developer": _clean(", ".join((details.get("developers") or [])[:2])),
            "released": (details.get("release_date") or {}).get("date") or "",
            "reviews": reviews, "total_reviews": total,
            "images": [f"url:{details.get('header_image')}"] if details.get("header_image") else [],
            "shots": len(details.get("screenshots") or []),
            "url": f"https://store.steampowered.com/app/{appid}",
            "key": f"{appid}-{percent}",
        }
        if not game["name"]:
            continue
        game["images"] += [f"steam:{appid}:{n}" for n in range(min(3, game["shots"]))]
        if not game["images"]:
            continue
        (free if game["kind"] == "free" else deals).append(game)
    # Store-picked offers first, then popular games with a big cut.
    deals.sort(key=lambda g: (g["tag"] is None, -(g["percent"] / 100) * math.log10(10 + g["total_reviews"])))
    return free, deals, events, franchise_sales


# --------------------------------------------------------------------------- collect

def collect() -> dict | None:
    """Everything free / discounted right now, or None when neither store answered."""
    now = datetime.now(timezone.utc)
    result = {"checked": now, "epic_now": [], "epic_next": [], "steam_free": [], "steam_deals": [], "events": [], "franchise_sales": 0}
    ok = False
    try:
        result["epic_now"], result["epic_next"] = fetch_epic(now)
        ok = True
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        print(f"Epic store check failed: {exc!r}", file=sys.stderr)
    try:
        result["steam_free"], result["steam_deals"], result["events"], result["franchise_sales"] = fetch_steam()
        ok = True
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        print(f"Steam store check failed: {exc!r}", file=sys.stderr)
    return result if ok else None


def _checked_line(data: dict) -> str:
    checked = data["checked"].astimezone(IST)
    return (f"Checked on the Epic Games Store and Steam India (INR) on {_short_day(checked)} {checked.year}, "
            f"{checked:%H:%M} IST. Prices and dates change - check the store before you buy.")


def _genre_line(game: dict) -> str:
    parts = []
    if game.get("genres"):
        parts.append(f"Steam lists it as {' and '.join(game['genres'])}")
    if game.get("developer"):
        parts.append(f"made by {game['developer']}")
    if game.get("released"):
        parts.append(f"released {game['released']}")
    return (", ".join(parts) + ".") if parts else ""


def _review_line(game: dict) -> str:
    if not game.get("total_reviews") or not game.get("reviews") or "no user" in game["reviews"].lower():
        return ""
    return f"It has {game['reviews']} reviews from {game['total_reviews']:,} players on Steam."


def _offer_line(game: dict) -> str:
    """One spoken sentence on the offer itself."""
    if game["store"] == "epic" and game["kind"] == "free":
        ends = game["end"].astimezone(IST).strftime("%I:%M %p").lstrip("0")
        return f"{game['name']} is free to keep on the Epic Games Store until {_long_day(game['end'])}, {ends} India time."
    if game["store"] == "epic":
        return f"{game['name']} will be free on the Epic Games Store from {_long_day(game['start'])}."
    if game["kind"] == "free":
        return f"{game['name']} is one hundred percent off on Steam right now, so it is free to keep."
    end = f" The deal ends on {_long_day(game['end'])}." if game.get("end") else ""
    lead = f"Today's Steam {game['tag']} is {game['name']}" if game.get("tag") else f"{game['name']} is on sale on Steam"
    return (f"{lead}{', a DLC,' if game.get('dlc') else ''} at {game['percent']} percent off, down from "
            f"{_rupees_spoken(game['was'])} to just {_rupees_spoken(game['price'])}.{end}")


# --------------------------------------------------------------------------- shorts

def _short_id(game: dict) -> str:
    return f"deal-{game['store']}-{game['kind']}-{game['key']}"


def _short_spec(game: dict, data: dict) -> dict:
    images = game["images"]
    pic = lambda n: images[n % len(images)]  # noqa: E731
    name = game["name"]
    if game["store"] == "epic":
        when = _short_day(game["end"] if game["kind"] == "free" else game["start"])
        fact, sub = ("FREE on Epic", f"Claim before {when}") if game["kind"] == "free" else ("Free next week", f"Epic Games | from {when}")
        title = (f"{name} is FREE on Epic Games | Claim Before {when} | Free Game" if game["kind"] == "free"
                 else f"Next Free Game on Epic Games | {name} Free from {when}")
        badge = "FREE GAME" if game["kind"] == "free" else "NEXT FREE GAME"
        price_line = f"Normally it costs {_rupees_spoken(game['was'])}." if game.get("was") else ""
        closing = "Claim it and it stays in your library forever." if game["kind"] == "free" else "Set a reminder so you don't miss it."
        scenes = [
            {"image": pic(0), "label": name},
            {"image": pic(1), "fact": fact, "sub": sub},
            {"image": pic(2), "fact": "Epic Games Store", "sub": f"Normally {_rs(game['was'])}" if game.get("was") else "Free to keep"},
            {"image": pic(0), "fact": "Claim it free" if game["kind"] == "free" else "Coming free", "sub": "Follow for daily free games"},
        ]
        script = [_offer_line(game), price_line, _about(game.get("about") or "", 35), closing]
    else:
        free = game["kind"] == "free"
        fact = "FREE on Steam" if free else f"-{game['percent']}%  {_rs(game['price'])}"
        title = (f"{name} is FREE on Steam | Free to Keep | Claim Now" if free
                 else f"{name} {game['percent']}% OFF on Steam | Now {_rs(game['price'])} | Steam Deal {_short_day(data['checked'])}")
        badge = "FREE GAME" if free else ("STEAM " + game["tag"].upper() if game.get("tag") else "STEAM DEAL")
        review = (game["reviews"], f"{game['total_reviews']:,} Steam reviews") if _review_line(game) else (
            " + ".join(game.get("genres") or []) or "Steam", game.get("developer") or "Steam India price")
        scenes = [
            {"image": pic(0), "label": name},
            {"image": pic(1), "fact": fact, "sub": "Free to keep" if free else f"was {_rs(game['was'])}"},
            {"image": pic(2), "fact": review[0], "sub": review[1]},
            {"image": pic(3), "fact": f"Ends {_short_day(game['end'])}" if game.get("end") else "Grab it now",
             "sub": "Follow for daily game deals"},
        ]
        script = [_offer_line(game), _about(game.get("about") or "", 35), _review_line(game)]
    script.append("Follow for free games and deals every day!")
    tags = [name, f"{name} free" if game["kind"] in ("free", "next") else f"{name} sale", f"{name} price",
            "free games", "epic games free game", "steam sale", "steam deals", "game deals today",
            "free pc games", "steam sale india", "epic games free games this week"]
    return {
        "id": _short_id(game),
        "slot": "deals",
        "badge": badge,
        "headline": "Free game alert" if game["kind"] in ("free", "next") else "Steam deal",
        "title": _title(title),
        "video_script": _clean(" ".join(s for s in script if s)),
        "scenes": scenes,
        "description": _clean(f"{_offer_line(game)}\n{game['url']}\n\n{_checked_line(data)}"),
        "hashtags": ["FreeGames", "EpicGames", "SteamSale", "GameDeals"],
        "tags": tags,
    }


def short_specs(data: dict) -> list[dict]:
    """Every short worth making today, best first: Epic free, Steam free, Epic next week, Steam deals."""
    games = [*data["epic_now"], *data["steam_free"], *data["epic_next"], *data["steam_deals"]]
    return [_short_spec(g, data) for g in games]


# --------------------------------------------------------------------------- long videos

def _section(game: dict) -> dict:
    script = [_offer_line(game)]
    if game["store"] == "epic":
        if game.get("was"):
            script.append(f"Normally it costs {_rupees_spoken(game['was'])} in India.")
        if game.get("about"):
            script.append(f"Here is what it is about. {_about(game['about'], 70)}")
        if game.get("developer"):
            script.append(f"It comes from {game['developer']}.")
        script.append("Once you claim it, it stays in your Epic library forever, even after the offer ends."
                      if game["kind"] == "free" else "Come back next week and claim it while it lasts.")
    else:
        if game.get("about"):
            script.append(_about(game["about"], 70))
        script += [_genre_line(game), _review_line(game)]
        if game["kind"] == "deal":
            saved = (game["was"] - game["price"]) // 100
            script.append(f"That saves you {saved:,} rupees compared to the normal price.")
    return {"heading": _clean(f"{game['name']} - {'FREE' if game['kind'] in ('free', 'next') else str(game['percent']) + '% off'}"),
            "script": _clean(" ".join(s for s in script if s)), "images": game["images"][:3]}


def _long_spec(spec_id: str, title: str, thumb: str, intro: str, games: list[dict], data: dict, extra: list[dict]) -> dict:
    sections = [{"heading": "Today's deals", "script": _clean(intro), "images": [g["images"][0] for g in games[:3]]}]
    sections += extra + [_section(g) for g in games]
    sections.append({
        "heading": "Which one are you grabbing",
        "script": ("That's the list for today. Prices on Steam are the India store prices in rupees, and they can change, "
                   "so always check the store page before you buy. Every link is in the description. "
                   "Tell me in the comments which game you are grabbing, and subscribe, because we check Epic and Steam "
                   "every single day and post the free games and the best deals as soon as they go live."),
        "images": [games[0]["images"][0]],
    })
    lines = []
    for g in games:
        offer = "FREE" if g["kind"] == "free" else ("free from " + _short_day(g["start"]) if g["kind"] == "next"
                                                    else f"-{g['percent']}% {_rs(g['price'])} (was {_rs(g['was'])})")
        lines.append(f"- {g['name']}: {offer} - {g['url']}")
    names = [g["name"] for g in games]
    return {
        "id": spec_id,
        "category": "gaming",
        "title": _title(title),
        "description": _clean("\n".join([f"{_clean(intro)}\n", *lines, "", _checked_line(data)])),
        "hashtags": ["FreeGames", "SteamSale", "EpicGames", "GameDeals"],
        "tags": [*names[:8], "free games", "steam sale", "steam deals today", "epic games free games",
                 "best game deals", "steam sale india", "cheap pc games", "games under 500 rupees"],
        "thumbnail": {"image": games[0]["images"][0], "text": thumb},
        "sections": sections,
    }


def long_specs(data: dict) -> list[dict]:
    """1) free games + the top Steam deals, 2) Steam deals under Rs 500 (else the biggest discounts) - each
    only when it has enough games to fill a long video."""
    day = _short_day(data["checked"])
    date_id = data["checked"].astimezone(IST).strftime("%Y-%m-%d")
    specs = []
    free = [*data["epic_now"], *data["steam_free"], *data["epic_next"]]
    top = data["steam_deals"][:LONG_DEALS]
    extra = []
    if (free or top) and (data["events"] or data["franchise_sales"]):
        live = " and ".join(data["events"])
        script = (f"First, the sales that are live on Steam right now. {live + ' is on. ' if live else ''}"
                  f"{'There are also ' + str(data['franchise_sales']) + ' franchise sales running, ' if data['franchise_sales'] else ''}"
                  "so if you follow a series, check the Steam front page for its bundle prices.")
        extra.append({"heading": "Sales live on Steam", "script": script, "images": [(free or top)[0]["images"][0]]})
    if len(free) + len(top) >= MIN_LONG_GAMES:
        lead = free[0]["name"] if free else top[0]["name"]
        specs.append(_long_spec(
            f"deals-roundup-{date_id}",
            f"Free Games + Best Steam Deals Today | {lead} {'FREE' if free else str(top[0]['percent']) + '% OFF'} | Epic Games + Steam {day}",
            "FREE GAMES + DEALS",
            (f"Here are all the free games and the best game deals for {_long_day(data['checked'])}. "
             f"{'We start with the games you can grab for free on Epic and Steam, then ' if free else 'We go through '}"
             f"the {len(top)} best Steam deals right now, with the India price in rupees for each one, what the game is, "
             "and what players think of it."),
            [*free, *top], data, extra,
        ))
    rest = [g for g in data["steam_deals"] if g not in top]
    budget = [g for g in rest if g["price"] <= BUDGET_PAISE][:12]
    if len(budget) >= MIN_LONG_GAMES:
        specs.append(_long_spec(
            f"deals-budget-{date_id}", f"Best Steam Deals Under Rs 500 Today | {budget[0]['name']} and More | {day}",
            "UNDER RS 500",
            (f"These are the best Steam deals under 500 rupees for {_long_day(data['checked'])}. "
             f"{len(budget)} games, all on sale right now, with the price, what each game is, and its Steam reviews."),
            budget, data, [],
        ))
    else:
        biggest = sorted(rest, key=lambda g: -g["percent"])[:12]
        if len(biggest) >= MIN_LONG_GAMES:
            specs.append(_long_spec(
                f"deals-biggest-{date_id}", f"Biggest Steam Discounts Today | Up to {biggest[0]['percent']}% OFF | {day}",
                f"UP TO {biggest[0]['percent']}% OFF",
                (f"These are the biggest discounts on Steam for {_long_day(data['checked'])}. "
                 f"{len(biggest)} games, from {biggest[0]['percent']} percent off down, with the India price for each."),
                biggest, data, [],
            ))
    return specs


def write_spec(spec: dict, kind: str = "short") -> Path:
    folder = SPEC_DIR / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{spec['id']}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    found = collect()
    if not found:
        sys.exit("Neither store answered")
    print(json.dumps({"shorts": short_specs(found), "long": long_specs(found)}, ensure_ascii=False, indent=2, default=str))
