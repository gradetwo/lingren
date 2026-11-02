#!/usr/bin/env python3
"""Measurement helpers for Phase 2 long-phrase tests.

Copied measurement logic from Phase 1 service/app/gate.py (envelope,
onset candidates, score alignment, takes gate) so long-phrase numbers
are comparable with Phase 1 short-phrase numbers. Only numpy/scipy/
librosa/soundfile are required. pyin (librosa) is used for F0 because
the gate's autocorrelation estimator is median-only, not per-note.
"""
import numpy as np
from scipy.signal import medfilt

ENVELOPE_CORR_MAX = 0.99
F0_DIFF_MIN_CENTS = 5.0
ONSET_DIFF_MIN_MS = 10.0


def load_mono(path):
    import soundfile as sf
    y, sr = sf.read(path, dtype="float32")
    if y.ndim == 2:
        y = y[:, 0]
    return np.asarray(y, dtype=np.float32), int(sr)


def log_rms_envelope(x, sr, win_ms=20.0, hop_ms=10.0):
    win = max(1, int(sr * win_ms / 1000.0))
    hop = max(1, int(sr * hop_ms / 1000.0))
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // hop
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop][:n]
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    return np.log(rms + 1e-9)


def envelope_correlation(a, sr_a, b, sr_b):
    ea, eb = log_rms_envelope(a, sr_a), log_rms_envelope(b, sr_b)
    n = min(len(ea), len(eb))
    if n < 8:
        return 1.0
    ea, eb = ea[:n], eb[:n]
    if np.std(ea) < 1e-9 or np.std(eb) < 1e-9:
        return 1.0 if np.allclose(ea, eb) else 0.0
    return float(np.corrcoef(ea, eb)[0, 1])


def pyin_series(path, target_sr):
    """Return (times_s, f0_hz, voiced) via librosa pyin."""
    import librosa
    y, sr = librosa.load(path, sr=target_sr, mono=True)
    f0, vf, _ = librosa.pyin(y, fmin=50, fmax=1000, sr=sr,
                             frame_length=2048, hop_length=512)
    times = np.arange(len(f0)) * 512.0 / sr
    voiced = ~np.isnan(f0)
    f0 = np.where(voiced, f0, 0.0)
    return times, f0, voiced


def f0_global_median(path, target_sr):
    times, f0, voiced = pyin_series(path, target_sr)
    vals = f0[voiced]
    vals = vals[(vals > 50) & (vals < 1000)]
    return float(np.median(vals)) if len(vals) else None


def per_note_f0_median(path, target_sr, notes, trim=0.15):
    """notes: list of (onset_s, dur_s, midi). Returns list of dicts with
    median F0 over frames whose centers fall in the trimmed note window.
    """
    times, f0, voiced = pyin_series(path, target_sr)
    out = []
    for onset, dur, midi in notes:
        lo = onset + trim * dur
        hi = onset + (1.0 - trim) * dur
        m = voiced & (times >= lo) & (times < hi)
        vals = f0[m]
        vals = vals[(vals > 50) & (vals < 1000)]
        med = float(np.median(vals)) if len(vals) else None
        out.append({"onset_s": round(onset, 3), "dur_s": dur, "midi": midi,
                    "f0_median_hz": round(med, 1) if med else None,
                    "n_frames": int(len(vals))})
    return out


def cents(a, b):
    if not a or not b:
        return None
    return float(1200.0 * np.log2(b / a))


def energy_onsets(x, sr, thresh_db=8.0, min_gap_ms=150.0):
    env = log_rms_envelope(x, sr)
    hop_s = 0.010
    diff = np.diff(env)
    jump_db = diff * 20.0 / np.log(10.0)
    min_gap = max(1, int(min_gap_ms / 10.0))
    onsets, last = [], -min_gap
    for i in range(1, len(jump_db)):
        if jump_db[i] > thresh_db and jump_db[i] == np.max(
                jump_db[max(0, i - 2):i + 3]) and i - last >= min_gap:
            onsets.append(i * hop_s)
            last = i
    return np.asarray(onsets)


def f0_series_ac(x, sr, fmin=50.0, fmax=1000.0):
    hop, win = 512, 2048
    min_lag = max(1, int(sr / fmax))
    max_lag = int(sr / fmin)
    times, f0s, voiced = [], [], []
    for start in range(0, max(0, len(x) - win), hop):
        frame = x[start:start + win].astype(np.float64)
        frame = frame - np.mean(frame)
        rms = float(np.sqrt(np.mean(frame ** 2)))
        if rms < 1e-4:
            times.append(start / sr)
            f0s.append(0.0)
            voiced.append(False)
            continue
        corr = np.correlate(frame, frame, mode="full")[win - 1:]
        corr = corr / (corr[0] + 1e-12)
        lag = int(np.argmax(corr[min_lag:max_lag + 1])) + min_lag
        times.append(start / sr)
        f0s.append(sr / lag if corr[lag] > 0.5 else 0.0)
        voiced.append(bool(corr[lag] > 0.5))
    return np.asarray(times), np.asarray(f0s), np.asarray(voiced)


def f0_change_points(x, sr):
    times, f0s, voiced = f0_series_ac(x, sr)
    if len(f0s) < 8:
        return np.asarray([])
    midi = np.where(voiced & (f0s > 0),
                    69.0 + 12.0 * np.log2(np.maximum(f0s, 1e-9) / 440.0),
                    np.nan)
    last, filled = np.nan, np.empty_like(midi)
    for i, v in enumerate(midi):
        if not np.isnan(v):
            last = v
        filled[i] = last if not np.isnan(last) else 0.0
    smooth = medfilt(filled, kernel_size=5)
    rounded = np.round(smooth)
    points = []
    for i in range(3, len(rounded)):
        if rounded[i] != rounded[i - 1] and np.all(
                rounded[i:i + 3] == rounded[i]):
            points.append(times[i])
    return np.asarray(points)


def detect_candidates(x, sr):
    e = energy_onsets(x, sr)
    f = f0_change_points(x, sr)
    all_c = np.sort(np.concatenate([e, f])) if len(e) or len(f) \
        else np.asarray([])
    merged = []
    for t in all_c:
        if merged and t - merged[-1] < 0.080:
            continue
        merged.append(float(t))
    return np.asarray(merged)


def align_to_score(candidates, score_onsets, score_total, window_s=0.35,
                   mode="rescaled"):
    out = []
    if len(candidates) == 0 or len(score_onsets) == 0:
        return [{"score_onset_s": round(float(s), 4),
                 "measured_onset_s": None, "deviation_ms": None}
                for s in score_onsets]
    first_c = float(candidates[0])
    span_score = max(score_total - score_onsets[0], 1e-6)
    span_meas = max(float(candidates[-1]) - first_c, 1e-6)
    scale = span_meas / span_score
    used = set()
    for s in score_onsets:
        expected = float(s) if mode == "score" else \
            first_c + (s - score_onsets[0]) * scale
        best, best_d = None, None
        for j, c in enumerate(candidates):
            if j in used:
                continue
            d = abs(float(c) - expected)
            if best_d is None or d < best_d:
                best, best_d = j, d
        if best is not None and best_d <= window_s:
            used.add(best)
            m = float(candidates[best])
            out.append({"score_onset_s": round(float(s), 4),
                        "measured_onset_s": round(m, 4),
                        "deviation_ms": round((m - float(s)) * 1000.0, 1)})
        else:
            out.append({"score_onset_s": round(float(s), 4),
                        "measured_onset_s": None, "deviation_ms": None})
    return out


def align_candidate_sequences(a, b, max_dist=0.200, skip_cost=0.200):
    a = [float(t) for t in a]
    b = [float(t) for t in b]
    n, m = len(a), len(b)
    inf = float("inf")
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    bk = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if dp[i][j] == inf:
                continue
            if i < n and dp[i][j] + skip_cost < dp[i + 1][j]:
                dp[i + 1][j] = dp[i][j] + skip_cost
                bk[i + 1][j] = ("skip_a", i, j)
            if j < m and dp[i][j] + skip_cost < dp[i][j + 1]:
                dp[i][j + 1] = dp[i][j] + skip_cost
                bk[i][j + 1] = ("skip_b", i, j)
            if i < n and j < m:
                d = abs(a[i] - b[j])
                if d <= max_dist and dp[i][j] + d < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = dp[i][j] + d
                    bk[i + 1][j + 1] = ("match", i, j)
    diffs = []
    i, j = n, m
    while (i, j) != (0, 0):
        step = bk[i][j]
        if step is None:
            break
        kind, pi, pj = step
        if kind == "match":
            diffs.append(abs(a[pi] - b[pj]))
        i, j = pi, pj
    return diffs


def onset_diff_ms(a, sr_a, b, sr_b):
    oa = detect_candidates(a, sr_a)
    ob = detect_candidates(b, sr_b)
    diffs = align_candidate_sequences(oa, ob)
    if len(diffs) < 2:
        return None
    return float(np.median(diffs) * 1000.0)


def f0_diff_cents_ac(a, sr_a, b, sr_b):
    """Gate F0 channel via autocorrelation median (Phase 1 method)."""
    def med(x, sr):
        times, f0s, voiced = f0_series_ac(x, sr)
        vals = f0s[voiced]
        vals = vals[(vals > 50) & (vals < 1000)]
        return float(np.median(vals)) if len(vals) else None
    fa, fb = med(a, sr_a), med(b, sr_b)
    if fa is None or fb is None:
        return None
    return float(abs(1200.0 * np.log2(fb / fa)))


def check_pair(a, sr_a, b, sr_b):
    bit_identical = (len(a) == len(b)) and bool(np.array_equal(a, b))
    env_corr = envelope_correlation(a, sr_a, b, sr_b)
    f0d = f0_diff_cents_ac(a, sr_a, b, sr_b)
    ond = onset_diff_ms(a, sr_a, b, sr_b)
    micro = ((f0d is not None and f0d > F0_DIFF_MIN_CENTS)
             or (ond is not None and ond > ONSET_DIFF_MIN_MS))
    passed = (not bit_identical) and (env_corr < ENVELOPE_CORR_MAX) and micro
    reasons = []
    if bit_identical:
        reasons.append("bit-identical")
    if env_corr >= ENVELOPE_CORR_MAX:
        reasons.append(f"envelope_corr {env_corr:.4f} >= 0.99")
    if not micro:
        reasons.append("no micro-difference channel active")
    return {"bit_identical": bit_identical,
            "envelope_corr": round(env_corr, 4),
            "f0_diff_cents": None if f0d is None else round(f0d, 2),
            "onset_diff_ms": None if ond is None else round(ond, 2),
            "f0_available": f0d is not None, "passed": passed,
            "fail_reasons": reasons}


def dev_stats(aligned):
    devs = [r["deviation_ms"] for r in aligned
            if r["deviation_ms"] is not None]
    if not devs:
        return {"n": 0, "recall": 0.0}
    a = np.abs(np.asarray(devs))
    return {"n": len(devs), "recall": round(len(devs) / len(aligned), 4),
            "median_abs_ms": round(float(np.median(a)), 1),
            "p90_abs_ms": round(float(np.percentile(a, 90)), 1),
            "max_abs_ms": round(float(np.max(a)), 1),
            "median_signed_ms": round(float(np.median(devs)), 1)}
