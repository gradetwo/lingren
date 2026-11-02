#!/usr/bin/env python3
"""Model-split harmony evaluation (Wang 2026-11-02 ~09:09,附加评估).

Same short score (你/好 x2, 64/67, 0.5 s each, bpm 120) rendered by
SoulX (already have A.wav base + G.wav transposed) and by VocalRender
(this script: base + transposed entries, seed 0, official prompt audio).

Group A (timbre separation): SoulX-base vs VR-base -- envelope corr,
F0 median diff, duration diff, spectral-centroid diff. Cross-model
differences must EXCEED same-model seed differences (SoulX seeds:
env 0.83-0.85; VR seeds: env 0.34-0.74) to count as "natural separation".
Group B (harmony simulation): SoulX-base (melody, score clock) +
VR-transposed (harmony, measured onsets) -- VR harmony onset deviations
vs score + total-duration drift (the cost of cross-model clock mismatch).

Caveat recorded in the report: prompts differ (SoulX zh_prompt vs VR
official prompt), so timbre differences cannot be attributed to the
model alone.
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
OUT = "/content/split_test"


def vr_entry(pitches, name):
    entry = {"item_name": name, "word": ["你", "好", "你", "好"],
             "pitch": pitches, "note": ["<NOTE_4>"] * 4,
             "pitch2word": [0, 1, 2, 3], "bpm": 120}
    p = f"{OUT}/{name}.json"
    with open(p, "w") as f:
        json.dump([entry], f)
    return p


def run(name, entry_path):
    out = f"{OUT}/{name}.wav"
    cmd = [sys.executable, SCRIPT, "--ckpt_dir", CKPT,
           "--json_file", entry_path, "--item_name", name,
           "--prompt_audio", "/content/vr/examples/prompt_audio/2003000081.wav",
           "--output", out, "--seed", "0"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        print(f"RUN {name} FAILED rc={proc.returncode}")
        print((proc.stderr or proc.stdout)[-3000:])
        raise SystemExit(1)
    print(f"RUN {name} ok")
    return out


def env_of(path):
    y, sr = sf.read(path, dtype="float32")
    if y.ndim == 2:
        y = y[:, 0]
    win, hop = int(sr * 0.02), int(sr * 0.01)
    n = 1 + (len(y) - win) // hop
    fr = np.lib.stride_tricks.sliding_window_view(y, win)[::hop][:n]
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    return np.log(rms + 1e-9)


def env_corr(pa, pb):
    ea, eb = env_of(pa), env_of(pb)
    n = min(len(ea), len(eb))
    return float(np.corrcoef(ea[:n], eb[:n])[0, 1])


def f0_median(path, sr_load):
    import librosa
    y, sr = librosa.load(path, sr=sr_load, mono=True)
    f0, _, _ = librosa.pyin(y, fmin=50, fmax=1000, sr=sr,
                            frame_length=2048, hop_length=512)
    vals = f0[~np.isnan(f0)]
    vals = vals[(vals > 50) & (vals < 1000)]
    return float(np.median(vals)) if len(vals) else None


def centroid_mean(path, sr_load):
    import librosa
    y, sr = librosa.load(path, sr=sr_load, mono=True)
    c = librosa.feature.spectral_centroid(y=y, sr=sr)
    return float(np.mean(c))


def energy_onsets(path):
    y, sr = sf.read(path, dtype="float32")
    if y.ndim == 2:
        y = y[:, 0]
    env = env_of(path)
    jump_db = np.diff(env) * 20.0 / np.log(10.0)
    onsets, last = [], -100
    for i in range(1, len(jump_db)):
        if (jump_db[i] > 8.0
                and jump_db[i] == np.max(jump_db[max(0, i - 2):i + 3])
                and i - last >= 15):
            onsets.append(round(i * 0.01, 3))
            last = i
    return onsets


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    vr_base = run("split_base", vr_entry([64, 67, 64, 67], "split_base"))
    vr_trans = run("split_trans", vr_entry([68, 71, 68, 71], "split_trans"))
    sx_base = "/content/soulx_test/A.wav"
    sx_trans = "/content/soulx_test/G.wav"
    res = {}
    res["durations"] = {}
    for k, p in [("soulx_base", sx_base), ("soulx_trans", sx_trans),
                 ("vr_base", vr_base), ("vr_trans", vr_trans)]:
        d, sr = sf.read(p, dtype="float32")
        res["durations"][k] = {"s": round(len(d) / sr, 3), "sr": sr}
    f0 = {"soulx_base": f0_median(sx_base, 24000),
          "vr_base": f0_median(vr_base, 48000),
          "vr_trans": f0_median(vr_trans, 48000)}
    res["f0_median_hz"] = {k: (round(v, 1) if v else None)
                           for k, v in f0.items()}
    res["groupA_cross_model"] = {
        "env_corr_soulx_vs_vr_base": round(env_corr(sx_base, vr_base), 4),
        "f0_cents_vr_base_vs_soulx_base":
            round(1200 * np.log2(f0["vr_base"] / f0["soulx_base"]), 1),
        "duration_diff_s": round(res["durations"]["vr_base"]["s"]
                                 - res["durations"]["soulx_base"]["s"], 3),
        "centroid_diff_hz": round(centroid_mean(vr_base, 48000)
                                  - centroid_mean(sx_base, 24000), 1),
    }
    res["groupB_vr_harmony_onsets"] = {
        "energy_onsets_s": energy_onsets(vr_trans),
        "score_onsets_s": [0.0, 0.5, 1.0, 1.5],
        "score_total_s": 2.0,
        "vr_trans_total_s": res["durations"]["vr_trans"]["s"],
    }
    print("SPLIT_RESULT " + json.dumps(res))


if __name__ == "__main__":
    main()
