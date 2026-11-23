"""Turn an edge-tts narration into the owner's own voice (owner 2026-11-23: "AI voice use mat kar uske jagah mera voice
use karo ... youtube repetitive content use nahi karne deta"; 2026-11-24 picked sample A: "1st voice best he").

Chatterbox VC (MIT, local RTX 3050) keeps the source's words, accent and timing - so edge-tts word timings for captions
and scene cues stay right - and gives it the timbre of the owner's recording. English narration comes from the Indian
English voice en-IN-PrabhatNeural (the cloned en-US voice "maxican lag raha he").

Runs in its own venv (torch + chatterbox-tts, laptop only):
    F:/AI AGENT/tools/voice/venv/Scripts/python.exe owner_voice.py <in audio> <out.wav> [<in> <out.wav> ...] <reference.wav>
"""
import os
import sys

os.environ.setdefault("HF_HOME", r"F:\AI AGENT\tools\voice\hf")

import torchaudio as ta  # noqa: E402
from chatterbox.vc import ChatterboxVC  # noqa: E402


def main(argv: list[str]) -> None:
    *pairs, ref = argv
    if not pairs or len(pairs) % 2:
        raise SystemExit("usage: owner_voice.py <in> <out.wav> [<in> <out.wav> ...] <reference.wav>")
    vc = ChatterboxVC.from_pretrained("cuda")
    for src, out in zip(pairs[::2], pairs[1::2]):
        ta.save(out, vc.generate(src, target_voice_path=ref), vc.sr)
        print("ok", out, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
