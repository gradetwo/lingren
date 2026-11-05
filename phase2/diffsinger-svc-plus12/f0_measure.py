#!/usr/bin/env python3
"""独立 f0 量测＋长期平均谱（LTAS），复现原报告方法口径。

f0（与原报告同法：pyworld dio＋stonemask，对渲染音频测，与谱面比）：
- 帧长 5 ms；每音取窗内中间 50%（25%–75%）帧的 voiced f0 中位；
  窗内 voiced 帧 >=3 才算检出该音，否则该音未检出。
- 偏差（半音）= 12*log2(f0_med / 谱面音高 Hz)。
- 汇总：检出音数、偏差中位、|偏差| p90、超 ±0.5 半音占比（按检出音计）、
  有声帧占比（voiced 帧 / 全部落入音符窗的帧，含窗首尾）。
- 量测对象须与谱面音高一致：下移后音频对原始谱面；移调前音频对 +12 谱面。

LTAS（音色／共振峰变化）：
- Welch（nperseg 4096，44.1kHz）求全轨平均谱；报谱质心（Hz）与 200–4000 Hz 内
  平滑谱峰频率；与对照轨（原版第一阶段）并表比较，质心差折半音数。
"""
import argparse
import json
import math

import numpy as np
import soundfile as sf


def midi_hz(m):
    return 440.0 * (2.0 ** ((float(m) - 69.0) / 12.0))


def measure_f0(y, sr, notes):
    import pyworld as pw
    y = y.astype(np.float64)
    f0, t = pw.dio(y, sr, frame_period=5.0)
    f0 = pw.stonemask(y, f0, t, sr)
    frame_s = 0.005
    devs = []
    detected = 0
    voiced_in_windows = 0
    total_in_windows = 0
    for n in notes:
        s, e = n["start_s"], n["end_s"]
        i0 = int(math.floor(s / frame_s))
        i1 = int(math.ceil(e / frame_s))
        i0 = max(0, i0)
        i1 = min(len(f0), i1)
        if i1 <= i0:
            continue
        total_in_windows += (i1 - i0)
        voiced_in_windows += int(np.sum(f0[i0:i1] > 0))
        # 中间 50%
        m0 = i0 + (i1 - i0) // 4
        m1 = i0 + 3 * (i1 - i0) // 4
        seg = f0[m0:m1]
        seg = seg[seg > 0]
        if len(seg) >= 3:
            detected += 1
            fmed = float(np.median(seg))
            devs.append(12.0 * math.log2(fmed / midi_hz(n["pitch"])))
    devs = np.array(devs)
    return {
        "n_notes": len(notes),
        "detected": detected,
        "dev_median_semitones": round(float(np.median(devs)), 3) if len(devs) else None,
        "dev_p90_abs_semitones": round(float(np.percentile(np.abs(devs), 90)), 3) if len(devs) else None,
        "over_pm0_5_frac": round(float(np.mean(np.abs(devs) > 0.5)), 3) if len(devs) else None,
        "voiced_frame_frac": round(voiced_in_windows / total_in_windows, 3) if total_in_windows else None,
    }


def measure_ltas(y, sr):
    from scipy.signal import welch
    f, pxx = welch(y.astype(np.float64), sr, nperseg=4096)
    mag = np.sqrt(np.maximum(pxx, 0))
    centroid = float(np.sum(f * mag) / np.sum(mag))
    band = (f >= 200) & (f <= 4000)
    fb, mb = f[band], mag[band]
    # 平滑（~5 bin 均值）后取峰
    k = np.ones(5) / 5.0
    sm = np.convolve(mb, k, mode="same")
    peak_hz = float(fb[int(np.argmax(sm))])
    return {"ltas_centroid_hz": round(centroid, 1), "ltas_peak_200_4000_hz": round(peak_hz, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    y, sr = sf.read(args.wav)
    if y.ndim > 1:
        y = y[:, 0]
    data = json.load(open(args.scores))
    notes = sorted(data["tracks"][args.track]["notes"], key=lambda n: n["start_s"])
    rec = {"track": args.track, "wav": args.wav, "sr": sr,
           "duration_s": round(len(y) / sr, 3)}
    rec.update(measure_f0(y, sr, notes))
    rec.update(measure_ltas(y, sr))
    json.dump(rec, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
