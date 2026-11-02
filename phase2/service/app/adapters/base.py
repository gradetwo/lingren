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

    def close(self) -> None:
        """Release job-scoped resources before another model uses the GPU."""

    def synthesize(self, part, voice: dict, seed: int, out_path: Path,
                   workdir: Path) -> tuple[Path, int, dict]:
        raise NotImplementedError
