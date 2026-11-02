#!/usr/bin/env python3
"""VocalRender Phase 1 empirical tests (runs on Colab, L4).

Questions:
  1. Does --seed (patch 03) control takes? S0a vs S0b (same seed 0)
     must be bit-identical for reproducibility; S1/S2 (seeds 1/2) must
     differ from S0a and pass the takes gate.
  2. If same-seed runs differ, which RNG escapes seeding (report only)?
Measures: bit-identical, durations, envelope log-RMS corr, pyin F0
median. Prints one JSON summary as the last line.
"""
import glob
import json
import subprocess
import sys

import numpy as np
import soundfile as sf

CKPT = glob.glob("/root/.cache/huggingface/hub/models--pymaster--VocalRender"
                 "/snapshots/*")[0] + "/VocalRender"
SCRIPT = "/content/vr/scripts/infer_vocalrender_svs_single.py"
OUT = "/content/vr_test"


def run(name, seed):
    out = f"{OUT}/{name}.wav"
    cmd = [sys.executable, SCRIPT, "--ckpt_dir", CKPT,
           "--json_file", "/content/vr/examples/opencpop_demo.json",
           "--item_name", "2003000087",
           "--prompt_audio", "/content/vr/examples/prompt_audio/2003000081.wav",
           "--output", out, "--seed", str(seed)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        print(f"RUN {name} FAILED rc={proc.returncode}")
        print((proc.stderr or proc.stdout)[-3000:])
        raise SystemExit(1)
    print(f"RUN {name} ok")
    return out


def env_corr(a_path, b_path):
    def env(p):
        y, sr = sf.read(p, dtype="float32")
        if y.ndim == 2:
            y = y[:, 0]
        win, hop = int(sr * 0.02), int(sr * 0.01)
        n = 1 + (len(y) - win) // hop
        fr = np.lib.stride_tricks.sliding_window_view(y, win)[::hop][:n]
        rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
        return np.log(rms + 1e-9)
    ea, eb = env(a_path), env(b_path)
    n = min(len(ea), len(eb))
    return float(np.corrcoef(ea[:n], eb[:n])[0, 1])


def f0_median(path):
    import librosa
    y, sr = librosa.load(path, sr=48000, mono=True)
    f0, _, _ = librosa.pyin(y, fmin=50, fmax=1000, sr=sr,
                            frame_length=2048, hop_length=512)
    vals = f0[~np.isnan(f0)]
    vals = vals[(vals > 50) & (vals < 1000)]
    return float(np.median(vals)) if len(vals) else None


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    outs = {}
    for name, seed in [("S0a", 0), ("S0b", 0), ("S1", 1), ("S2", 2)]:
        outs[name] = run(name, seed)
    res = {}
    a = sf.read(outs["S0a"], dtype="float32")[0]
    b = sf.read(outs["S0b"], dtype="float32")[0]
    res["S0a_S0b_bit_identical"] = bool(np.array_equal(a, b))
    res["durations"] = {}
    res["f0_median_hz"] = {}
    for k, p in outs.items():
        data, sr = sf.read(p, dtype="float32")
        res["durations"][k] = round(len(data) / sr, 3)
        res["f0_median_hz"][k] = round(f0_median(p), 1)
    res["env_corr"] = {"S0a_S1": round(env_corr(outs["S0a"], outs["S1"]), 4),
                       "S0a_S2": round(env_corr(outs["S0a"], outs["S2"]), 4),
                       "S1_S2": round(env_corr(outs["S1"], outs["S2"]), 4)}
    print("VR_RESULT " + json.dumps(res))


if __name__ == "__main__":
    main()
