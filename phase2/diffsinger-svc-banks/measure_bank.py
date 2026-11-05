#!/usr/bin/env python3
"""对第一阶段渲染 wav 做独立 f0 量测（与原报告同法：pyworld dio）。

用法：
  python3 measure_bank.py --wav vox_stage1_lang.wav --scores scores/vocal_scores.json \
      --track vox --t0 25.55 --start-s 25.6 --end-s 55.6 [--out vox_stage1_lang_measure.json]

量测定义（与原报告一致）：
- dio 参数：f0_floor=27 Hz（谱面最低 27 Hz），f0_ceil=800 Hz，frame_period=5 ms。
- 每音取其中部 50% 帧（避开起音/收尾过渡），中位 f0 -> MIDI，与谱面 pitch 比偏差（半音）。
- 检出音：该音中部帧有声帧占比 >=50% 且 |偏差中位| < 2 半音。
- 有声帧占比：全部音跨度内 dio f0>0 的帧占比。
- 输出 JSON + 一行摘要到 stdout。
"""
import argparse, json
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--t0", type=float, required=True,
                    help="wav 第 0 采样对应的谱面时间（秒）")
    ap.add_argument("--start-s", type=float, default=None)
    ap.add_argument("--end-s", type=float, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import soundfile as sf
    import pyworld as pw
    wav, sr = sf.read(args.wav)
    if wav.ndim > 1:
        wav = wav[:, 0]
    wav = np.ascontiguousarray(wav.astype(np.float64))
    f0, t = pw.dio(wav, sr, f0_floor=27.0, f0_ceil=800.0, frame_period=5.0)
    f0 = pw.stonemask(wav, f0, t, sr)
    frame_s = 0.005
    print(f"dio: {len(f0)} frames, sr={sr}, wav={len(wav)/sr:.2f}s, "
          f"voiced frames={int((f0 > 0).sum())} ({100*(f0 > 0).mean():.1f}%)", flush=True)

    data = json.load(open(args.scores))
    notes = sorted(data["tracks"][args.track]["notes"], key=lambda n: n["start_tick"])
    if args.start_s is not None:
        notes = [n for n in notes if n["start_s"] >= args.start_s]
    if args.end_s is not None:
        notes = [n for n in notes if n["end_s"] <= args.end_s]

    def f0_at(sec):
        idx = int(round((sec - args.t0) / frame_s))
        if 0 <= idx < len(f0):
            return f0[idx]
        return 0.0

    devs, detected, voiced_total, frames_total = [], 0, 0, 0
    per_note = []
    for n in notes:
        s, e = n["start_s"], n["end_s"]
        mid = (s + e) / 2
        half = (e - s) * 0.25
        span = np.arange(max(s, mid - half), min(e, mid + half), frame_s)
        vals = np.array([f0_at(x) for x in span])
        voiced = vals[vals > 0]
        vshare = len(voiced) / max(1, len(vals))
        voiced_total += len(voiced)
        frames_total += len(vals)
        if len(voiced) >= max(1, len(vals) // 2):
            med_hz = float(np.median(voiced))
            med_midi = 69 + 12 * np.log2(med_hz / 440.0)
            dev = med_midi - float(n["pitch"])
            devs.append(dev)
            is_det = abs(dev) < 2.0
            detected += int(is_det)
            per_note.append({"lyric": n.get("lyric"), "pitch": n["pitch"],
                             "pitch_name": n.get("pitch_name"),
                             "start_s": round(s, 3), "median_hz": round(med_hz, 2),
                             "dev_semitones": round(dev, 3), "voiced_share": round(vshare, 3),
                             "detected": bool(is_det)})
        else:
            per_note.append({"lyric": n.get("lyric"), "pitch": n["pitch"],
                             "pitch_name": n.get("pitch_name"),
                             "start_s": round(s, 3), "median_hz": None,
                             "dev_semitones": None, "voiced_share": round(vshare, 3),
                             "detected": False})
    devs = np.array(devs) if devs else np.array([np.nan])
    res = {
        "wav": args.wav, "track": args.track,
        "window_s": [args.start_s, args.end_s],
        "n_notes": len(notes),
        "n_detected": detected,
        "detect_rate": round(detected / max(1, len(notes)), 3),
        "dev_median_semitones": round(float(np.nanmedian(devs)), 3) if len(devs) else None,
        "dev_p90_abs_semitones": round(float(np.nanpercentile(np.abs(devs), 90)), 3) if len(devs) else None,
        "within_pm0.5_semitones": round(float(np.mean(np.abs(devs) <= 0.5)), 3) if len(devs) else None,
        "voiced_frame_share": round(voiced_total / max(1, frames_total), 3),
        "per_note": per_note,
    }
    print(json.dumps({k: v for k, v in res.items() if k != "per_note"},
                     ensure_ascii=False), flush=True)
    if args.out:
        json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"MEASURE-OK {args.out}", flush=True)


if __name__ == "__main__":
    main()
