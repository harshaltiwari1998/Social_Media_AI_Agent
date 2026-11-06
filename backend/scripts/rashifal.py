"""Daily Hindi rashifal shorts - one per rashi (12) for a given date.

The sky part is real: the Moon's sidereal rashi and nakshatra (Lahiri ayanamsa) and the tithi are computed with
skyfield + JPL DE421 (cached in backend/.cache/skyfield). The reading for each rashi follows the traditional Chandra
gochar - which house (bhaav) the Moon transits counted from that rashi - so the 12 shorts differ for a reason and
change every ~2.5 days with the Moon. Lucky colour / number come from the rashi's ruling planet (tradition).
Every short says it is for entertainment, avoids fear, health and money advice, and carries no religious messaging.

    python scripts/rashifal.py --date 2026-11-06 [--festival "धनतेरस"]   # renders 12 shorts into MEDIA_OUTPUT_DIR/videos
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings  # noqa: E402
from app.media.text_render import get_font, render_block, wrap  # noqa: E402
from app.media.video_gen import generate_video  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
CARD = 1080
GOLD, WHITE = (255, 210, 90), (255, 255, 255)

# name, glyph, ruling planet, lucky colour, lucky number, theme colour
RASHIS = [
    ("मेष", "♈", "मंगल", "लाल", 9, (200, 50, 40)),
    ("वृषभ", "♉", "शुक्र", "सफेद", 6, (60, 150, 90)),
    ("मिथुन", "♊", "बुध", "हरा", 5, (230, 170, 40)),
    ("कर्क", "♋", "चंद्र", "सफेद", 2, (90, 140, 210)),
    ("सिंह", "♌", "सूर्य", "सुनहरा", 1, (230, 120, 30)),
    ("कन्या", "♍", "बुध", "हरा", 5, (80, 160, 110)),
    ("तुला", "♎", "शुक्र", "आसमानी", 6, (190, 110, 200)),
    ("वृश्चिक", "♏", "मंगल", "लाल", 9, (170, 40, 70)),
    ("धनु", "♐", "गुरु", "पीला", 3, (220, 160, 40)),
    ("मकर", "♑", "शनि", "नीला", 8, (60, 80, 160)),
    ("कुंभ", "♒", "शनि", "नीला", 8, (40, 130, 190)),
    ("मीन", "♓", "गुरु", "पीला", 3, (60, 170, 180)),
]
NAKSHATRAS = ["अश्विनी", "भरणी", "कृत्तिका", "रोहिणी", "मृगशिरा", "आर्द्रा", "पुनर्वसु", "पुष्य", "आश्लेषा", "मघा",
              "पूर्वा फाल्गुनी", "उत्तरा फाल्गुनी", "हस्त", "चित्रा", "स्वाति", "विशाखा", "अनुराधा", "ज्येष्ठा", "मूल",
              "पूर्वाषाढ़ा", "उत्तराषाढ़ा", "श्रवण", "धनिष्ठा", "शतभिषा", "पूर्वा भाद्रपद", "उत्तरा भाद्रपद", "रेवती"]
TITHIS = ["प्रतिपदा", "द्वितीया", "तृतीया", "चतुर्थी", "पंचमी", "षष्ठी", "सप्तमी", "अष्टमी", "नवमी", "दशमी",
          "एकादशी", "द्वादशी", "त्रयोदशी", "चतुर्दशी"]
MONTHS = ["जनवरी", "फरवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
ORD = ["पहले", "दूसरे", "तीसरे", "चौथे", "पांचवें", "छठे", "सातवें", "आठवें", "नौवें", "दसवें", "ग्यारहवें", "बारहवें"]

# Traditional Chandra gochar by house from the rashi: (headline, sub, reading, tip). Calm wording, no fear, no
# health or money advice.
GOCHAR = {
    1: ("मन रहेगा प्रसन्न", "आत्मविश्वास बढ़ेगा", "आज मन प्रसन्न रहेगा और आत्मविश्वास बढ़ा हुआ महसूस होगा। नए काम की शुरुआत के लिए दिन अच्छा माना जाता है।", "दिन की शुरुआत किसी अच्छे काम से करें"),
    2: ("वाणी पर ध्यान", "परिवार का साथ", "परिवार के साथ समय बीतेगा। बातचीत में मीठी वाणी रखें, इससे बिगड़ते काम भी बन सकते हैं।", "सोच-समझकर बोलें"),
    3: ("साहस और उत्साह", "भाई-बहनों का सहयोग", "आज साहस और उत्साह भरपूर रहेगा। भाई-बहनों और दोस्तों का सहयोग मिलेगा, छोटी यात्रा सुखद रह सकती है।", "किसी पुराने दोस्त से बात करें"),
    4: ("मन थोड़ा बेचैन", "घर पर ध्यान दें", "मन थोड़ा बेचैन रह सकता है। घर-परिवार के कामों पर ध्यान दें और जल्दबाज़ी में कोई बड़ा फैसला न लें।", "थोड़ा समय शांति से बिताएं"),
    5: ("बुद्धि से काम लें", "पढ़ाई और रचनात्मक काम", "पढ़ाई और रचनात्मक कामों में मन लगेगा। भावनाओं में बहकर नहीं, बुद्धि से फैसले लें।", "कुछ नया सीखने की कोशिश करें"),
    6: ("विरोधियों पर भारी", "रुके काम बनेंगे", "आज आप विरोधियों पर भारी रहेंगे। मेहनत का फल मिलेगा और रुके हुए काम आगे बढ़ सकते हैं।", "अपने काम पर पूरा ध्यान रखें"),
    7: ("साझेदारी में शुभ", "जीवनसाथी का साथ", "जीवनसाथी और साझेदारों का साथ मिलेगा। मिलकर किए गए काम में सफलता के योग हैं।", "अपनों के साथ समय बिताएं"),
    8: ("सावधानी रखें", "विवाद से बचें", "आज थोड़ी सावधानी रखने का दिन है। बहस और विवाद से दूर रहें, धैर्य से हर काम पूरा होगा।", "धैर्य बनाए रखें"),
    9: ("भाग्य का साथ", "बड़ों का आशीर्वाद", "बड़ों और गुरुजनों की सलाह काम आएगी। अच्छे कामों में मन लगेगा और भाग्य का साथ मिल सकता है।", "बड़ों से आशीर्वाद लें"),
    10: ("कार्यक्षेत्र में सफलता", "मान-सम्मान बढ़ेगा", "कार्यक्षेत्र में आपके काम की सराहना हो सकती है। मान-सम्मान बढ़ने के योग हैं।", "अपनी जिम्मेदारी समय पर पूरी करें"),
    11: ("इच्छाएं होंगी पूरी", "दोस्तों से लाभ", "आज इच्छाएं पूरी होने का दिन माना जाता है। दोस्तों और बड़े भाई-बहनों से लाभ मिल सकता है।", "दोस्तों का धन्यवाद करें"),
    12: ("खर्च पर नज़र", "आराम ज़रूरी", "आज खर्च थोड़ा ज़्यादा रह सकता है, इसलिए बजट पर नज़र रखें। थकान महसूस हो तो आराम ज़रूर करें।", "समय पर सोएं"),
}


def sky(day: date) -> dict:
    """Moon rashi/nakshatra and tithi at 6:00 IST (around sunrise) - skyfield + DE421, Lahiri ayanamsa."""
    from skyfield.api import Loader
    from skyfield.framelib import ecliptic_frame

    load = Loader(str(ROOT / ".cache" / "skyfield"))
    ts, eph = load.timescale(), load("de421.bsp")
    t = ts.from_datetime(datetime(day.year, day.month, day.day, 6, 0, tzinfo=IST))
    ayanamsa = 23.853 + (day.year + day.timetuple().tm_yday / 365.25 - 2000) * 50.29 / 3600

    def lon(body: str) -> float:
        return (eph["earth"].at(t).observe(eph[body]).apparent().frame_latlon(ecliptic_frame)[1].degrees - ayanamsa) % 360

    moon, sun = lon("moon"), lon("sun")
    tithi = int(((moon - sun) % 360) // 12)  # 0..29
    paksha = "शुक्ल" if tithi < 15 else "कृष्ण"
    name = "पूर्णिमा" if tithi == 14 else "अमावस्या" if tithi == 29 else TITHIS[tithi % 15]
    return {"moon_rashi": int(moon // 30), "nakshatra": NAKSHATRAS[int(moon // (360 / 27))], "tithi": f"{paksha} {name}"}


def _font(size: int, glyph: bool = False):
    if glyph:
        for path in ("C:/Windows/Fonts/seguisym.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if Path(path).exists():
                return ImageFont.truetype(path, size)
    return get_font("bold", size)


def _stars(theme: tuple, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (CARD, CARD))
    px = img.load()
    for y in range(CARD):
        k = y / CARD
        row = tuple(int(8 + c * 0.35 * (1 - k)) for c in theme)
        for x in range(CARD):
            px[x, y] = row
    d = ImageDraw.Draw(img)
    for _ in range(260):
        x, y, r = rnd.randrange(CARD), rnd.randrange(CARD), rnd.choice((1, 1, 1, 2, 2, 3))
        a = rnd.randrange(120, 255)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(a, a, min(255, a + 20)))
    glow = Image.new("RGB", (CARD, CARD))
    ImageDraw.Draw(glow).ellipse((240, 160, 840, 760), fill=tuple(int(c * 0.6) for c in theme))
    return Image.blend(img, glow.filter(ImageFilter.GaussianBlur(120)), 0.45)


def _text(card: Image.Image, text: str, size: int, y: int, color=WHITE, lines: int = 2) -> int:
    font = _font(size)
    block = render_block(font, wrap(font, text, max_width=900, max_lines=lines), color=color, stroke=6, shadow=8)
    card.paste(block, ((CARD - block.width) // 2, y), block)
    return y + block.height


def cards(r: int, day: date, sk: dict, festival: str | None, out: Path) -> list[str]:
    name, glyph, lord, colour, num, theme = RASHIS[r]
    house = (sk["moon_rashi"] - r) % 12 + 1
    head, sub, _, tip = GOCHAR[house]
    when = f"{day.day} {MONTHS[day.month - 1]} {day.year}" + (f" | {festival}" if festival else "")
    out.mkdir(parents=True, exist_ok=True)
    paths = []

    def save(k: int, card: Image.Image) -> None:
        p = out / f"{r:02d}_{k}.png"
        card.save(p)
        paths.append(str(p.resolve()))

    c = _stars(theme, r * 31 + 1)
    g = _font(330, glyph=True)
    d = ImageDraw.Draw(c)
    w = d.textlength(glyph, font=g)
    d.text(((CARD - w) / 2, 150), glyph, font=g, fill=GOLD, stroke_width=4, stroke_fill=(60, 40, 0))
    y = _text(c, f"{name} राशिफल", 110, 610, GOLD)
    _text(c, when, 58, y + 20)
    save(1, c)

    c = _stars(theme, r * 31 + 2)
    y = _text(c, "आज चंद्रमा", 70, 230)
    y = _text(c, f"{RASHIS[sk['moon_rashi']][0]} राशि में", 110, y + 10, GOLD)
    y = _text(c, f"{sk['nakshatra']} नक्षत्र | {sk['tithi']}", 54, y + 20)
    _text(c, f"{name} से {ORD[house - 1]} भाव में", 66, y + 60)
    save(2, c)

    c = _stars(theme, r * 31 + 3)
    y = _text(c, head, 120, 300, GOLD)
    _text(c, sub, 70, y + 40)
    save(3, c)

    c = _stars(theme, r * 31 + 4)
    y = _text(c, f"शुभ रंग: {colour}", 96, 260, GOLD)
    y = _text(c, f"शुभ अंक: {num}", 96, y + 30, GOLD)
    y = _text(c, f"स्वामी ग्रह: {lord}", 64, y + 40)
    _text(c, f"सलाह: {tip}", 60, y + 60)
    save(4, c)
    return paths


def script(r: int, day: date, sk: dict, festival: str | None) -> str:
    name, _, lord, colour, num, _ = RASHIS[r]
    house = (sk["moon_rashi"] - r) % 12 + 1
    _, _, reading, tip = GOCHAR[house]
    hello = f"{festival} की शुभकामनाएं! " if festival else ""
    return (f"{hello}{name} राशि वालों, आज का राशिफल। आज चंद्रमा {RASHIS[sk['moon_rashi']][0]} राशि में है, जो आपकी "
            f"राशि से {ORD[house - 1]} भाव में है। {reading} आपका शुभ रंग {colour} और शुभ अंक {num} है। "
            f"सलाह: {tip}। यह राशिफल चंद्रमा की चाल और ज्योतिष परंपरा पर आधारित है, सिर्फ मनोरंजन के लिए। "
            f"अपनी राशि कमेंट में लिखें!")


def post_text(r: int, day: date, festival: str | None) -> tuple[str, list[str]]:
    name = RASHIS[r][0]
    when = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    title = f"{name} राशिफल {when} | Aaj Ka Rashifal" + (f" | {festival}" if festival else "")
    desc = (f"{name} राशि का {when} का राशिफल - चंद्रमा की गोचर स्थिति के अनुसार। शुभ रंग, शुभ अंक और आज की सलाह।\n"
            "यह राशिफल ज्योतिष परंपरा पर आधारित है और सिर्फ मनोरंजन के लिए है।\n\n#Rashifal #AajKaRashifal #Horoscope #Shorts")
    tags = [f"{name} राशिफल", "आज का राशिफल", "Aaj Ka Rashifal", "Rashifal", "Horoscope", "Daily horoscope",
            "Hindi rashifal", f"{name} rashi"]
    return f"{title}\n\n{desc}", tags


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True)
    ap.add_argument("--festival")
    args = ap.parse_args()
    day = date.fromisoformat(args.date)
    sk = sky(day)
    print("sky:", json.dumps(sk, ensure_ascii=False))
    settings.tts_voice = "hi-IN-SwaraNeural"
    out = Path(settings.media_output_dir) / "rashifal" / day.isoformat()
    made = []
    for r in range(12):
        name = RASHIS[r][0]
        res = generate_video(cards(r, day, sk, args.festival, out), name, script(r, day, sk, args.festival),
                             headline=f"{name} राशिफल", badge="आज का राशिफल")
        path = res.get("data", {}).get("path") if res.get("success") else None
        print(name, path or res.get("error"))
        made.append({"rashi": r, "video": path, "text": post_text(r, day, args.festival)})
    (out / "made.json").write_text(json.dumps(made, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
