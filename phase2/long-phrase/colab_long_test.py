#!/usr/bin/env python3
"""SoulX long-phrase tests (runs on Colab, L4). Phase 2 long-phrase line.

Arms (same prompt zh_prompt, same seed 0, auto_shift OFF unless noted):
  base  : long score (23.31 s, 55 sung syllables, MIDI 50-69)
  ps_p4 : base score, pitch_shift=+4
  tr_p4 : score transposed +4, pitch_shift=0
  ps_m3 : base score, pitch_shift=-3
  tr_m3 : score transposed -3, pitch_shift=0
  take1 : base score, seed 1 (takes-gate second take)
Compares: per-note F0 trajectory (pyin, trimmed windows), global F0
median, envelope log-RMS correlation, durations, score-clock boundary
deviations (base arm, score mode). Prints LONG_RESULT JSON last.
"""
import glob
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, "/content")
from long_analysis import (cents, check_pair, detect_candidates,
                           align_to_score, dev_stats, envelope_correlation,
                           f0_global_median, load_mono, per_note_f0_median)

REPO = "/content/soulx"
MODEL = glob.glob("/root/.cache/huggingface/hub/models--Soul-AILab--SoulX-Singer"
                  "/snapshots/*/model.pt")[0]
DRIVER = "/content/soulx_driver.py"
PROMPT_WAV = "/content/soulx/example/audio/zh_prompt.mp3"
PROMPT_META = "/content/soulx/example/audio/zh_prompt.json"
OUT = "/content/soulx_long"
SCORES = "/content/scores"


def run(name, meta_path, seed, pitch_shift=0):
    out = f"{OUT}/{name}.wav"
    cmd = [sys.executable, DRIVER, "--repo", REPO, "--model-path", MODEL,
           "--config", f"{REPO}/soulxsinger/config/soulxsinger.yaml",
           "--phoneset", f"{REPO}/soulxsinger/utils/phoneme/phone_set.json",
           "--prompt-wav", PROMPT_WAV, "--prompt-meta", PROMPT_META,
           "--target-meta", meta_path, "--out", out, "--seed", str(seed),
           "--pitch-shift", str(pitch_shift), "--device", "cuda", "--fp16",
           "--no-auto-shift"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
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
        print(f"RUN {name} FAILED rc={proc.returncode}", flush=True)
        print((proc.stderr or proc.stdout)[-3000:], flush=True)
        raise SystemExit(1)
    print(f"RUN {name} ok: {summary}", flush=True)
    return out, summary


def score_notes(meta_path):
    """Return (notes, onsets, total_s): notes = (onset, dur, midi) for
    sung syllables only; onsets = score-clock onsets of sung syllables."""
    meta = json.load(open(meta_path))[0]
    texts = meta["text"].split()
    durs = [float(x) for x in meta["duration"].split()]
    pitches = [int(x) for x in meta["note_pitch"].split()]
    notes, onsets, t = [], [], 0.0
    for txt, d, p in zip(texts, durs, pitches):
        if txt != "<SP>":
            notes.append((t, d, p))
            onsets.append(t)
        t += d
    return notes, onsets, t


def per_note_pair(a_path, b_path, notes):
    """Per-note F0 cents difference between two arms (same note list)."""
    pa = per_note_f0_median(a_path, 24000, notes)
    pb = per_note_f0_median(b_path, 24000, notes)
    diffs, rows = [], []
    for ra, rb in zip(pa, pb):
        fa, fb = ra["f0_median_hz"], rb["f0_median_hz"]
        c = cents(fa, fb) if fa and fb else None
        if c is not None:
            diffs.append(abs(c))
        rows.append({"onset_s": ra["onset_s"], "midi": ra["midi"],
                     "f0_a_hz": fa, "f0_b_hz": fb,
                     "cents_b_minus_a": round(c, 1) if c is not None else None})
    d = np.asarray(diffs) if diffs else np.asarray([0.0])
    return {"n_notes": len(diffs),
            "median_abs_cents": round(float(np.median(d)), 1),
            "p90_abs_cents": round(float(np.percentile(d, 90)), 1),
            "max_abs_cents": round(float(np.max(d)), 1)}, rows


def main():
    os.makedirs(OUT, exist_ok=True)
    arms = {}
    arms["base"], s_base = run("base", f"{SCORES}/soulx_base.json", 0)
    arms["ps_p4"], s_ps4 = run("ps_p4", f"{SCORES}/soulx_base.json", 0, 4)
    arms["tr_p4"], s_tr4 = run("tr_p4", f"{SCORES}/soulx_trans_p4.json", 0)
    arms["ps_m3"], s_psm3 = run("ps_m3", f"{SCORES}/soulx_base.json", 0, -3)
    arms["tr_m3"], s_trm3 = run("tr_m3", f"{SCORES}/soulx_trans_m3.json", 0)
    arms["take1"], s_take1 = run("take1", f"{SCORES}/soulx_base.json", 1)

    notes, onsets, total_s = score_notes(f"{SCORES}/soulx_base.json")
    wavs = {k: load_mono(p) for k, p in arms.items()}
    res = {"summaries": {"base": s_base, "ps_p4": s_ps4, "tr_p4": s_tr4,
                         "ps_m3": s_psm3, "tr_m3": s_trm3, "take1": s_take1},
           "score_total_s": round(total_s, 2), "n_notes": len(notes)}
    res["durations"] = {k: round(len(v[0]) / v[1], 3)
                        for k, v in wavs.items()}
    res["env_corr"] = {
        "ps_p4_vs_tr_p4": round(envelope_correlation(*wavs["ps_p4"], *wavs["tr_p4"]), 4),
        "ps_m3_vs_tr_m3": round(envelope_correlation(*wavs["ps_m3"], *wavs["tr_m3"]), 4),
        "base_vs_ps_p4": round(envelope_correlation(*wavs["base"], *wavs["ps_p4"]), 4),
        "base_vs_tr_p4": round(envelope_correlation(*wavs["base"], *wavs["tr_p4"]), 4),
    }
    med = {k: f0_global_median(p, 24000) for k, p in arms.items()}
    res["f0_global_median_hz"] = {k: (round(v, 1) if v else None)
                                  for k, v in med.items()}
    res["f0_global_cents_vs_base"] = {
        k: (round(cents(med["base"], v), 1) if med["base"] and v else None)
        for k, v in med.items()}
    # Per-note trajectory comparison: pitch_shift arm vs transposed arm.
    # Note lists differ by transposition only in MIDI label; the F0
    # comparison itself is label-free (same windows, same order).
    for tag, a, b in [("p4", "ps_p4", "tr_p4"), ("m3", "ps_m3", "tr_m3")]:
        stats, rows = per_note_pair(arms[a], arms[b], notes)
        res[f"per_note_{tag}"] = stats
        with open(f"{OUT}/per_note_{tag}.json", "w") as f:
            json.dump(rows, f)
    # Model slack: base arm per-note F0 vs its own MIDI expectation.
    base_notes = per_note_f0_median(arms["base"], 24000, notes)
    slack = [cents(440.0 * 2 ** ((r["midi"] - 69) / 12.0), r["f0_median_hz"])
             for r in base_notes if r["f0_median_hz"]]
    res["base_slack_cents"] = {
        "n": len(slack),
        "median": round(float(np.median(slack)), 1) if slack else None,
        "p90_abs": round(float(np.percentile(np.abs(slack), 90)), 1) if slack else None,
        "max_abs": round(float(np.max(np.abs(slack))), 1) if slack else None}
    # Score-clock boundary deviations on the base arm.
    x, sr = wavs["base"]
    cands = detect_candidates(x, sr)
    aligned = align_to_score(cands, onsets, total_s, mode="score")
    res["base_boundary_score_mode"] = dev_stats(aligned)
    res["base_boundary_n_candidates"] = int(len(cands))
    with open(f"{OUT}/base_boundary.json", "w") as f:
        json.dump(aligned, f)
    # Takes gate: base (seed 0) vs take1 (seed 1).
    res["takes_gate_soulx"] = check_pair(*wavs["base"], *wavs["take1"])
    with open("/content/long_result.json", "w") as f:
        json.dump(res, f, indent=1)
    print("LONG_RESULT " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
