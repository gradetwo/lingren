#!/usr/bin/env python3
"""SoulX Phase 1 empirical tests (runs on Colab, L4).

Arms (tiny 2.0 s score, same prompt, driver per take):
  A seed0 / B seed0-again / C seed1 / D seed2 (takes gate + reproducibility)
  E seed0 auto_shift ON (f0_shift reporting)
  F seed0 pitch_shift=4 / G seed0 transposed score +4 (Phase 0 re-test:
    is the pitch_shift knob really worse than score transposition?)
Measures: bit-identical, envelope log-RMS corr, pyin F0 median, onset
median diff. Prints one JSON summary as the last line.
"""
import glob
import json
import subprocess
import sys

import numpy as np
import soundfile as sf

REPO = "/content/soulx"
MODEL = glob.glob("/root/.cache/huggingface/hub/models--Soul-AILab--SoulX-Singer"
                  "/snapshots/*/model.pt")[0]
DRIVER = "/content/soulx_driver.py"
PROMPT_WAV = "/content/soulx/example/audio/zh_prompt.mp3"
PROMPT_META = "/content/soulx/example/audio/zh_prompt.json"
OUT = "/content/soulx_test"


def target_meta(pitches, name):
    n = len(pitches)
    meta = {"index": name, "language": "Mandarin", "time": [0, n * 500],
            "duration": " ".join(["0.500"] * n),
            "text": " ".join(["你", "好"] * (n // 2)),
            "phoneme": " ".join(["zh_ni3", "zh_hao3"] * (n // 2)),
            "note_pitch": " ".join(str(p) for p in pitches),
            "note_type": " ".join(["2"] * n),
            "f0": ""}
    p = f"{OUT}/{name}_meta.json"
    with open(p, "w") as f:
        json.dump([meta], f)
    return p


def run(name, meta_path, seed, auto_shift=False, pitch_shift=0):
    out = f"{OUT}/{name}.wav"
    cmd = [sys.executable, DRIVER, "--repo", REPO, "--model-path", MODEL,
           "--config", f"{REPO}/soulxsinger/config/soulxsinger.yaml",
           "--phoneset", f"{REPO}/soulxsinger/utils/phoneme/phone_set.json",
           "--prompt-wav", PROMPT_WAV, "--prompt-meta", PROMPT_META,
           "--target-meta", meta_path, "--out", out, "--seed", str(seed),
           "--pitch-shift", str(pitch_shift), "--device", "cuda", "--fp16"]
    cmd.append("--auto-shift" if auto_shift else "--no-auto-shift")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
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
        print(f"RUN {name} FAILED rc={proc.returncode}")
        print((proc.stderr or proc.stdout)[-3000:])
        raise SystemExit(1)
    print(f"RUN {name} ok: {summary}")
    return out, summary


def f0_median(path):
    import librosa
    y, sr = librosa.load(path, sr=24000, mono=True)
    f0, vf, _ = librosa.pyin(y, fmin=50, fmax=1000, sr=sr,
                             frame_length=2048, hop_length=512)
    vals = f0[~np.isnan(f0)] if vf is not None else f0[~np.isnan(f0)]
    vals = vals[(vals > 50) & (vals < 1000)]
    return float(np.median(vals)) if len(vals) else None


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


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    base = target_meta([64, 67, 64, 67], "base")
    trans = target_meta([68, 71, 68, 71], "trans")
    outs = {}
    outs["A"], sA = run("A", base, 0)
    outs["B"], sB = run("B", base, 0)
    outs["C"], sC = run("C", base, 1)
    outs["D"], sD = run("D", base, 2)
    outs["E"], sE = run("E", base, 0, auto_shift=True)
    outs["F"], sF = run("F", base, 0, pitch_shift=4)
    outs["G"], sG = run("G", trans, 0)
    res = {"summaries": {"A": sA, "B": sB, "C": sC, "D": sD,
                         "E": sE, "F": sF, "G": sG}}
    a, b = sf.read(outs["A"], dtype="float32")[0], sf.read(outs["B"], dtype="float32")[0]
    res["A_B_bit_identical"] = bool(np.array_equal(a, b))
    res["env_corr"] = {"A_C": round(env_corr(outs["A"], outs["C"]), 4),
                       "A_D": round(env_corr(outs["A"], outs["D"]), 4),
                       "A_F": round(env_corr(outs["A"], outs["F"]), 4),
                       "A_G": round(env_corr(outs["A"], outs["G"]), 4),
                       "F_G": round(env_corr(outs["F"], outs["G"]), 4)}
    med = {}
    for k, p in outs.items():
        med[k] = f0_median(p)
    res["f0_median_hz"] = {k: (round(v, 1) if v else None)
                           for k, v in med.items()}
    def cents(x, y):
        return round(1200 * np.log2(y / x), 1) if x and y else None
    res["f0_cents_vs_A"] = {k: cents(med["A"], v) for k, v in med.items()}
    print("SOULX_RESULT " + json.dumps(res))


if __name__ == "__main__":
    main()
