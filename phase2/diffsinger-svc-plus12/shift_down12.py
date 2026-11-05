#!/usr/bin/env python3
"""把 +12 半音合成的第一阶段音频下移 12 半音，回谱面音高。

工具：librosa.effects.pitch_shift（相位声码器，n_steps=-12，默认 bins_per_octave=12）。
这是故意不用共振峰保持的移调：本试验的路线就是"合成在高音区、音频整体下移"，
共振峰随之下移、音色变暗正是要量测的代价（见 REPORT-plus12.md）。
输入：/content/stage1_plus12/<轨>_stage1_plus12.wav（44.1kHz，峰值已归一 0.89）
输出：/content/stage1_plus12/<轨>_stage1_plus12_down12.wav（44.1kHz，移调后再归一到峰值 0.89）
      ＋同名 .json sidecar（移调工具、移调量、移调前后峰值/RMS、时长）
"""
import argparse
import json
import os
import time

import numpy as np
import soundfile as sf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", nargs="+", required=True)
    ap.add_argument("--indir", default="/content/stage1_plus12")
    ap.add_argument("--steps", type=int, default=-12)
    ap.add_argument("--suffix", default="_stage1_plus12")
    args = ap.parse_args()
    import librosa
    print(f"librosa {librosa.__version__}", flush=True)
    for t in args.tracks:
        src = os.path.join(args.indir, f"{t}{args.suffix}.wav")
        dst = os.path.join(args.indir, f"{t}{args.suffix}_down12.wav")
        t0 = time.time()
        y, sr = sf.read(src)
        assert sr == 44100, (src, sr)
        if y.ndim > 1:
            y = y[:, 0]
        peak_before = float(np.max(np.abs(y)))
        rms_before = float(np.sqrt(np.mean(y ** 2)))
        y2 = librosa.effects.pitch_shift(y.astype(np.float32), sr=sr, n_steps=args.steps)
        peak_shifted = float(np.max(np.abs(y2)))
        gain = 0.89 / peak_shifted if peak_shifted > 0 else 1.0
        out = (y2 * gain).astype(np.float32)
        sf.write(dst, out, sr, subtype="PCM_16")
        rec = {
            "track": t,
            "tool": "librosa.effects.pitch_shift",
            "librosa_version": librosa.__version__,
            "shift_semitones": args.steps,
            "bins_per_octave": 12,
            "formant_preserved": False,
            "sr": sr,
            "duration_s": round(len(out) / sr, 3),
            "peak_before_shift": round(peak_before, 4),
            "rms_before_shift": round(rms_before, 4),
            "peak_after_shift_before_norm": round(peak_shifted, 4),
            "gain_to_0.89": round(gain, 4),
            "peak_normalized": 0.89,
            "shift_s": round(time.time() - t0, 1),
            "source": os.path.basename(src),
        }
        json.dump(rec, open(dst.replace(".wav", ".json"), "w"), ensure_ascii=False, indent=1)
        print(f"SHIFT-OK {t}: {len(out)/sr:.2f}s peak {peak_before:.4f} -> {peak_shifted:.4f} -> 0.89 gain {gain:.4f} ({time.time()-t0:.0f}s)", flush=True)
    print("SHIFT-DOWN12-DONE", flush=True)


if __name__ == "__main__":
    main()
