#!/usr/bin/env python3
"""VocalRender long-phrase tests (runs on Colab, L4). Phase 2 long-phrase line.

Runs the long VR score (25.60 s score time, 60 non-SP words, 15 SP spans,
bpm 58) with seed 0, then seed 1 for the takes gate. Reports:
  - output duration vs score time (self-timing drift),
  - energy+F0 joint alignment recall + deviation distribution (rescaled
    mode, same as Phase 1),
  - SP-span handling: candidates inside SP spans, SP-span RMS vs
    syllable RMS,
  - takes gate (seed 0 vs seed 1).
Prints VR_LONG_RESULT JSON last.
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, "/content")
from long_analysis import (check_pair, detect_candidates, align_to_score,
                           dev_stats, load_mono)

CKPT = glob.glob("/root/.cache/huggingface/hub/models--pymaster--VocalRender"
                 "/snapshots/*")[0] + "/VocalRender"
SCRIPT = "/content/vr/scripts/infer_vocalrender_svs_single.py"
OUT = "/content/vr_long"


def run(name, seed):
    out = f"{OUT}/{name}.wav"
    cmd = [sys.executable, SCRIPT, "--ckpt_dir", CKPT,
           "--json_file", "/content/scores/vr_long.json",
           "--item_name", "long_phrase_vr",
           "--prompt_audio", "/content/vr/examples/prompt_audio/2003000081.wav",
           "--output", out, "--seed", str(seed)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        print(f"RUN {name} FAILED rc={proc.returncode}", flush=True)
        print((proc.stderr or proc.stdout)[-3000:], flush=True)
        raise SystemExit(1)
    print(f"RUN {name} ok", flush=True)
    return out


def span_rms(x, sr, onset, dur):
    a = int(onset * sr)
    b = int(min(len(x), (onset + dur) * sr))
    if b <= a:
        return None
    seg = x[a:b].astype(np.float64)
    return float(np.sqrt(np.mean(seg ** 2)))


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = json.load(open("/content/scores/manifest.json"))
    score_s = manifest["vr_score_s"]
    onsets = [r["onset_s"] for r in manifest["vr_onsets"]]
    sp_spans = manifest["vr_sp_spans"]
    out0 = run("vr0", 0)
    x, sr = load_mono(out0)
    dur = len(x) / sr
    res = {"score_s": score_s, "output_s": round(dur, 3),
           "duration_ratio": round(dur / score_s, 4),
           "duration_diff_s": round(dur - score_s, 3)}
    cands = detect_candidates(x, sr)
    aligned = align_to_score(cands, onsets, score_s, mode="rescaled")
    res["alignment_rescaled"] = dev_stats(aligned)
    res["n_candidates"] = int(len(cands))
    meas = [r["measured_onset_s"] for r in aligned
            if r["measured_onset_s"] is not None]
    if meas:
        res["measured_span_s"] = round(meas[-1] - meas[0], 3)
        res["score_span_s"] = round(onsets[-1] - onsets[0], 3)
        res["total_drift_ms"] = round((meas[-1] - onsets[-1]) * 1000.0, 1)
    with open(f"{OUT}/vr0_alignment.json", "w") as f:
        json.dump(aligned, f)
    # SP handling: candidates landing inside SP spans, and SP RMS.
    # SP spans are score-clock; rescale them by the measured/score span
    # ratio so the check is against measured time, not score time.
    scale = ((meas[-1] - meas[0]) / (onsets[-1] - onsets[0])
             if len(meas) >= 2 else 1.0)
    first_c = float(cands[0]) if len(cands) else 0.0
    sp_hits = 0
    sp_rms, syl_rms = [], []
    for sp in sp_spans:
        m_on = first_c + (sp["onset_s"] - onsets[0]) * scale
        m_dur = sp["dur_s"] * scale
        hits = [c for c in cands if m_on <= c < m_on + m_dur]
        sp_hits += len(hits)
        r = span_rms(x, sr, m_on, m_dur)
        if r is not None:
            sp_rms.append(r)
    # Syllable RMS: use aligned measured onsets, 200 ms window each.
    for r in aligned:
        if r["measured_onset_s"] is not None:
            v = span_rms(x, sr, r["measured_onset_s"], 0.2)
            if v is not None:
                syl_rms.append(v)
    res["sp"] = {
        "n_spans": len(sp_spans),
        "candidates_inside_sp_spans": int(sp_hits),
        "median_sp_rms": round(float(np.median(sp_rms)), 5) if sp_rms else None,
        "median_syllable_rms": round(float(np.median(syl_rms)), 5) if syl_rms else None,
        "sp_to_syllable_rms_ratio":
            round(float(np.median(sp_rms) / np.median(syl_rms)), 3)
            if sp_rms and syl_rms and np.median(syl_rms) > 0 else None}
    # Takes gate on the long phrase.
    out1 = run("vr1", 1)
    x1, sr1 = load_mono(out1)
    res["take1_s"] = round(len(x1) / sr1, 3)
    res["takes_gate_vr"] = check_pair(x, sr, x1, sr1)
    with open("/content/vr_long_result.json", "w") as f:
        json.dump(res, f, indent=1)
    print("VR_LONG_RESULT " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
