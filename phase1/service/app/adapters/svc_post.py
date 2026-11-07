"""SoulX SVC post-process shared by the YuE2/MuLaCover adapters
(design section 6).

apply_svc_post(dry_wav, voice, out_path, workdir, seed) converts a
generated dry vocal to the target voice's timbre: the target is
part.voice_id's registered voice reference audio. Fixed by design:
pitch_shift 0, auto_shift off, fp16 on cuda. Loud failures, never
silent: voice missing, voice without reference audio, reference
audio longer than 30 s (a 227 s whole-song prompt once turned a
whole round into 50 Hz hum -- design section 3.3), missing
SVC_REPO / SVC_MODEL_PATH. The 30 s cap and the target duration
are recorded in the returned info dict.

Both inputs are resampled to 24 kHz mono first (scipy polyphase),
matching the folk SVC recipe: every SVC run in the experiments
used 24 kHz inputs and produced 24 kHz output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import soundfile as sf

from .base import AdapterError

_DRIVER = Path(__file__).resolve().parent.parent / "svc_driver.py"

MAX_TARGET_S = 30.0
_SVC_SR = 24000


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise AdapterError(f"missing environment variable {name}")
    return v


def _to_24k_mono(src: Path, dst: Path) -> tuple[int, float]:
    """Read any wav, write 24 kHz mono float wav. Returns (src sr,
    duration_s)."""
    data, sr = sf.read(str(src), dtype="float32")
    if data.ndim == 2:
        data = data.mean(axis=1)
    dur = len(data) / sr
    if sr != _SVC_SR:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(_SVC_SR, sr)
        data = resample_poly(data, _SVC_SR // g, sr // g).astype("float32")
    sf.write(str(dst), data, _SVC_SR, subtype="PCM_16")
    return sr, round(dur, 4)


def apply_svc_post(dry_path: Path, voice: dict | None, out_path: Path,
                   workdir: Path, seed: int) -> dict:
    """Run SoulX SVC on dry_path -> out_path. Returns receipt info."""
    if not voice:
        raise AdapterError(
            "SVC post-process needs a voice (options.svc is on) but "
            "no voice was passed")
    audio_path = voice.get("audio_path")
    if not audio_path or not Path(audio_path).is_file():
        raise AdapterError(
            "SVC post-process needs the target voice's reference "
            "audio, but the voice has none")
    try:
        target_info = sf.info(str(audio_path))
        target_dur = round(float(target_info.duration), 3)
    except Exception as e:
        raise AdapterError(
            f"SVC target audio unreadable: {audio_path}: {e}") from e
    if target_dur > MAX_TARGET_S:
        raise AdapterError(
            f"SVC target audio is {target_dur} s, over the 30 s cap "
            "(a long whole-song prompt once turned a whole round "
            "into 50 Hz hum); register a short 2-15 s clip as the "
            "voice instead")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    src_24k = workdir / "svc_source_24k.wav"
    tim_24k = workdir / "svc_timbre_24k.wav"
    src_sr, src_dur = _to_24k_mono(Path(dry_path), src_24k)
    _to_24k_mono(Path(audio_path), tim_24k)
    device = os.environ.get("SVC_DEVICE", "cuda")
    cmd = [sys.executable, str(_DRIVER),
           "--repo", _env("SVC_REPO"),
           "--model-path", os.environ.get(
               "SVC_MODEL_PATH",
               "pretrained_models/SoulX-Singer/model-svc.pt"),
           "--device", device,
           "--source", str(src_24k), "--timbre", str(tim_24k),
           "--pitch-shift", "0",
           "--out", str(out_path),
           "--workdir", str(workdir / "svc_run")]
    cfg = os.environ.get("SVC_CONFIG")
    if cfg:
        cmd += ["--config", cfg]
    if device == "cuda":
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
            "SVC driver failed (exit %s): %s"
            % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
    return {"svc": True,
            "target_voice_id": voice.get("voice_id"),
            "target_duration_s": target_dur,
            "target_sr": int(target_info.samplerate),
            "source_sr": src_sr, "source_duration_s": src_dur,
            "seed": seed, "pitch_shift": 0, "auto_shift": False,
            "duration_s": summary.get("duration_s"),
            "sample_rate": summary.get("sr"),
            "peak": summary.get("peak")}
