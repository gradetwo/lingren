"""Adapter interface (design section 3.2).

An adapter turns one part + one voice into one take's wav file.
It returns (wav_path, sample_rate, info) where info is receipt material
(seeds actually used, pitch-shift applied, timings, warnings).
Adapters never decide the takes gate; the worker does that.
"""

from __future__ import annotations

from pathlib import Path


class AdapterError(Exception):
    pass


class BaseAdapter:
    name = "base"

    # Most adapters need a registered voice (SoulX: prompt meta +
    # reference audio; VocalRender: reference audio). Adapters whose
    # generation needs no voice override this with a method
    # needs_voice(part) -> bool (YuE2/MuLaCover: only when the SVC
    # post-process switch is on). jobs._run_part calls it when
    # callable, reads it as a bool otherwise.
    needs_voice = True

    def synthesize(self, part, voice: dict, seed: int, out_path: Path,
                   workdir: Path) -> tuple[Path, int, dict]:
        raise NotImplementedError
