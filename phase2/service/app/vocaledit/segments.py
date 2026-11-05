"""行间缺口一级切点与换气拆分（vocaledit 结构性修复接线）。

来源：离线实验 ``pernote_warp_v2.py``（拆分逻辑）与
``pernote_warp_pivot.py``（缺口逻辑），经三轮离线验证（v2／Pivot／Tier-1
重设计＋v2 可信重测）。证据见 ``evidence/``。
本模块只含已验证的结构性修复，不含任何 warp 路线代码（无 DP 匹配、无
Tier-1 筛选、无亚行段构造），不碰 warp_harness 配置、Custom2400_480
与 Q28 标准，不新增、不改动任何服务端点。

两条结构性规则（v2 缺陷的根因）：
1. 行间缺口与行内换气分开处理：行间缺口 take→乐谱全长映射为 gap-pad
   段，不压、不拉；≤0.5 秒垫段规则只适用于行内换气。v2 曾把行间缺口
   当行内换气喂给 :func:`split_segment`，致 seg 81／82 缺陷：10 秒
   行间空白压成 0.5 秒垫段、0.56 秒歌唱拉 20 倍到 11.2 秒乐谱跨度
   （证据：``evidence/pernote_v2remeasure_report.json`` 的
   ``seg81_82_fix``；修后 gap14：take 9.97 秒→乐谱 10.8 秒，注 108
   歌唱 0.56 秒→0.9 秒）。
2. 垫段输出恰为目标长（截断／补零 take 原音频，非合成静音）；等长切
   点淡化（接头不缩短总长）；换气时间用 take 时间（静音段在 take 音
   频上实测，直接用，不经乐谱线性换算）；16k 转换不读缓存（本模块
   :func:`to_16k` 为数组进出，无文件、无缓存可读）。

时间基：全部时间均为 take 时间，除非名字里写明 score。
"""

import math

import numpy as np
from scipy.signal import resample_poly

# ---- 拆分参数（v2 实测定稿） ----
SPLIT_OVERLAP = 0.20   # 有效重叠即拆分（s）
VOCAL_HEAD = 0.08      # 换气前至少保留歌唱音头（s）
TAIL_REM = 0.05        # 尾段 <50ms 不生成碎尾段
PAD_MAX = 0.50         # 垫段目标长上限（s），只适用于行内换气
PAD_FRAC = 0.45        # 垫段不超乐谱跨度 45%
SIL_FORCE_DUR = 0.25   # 强制切点静音时长（s）：调用方收集静音段时，
                       # ≥0.25s 的静音段与行间空白同为一级切点
CROSSFADE_N = 240      # 等长切点淡化长度（采样，48kHz 下 5ms）


def split_segment(seg, spans):
    """单段迭代拆分（v2 原样移植）。

    seg：``{"take": (t0, t1), "score": (s0, s1), "note": k, "mode": "warp"}``；
    spans：行内静音段 ``[(a, b)]``，take 时间。注意：行间缺口不得出现
    在 spans 里（见模块 docstring 规则 1），行间缺口走
    :func:`build_line_gaps`。
    返回 ``(段列表, 拆分次数)``（warp 段已递归拆尽，pad 段不再拆）。

    拆分规则：有效重叠 ≥0.20s 且换气前歌唱音头 ≥0.08s 即拆分；垫段目标
    长 ``min(重叠, 0.50s, 0.45×乐谱跨度)``，剩余跨度按歌唱子段有效时
    长比例分配；尾段 <0.05s 时不生成碎尾段（换气直抵下一音 onset）。
    """
    out, work = [], [seg]
    n_split = 0
    while work:
        sg = work.pop(0)
        if sg["mode"] != "warp":
            out.append(sg)
            continue
        (t0, t1), (s0, s1) = sg["take"], sg["score"]
        hit = None
        for a, b in spans:
            a_clip, b_clip = max(a, t0), min(b, t1)
            overlap = b_clip - a_clip
            if overlap >= SPLIT_OVERLAP and (a_clip - t0) >= VOCAL_HEAD:
                hit = (a_clip, b_clip, overlap)
                break
        if hit is None:
            out.append(sg)
            continue
        n_split += 1
        a_clip, b_clip, overlap = hit
        score_span = s1 - s0
        pad_target = min(overlap, PAD_MAX, PAD_FRAC * score_span)
        vocal_rem = score_span - pad_target
        tail = t1 - b_clip
        if tail < TAIL_REM:
            # 换气直抵下一音 onset：[歌唱段，换气垫段]，垫段 src 取 (a_clip, t1)
            out.append({"take": (t0, a_clip), "score": (s0, s0 + vocal_rem),
                        "note": sg["note"], "mode": "warp"})
            out.append({"take": (a_clip, t1), "score": (s0 + vocal_rem, s1),
                        "note": sg["note"], "mode": "pad"})
        else:
            v1_dur = a_clip - t0
            v1_score = vocal_rem * (v1_dur / (v1_dur + tail))
            work.insert(0, {"take": (b_clip, t1),
                            "score": (s0 + v1_score + pad_target, s1),
                            "note": sg["note"], "mode": "warp"})
            out.append({"take": (t0, a_clip), "score": (s0, s0 + v1_score),
                        "note": sg["note"], "mode": "warp"})
            out.append({"take": (a_clip, b_clip),
                        "score": (s0 + v1_score, s0 + v1_score + pad_target),
                        "note": sg["note"], "mode": "pad"})
    return out, n_split


def map_line_gap(take_gap, score_gap, note=None):
    """行间缺口全长映射为 gap-pad 段（pivot 原样移植）。

    take_gap：``(t0, t1)`` take 时间行间空白；score_gap：``(s0, s1)``
    乐谱时间对应空白。不压、不拉：垫段目标长即乐谱跨度全长。
    返回 gap-pad 段 dict（mode 为 ``"gap-pad"``）。
    """
    t0, t1 = take_gap
    s0, s1 = score_gap
    if t1 < t0:
        raise ValueError(f"line gap take range negative: {(t0, t1)}")
    if s1 <= s0:
        raise ValueError(f"line gap score span non-positive: {(s0, s1)}")
    return {"take": (t0, t1), "score": (s0, s1),
            "note": note, "mode": "gap-pad"}


def build_line_gaps(line_take_bounds, line_score_bounds, line_last_notes=None):
    """逐行间缺口构造 gap-pad 段（pivot 原样移植）。

    line_take_bounds／line_score_bounds：逐行 ``(起, 止)`` 列表，行 take
    边界取实测（行起 onset、歌唱体末尾 sung_end），行乐谱边界取乐谱
    行起止。返回 ``(gap-pad 段列表, 缺口记录列表)``；缺口记录含 line／
    take／score／take_s／score_s（与 pivot 报告的 line_gaps 同形）。
    """
    if len(line_take_bounds) != len(line_score_bounds):
        raise ValueError("line take/score bounds length mismatch")
    last_notes = line_last_notes or [None] * len(line_take_bounds)
    segs, gaps = [], []
    for li in range(len(line_take_bounds) - 1):
        t0, t1 = line_take_bounds[li][1], line_take_bounds[li + 1][0]
        s0, s1 = line_score_bounds[li][1], line_score_bounds[li + 1][0]
        segs.append(map_line_gap((t0, t1), (s0, s1), note=last_notes[li]))
        gaps.append({"line": li, "take": [round(t0, 3), round(t1, 3)],
                     "score": [round(s0, 3), round(s1, 3)],
                     "take_s": round(t1 - t0, 3),
                     "score_s": round(s1 - s0, 3)})
    return segs, gaps


def render_pad(audio_slice, target_samples):
    """垫段渲染：take 原音频截断／补零至目标长（pivot／v2 原样移植）。

    切片长于目标时截断（静音截断无损，歌唱内容在两侧 warp 段内）；
    短于目标时补零（不是合成静音波形）。输出长度恰为 target_samples。
    """
    audio = np.asarray(audio_slice, dtype=np.float32)
    target_samples = int(target_samples)
    if len(audio) > target_samples:
        return audio[:target_samples].copy()
    if len(audio) < target_samples:
        pad = np.zeros(target_samples - len(audio), dtype=np.float32)
        return np.concatenate([audio, pad])
    return audio.copy()


def crossfade_join(a, b, n=CROSSFADE_N):
    """等长切点淡化（v2 原样移植，本地遮蔽 v1 的重叠式实现）。

    v1 以重叠方式交叉淡化，每接头缩短 240 采样（5ms），110 段累计
    -0.545s 系统性提前漂移，污染重测。本实现：前段尾 n 采样淡出、
    后段头 n 采样淡入后直接拼接，总长不变，时基精确；代价为接头处
    约 5ms 幅度谷（接头多在音符 onset 处，可接受）。
    """
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if len(a) < n or len(b) < n:
        return np.concatenate([a, b])
    t = np.arange(n) / n
    fade_out = 0.5 * (1 + np.cos(np.pi * t))
    fade_in = 0.5 * (1 - np.cos(np.pi * t))
    a = a.copy()
    b = b.copy()
    a[-n:] = a[-n:] * fade_out
    b[:n] = b[:n] * fade_in
    return np.concatenate([a, b])


def silence_spans(x48, sr=48000, thresh_ratio=0.05, min_len_s=0.15):
    """静音段检测（v1 原样移植）。

    RMS 曲线（hop 10ms，窗 20ms）低于 p95 的 5% 且持续 ≥150ms 的段。
    返回 take 时间 ``[(a, b)]``，直接用作 :func:`split_segment` 的
    spans（换气时间用 take 时间，不经乐谱线性换算——v2 曾误把 take
    时间当乐谱时间换算，已订正）。
    """
    hop = sr // 100
    win = sr // 50
    n = (len(x48) - win) // hop
    if n <= 0:
        return []
    rms = np.sqrt(np.mean(
        x48[np.arange(n)[:, None] * hop + np.arange(win)[None, :]] ** 2,
        axis=1))
    thr = thresh_ratio * np.percentile(rms, 95)
    quiet = rms < thr
    spans, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * hop / sr >= min_len_s:
                spans.append((i * hop / sr, j * hop / sr))
            i = j
        else:
            i += 1
    return spans


def to_16k(x48, sr=48000):
    """48kHz 单声道转 16kHz（数组进出，无文件、无缓存）。

    v2 的缓存陷阱：16k 转换输出已存在即跳过，致重测静默读了上一轮
    warp 的 16k。本函数只接受数组、只返回数组，不存在可读的缓存
    文件，调用方每次重算。48k→16k 为 3:1 抗混叠抽取。
    """
    x = np.asarray(x48, dtype=np.float32)
    if sr == 16000:
        return x.copy()
    g = math.gcd(sr, 16000)
    up, down = 16000 // g, sr // g
    return resample_poly(x, up, down).astype(np.float32)
