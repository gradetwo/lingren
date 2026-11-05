"""边界感知重测仪器（vocaledit 结构性修复接线）。

来源：离线实验 ``pernote_warp_v2.py``（三路复合候选检测器）与
``pernote_warp_pivot.py``（边界感知重测＋仪器对照验证），经三轮离线
验证。证据见 ``evidence/``。
本模块只含重测仪器，不含任何 warp 路线代码（无 DP 匹配、无 Tier-1
筛选、无亚行段构造），不碰 warp_harness 配置、Custom2400_480 与
Q28 标准，不新增、不改动任何服务端点。

为什么不用 v1 式重测：v1 式重测（FCPE 浊音 onset 锚法＋通用 flux 峰）
要求浊音断裂，而连唱边界处浊音连续，故对连唱锚点是盲的（v2 重测
FCPE 中位 290ms 即为此盲区所致）。本仪器用三路检测器在 warp 后音
频上重检边界，与乐谱位置比对，并以音中点对照验证仪器本身不放水。

接口约定：本模块不跑 FCPE、不读模型。``f0``（100fps 浊音帧数组，
无浊音处为 NaN 或 ≤0）由调用方提供；检测器与重测均为纯函数。
"""

import bisect

import numpy as np

# ---- 检测器参数（v2 实测定稿） ----
V_MIN_RUN = 7            # V-Onset 最小浊音帧（70ms）
V_PRE_UNVOICED = 2       # V-Onset 前置无浊音帧（20ms）
V_REFINE_MS = 40         # V-Onset 精修回搜窗口（ms）
V_REFINE_RATIO = 1.5     # 精修峰须 ≥1.5×起点处 flux 才采纳，否则保留原点
STEP_MIN_CENTS = 65      # F0 阶跃门限（cents）
STEP_POST_MAD = 25       # 阶跃后窗口 MAD 上限（cents）
STEP_SIGN_MIN = 30       # 前后 120ms 均值差同号且幅度下限（cents）
FLUX_BAND = (35, 257)    # 800–6000Hz：bin=f×2048/48000，35→820Hz，256→6000Hz
FLUX_PROM = 0.20         # 高频 Flux 归一化 prominence 下限
DIP_DB = 3.0             # RMS 凹陷深度下限（dB）
DIP_RUN = (2, 10)        # 凹陷持续帧数（20–100ms）
MERGE_MS = 80            # 候选归并邻域（ms）
CUE_PRIO = {"V-Onset": 0, "F0-Step": 1, "Flux-Dip": 2}
# ---- 重测参数（pivot 实测定稿） ----
REMATCH_WIN = 0.50       # 最近候选搜索窗（s）
HIT_WIN = 0.05           # 命中窗 ±50ms
CTRL_FP_MAX = 0.10       # 对照误检率上限（音中点）
ANCHOR_HIT_MIN = 0.70    # 仪器有效性：锚点命中率下限（±50ms 内占比）


def stft_mag(x, nperseg=2048, hop=480):
    """幅度谱（vr-warp-validation measure.py 原样移植）：Hann 窗，rfft。

    返回 ``(1025, n_frames)``。输入短于一窗时返回 ``(1025, 0)`` 空谱
    （防御性处理，离线实现在此输入上会报错；正常输入行为不变）。
    """
    x = np.asarray(x, dtype=np.float32)
    win = np.hanning(nperseg).astype(np.float32)
    n = (len(x) - nperseg) // hop + 1
    if n <= 0:
        return np.zeros((nperseg // 2 + 1, 0), dtype=np.float32)
    mags = []
    for s in range(0, n, 1024):
        e = min(s + 1024, n)
        idx = (np.arange(s, e)[:, None] * hop + np.arange(nperseg)[None, :])
        frames = x[idx] * win[None, :]
        mags.append(np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32))
    return np.concatenate(mags, axis=0).T


def rms_curve(x, win=1024, hop=480):
    """短时 RMS 曲线（measure.py 原样移植）：10ms 网格。"""
    x = np.asarray(x, dtype=np.float64)
    cs = np.concatenate([[0.0], np.cumsum(x ** 2)])
    n = (len(x) - win) // hop + 1
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    idx = np.arange(n) * hop
    return np.sqrt(np.maximum((cs[idx + win] - cs[idx]) / win, 0.0))


def cents_from_f0(f0):
    """f0（Hz）转连续 MIDI 音分（cents＝100×midi；无浊音处为 NaN）。"""
    c = np.full(len(f0), np.nan)
    v = np.isfinite(f0) & (f0 > 0)
    c[v] = 1200.0 * np.log2(f0[v] / 440.0) + 6900.0
    return c


def median_filter_nan(c, k=5):
    """滚动中值滤波（忽略 NaN，窗口内有效点 <3 则 NaN）。"""
    n, h, out = len(c), k // 2, np.full(len(c), np.nan)
    for i in range(n):
        w = c[max(0, i - h):min(n, i + h + 1)]
        w = w[np.isfinite(w)]
        if len(w) >= 3:
            out[i] = np.median(w)
    return out


def band_flux(x48):
    """谱通量（v2 原样移植）：10ms 网格，与 FCPE 帧对齐。

    返回 ``(full_flux, band_flux_norm, n_frames)``；band 为 800–6000Hz
    对数梅尔正半波谱通量，按 p99 归一化。
    """
    mag = stft_mag(x48)
    if mag.shape[1] < 2:
        return np.zeros(1), np.zeros(1), 1
    full = np.concatenate(
        [[0], np.sum(np.maximum(np.diff(mag, axis=1), 0), axis=0)])
    band = np.concatenate([[0], np.sum(np.maximum(
        np.diff(np.log(mag[FLUX_BAND[0]:FLUX_BAND[1]] + 1e-6), axis=1),
        0), axis=0)])
    return full, band / (np.percentile(band, 99) + 1e-12), len(full)


def pick_peaks_prom(strength, prom_min, distance=15):
    """简易峰拾取（v2 原样移植）：局部极大＋地形 prominence＋最小间距。"""
    n = len(strength)
    cand = []
    for i in range(1, n - 1):
        if strength[i] >= strength[i - 1] and strength[i] > strength[i + 1]:
            lo = max(0, i - distance)
            hi = min(n, i + distance + 1)
            prom = strength[i] - max(np.min(strength[lo:i + 1]),
                                     np.min(strength[i:hi]))
            if prom >= prom_min:
                cand.append((i, strength[i], prom))
    cand.sort(key=lambda t: -t[1])
    kept, out = [], []
    for i, s, p in cand:
        if all(abs(i - j) > distance for j in kept):
            kept.append(i)
            out.append(i)
    return sorted(out)


def detect_v_onsets(f0, full_flux):
    """V-Onset 路（v2 原样移植）：浊音 run ≥7 帧且前置 ≥2 帧无浊音；
    起点前 40ms 内取谱 Flux 最大值精修。返回 ``[(t, "V-Onset", 1.0)]``
    与精修计数。"""
    voiced = np.isfinite(f0) & (f0 > 0)
    onsets, refined = [], 0
    i, n = 0, len(voiced)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            pre_ok = (i == 0) or (np.sum(~voiced[max(0, i - V_PRE_UNVOICED):i])
                                  >= V_PRE_UNVOICED)
            if j - i >= V_MIN_RUN and pre_ok:
                t = i / 100.0
                fi = min(i, len(full_flux) - 1)
                lo = max(0, fi - V_REFINE_MS // 10)
                seg = full_flux[lo:fi + 1]
                if len(seg) and seg.max() >= V_REFINE_RATIO * max(full_flux[fi], 1e-12) \
                        and seg.max() > 0:
                    t = (lo + int(np.argmax(seg))) / 100.0
                    refined += 1
                onsets.append((t, "V-Onset", 1.0))
            i = j
        else:
            i += 1
    return onsets, refined


def detect_f0_steps(f0):
    """F0-Step 路（v2 原样移植）：5 帧中值滤波后，双窗对称阶跃算子
    （保护间隔各 2 帧），抑制 5–7Hz 颤音；阶跃 ≥65 音分、阶跃后平台
    MAD ≤25 音分、前后 120ms 均值差同号且 ≥30 音分才触发。
    返回 ``[(t, "F0-Step", |delta_cents|)]``。"""
    c = median_filter_nan(cents_from_f0(f0), 5)
    n = len(c)
    delta = np.full(n, np.nan)
    for t in range(12, n - 13):
        pre = c[t - 11:t - 2]
        post = c[t + 3:t + 12]
        pre = pre[np.isfinite(pre)]
        post = post[np.isfinite(post)]
        if len(pre) >= 6 and len(post) >= 6:
            delta[t] = np.median(post) - np.median(pre)
    out = []
    for t in range(12, n - 13):
        if not np.isfinite(delta[t]) or abs(delta[t]) < STEP_MIN_CENTS:
            continue
        neigh = delta[max(0, t - 3):t + 4]
        if abs(delta[t]) < np.nanmax(np.abs(neigh)):
            continue  # 非局部极大
        post = c[t + 3:t + 12]
        post = post[np.isfinite(post)]
        if len(post) < 6 or np.median(np.abs(post - np.median(post))) > STEP_POST_MAD:
            continue
        m_pre = c[t - 12:t]
        m_post = c[t + 1:t + 13]
        m_pre = m_pre[np.isfinite(m_pre)]
        m_post = m_post[np.isfinite(m_post)]
        if len(m_pre) < 6 or len(m_post) < 6:
            continue
        d120 = np.mean(m_post) - np.mean(m_pre)
        if np.sign(d120) != np.sign(delta[t]) or abs(d120) < STEP_SIGN_MIN:
            continue
        best_tau, best_slope = t, -1.0
        for tau in range(max(1, t - 3), min(n - 1, t + 3) + 1):
            if np.isfinite(c[tau + 1]) and np.isfinite(c[tau - 1]):
                s = abs(c[tau + 1] - c[tau - 1])
                if s > best_slope:
                    best_slope, best_tau = s, tau
        out.append((best_tau / 100.0, "F0-Step", abs(delta[t])))
    return out


def detect_flux_dips(x48, band_norm, f0):
    """Flux-Dip 路（v2 原样移植）：高频 Flux 峰（prom≥0.20）＋RMS 凹陷
    ≥3dB 持续 20–100ms，限浊音段内。返回 ``[(t, "Flux-Dip", 凹陷深度)]``。"""
    voiced = np.isfinite(f0) & (f0 > 0)
    rms = rms_curve(x48)
    edb = 20.0 * np.log10(rms + 1e-6)
    n = min(len(edb), len(band_norm), len(voiced))
    d = np.full(n, 0.0)
    for t in range(6, n - 6):
        d[t] = min(np.mean(edb[t - 6:t - 2]), np.mean(edb[t + 2:t + 6])) - edb[t]
    peaks = set(pick_peaks_prom(band_norm[:n], FLUX_PROM, distance=15))
    out, i = [], 0
    while i < n:
        if d[i] >= DIP_DB:
            j = i
            while j < n and d[j] >= DIP_DB:
                j += 1
            run = j - i
            if DIP_RUN[0] <= run <= DIP_RUN[1]:
                t0 = i + int(np.argmax(d[i:j]))
                if any(abs(t0 - p) <= 3 for p in peaks):
                    lo = max(0, t0 - 10)
                    hi = min(n, t0 + 11)
                    if np.mean(voiced[lo:hi]) >= 0.5:
                        out.append((t0 / 100.0, "Flux-Dip", float(d[t0])))
            i = j
        else:
            i += 1
    return out


def merge_candidates(all_cands, f0):
    """80ms 邻域归并（v2 原样移植）：优先级 V-Onset→F0-Step→Flux-Dip；
    同优先级取强度大者。输出 ``(t_k, cue, m_hat, m_pre)``，midi＝cents/100；
    m_hat 为 [t+20ms, t+100ms] 内浊音帧中位 MIDI，m_pre 为
    [t-100ms, t-20ms] 内浊音帧中位 MIDI。"""
    cents = cents_from_f0(f0)
    voiced = np.isfinite(f0) & (f0 > 0)

    def midi_med(t_lo, t_hi):
        a = max(0, int(round(t_lo * 100)))
        b = min(len(cents), int(round(t_hi * 100)))
        seg = cents[a:b]
        seg = seg[np.isfinite(seg) & voiced[a:b]]
        return float(np.median(seg) / 100.0) if len(seg) >= 3 else None

    order = sorted(all_cands, key=lambda c: c[0])
    clusters = []
    for t, cue, strength in order:
        if clusters and t - clusters[-1][0][0] <= MERGE_MS / 1000.0:
            clusters[-1].append((t, cue, strength))
        else:
            clusters.append([(t, cue, strength)])
    merged = []
    for cl in clusters:
        win = min(cl, key=lambda c: (CUE_PRIO[c[1]], -c[2]))
        t, cue, _ = win
        merged.append((t, cue, midi_med(t + 0.02, t + 0.10),
                       midi_med(t - 0.10, t - 0.02)))
    return merged


def detect_all(x48, f0):
    """三路检测器（pivot 原样移植）：返回
    ``(merged, raw, full_flux, band_norm)``，raw 按 cue 分组。"""
    full_flux, band_norm, _ = band_flux(x48)
    v_on, _ = detect_v_onsets(f0, full_flux)
    f_steps = detect_f0_steps(f0)
    f_dips = detect_flux_dips(x48, band_norm, f0)
    merged = merge_candidates(v_on + f_steps + f_dips, f0)
    raw = {"V-Onset": [t for t, _, _ in v_on],
           "F0-Step": {round(t, 3): s for t, _, s in f_steps},
           "Flux-Dip": [t for t, _, _ in f_dips]}
    return merged, raw, full_flux, band_norm


def _res_stats(res_ms):
    """残差统计（signed ms 数组；为空则 None）。"""
    if len(res_ms) == 0:
        return {"matched": 0, "median_ms": None, "p90_ms": None,
                "le25_share": None, "le50_share": None,
                "median_signed_ms": None}
    a = np.abs(res_ms)
    return {"matched": int(len(res_ms)),
            "median_ms": round(float(np.median(a)), 1),
            "p90_ms": round(float(np.percentile(a, 90)), 1),
            "le25_share": round(float(np.mean(a <= 25)), 4),
            "le50_share": round(float(np.mean(a <= 50)), 4),
            "median_signed_ms": round(float(np.median(res_ms)), 1)}


def boundary_remeasure(candidates, notes, anchor_set=frozenset()):
    """边界感知重测（pivot 原样移植）：warp 后音频的三路候选边界与乐
    谱位置比对。每音取 ±0.5s 内最近候选，残差＝候选－乐谱位置。

    candidates：``[(t, cue, m_hat, m_pre)]``；notes：``{"start_s": ...}``
    列表；anchor_set：另行统计的锚点音下标集合（可为空；为空时
    anchored 为空，interpolated 即全体）。分锚点音／未打锚音统计，
    并按候选 cue 分列（Flux-Dip 可能受接头淡化影响，F0-Step＋
    V-Onset 为接头免疫对照）。
    """
    times = np.array(sorted(t for t, _, _, _ in candidates))
    cue_of = {round(t, 6): cue for t, cue, _, _ in candidates}
    per_note = []
    for idx, nt in enumerate(notes):
        exp = nt["start_s"]
        i = bisect.bisect_left(times, exp)
        best, best_d = None, None
        for j in (i - 1, i):
            if 0 <= j < len(times):
                d = abs(float(times[j]) - exp)
                if d <= REMATCH_WIN and (best_d is None or d < best_d):
                    best, best_d = float(times[j]), d
        if best is None:
            per_note.append({"note": idx, "res_ms": None, "cue": None})
        else:
            per_note.append({"note": idx, "res_ms": round((best - exp) * 1000, 1),
                             "cue": cue_of.get(round(best, 6))})
    anchored = [p for p in per_note if p["note"] in anchor_set and p["res_ms"] is not None]
    interp = [p for p in per_note if p["note"] not in anchor_set and p["res_ms"] is not None]
    strict_a = [p for p in anchored if p["cue"] in ("F0-Step", "V-Onset")]
    strict_i = [p for p in interp if p["cue"] in ("F0-Step", "V-Onset")]
    by_cue = {}
    for cue in ("V-Onset", "F0-Step", "Flux-Dip"):
        rs = np.array([p["res_ms"] for p in per_note
                       if p["cue"] == cue and p["res_ms"] is not None])
        by_cue[cue] = _res_stats(rs)
    return {
        "anchored": _res_stats(np.array([p["res_ms"] for p in anchored])),
        "interpolated": _res_stats(np.array([p["res_ms"] for p in interp])),
        "overall": _res_stats(np.array([p["res_ms"] for p in per_note
                                        if p["res_ms"] is not None])),
        "anchored_strict_f0_vonset": _res_stats(
            np.array([p["res_ms"] for p in strict_a])),
        "interpolated_strict_f0_vonset": _res_stats(
            np.array([p["res_ms"] for p in strict_i])),
        "by_cue": by_cue,
        "unmatched_notes": sum(1 for p in per_note if p["res_ms"] is None),
        "per_note": per_note,
    }


def instrument_control(candidates, notes, warped_dur_s):
    """仪器对照验证（pivot 原样移植）：音中点（音符内、无边界）±50ms
    内的候选命中率；理论随机率＝候选密度×0.1s 窗。误检率高则仪器
    放水，残差无意义。音符最短 0.3s，中点距边界 ≥0.15s，±50ms 窗
    不与真边界窗重叠，控制干净。"""
    times = np.array(sorted(t for t, _, _, _ in candidates))
    hits = 0
    for nt in notes:
        mid = nt["start_s"] + nt["dur_s"] / 2.0
        i = bisect.bisect_left(times, mid)
        near = False
        for j in (i - 1, i):
            if 0 <= j < len(times) and abs(float(times[j]) - mid) <= HIT_WIN:
                near = True
                break
        hits += 1 if near else 0
    fp = hits / len(notes) if notes else None
    expected = len(times) / warped_dur_s * 0.1 if warped_dur_s else None
    return {"midpoint_n": len(notes), "midpoint_hits": hits,
            "midpoint_fp_rate": round(fp, 4) if fp is not None else None,
            "expected_random_rate": round(expected, 4)
            if expected is not None else None,
            "candidates_per_sec": round(len(times) / warped_dur_s, 3)
            if warped_dur_s else None,
            "gate_fp_max": CTRL_FP_MAX}


def validate_instrument(control, anchored_stats):
    """仪器有效性判定（pivot 原样移植）：对照误检率 ≤10% 且锚点
    ±50ms 内命中占比 ≥70%，否则重测残差无意义。"""
    fp_ok = (control["midpoint_fp_rate"] is not None
             and control["midpoint_fp_rate"] <= CTRL_FP_MAX)
    le50 = anchored_stats.get("le50_share") if anchored_stats else None
    hit_ok = le50 is not None and le50 >= ANCHOR_HIT_MIN
    return {"fp_ok": bool(fp_ok), "hit_ok": bool(hit_ok),
            "valid": bool(fp_ok and hit_ok),
            "gate_fp_max": CTRL_FP_MAX, "gate_hit_min": ANCHOR_HIT_MIN}
