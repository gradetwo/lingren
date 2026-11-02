#!/usr/bin/env python3
"""Build the synthetic control corpus for the prompt pipeline tests.

Writes 16-bit mono wav files next to this script (tests/corpus/).
Real-singing cases come from phase0/phase1 evidence and are NOT copied
here; the test runner references them in place.
"""
import wave
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "corpus"
OUT.mkdir(parents=True, exist_ok=True)
SR = 48000


def write(name, x):
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(name, len(x) / SR)


def tone(freq, dur, amp=0.5):
    t = np.arange(int(SR * dur)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


# too short: 1.0 s pure tone -> reject (too short after trim)
write("synth_short_1s.wav", tone(440, 1.0))
# pure tone in range: 4.0 s -> reject (pure-tone-like, not singing)
write("synth_tone_4s.wav", tone(440, 4.0))
# white noise: 4.0 s -> reject (mostly unpitched)
rng = np.random.default_rng(7)
write("synth_noise_4s.wav", rng.normal(0, 0.3, int(SR * 4.0)))
# severe clipping: sine driven to 1.5 then hard-clipped -> reject
write("synth_clipped_4s.wav", np.clip(tone(440, 4.0, amp=1.5), -1.0, 1.0))
# short bursts (100 ms tone, pitch hops, 50 ms gaps): speech-like warn
parts = []
for i, f in enumerate([220, 330, 262, 392, 294, 370] * 4):
    parts.append(tone(f, 0.10, amp=0.5))
    parts.append(np.zeros(int(SR * 0.05)))
write("synth_bursts_4s.wav", np.concatenate(parts)[: int(SR * 4.0)])
# quiet singing-like level: tone with slow vibrato at -30 dBFS peak
t = np.arange(int(SR * 4.0)) / SR
vib = 440 * 2 ** (0.06 * np.sin(2 * np.pi * 5 * t))
phase = 2 * np.pi * np.cumsum(vib) / SR
write("synth_quiet_vibrato_4s.wav", 0.03 * np.sin(phase))
