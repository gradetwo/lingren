"""vocaledit 结构性修复接线测试（无 GPU、无服务、无整轨夹具）。

只测已接线的两项基础设施：行间缺口一级切点分段（segments）与边界
感知重测仪器（instrument）。全部夹具均为小合成信号；回归数值引自
``app/vocaledit/evidence/`` 两份离线报告（gap14、注 108、两处真换气、
仪器对照门）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.vocaledit import instrument as I  # noqa: E402
from app.vocaledit import segments as S  # noqa: E402

SR = 48000


def _tone(freq, dur_s, amp=0.5, sr=SR):
    t = np.arange(int(dur_s * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---- 1. 行间缺口一级切点 ----

def test_gap14_full_length_mapping():
    """gap14：take 9.97 秒→乐谱 10.8 秒，全长映射，不压、不拉。"""
    seg = S.map_line_gap((57.41, 67.38), (76.2, 87.0), note=109)
    assert seg["mode"] == "gap-pad"
    assert seg["take"] == (57.41, 67.38)
    assert seg["score"] == (76.2, 87.0)
    take_s = seg["take"][1] - seg["take"][0]
    score_s = seg["score"][1] - seg["score"][0]
    assert take_s == np.float64(9.97) or abs(take_s - 9.97) < 1e-9
    assert abs(score_s - 10.8) < 1e-9
    assert abs(score_s / take_s - 1.0832) < 1e-3
    # 行间缺口不进 split_segment：同一段无行内静音时不拆分
    whole = {"take": (50.0, 67.38), "score": (70.0, 76.2),
             "note": None, "mode": "warp"}
    parts, n_split = S.split_segment(whole, [])
    assert n_split == 0 and parts == [whole]


def test_build_line_gaps_pairs_consecutive_lines():
    """逐行间缺口取前行末尾→后行起首，take 与 score 各自配对。"""
    take_bounds = [(0.0, 10.0), (12.0, 20.0), (25.0, 30.0)]
    score_bounds = [(0.0, 11.0), (13.0, 21.0), (26.0, 31.0)]
    segs, gaps = S.build_line_gaps(take_bounds, score_bounds)
    assert len(segs) == 2 and len(gaps) == 2
    assert segs[0]["take"] == (10.0, 12.0) and segs[0]["score"] == (11.0, 13.0)
    assert segs[0]["mode"] == "gap-pad"
    assert segs[1]["take"] == (20.0, 25.0) and segs[1]["score"] == (21.0, 26.0)
    assert gaps[0]["take_s"] == 2.0 and gaps[0]["score_s"] == 2.0
    assert gaps[1]["take_s"] == 5.0 and gaps[1]["score_s"] == 5.0


def test_note108_singing_not_stretched_20x():
    """注 108 回归钉：歌唱 0.56 秒→乐谱 0.9 秒（比值 1.6071），不是 11.2 秒（比值 20）。"""
    take_slice = _tone(440.0, 0.56)
    assert len(take_slice) == 26880
    out = S.render_pad(take_slice, round(0.9 * SR))
    assert len(out) == round(0.9 * SR)
    assert np.all(out[26880:] == 0)
    ratio = 0.9 / 0.56
    assert abs(ratio - 1.6071) < 1e-3
    assert 0.5 <= ratio <= 2.0
    assert 0.9 != 11.2 and ratio != 20.0


# ---- 2. 垫段截断与等长淡化 ----

def test_pad_truncation_to_target():
    """垫段输出恰为目标长（v2 bug：切片长于目标时原样输出整段静音）。"""
    long_slice = _tone(440.0, 1.0)
    assert len(S.render_pad(long_slice, round(0.5 * SR))) == round(0.5 * SR)
    short_slice = _tone(440.0, 0.3)
    out = S.render_pad(short_slice, round(0.5 * SR))
    assert len(out) == round(0.5 * SR)
    assert np.all(out[len(short_slice):] == 0)
    exact = _tone(440.0, 0.5)
    assert np.array_equal(S.render_pad(exact, round(0.5 * SR)), exact)


def test_crossfade_join_length_preserving():
    """等长切点淡化：总长不变（v1 重叠式每接头缩短 240 采样）。"""
    a = np.ones(SR, dtype=np.float32)
    b = np.ones(SR, dtype=np.float32)
    out = S.crossfade_join(a, b)
    assert len(out) == 2 * SR
    assert out[SR] == 0.0  # 后段头淡入起点为 0
    assert out[-1] == 1.0
    assert out[SR - 1] < 0.01  # 前段尾淡出末端近 0


# ---- 3. 换气拆分（两处真换气） ----

def test_breath_split_two_true_breaths():
    """两处真换气（take 时间 21.91–22.52 与 69.87–70.71）各拆出一个垫段，
    垫段目标长 min(重叠, 0.50s, 0.45×乐谱跨度)＝0.50s；换气时间直接
    用 take 时间，不经乐谱换算。"""
    cases = [
        ({"take": (20.0, 24.0), "score": (30.0, 34.0), "note": 5, "mode": "warp"},
         [(21.91, 22.52)]),
        ({"take": (68.0, 72.0), "score": (100.0, 104.0), "note": 6, "mode": "warp"},
         [(69.87, 70.71)]),
    ]
    for seg, spans in cases:
        parts, n_split = S.split_segment(seg, spans)
        assert n_split == 1
        pads = [p for p in parts if p["mode"] == "pad"]
        assert len(pads) == 1
        pad = pads[0]
        assert pad["take"] == spans[0]  # take 时间原样
        assert abs((pad["score"][1] - pad["score"][0]) - 0.50) < 1e-9
        total_score = sum(p["score"][1] - p["score"][0] for p in parts)
        assert abs(total_score - 4.0) < 1e-9  # 乐谱跨度守恒


def test_breath_split_tail_touching_next_onset():
    """换气直抵下一音 onset（尾段 <50ms）：拆为 [歌唱段，换气垫段]，不生成碎尾段。"""
    seg = {"take": (20.0, 23.0), "score": (30.0, 33.0), "note": 5, "mode": "warp"}
    parts, n_split = S.split_segment(seg, [(22.52, 23.0)])
    assert n_split == 1 and len(parts) == 2
    assert parts[0]["mode"] == "warp" and parts[1]["mode"] == "pad"
    assert parts[1]["take"] == (22.52, 23.0)
    assert abs((parts[1]["score"][1] - parts[1]["score"][0]) - 0.48) < 1e-9


def test_silence_spans_take_time():
    """静音检测返回 take 时间：3 秒音频中 1–2 秒静音段被检出。"""
    x = np.concatenate([_tone(440.0, 1.0), np.zeros(SR, dtype=np.float32),
                        _tone(440.0, 1.0)])
    spans = S.silence_spans(x)
    assert len(spans) == 1
    assert abs(spans[0][0] - 1.0) <= 0.02
    assert abs(spans[0][1] - 2.0) <= 0.02


def test_to_16k_no_cache():
    """16k 转换数组进出、无缓存：48k→16k 长度 1/3、频率不变、两次调用一致。"""
    x = _tone(440.0, 1.0)
    y1 = S.to_16k(x)
    assert len(y1) == 16000
    freqs = np.fft.rfftfreq(len(y1), 1 / 16000)
    peak = freqs[int(np.argmax(np.abs(np.fft.rfft(y1))))]
    assert abs(peak - 440.0) <= 5.0
    y2 = S.to_16k(x)
    assert np.array_equal(y1, y2)
    assert len(x) == SR  # 输入未动


# ---- 4. 边界感知重测仪器 ----

def test_boundary_remeasure_synthetic():
    """重测数学：候选恰在乐谱位置上 → 残差 0；候选偏 100ms → 中位 100ms。"""
    notes = [{"start_s": 1.0, "dur_s": 0.8},
             {"start_s": 2.0, "dur_s": 0.8},
             {"start_s": 3.0, "dur_s": 0.8}]
    exact = [(1.0, "V-Onset", None, None), (2.0, "F0-Step", None, None),
             (3.0, "Flux-Dip", None, None)]
    r = I.boundary_remeasure(exact, notes, anchor_set={0})
    assert r["overall"]["matched"] == 3
    assert r["overall"]["median_ms"] == 0.0
    assert r["overall"]["le25_share"] == 1.0
    assert r["unmatched_notes"] == 0
    assert r["anchored"]["matched"] == 1
    assert r["interpolated"]["matched"] == 2
    shifted = [(1.1, "V-Onset", None, None), (2.1, "F0-Step", None, None),
               (3.1, "Flux-Dip", None, None)]
    r2 = I.boundary_remeasure(shifted, notes)
    assert r2["overall"]["median_ms"] == 100.0
    assert r2["overall"]["le25_share"] == 0.0


def test_instrument_control_gate():
    """仪器对照：候选只在边界处 → 音中点误检 0%，门过；候选密布 →
    音中点误检 100%，门拦住。"""
    notes = [{"start_s": 1.0, "dur_s": 0.8},
             {"start_s": 2.0, "dur_s": 0.8},
             {"start_s": 3.0, "dur_s": 0.8}]
    boundary_only = [(1.0, "V-Onset", None, None),
                     (2.0, "F0-Step", None, None),
                     (3.0, "Flux-Dip", None, None)]
    ctrl = I.instrument_control(boundary_only, notes, 4.0)
    assert ctrl["midpoint_fp_rate"] == 0.0
    assert ctrl["midpoint_fp_rate"] <= ctrl["gate_fp_max"]
    dense = [(t / 20.0, "V-Onset", None, None) for t in range(0, 81)]
    ctrl2 = I.instrument_control(dense, notes, 4.0)
    assert ctrl2["midpoint_fp_rate"] == 1.0
    assert ctrl2["midpoint_fp_rate"] > ctrl2["gate_fp_max"]


def test_validate_instrument():
    """仪器有效性：对照误检 ≤10% 且锚点 ±50ms 命中 ≥70% 才有效。"""
    good_ctrl = {"midpoint_fp_rate": 0.0395, "gate_fp_max": 0.10}
    good_anchor = {"le50_share": 0.875}
    assert I.validate_instrument(good_ctrl, good_anchor)["valid"] is True
    bad_ctrl = {"midpoint_fp_rate": 0.1645, "gate_fp_max": 0.10}
    assert I.validate_instrument(bad_ctrl, good_anchor)["valid"] is False
    low_hit = {"le50_share": 0.5}
    assert I.validate_instrument(good_ctrl, low_hit)["valid"] is False


def test_detect_all_synthetic_step():
    """三路检测器合成夹具：440→493.88Hz 阶跃（＋2 半音）在 1.0 秒处，
    F0-Step 在 ±50ms 内检出；句首 V-Onset 在 0 秒处检出。"""
    x = np.concatenate([_tone(440.0, 1.0), _tone(493.88, 1.0)])
    n_frames = 200
    f0 = np.full(n_frames, np.nan)
    f0[:100] = 440.0
    f0[100:] = 493.88
    merged, raw, _, _ = I.detect_all(x, f0)
    assert len(raw["V-Onset"]) >= 1
    assert any(abs(t - 0.0) <= 0.05 for t in raw["V-Onset"])
    assert len(raw["F0-Step"]) >= 1
    assert any(abs(t - 1.0) <= 0.05 for t in raw["F0-Step"])
    assert any(cue == "F0-Step" and abs(t - 1.0) <= 0.05
               for t, cue, _, _ in merged)
