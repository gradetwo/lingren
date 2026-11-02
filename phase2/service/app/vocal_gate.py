"""Melodyne-alignment Phase 1: Lingren-side vocal gate + boundary refiner.

Mirrors the Yeban Rust refiner (``yeban-dsp/src/vocal_refine.rs``) and the
timemap builder allocation (``yeban-dsp/src/vocal_timemap.rs``) plus the
``vocal_align`` driver pipeline (pass 1 -> collision -> pass 2 -> tiling ->
ratio -> verdict), so the retry decision taken here agrees with the gate
Yeban applies at import. Where the two implementations can differ, the
ADR (Yeban ADR-0067) pins the agreement tolerance; the cross-check script
(``vocaledit-pilot-20261102/crosscheck.py``) measures it on lines 13/0.

VOP definition (both sides): the voiced vowel onset -- the first frame
where 300-3000 Hz band energy rises significantly AND the frame is voiced
(FFT-autocorrelation peak), NOT an energy-flux peak. Frame grid: 2048
window (fixed, any sample rate), 480 hop, frame time = window center,
symmetric Hann. All times are take-internal seconds, the same base as
the receipt ``measured_onset_s`` (NOT project time). Scores carry no
note_id in Phase 1; lines are note-index ranges.

Gate thresholds (expert Q11/Q15 + Q13 clamp rule, same as Rust
``gate_verdict`` + driver clamp rule):
  line lyric span ratio R in [0.80, 1.25] (R = score_span / sung_span);
  >= 90% of voiced-core required rates in [0.70, 1.40];
  no single voiced-core required rate outside [0.55, 1.80];
  any render clamp (required rate outside [0.60, 1.65]) is itself a
  failure, even when the required rate is inside the hard bound.
A failing line triggers ``SynthesisRetryRequired``: the caller retries
that line with a new seed (see POST /v1/jobs/{id}/retry-lines).

Why a re-implementation instead of gate.py primitives: gate.py's F0
series runs on a 512-sample hop (21 ms at the stub adapter's 24 kHz --
too coarse for VOP work) and its onset detector is broadband-RMS based,
while the Rust refiner's discriminators are band energy + autocorr peak.
Re-implementing the same algorithm (same constants, same rules) gives
frame-level agreement; gate.py keeps its takes-difference role unchanged.
"""

from __future__ import annotations

import numpy as np

WIN = 2048
HOP = 480
BAND_LO_HZ = 300.0
BAND_HI_HZ = 3000.0
F0_MIN_HZ = 50.0
F0_MAX_HZ = 1000.0
REFINE_HALF_WINDOW_S = 0.080
EXPAND_WINDOW_S = 0.300
PRE_WINDOW_S = 0.150
VOP_RISE_RATIO = 4.0
VOICED_PEAK = 0.35
VOICED_BAND_FLOOR = 50.0
OFF_DROP_DB = 14.0
OFF_SUSTAIN_FRAMES = 3
REVIEW_CONFIDENCE = 0.65
LOCK_VOP_S = 0.035
LOCK_PRE_S = 0.010
LOCK_RELEASE_S = 0.030
MIN_CORE_S = 0.065
SILENCE_DB = -55.0
SILENCE_MIN_S = 0.030
CORE_CLAMP = (0.60, 1.65)
BREATH_RANGE = (0.90, 1.0)
GATE_RATIO_RANGE = (0.80, 1.25)
GATE_BAND = (0.70, 1.40)
GATE_HARD = (0.55, 1.80)
GATE_FRACTION_MIN = 0.90
MAX_RETRY_GENERATIONS = 3
RETRY_SEED_BUMP = 1000


def _median(values) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return float(values[mid])
    return float((values[mid - 1] + values[mid]) / 2.0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def analyze_frames(x: np.ndarray, sr: int, start_s: float,
                   end_s: float):
    """Per-frame (time, band_energy, f0_hz, peak, voiced), Rust-mirror."""
    win, hop = WIN, HOP
    window = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(win) / (win - 1)))
    lo_bin = max(1, int(np.floor(BAND_LO_HZ * win / sr)))
    hi_bin = min(win // 2, int(np.ceil(BAND_HI_HZ * win / sr)))
    min_lag = int(np.floor(sr / F0_MAX_HZ))
    max_lag = int(np.ceil(sr / F0_MIN_HZ))
    lag_hi = min(max_lag, win - 1)
    frames = []
    start = int(max(0.0, start_s) * sr)
    end = int(max(0.0, end_s) * sr)
    n = len(x)
    while start < end and start < n:
        seg = np.zeros(win)
        avail = min(win, n - start)
        if avail > 0:
            seg[:avail] = x[start:start + avail]
        spec = np.fft.rfft(seg * window)
        band = float(np.sum(np.abs(spec[lo_bin:hi_bin + 1]) ** 2))
        power = np.abs(spec) ** 2
        ac = np.fft.irfft(power, n=win)
        scale = ac[0]
        best_lag, best_peak = 0, 0.0
        if scale > 0:
            seg_ac = ac[min_lag:lag_hi + 1] / scale
            j = int(np.argmax(seg_ac))
            best_lag = min_lag + j
            best_peak = float(seg_ac[j])
        peak_lag = float(best_lag)
        if best_lag > min_lag and best_lag + 1 < win and scale > 0:
            left = ac[best_lag - 1] / scale
            center = ac[best_lag] / scale
            right = ac[best_lag + 1] / scale
            denom = left - 2.0 * center + right
            if abs(denom) > 1e-12:
                shift = 0.5 * (left - right) / denom
                if abs(shift) <= 1.0:
                    peak_lag = best_lag + shift
        f0 = sr / peak_lag if best_peak > 0 and peak_lag > 0 else 0.0
        voiced = (best_peak > VOICED_PEAK
                  and F0_MIN_HZ <= f0 <= F0_MAX_HZ
                  and best_lag > min_lag and best_lag < lag_hi
                  and band >= VOICED_BAND_FLOOR)
        frames.append({"t": (start + win / 2) / sr,
                       "band": band,
                       "f0": f0 if voiced else 0.0,
                       "peak": best_peak,
                       "voiced": voiced})
        start += hop
    return frames


def _vop_qualifies(frames, idx: int, floor: float,
                   window_max: float) -> bool:
    fr = frames[idx]
    if not fr["voiced"]:
        return False
    if fr["band"] < 0.01 * window_max:
        return False
    prev = frames[idx - 1]
    rise = ((fr["band"] > VOP_RISE_RATIO * floor
             and prev["band"] <= VOP_RISE_RATIO * floor)
            or (prev["band"] > 0
                and fr["band"] / prev["band"] >= 1.5)
            or (idx >= 3 and frames[idx - 3]["band"] > 0
                and fr["band"] / frames[idx - 3]["band"] >= 2.0))
    if not rise:
        return False
    nxt = sorted(f["band"] for f in frames[idx + 1:idx + 4])
    if not nxt:
        return False
    return nxt[len(nxt) // 2] >= 0.5 * fr["band"]


def _find_vop(frames, start: float, end: float, floor: float,
              next_bound: float, closest_to: float | None):
    window = [i for i, f in enumerate(frames)
              if start <= f["t"] <= end and f["t"] < next_bound]
    if not window:
        return None
    window_max = max(frames[i]["band"] for i in window)
    best, best_dist = None, float("inf")
    for idx in window:
        if idx == 0:
            continue
        if not _vop_qualifies(frames, idx, floor, window_max):
            continue
        if closest_to is None:
            return idx
        dist = abs(frames[idx]["t"] - closest_to)
        if dist < best_dist:
            best_dist, best = dist, idx
    return best


def refine_note(x: np.ndarray, sr: int, coarse_s: float,
                prev_bound_s: float, next_bound_s: float) -> dict:
    """Refine one note's (pre, vop, off) around a coarse onset."""
    search_start = coarse_s - REFINE_HALF_WINDOW_S
    search_end = coarse_s + REFINE_HALF_WINDOW_S
    floor_start = coarse_s - EXPAND_WINDOW_S
    frames = analyze_frames(x, sr, floor_start,
                            max(next_bound_s, coarse_s + EXPAND_WINDOW_S))
    floor_bands = [f["band"] for f in frames
                   if floor_start <= f["t"] < search_start]
    if floor_bands:
        floor = max(_median(floor_bands), 1e-12)
    else:
        floor = max(min((f["band"] for f in frames), default=1e-12), 1e-12)
    vop_idx = _find_vop(frames, max(search_start, prev_bound_s), search_end,
                        floor, next_bound_s, None)
    if vop_idx is None:
        vop_idx = _find_vop(frames,
                            max(coarse_s - EXPAND_WINDOW_S, prev_bound_s),
                            coarse_s + EXPAND_WINDOW_S,
                            floor, next_bound_s, coarse_s)
    if vop_idx is None:
        return {"pre": coarse_s, "vop": coarse_s, "off": coarse_s,
                "anchored": False, "confidence": 0.0, "f0": 0.0,
                "hnr": -20.0}
    vop = frames[vop_idx]
    vop_s = vop["t"]
    vowel_bands = sorted(f["band"] for f in frames
                         if vop_s <= f["t"] <= vop_s + 0.100)
    vowel_band = max(vowel_bands[len(vowel_bands) // 2], 1e-12) \
        if vowel_bands else vop["band"]
    threshold = 0.5 * vowel_band
    pre_window_start = vop_s - PRE_WINDOW_S
    j = vop_idx
    while (j > 0 and frames[j]["t"] > pre_window_start
           and frames[j]["band"] >= threshold):
        j -= 1
    while (j > 0 and frames[j]["t"] > pre_window_start
           and frames[j]["band"] < threshold
           and frames[j]["band"] >= VOICED_BAND_FLOOR):
        j -= 1
    if frames[j]["band"] >= threshold or frames[j]["band"] < VOICED_BAND_FLOOR:
        pre_s = frames[j]["t"] + 0.010
    else:
        pre_s = pre_window_start
    pre_s = _clamp(pre_s, prev_bound_s, vop_s)
    peak_band = max([f["band"] for f in frames
                     if vop_s <= f["t"] <= min(vop_s + 0.500, next_bound_s)]
                    or [1e-12])
    peak_band = max(peak_band, 1e-12)
    drop_line = peak_band * 10.0 ** (-OFF_DROP_DB / 10.0)
    off_s = next_bound_s
    run, last_voiced = 0, vop_s
    for f in frames:
        if not (vop_s <= f["t"] <= next_bound_s):
            continue
        if f["voiced"]:
            last_voiced, run = f["t"], 0
        elif f["band"] < drop_line:
            run += 1
            if run >= OFF_SUSTAIN_FRAMES:
                off_s = last_voiced
                break
        else:
            run = 0
    if off_s == next_bound_s:
        off_s = last_voiced
    off_s = _clamp(off_s, vop_s, next_bound_s)
    anchored = off_s - vop_s >= MIN_CORE_S
    core = [f for f in frames
            if vop_s + LOCK_VOP_S <= f["t"] <= off_s - LOCK_RELEASE_S
            and f["voiced"]]
    if not core:
        core = [f for f in frames
                if vop_s <= f["t"] <= off_s and f["voiced"]]
    f0_med = _median([f["f0"] for f in core])
    peak_med = _clamp(_median([f["peak"] for f in core]), 0.01, 0.99)
    hnr = 10.0 * np.log10(peak_med / (1.0 - peak_med)) if core else -20.0
    context = sorted(f["band"] for f in frames
                     if floor_start <= f["t"] <= search_end)
    local_floor = max(context[1], 1e-12) if len(context) >= 3 else floor
    jump_db = 10.0 * np.log10(vop["band"] / local_floor)
    onset_strength = _clamp((jump_db - 3.0) / 17.0, 0.0, 1.0)
    if f0_med > 0 and core:
        good = sum(1 for f in core
                   if abs(1200.0 * np.log2(f["f0"] / f0_med)) <= 100.0)
        consistency = good / len(core)
    else:
        consistency = 0.0
    return {"pre": pre_s, "vop": vop_s, "off": off_s,
            "anchored": anchored,
            "confidence": onset_strength * consistency,
            "f0": f0_med, "hnr": float(hnr)}


def rms_curve(x: np.ndarray, sr: int) -> np.ndarray:
    """Linear RMS, 20 ms window / 10 ms hop (Rust-mirror, index-based)."""
    win = max(1, int(sr * 20 / 1000))
    hop = max(1, int(sr * 10 / 1000))
    out = []
    start = 0
    n = len(x)
    while start < n:
        end = min(start + win, n)
        out.append(float(np.sqrt(np.mean(x[start:end] ** 2))))
        start += hop
    return np.asarray(out)


def gap_segments(rms: np.ndarray, sr: int, start_s: float,
                 end_s: float) -> list[tuple[float, float, str]]:
    """Split [start_s, end_s) into Breath/Silence runs (Rust-mirror).

    Silence = RMS below -55 dBFS sustained >= 30 ms; anything else is
    Breath. Short sub-threshold runs stay inside surrounding Breath.
    """
    if end_s <= start_s:
        return []
    hop = max(1, int(sr * 10 / 1000))
    hop_s = hop / sr
    threshold = 10.0 ** (SILENCE_DB / 20.0)
    start_idx = min(int(start_s * sr) // hop, len(rms))
    end_idx = min(int(end_s * sr) // hop, len(rms))
    min_run = int(np.ceil(SILENCE_MIN_S / hop_s))
    segments: list[tuple[float, float, str]] = []
    cursor, idx = start_s, start_idx
    while idx < end_idx:
        if rms[idx] < threshold:
            run_start = idx
            while idx < end_idx and rms[idx] < threshold:
                idx += 1
            if idx - run_start >= min_run:
                seg_start = max(run_start * hop_s, cursor)
                if seg_start > cursor:
                    segments.append((cursor, seg_start, "breath"))
                seg_end = min(idx * hop_s, end_s)
                segments.append((seg_start, seg_end, "silence"))
                cursor = seg_end
        else:
            idx += 1
    if cursor < end_s:
        segments.append((cursor, end_s, "breath"))
    return segments


def build_core_rates(syllables, targets, out_end_s: float,
                     tail_in_end: float, rms: np.ndarray,
                     sr: int):
    """Port of Rust build_timemap's per-syllable allocation.

    syllables: [(pre, vop, off_c)] take seconds, tiling-clamped.
    targets: [score_vop] line-relative seconds, same length.
    Returns (required_rates, clamps, warnings): required_rates are the
    gate's rates (pre-clamp, as Rust BuiltInterval.rate); clamps is the
    list of clamp events (presence alone fails the gate).
    """
    rates: list[float] = []
    clamps: list[dict] = []
    warnings: list[str] = []
    for i, ((pre_s, vop_s, off_s), target_vop) in enumerate(
            zip(syllables, targets)):
        target_pre = target_vop - (vop_s - pre_s)
        lock_start_out = target_pre - LOCK_PRE_S
        lock_end_out = target_vop + LOCK_VOP_S
        lock_end_in = vop_s + LOCK_VOP_S
        if i + 1 < len(syllables):
            next_pre = syllables[i + 1][0]
            next_target = targets[i + 1]
            next_target_pre = next_target - (syllables[i + 1][1]
                                             - syllables[i + 1][0])
            next_lock_start_out = next_target_pre - LOCK_PRE_S
            gap_in_end = next_pre - LOCK_PRE_S
        else:
            next_lock_start_out = out_end_s
            gap_in_end = tail_in_end
        core_out_start = lock_end_out
        core_out_end = next_lock_start_out
        release_in_end = off_s
        if off_s > gap_in_end:
            warnings.append(f"syllable {i}: off clamped to gap end")
            release_in_end = gap_in_end
        core_in_end = min(max(off_s - LOCK_RELEASE_S, lock_end_in),
                          release_in_end)
        core_src = max(0.0, core_in_end - lock_end_in)
        avail = max(0.0, core_out_end - core_out_start)
        release_tgt = min(LOCK_RELEASE_S, avail)
        segs = gap_segments(rms, sr, release_in_end, gap_in_end)
        breath_src = sum(e - s for s, e, k in segs if k == "breath")
        silence_src = sum(e - s for s, e, k in segs if k == "silence")
        need = max(0.0, avail - release_tgt)
        is_last = i + 1 == len(syllables)
        breath_tgt = breath_src
        if is_last:
            if need >= core_src:
                if silence_src > 0.0:
                    core_tgt, breath_tgt, silence_tgt = \
                        core_src, 0.0, need - core_src
                else:
                    core_tgt, breath_tgt, silence_tgt = need, 0.0, 0.0
            elif need >= core_src * CORE_CLAMP[0]:
                core_tgt, breath_tgt, silence_tgt = need, 0.0, 0.0
            else:
                core_tgt, breath_tgt, silence_tgt = \
                    core_src * CORE_CLAMP[0], 0.0, 0.0
                if core_src > 0.0:
                    clamps.append({"out_s": core_out_start,
                                   "requested_rate": need / core_src,
                                   "clamped_rate": CORE_CLAMP[0]})
                if need < core_tgt:
                    warnings.append(f"syllable {i}: tail gap too tight")
        elif silence_src > 0.0 and need >= breath_src + core_src:
            core_tgt = core_src
            silence_tgt = need - breath_src - core_src
        elif need >= breath_src + core_src * CORE_CLAMP[0]:
            silence_tgt = 0.0
            core_tgt = need - breath_src
        else:
            core_tgt = core_src * CORE_CLAMP[0]
            breath_tgt = breath_src * BREATH_RANGE[0]
            silence_tgt = 0.0
            if core_src > 0.0:
                clamps.append({"out_s": core_out_start,
                               "requested_rate": max(0.0, need - breath_src)
                               / core_src,
                               "clamped_rate": CORE_CLAMP[0]})
            if need < core_tgt + breath_tgt:
                warnings.append(f"syllable {i}: gap too tight")
        if core_src <= 0.0:
            if core_tgt > 0.0:
                warnings.append(f"syllable {i}: zero-length core dropped")
            core_tgt = 0.0
        required_rate = core_tgt / core_src if core_src > 0.0 else 1.0
        clamped_rate = required_rate
        if core_src > 0.0:
            if required_rate < CORE_CLAMP[0]:
                clamped_rate = CORE_CLAMP[0]
            elif required_rate > CORE_CLAMP[1]:
                clamped_rate = CORE_CLAMP[1]
            if abs(clamped_rate - required_rate) > 1e-9:
                clamps.append({"out_s": core_out_start,
                               "requested_rate": required_rate,
                               "clamped_rate": clamped_rate})
        rates.append(required_rate)
    return rates, clamps, warnings


def interpolate_coarse(score_starts: list[float],
                       measured: list[float | None]) -> list[float]:
    """Fill None measured onsets by linear interpolation in score->take
    space between nearest measured neighbors; edges flat-extrapolate
    (numpy.interp clamps to end values). Mirrors the pilot TSV build."""
    xs = np.array(score_starts, dtype=float)
    ys = np.array([m if m is not None else np.nan for m in measured],
                  dtype=float)
    ok = ~np.isnan(ys)
    if not ok.any():
        return [0.0] * len(measured)
    return [float(v) for v in np.interp(xs, xs[ok], ys[ok])]


def line_gate(x: np.ndarray, sr: int, notes: list[dict],
              coarse: list[float], prev_coarse: float | None,
              next_coarse: float | None, take_dur_s: float) -> dict:
    """Full driver pipeline for one line; notes are sung notes only.

    notes: [{"start": score start_s, "dur": score dur_s}] in score order.
    coarse: take-time coarse onset per note (already interpolated).
    prev_coarse: take-time coarse of the note before the line (None for
    the song's first note); next_coarse: same after the line (None for
    the song's last note). take_dur_s: take length in seconds.
    Returns the gate verdict dict (pass, ratio, rates, per-note rows,
    drop/relax/collision flags) used by the retry endpoint.
    """
    n = len(notes)
    if n == 0:
        return {"pass": True, "line_ratio": 1.0, "ratio_source": "none",
                "in_range_fraction": 1.0, "out_of_hard_count": 0,
                "clamps": [], "failures": ["no sung notes in range"],
                "notes": [], "tiling_dropped": [], "bound_relaxed": [],
                "collision_dropped": [], "pass2_dropped": [],
                "warnings": []}
    base = notes[0]["start"]
    score_vops = [nt["start"] - base for nt in notes]
    out_end = (notes[-1]["start"] + notes[-1]["dur"]) - base
    segment_end = next_coarse if next_coarse is not None else take_dur_s
    tail_in_end = segment_end
    bounds = []
    for i in range(n):
        prev_b = coarse[i - 1] if i > 0 else (
            prev_coarse if prev_coarse is not None else coarse[0] - 0.2)
        next_b = coarse[i + 1] if i + 1 < n else segment_end
        bounds.append((prev_b, next_b))
    refined = [refine_note(x, sr, coarse[i], bounds[i][0], bounds[i][1])
               for i in range(n)]
    collision_dropped: list[int] = []
    last_pos: int | None = None
    for pos in range(n):
        if not refined[pos]["anchored"]:
            continue
        if last_pos is not None:
            last, cur = refined[last_pos], refined[pos]
            if abs(cur["vop"] - last["vop"]) < 0.010:
                if abs(cur["vop"] - coarse[pos]) > abs(last["vop"]
                                                       - coarse[last_pos]):
                    refined[pos]["anchored"] = False
                    collision_dropped.append(pos)
                else:
                    refined[last_pos]["anchored"] = False
                    collision_dropped.append(last_pos)
                    last_pos = pos
                continue
        last_pos = pos
    bound_relaxed: list[int] = []
    pass2_dropped: list[int] = []
    prev_kept: float | None = None
    for i in range(n):
        note = refined[i]
        expanded = note["anchored"] and abs(note["vop"] - coarse[i]) > 0.080
        if expanded:
            loose = bounds[i][0]
            strict = max(prev_kept + MIN_CORE_S, loose) \
                if prev_kept is not None else loose
            new_note = refine_note(x, sr, coarse[i], strict, bounds[i][1])
            relaxed = False
            if not new_note["anchored"] and strict > loose:
                retry = refine_note(x, sr, coarse[i], loose, bounds[i][1])
                if retry["anchored"]:
                    new_note, relaxed = retry, True
            if new_note["anchored"]:
                if relaxed:
                    bound_relaxed.append(i)
                refined[i] = new_note
            else:
                refined[i]["anchored"] = False
                pass2_dropped.append(i)
        if refined[i]["anchored"]:
            prev_kept = refined[i]["vop"]
    anchored_idx = [i for i in range(n) if refined[i]["anchored"]]
    if not anchored_idx:
        return {"pass": False, "line_ratio": 0.0, "ratio_source": "none",
                "in_range_fraction": 0.0, "out_of_hard_count": 0,
                "clamps": [],
                "failures": ["no anchored notes"],
                "notes": [_note_row(i, notes, coarse, refined, None, None)
                          for i in range(n)],
                "tiling_dropped": [], "bound_relaxed": bound_relaxed,
                "collision_dropped": collision_dropped,
                "pass2_dropped": pass2_dropped, "warnings": []}
    tiling_dropped: list[int] = []
    syllables: list[tuple[float, float, float]] = []
    kept: list[int] = []
    for pos, i in enumerate(anchored_idx):
        off_limit = refined[anchored_idx[pos + 1]]["pre"] - LOCK_PRE_S \
            if pos + 1 < len(anchored_idx) else tail_in_end
        off_c = min(refined[i]["off"], off_limit)
        if off_c - refined[i]["vop"] < MIN_CORE_S:
            tiling_dropped.append(i)
            continue
        syllables.append((refined[i]["pre"], refined[i]["vop"], off_c))
        kept.append(i)
    if not kept:
        return {"pass": False, "line_ratio": 0.0, "ratio_source": "none",
                "in_range_fraction": 0.0, "out_of_hard_count": 0,
                "clamps": [],
                "failures": ["no anchored notes after tiling"],
                "notes": [_note_row(i, notes, coarse, refined, None, None,
                                    i in tiling_dropped)
                          for i in range(n)],
                "tiling_dropped": tiling_dropped,
                "bound_relaxed": bound_relaxed,
                "collision_dropped": collision_dropped,
                "pass2_dropped": pass2_dropped, "warnings": []}
    targets = [score_vops[i] for i in kept]
    rms = rms_curve(x, sr)
    rates, clamps, warnings = build_core_rates(syllables, targets, out_end,
                                               tail_in_end, rms, sr)
    first_final = 0 not in tiling_dropped and refined[0]["anchored"]
    last_final = (n - 1) not in tiling_dropped and refined[n - 1]["anchored"]
    if first_final and last_final:
        sung_span = max(1e-9, syllables[-1][2] - syllables[0][1])
        score_span = max(1e-9, out_end - score_vops[0])
        ratio_source = "anchored"
    else:
        sung_end = syllables[-1][2] if last_final else bounds[-1][1]
        sung_span = max(1e-9, sung_end - coarse[0])
        score_span = max(1e-9, out_end - score_vops[0])
        ratio_source = "segment"
    line_ratio = score_span / sung_span
    in_range = sum(1 for r in rates if GATE_BAND[0] <= r <= GATE_BAND[1])
    fraction = in_range / len(rates) if rates else 0.0
    out_of_hard = sum(1 for r in rates
                      if r < GATE_HARD[0] or r > GATE_HARD[1])
    failures: list[str] = []
    if not (GATE_RATIO_RANGE[0] <= line_ratio <= GATE_RATIO_RANGE[1]):
        failures.append(f"line ratio {line_ratio:.4f} outside "
                        f"[{GATE_RATIO_RANGE[0]}, {GATE_RATIO_RANGE[1]}]")
    if fraction < GATE_FRACTION_MIN:
        failures.append(f"only {fraction:.3f} of voiced cores in "
                        f"[{GATE_BAND[0]}, {GATE_BAND[1]}], need "
                        f">= {GATE_FRACTION_MIN}")
    if out_of_hard:
        failures.append(f"{out_of_hard} voiced cores outside "
                        f"[{GATE_HARD[0]}, {GATE_HARD[1]}]")
    if clamps:
        failures.append("render clamps present, retry required")
    rate_by_note = dict(zip(kept, rates))
    off_used_by_note = {kept[i]: syllables[i][2]
                        for i in range(len(kept))}
    dropped_set = set(tiling_dropped)
    rows = [_note_row(i, notes, coarse, refined, rate_by_note.get(i),
                      off_used_by_note.get(i), i in dropped_set)
            for i in range(n)]
    return {"pass": not failures, "line_ratio": round(line_ratio, 4),
            "ratio_source": ratio_source,
            "in_range_fraction": round(fraction, 4),
            "out_of_hard_count": out_of_hard, "clamps": clamps,
            "failures": failures, "notes": rows,
            "tiling_dropped": tiling_dropped,
            "bound_relaxed": bound_relaxed,
            "collision_dropped": collision_dropped,
            "pass2_dropped": pass2_dropped, "warnings": warnings}


def _note_row(i: int, notes, coarse, refined, rate, off_used,
              tiling_dropped: bool = False) -> dict:
    r = refined[i]
    return {"index": i, "score_start": notes[i]["start"],
            "coarse": round(coarse[i], 4),
            "anchored": bool(r["anchored"] and not tiling_dropped),
            "pre": round(r["pre"], 4), "vop": round(r["vop"], 4),
            "off": round(r["off"], 4),
            "off_used": None if off_used is None else round(off_used, 4),
            "confidence": round(r["confidence"], 3),
            "f0": round(r["f0"], 1), "hnr": round(r["hnr"], 2),
            "core_required_rate": None if rate is None else round(rate, 4)}
