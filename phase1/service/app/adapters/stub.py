"""Deterministic pseudo-singing adapter for API/gate tests (no GPU).

Renders each note as a sine with vibrato, per-note gain wander and
per-note onset jitter (both seeded), mimicking the real take variation
Phase 0 measured on SoulX (onset jitter ~25-30 ms, F0 micro-variation).
Sample rate 24,000 Hz, like SoulX output. NOT a singing model: it exists
so the job API, gate retries and receipts can be tested without a GPU.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf
from pathlib import Path

from ..schemas import PartIn
from .base import BaseAdapter


def _midi_freq(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


class StubAdapter(BaseAdapter):
    name = "stub"

    def synthesize(self, part: PartIn, voice: dict, seed: int,
                   out_path: Path, workdir: Path) -> tuple[Path, int, dict]:
        sr = 24000
        notes = sorted(part.score.notes, key=lambda n: n.start_s)
        total = max((n.start_s + n.dur_s for n in notes), default=0.0)
        rng = np.random.default_rng(seed)
        x = np.zeros(int(np.ceil(total * sr)) + sr, dtype=np.float64)
        for note in notes:
            if note.lyric in ("", "<SP>") or note.midi <= 0:
                continue
            jitter = float(rng.uniform(-0.020, 0.020))
            gain = float(rng.uniform(0.88, 1.12))
            f0 = _midi_freq(note.midi)
            start = max(0.0, note.start_s + jitter)
            t = np.arange(int(note.dur_s * sr)) / sr
            vib = 1.0 + 0.006 * np.sin(2 * np.pi * 5.5 * t
                                       + rng.uniform(0, 2 * np.pi))
            env = np.minimum(1.0, t / 0.03) * np.exp(-t * 0.15)
            sig = gain * env * np.sin(2 * np.pi * f0 * vib * t)
            s0 = int(start * sr)
            s1 = min(len(x), s0 + len(sig))
            x[s0:s1] += sig[:s1 - s0]
        peak = np.max(np.abs(x)) if len(x) else 0.0
        if peak > 0:
            x = x / peak * 0.7
        sf.write(str(out_path), x.astype(np.float32), sr)
        return out_path, sr, {"adapter": "stub", "seed": seed,
                              "note": "pseudo-singing, not a model"}
