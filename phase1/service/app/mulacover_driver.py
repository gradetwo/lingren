#!/usr/bin/env python3
"""MuLaCover single-take driver for the 伶人 service (four-pipeline work).

Usage: see adapters/mulacover.py. Reads a manifest JSON (written by
the adapter) describing per-segment inputs; generates each segment
with the `mulacover` CLI (same flags as the folk minimal experiment:
stretched melody+chords MIDIs, tags, cfg_scale 2.5, temperature 1.0,
topk 250, one base seed for all segments), separates each complete
mix with Demucs 4.1.0 htdemucs, then splices the dry vocals onto
the score clock. Segments with no notes stay digital silence.
Prints one JSON summary line on stdout as the last line:
  {"out": ..., "sr": ..., "duration_s": ...,
   "segments": [{"bars": [...], "rate": ..., "peak": ...}, ...]}

Splice method is copied from the folk seggen experiment
(mulacover-min-exp/seggen_splice.py): DIO (5 ms, f0 50-800,
StoneMask) first/last voiced frames, crop, librosa phase-vocoder
time-stretch to the exact slot length (pitch preserved), place at
the slot start. The `mulacover` console script must be next to this
interpreter (the MuLaCover checkout installed with
`uv pip install -e '.[audio]'`, per mc_minexp_setup.sh).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _mulacover_bin() -> str:
    cand = Path(sys.executable).parent / "mulacover"
    if cand.is_file():
        return str(cand)
    found = shutil.which("mulacover")
    if found:
        return found
    print("mulacover driver: no `mulacover` console script next to "
          f"{sys.executable} or on PATH (MuLaCover checkout not "
          "installed in this interpreter)", file=sys.stderr)
    raise SystemExit(2)


def _demucs_separate(audio_path: Path, workdir: Path) -> Path:
    out_dir = workdir / "demucs"
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals",
           "-n", "htdemucs", "-o", str(out_dir), str(audio_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=3600)
    if proc.returncode != 0:
        sys.stderr.write((proc.stderr or proc.stdout)[-3000:])
        raise SystemExit(proc.returncode or 1)
    vocals = out_dir / "htdemucs" / audio_path.stem / "vocals.wav"
    if not vocals.is_file():
        print(f"mulacover driver: demucs produced no vocals wav: "
              f"{vocals}", file=sys.stderr)
        raise SystemExit(3)
    return vocals


def _generate_segment(mulacover_bin: str, seg: dict, seg_dir: Path,
                      model_path: str, device: str, seed: int,
                      temperature: float, topk: int,
                      cfg_scale: float) -> Path:
    seg_dir.mkdir(parents=True, exist_ok=True)
    complete = seg_dir / "complete.wav"
    cmd = [mulacover_bin,
           "--model_path", model_path,
           "--melody_midi", seg["melody_midi"],
           "--chord_midi", seg["chord_midi"],
           "--lyrics", seg["lyrics"],
           "--tags", seg["tags"],
           "--save_path", str(complete),
           "--device", device, "--seed", str(seed),
           "--temperature", str(temperature), "--topk", str(topk),
           "--cfg_scale", str(cfg_scale)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=3600)
    if proc.returncode != 0 or not complete.is_file():
        sys.stderr.write((proc.stderr or proc.stdout)[-3000:])
        print(f"mulacover driver: segment {seg['bars']} failed "
              f"(exit {proc.returncode})", file=sys.stderr)
        raise SystemExit(proc.returncode or 3)
    return _demucs_separate(complete, seg_dir)


def _splice(dry_slots: list, span_s: float, out_path: Path) -> list[dict]:
    """DIO-crop + phase-vocoder stretch each dry vocal onto its slot
    (copied from seggen_splice.py). Returns per-segment reports."""
    import librosa
    import numpy as np
    import pyworld as pw
    import soundfile as sf

    sr = None
    report = []
    canvas = None
    for item in dry_slots:
        d, sr_in = sf.read(str(item["dry"]))
        if sr is None:
            sr = int(sr_in)
            canvas = np.zeros((int(round(span_s * sr)), 2),
                              dtype=np.float64)
        if int(sr_in) != sr:
            d = np.stack(
                [librosa.resample(d[:, ch].astype(np.float32),
                                  orig_sr=int(sr_in), target_sr=sr)
                 for ch in range(d.shape[1])], axis=1) \
                if d.ndim == 2 else librosa.resample(
                    d.astype(np.float32), orig_sr=int(sr_in),
                    target_sr=sr)
        if d.ndim == 1:
            d = np.stack([d, d], axis=1)
        mono = np.ascontiguousarray(d.mean(axis=1), dtype=np.float64)
        f0, t = pw.dio(mono, sr, frame_period=5.0, f0_floor=50,
                       f0_ceil=800)
        f0 = pw.stonemask(mono, f0, t, sr)
        vidx = np.where(f0 > 0)[0]
        if len(vidx) == 0:
            print(f"mulacover driver: segment {item['bars']} dry "
                  "vocal has no voiced frames", file=sys.stderr)
            raise SystemExit(3)
        v0, v1 = float(t[vidx[0]]), float(t[vidx[-1]])
        s0, s1 = item["slot"]
        slot_len = s1 - s0
        rate = (v1 - v0) / slot_len
        i0, i1 = int(v0 * sr), int(v1 * sr)
        cropped = d[i0:i1]
        stretched = np.stack(
            [librosa.effects.time_stretch(
                cropped[:, ch].astype(np.float32), rate=rate)
             for ch in range(2)], axis=1).astype(np.float64)
        n_slot = int(round(slot_len * sr))
        if len(stretched) < n_slot:
            stretched = np.pad(stretched,
                               ((0, n_slot - len(stretched)), (0, 0)))
        else:
            stretched = stretched[:n_slot]
        o0 = int(round(s0 * sr))
        canvas[o0:o0 + n_slot] += stretched
        report.append({"bars": item["bars"],
                       "dry_dur_s": round(len(d) / sr, 2),
                       "voiced_span_s": [round(v0, 2), round(v1, 2)],
                       "slot_s": [s0, s1], "rate": round(rate, 3),
                       "peak": round(float(np.max(np.abs(stretched)))
                                     if len(stretched) else 0.0, 4)})
    if canvas is None:  # no sung segments at all
        raise SystemExit("mulacover driver: no segments to splice")
    peak_all = float(np.max(np.abs(canvas))) if canvas.size else 0.0
    sf.write(str(out_path), canvas, sr)
    return report, sr, peak_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="MuLaCover checkout (uv pip install -e "
                         "'.[audio]' done)")
    ap.add_argument("--model-path", required=True,
                    help="weights dir (MuLaCover + Qwen3-Embedding + "
                         "HeartCodec-oss)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--topk", type=int, default=250)
    ap.add_argument("--cfg-scale", type=float, default=2.5)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    repo = Path(args.repo)
    if (repo / "mulacover").is_dir():
        sys.path.insert(0, str(repo))
    manifest = json.loads(Path(args.manifest).read_text())
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    mc_bin = _mulacover_bin()
    dry_slots = []
    for i, seg in enumerate(manifest["segments"]):
        if seg["notes"] == 0:
            continue  # vocal-free slot stays digital silence
        seg_dir = workdir / f"seg{i}"
        dry = _generate_segment(
            mc_bin, seg, seg_dir, args.model_path, args.device,
            args.seed, args.temperature, args.topk, args.cfg_scale)
        dry_slots.append({"dry": dry, "slot": seg["slot"],
                          "bars": seg["bars"]})
    report, sr, peak = _splice(dry_slots, float(manifest["span_s"]),
                               Path(args.out))
    print(json.dumps({"out": args.out, "sr": sr,
                      "duration_s": round(float(manifest["span_s"]), 4),
                      "peak": round(peak, 4), "segments": report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
