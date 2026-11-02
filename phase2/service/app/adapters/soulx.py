"""SoulX-Singer adapter (Phase 1).

Runs the SoulX driver as a subprocess so model code (torch, CUDA/MPS)
stays out of the API process. The driver accepts the patched CLI surface:
--seed (patch 02), --auto-shift on/off, and reports the applied
auto_shift amount via model.last_f0_shift (patch 01) when present.

Required environment:
  SOULX_REPO        SoulX-Singer checkout (patched per patches/*.patch)
  SOULX_MODEL_PATH  model.pt
  SOULX_CONFIG      soulxsinger/config/soulxsinger.yaml
  SOULX_PHONESET    soulxsinger/utils/phoneme/phone_set.json
  SOULX_DEVICE      cuda | mps | cpu (default cuda)
  SOULX_FP16        1 | 0 (default 1 on cuda, 0 elsewhere)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..schemas import PartIn
from ..score_converter import soulx_target_meta
from .base import AdapterError, BaseAdapter
from .soulx_session import SoulXSession

_DRIVER = Path(__file__).resolve().parent.parent / "soulx_driver.py"


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise AdapterError(f"missing environment variable {name}")
    return v


class SoulXAdapter(BaseAdapter):
    name = "soulx"

    def __init__(self):
        self._session = None

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def synthesize(self, part: PartIn, voice: dict, seed: int,
                   out_path: Path, workdir: Path) -> tuple[Path, int, dict]:
        prompt_meta = voice.get("prompt_meta_path")
        prompt_wav = voice.get("audio_path")
        if not prompt_meta or not prompt_wav:
            raise AdapterError(
                "voice has no prompt metadata: POST /v1/voices must include "
                "a SoulX prompt-meta JSON (segment with phoneme/duration/"
                "note_pitch) alongside the reference audio. Raw-audio-only "
                "voice registration via the full preprocess pipeline is a "
                "Phase 2 item.")
        target_meta = soulx_target_meta(part.score)
        target_path = workdir / "target_meta.json"
        target_path.write_text(json.dumps([target_meta]))
        opts = part.options or {}
        auto_shift = bool(opts.get("auto_shift", False))
        pitch_shift = int(opts.get("pitch_shift", 0))
        device = _env("SOULX_DEVICE", "cuda")
        fp16 = os.environ.get("SOULX_FP16",
                              "1" if device == "cuda" else "0") == "1"
        started = time.monotonic()
        if os.environ.get('SOULX_REUSE_MODEL', '0') == '1':
            if self._session is None:
                self._session = SoulXSession(
                    {'repo': _env('SOULX_REPO'), 'model_path': _env('SOULX_MODEL_PATH'),
                     'config': _env('SOULX_CONFIG'), 'phoneset': _env('SOULX_PHONESET'),
                     'device': device, 'fp16': fp16}, workdir / 'soulx-worker.log')
            try:
                summary = self._session.synthesize(
                    {'prompt_wav': str(prompt_wav), 'prompt_meta': str(prompt_meta),
                     'target_meta': str(target_path), 'out': str(out_path), 'seed': seed,
                     'pitch_shift': pitch_shift, 'auto_shift': auto_shift})
            except Exception:
                self.close()
                raise
            return out_path, int(summary['sr']), self._info(
                summary, seed, auto_shift, pitch_shift, time.monotonic() - started)
        cmd = [sys.executable, str(_DRIVER),
               "--repo", _env("SOULX_REPO"),
               "--model-path", _env("SOULX_MODEL_PATH"),
               "--config", _env("SOULX_CONFIG"),
               "--phoneset", _env("SOULX_PHONESET"),
               "--prompt-wav", str(prompt_wav),
               "--prompt-meta", str(prompt_meta),
               "--target-meta", str(target_path),
               "--out", str(out_path),
               "--seed", str(seed),
               "--pitch-shift", str(pitch_shift),
               "--device", device]
        cmd.append("--auto-shift" if auto_shift else "--no-auto-shift")
        if fp16:
            cmd.append("--fp16")
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3600)
        summary = None
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    summary = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if proc.returncode != 0 or summary is None:
            raise AdapterError(
                "SoulX driver failed (exit %s): %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
        return out_path, int(summary.get("sr", 24000)), self._info(
            summary, seed, auto_shift, pitch_shift, time.monotonic() - started)

    @staticmethod
    def _info(summary, seed, auto_shift, pitch_shift, elapsed):
        return {"adapter": "soulx", "seed": seed,
                "auto_shift": auto_shift,
                "pitch_shift": pitch_shift,
                "f0_shift_semitones": summary.get("f0_shift_semitones"),
                "duration_s": summary.get("duration_s"),
                "model_reused": summary.get('model_reused', False),
                "timings": {**summary.get('timings', {}),
                            'adapter_wall_s': round(elapsed, 6)}}
