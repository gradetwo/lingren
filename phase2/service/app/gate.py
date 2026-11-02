"""Takes difference gate (Phase 1, from Phase 0 measured data).

A new take passes against every already-accepted take only if ALL of:
  1. not bit-identical (sample arrays differ);
  2. envelope log-RMS correlation < 0.99;
  3. at least one micro-difference channel active:
     F0 median absolute difference > 5 cents, OR
     onset median absolute difference > 10 ms.
Sample-level waveform correlation is deliberately NOT used: it is
phase-sensitive and false-kills genuine micro-difference takes
(Phase 0: same content shifted 10 ms drops it to ~0.02).

Only numpy/scipy are required. F0 uses a small autocorrelation
estimator (monophonic singing); if it fails, the gate falls back to
envelope + onset channels and records f0_available=False.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import medfilt

ENVELOPE_CORR_MAX = 0.99
F0_DIFF_MIN_CENTS = 5.0
ONSET_DIFF_MIN_MS = 10.0


def log_rms_envelope(x: np.ndarray, sr: int, win_ms: float = 20.0,
                     hop_ms: float = 10.0) -> np.ndarray:
    """Log-RMS envelope, one value per hop. Returns dB-ish log values."""
    win = max(1, int(sr * win_ms / 1000.0))
    hop = max(1, int(sr * hop_ms / 1000.0))
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // hop
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop][:n]
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    return np.log(rms + 1e-9)


def envelope_correlation(a: np.ndarray, sr_a: int, b: np.ndarray,
                         sr_b: int) -> float:
    """Correlation of log-RMS envelopes after resampling to a common hop.

    Envelopes are computed at 10 ms hop on each file's own rate, then the
    longer is truncated to the shorter (takes of one score share the same
    nominal length; VocalRender takes differ in length by design, and
    truncation still separates them: Phase 0 VR pairs scored 0.44-0.65).
    """
    ea = log_rms_envelope(a, sr_a)
    eb = log_rms_envelope(b, sr_b)
    n = min(len(ea), len(eb))
    if n < 8:
        return 1.0
    ea, eb = ea[:n], eb[:n]
    if np.std(ea) < 1e-9 or np.std(eb) < 1e-9:
        return 1.0 if np.allclose(ea, eb) else 0.0
    return float(np.corrcoef(ea, eb)[0, 1])


def estimate_f0_median(x: np.ndarray, sr: int, fmin: float = 50.0,
                       fmax: float = 1000.0) -> float | None:
    """Median F0 over voiced frames via normalized autocorrelation.

    Hop 512 samples; a frame counts as voiced when its peak normalized
    autocorrelation exceeds 0.5. Returns None when too few voiced frames.
    This is a median-only estimator: it feeds the gate's F0-difference
    channel, not a tuning display.
    """
    hop = 512
    win = 2048
    if len(x) < win + hop:
        return None
    min_lag = max(1, int(sr / fmax))
    max_lag = int(sr / fmin)
    f0s = []
    for start in range(0, len(x) - win, hop):
        frame = x[start:start + win].astype(np.float64)
        frame = frame - np.mean(frame)
        if np.sqrt(np.mean(frame ** 2)) < 1e-4:
            continue
        corr = np.correlate(frame, frame, mode="full")[win - 1:]
        corr = corr / (corr[0] + 1e-12)
        seg = corr[min_lag:max_lag + 1]
        lag = int(np.argmax(seg)) + min_lag
        if corr[lag] > 0.5:
            # Parabolic interpolation: integer lags quantize F0 so
            # coarsely (~5 cents at 335 Hz / 24 kHz) that genuine
            # take differences collapse to 0 cents. Fractional lag
            # restores ~1-cent resolution.
            if 0 < lag < len(corr) - 1:
                y0, y1, y2 = corr[lag - 1], corr[lag], corr[lag + 1]
                denom = y0 - 2 * y1 + y2
                frac = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
                frac = float(np.clip(frac, -0.5, 0.5))
            else:
                frac = 0.0
            f0s.append(sr / (lag + frac))
    if len(f0s) < 5:
        return None
    return float(np.median(medfilt(np.asarray(f0s), kernel_size=5)))


def f0_diff_cents(a: np.ndarray, sr_a: int, b: np.ndarray,
                  sr_b: int) -> float | None:
    """Absolute median-F0 difference in cents, or None if unmeasurable."""
    fa = estimate_f0_median(a, sr_a)
    fb = estimate_f0_median(b, sr_b)
    if fa is None or fb is None or fa <= 0 or fb <= 0:
        return None
    return float(abs(1200.0 * np.log2(fb / fa)))


def energy_onsets(x: np.ndarray, sr: int, thresh_db: float = 8.0,
                  min_gap_ms: float = 150.0) -> np.ndarray:
    """Energy-jump onset times (seconds), Phase 0 method, kept as one
    channel of the joint alignment detector (see alignment.py)."""
    env = log_rms_envelope(x, sr)
    hop_s = 0.010
    diff = np.diff(env)
    # 30 ms window jump in dB: env is natural log; convert.
    jump_db = diff * 20.0 / np.log(10.0)
    min_gap = max(1, int(min_gap_ms / 10.0))
    onsets = []
    last = -min_gap
    for i in range(1, len(jump_db)):
        if jump_db[i] > thresh_db and jump_db[i] == np.max(
                jump_db[max(0, i - 2):i + 3]) and i - last >= min_gap:
            onsets.append(i * hop_s)
            last = i
    return np.asarray(onsets)


def f0_series(x: np.ndarray, sr: int, fmin: float = 50.0,
              fmax: float = 1000.0):
    """Per-frame F0 (Hz) with voiced mask. Hop 512 samples."""
    hop = 512
    win = 2048
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


def f0_change_points(x: np.ndarray, sr: int) -> np.ndarray:
    """Times where the stable F0 level changes by >= 1 semitone."""
    times, f0s, voiced = f0_series(x, sr)
    if len(f0s) < 8:
        return np.asarray([])
    midi = np.where(voiced & (f0s > 0), 69.0 + 12.0 * np.log2(
        np.maximum(f0s, 1e-9) / 440.0), np.nan)
    last = np.nan
    filled = np.empty_like(midi)
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


def detect_candidates(x: np.ndarray, sr: int) -> np.ndarray:
    """Union of energy onsets and F0-change points, merged within 80 ms.

    Gapless legato singing has weak energy jumps, so energy alone finds
    too few onsets; note changes still move F0, so the F0 channel
    recovers the boundaries. alignment.py reuses this detector.
    """
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


def align_to_score(candidates: np.ndarray, score_onsets: list[float],
                   score_total: float, window_s: float = 0.35,
                   mode: str = "rescaled") -> list[dict]:
    """Monotonically assign candidates to score syllables.

    score_onsets: score-clock onset per syllable (seconds).
    score_total: score-clock end of the last syllable (seconds).
    Returns per-syllable {score_onset_s, measured_onset_s, deviation_ms}.
    Modes:
      rescaled (VocalRender): the first candidate anchors the scale,
        scale = (last_candidate - first_candidate) /
        (score_total - score_onsets[0]). VR decides its own timing, so
        deviations are reported against the score clock but assignment
        uses scaled expectations.
      score (SoulX validation): expected = score onset directly
        (offset 0, scale 1). SoulX follows the score clock, so rescaling
        is wrong for it: when the first syllable sits at t=0 (no
        detectable attack) the rescaled anchor misattributes the second
        boundary to the first syllable (observed: 597 ms "deviation").
        Score mode reports that syllable as None instead -- honest miss.
    Unassigned syllables get measured_onset_s = None (honest miss).
    """
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
        if mode == "score":
            expected = float(s)
        else:
            expected = first_c + (s - score_onsets[0]) * scale
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


def align_candidate_sequences(a: np.ndarray, b: np.ndarray,
                              max_dist: float = 0.200,
                              skip_cost: float = 0.200) -> list[float]:
    """Align two onset-candidate sequences (DP edit alignment).

    Matches are one-to-one, in order, only within max_dist; unmatched
    candidates cost skip_cost each. Returns the matched |time diffs|.
    This is the gate's onset comparison: candidate counts differ across
    takes (vibrato crossings, attack transients, spurious extras), so
    positional matching fabricates ~640 ms "differences" from pure
    misalignment, and score-anchored scaling breaks when the first
    syllable sits at t=0 (no detectable attack) or the last candidate
    is spurious. Sequence alignment sidesteps both: it compares the
    boundary patterns directly.
    """
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


def onset_diff_ms(a: np.ndarray, sr_a: int, b: np.ndarray,
                  sr_b: int) -> float | None:
    """Median absolute difference between DP-matched onset candidates.

    Returns None when fewer than 2 candidates match.
    """
    oa = detect_candidates(a, sr_a)
    ob = detect_candidates(b, sr_b)
    diffs = align_candidate_sequences(oa, ob)
    if len(diffs) < 2:
        return None
    return float(np.median(diffs) * 1000.0)


def check_pair(a: np.ndarray, sr_a: int, b: np.ndarray,
               sr_b: int) -> dict:
    """Run the full gate for one pair of takes. Returns metrics + verdict."""
    bit_identical = (len(a) == len(b)) and bool(np.array_equal(a, b))
    env_corr = envelope_correlation(a, sr_a, b, sr_b)
    f0d = f0_diff_cents(a, sr_a, b, sr_b)
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
    return {
        "bit_identical": bit_identical,
        "envelope_corr": round(env_corr, 4),
        "f0_diff_cents": None if f0d is None else round(f0d, 2),
        "onset_diff_ms": None if ond is None else round(ond, 2),
        "f0_available": f0d is not None,
        "passed": passed,
        "fail_reasons": reasons,
    }
