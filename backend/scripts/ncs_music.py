"""NCS (NoCopyrightSounds) background music for the Pokemon shorts (owner 2026-11-26: "sab shorts me pokemon ke to
background me NCS music laga dena"). Instrumental tracks only (vocals would fight the narration), listed in
backend/assets/ncs_tracks.json; the mp3s live on the laptop in F:/AI AGENT/music_ncs (NCS_DIR) - CI has none, so a
CI build simply has no music. NCS lets creators use its music in videos when the description credits it in their
format (credit()). The track is picked from the video's key (the line / pair folder name), so the build and the
description always name the same song."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

TRACKS = json.loads((Path(__file__).resolve().parent.parent / "assets" / "ncs_tracks.json").read_text(encoding="utf-8"))
NCS_DIR = Path(os.environ.get("NCS_DIR", r"F:/AI AGENT/music_ncs"))
UNDER_VOICE, ALONE = 0.045, 0.10  # music RMS under the narration / in the gaps


def track_for(key: str) -> dict:
    return TRACKS[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(TRACKS)]


def credit(key: str) -> str:
    t = track_for(key)
    return (f"Music: {t['name']} [NCS Release]\nMusic provided by NoCopyrightSounds\n"
            f"Free Download/Stream: http://ncs.io/{t['slug']}")


def add_bed(mix: np.ndarray, sr: int, speech: list[tuple[float, float]], key: str) -> tuple[np.ndarray, bool]:
    """Mix the key's track under `mix` (float32 stereo): starts where the track gets going, ducks under every narration
    block (speech = [(start s, length s)]), fades in 1 s / out 2 s. Returns (mix, music_used)."""
    t = track_for(key)
    f = NCS_DIR / f"{t['slug']}.mp3"
    if not f.exists():
        return mix, False
    from scripts import lucario_stone_video as sv
    music = sv._decode(f, sr)
    n = len(mix)
    # start where the track is lively: first second whose next 10 s reach 70 % of the loudest 10 s (in the first 2 min)
    win = sr * 10
    rms = [float(np.sqrt(np.mean(music[i:i + win] ** 2))) for i in range(0, min(len(music) - win, sr * 120), sr)]
    start = 0
    if rms:
        top = max(rms)
        start = max(0, next(i for i, r in enumerate(rms) if r >= 0.7 * top) - 2) * sr
    music = music[start:]
    while len(music) < n:
        music = np.concatenate([music, music])
    music = music[:n].copy()
    music /= float(np.sqrt(np.mean(music ** 2))) or 1.0
    env = np.full(n, ALONE, dtype=np.float32)
    for at, length in speech:
        a, b = int(max(0.0, at - 0.25) * sr), int((at + length + 0.25) * sr)
        env[a:b] = UNDER_VOICE
    k = int(0.3 * sr)  # smooth the ducking
    env = np.convolve(env, np.ones(k, dtype=np.float32) / k, mode="same")
    fade_in, fade_out = min(n, sr), min(n, 2 * sr)
    env[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)
    env[n - fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)
    return mix + music * env[:, None], True
