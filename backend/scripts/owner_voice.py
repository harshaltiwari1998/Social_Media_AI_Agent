"""Narration in the owner's own voice (owner 2026-11-23: "AI voice use mat kar uske jagah mera voice use karo ... youtube
repetitive content use nahi karne deta"). Laptop only: Chatterbox (MIT) on the RTX 3050, in its own venv.

English (owner 2026-11-24 picked sample A: "1st voice best he"): Chatterbox VC turns the Indian English edge-tts narration
(en-IN-PrabhatNeural) into the owner's voice. It keeps the source's words, accent and timing, so the edge-tts word timings
for captions and scene cues stay right (the cloned voice from Chatterbox's own English TTS "maxican lag raha he").
    owner_voice.py <in audio> <out.wav> [<in> <out.wav> ...] <reference.wav>

Hindi (owner 2026-11-24: "1st voice for hindi" = Chatterbox multilingual TTS, the voice/test_hindi.wav sample): speaks the
text itself, one sentence at a time, and writes <out.wav>.json = [[sentence, start, end], ...] so the caller can time the
captions (no word timings from this model).
    owner_voice.py --tts hi <text file> <out.wav> <reference.wav>
"""
import json
import os
import re
import sys

os.environ.setdefault("HF_HOME", r"F:\AI AGENT\tools\voice\hf")

import torch  # noqa: E402
import torchaudio as ta  # noqa: E402

GAP = 0.3  # seconds of silence between sentences


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[।.!?])\s+", " ".join(text.split()))
    return [p for p in parts if p.strip()]


def tts(lang: str, text_file: str, out: str, ref: str) -> None:
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    m = ChatterboxMultilingualTTS.from_pretrained(device="cuda")
    pieces, timing, t = [], [], 0.0
    for s in sentences(open(text_file, encoding="utf-8").read()):
        wav = m.generate(s, language_id=lang, audio_prompt_path=ref, exaggeration=0.6, cfg_weight=0.5)
        dur = wav.shape[-1] / m.sr
        timing.append([s, round(t, 3), round(t + dur, 3)])
        pieces += [wav, torch.zeros(1, int(m.sr * GAP))]
        t += dur + GAP
    ta.save(out, torch.cat(pieces, dim=1), m.sr)
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump(timing, f, ensure_ascii=False)
    print("ok", out, len(timing), "sentences", flush=True)


def vc(argv: list[str]) -> None:
    from chatterbox.vc import ChatterboxVC

    *pairs, ref = argv
    if not pairs or len(pairs) % 2:
        raise SystemExit("usage: owner_voice.py <in> <out.wav> [<in> <out.wav> ...] <reference.wav>")
    model = ChatterboxVC.from_pretrained("cuda")
    for src, out in zip(pairs[::2], pairs[1::2]):
        ta.save(out, model.generate(src, target_voice_path=ref), model.sr)
        print("ok", out, flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--tts":
        tts(*a[1:5])
    else:
        vc(a)
